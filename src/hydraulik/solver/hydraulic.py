"""SIMPLE-artiger Druckkorrektur-Solver auf dem hydraulischen Netzgraphen.

Segregierter Ablauf je Iteration:
  1. Impulsprädiktor je Kante in Inkrementform (Newton-konsistent):
       Q* = Q_k + α_q · (p_i − p_j + Δp_source − (a·Q_k + b·Q_k·|Q_k|)) / J,
       J = a + 2b·|Q_k|  (Ableitung der Impulsgleichung, mit Floor gegen Q→0).
  2. Kontinuitätsdefekt an den Knoten → Druckkorrekturgleichung
       K·p' = r  mit  K = A·diag(1/J)·Aᵀ  (gewichteter Graph-Laplacian,
       SPD nach Pinning der Druck-Randknoten).
  3. Korrektur Q ← Q* + (1/J)·(p'_i − p'_j), Druck-Update p ← p + α_p·p'.

Mit α_p = α_q = 1 ist ein Iterationsschritt EXAKT ein Newton-Schritt des
gekoppelten Systems, gelöst über das Schur-Komplement des Druckblocks –
der Solver behält also die segregierte SIMPLE-Struktur (Prädiktor +
Druckkorrektur), konvergiert aber quadratisch. Die naive Prädiktorform
Q* = Δp/R_lin (Picard/„linear theory") oszilliert bei quadratischen
Widerständen bekanntermaßen und wird deshalb nicht verwendet.

Knoten (p) und Kanten (Q) sind konstruktiv versetzt angeordnet ("staggered"):
Checkerboarding kann auf dem Graphen nicht auftreten.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import scipy.sparse as sp
from scipy.sparse.linalg import spsolve

from ..exceptions import ConvergenceError
from ..network import CompiledNetwork
from .settings import SolverSettings


@dataclass
class HydraulicState:
    p: np.ndarray                 # Knotendrücke [Pa]
    q: np.ndarray                 # Kantenvolumenströme [m³/s]
    iterations: int
    mass_residual: float
    momentum_residual: float
    converged: bool
    residual_history: list[tuple[float, float]] = field(default_factory=list)


def solve_hydraulics(net: CompiledNetwork, settings: SolverSettings | None = None) -> HydraulicState:
    s = settings or SolverSettings()
    fluid = net.fluid
    n, m = len(net.nodes), len(net.edges)

    # Inzidenzmatrix A (n×m): +1 am Von-Knoten, −1 am Zu-Knoten
    rows, cols, data = [], [], []
    for e in net.edges:
        rows += [e.node_from, e.node_to]
        cols += [e.index, e.index]
        data += [1.0, -1.0]
    A = sp.csr_matrix((data, (rows, cols)), shape=(n, m))

    pinned = np.array([nd.pinned for nd in net.nodes])
    p_bc = np.array([nd.p_bc if nd.p_bc is not None else s.p_ref for nd in net.nodes])
    sources = np.array([nd.flow_bc for nd in net.nodes])
    fixed = np.array([e.is_fixed for e in net.edges], dtype=bool)

    if m == 0:
        # Netz ohne Kanten (nur verschmolzene Knoten mit Randbedingungen und
        # Fühlern): trivial — Drücke aus den Ankern, nichts zu iterieren.
        return HydraulicState(np.where(pinned, p_bc, s.p_ref).astype(float),
                              np.zeros(0), 0, 0.0, 0.0, True, [])
    q_fix = np.array([e.fixed_q if e.fixed_q is not None else 0.0 for e in net.edges])

    # Startwerte
    p = np.where(pinned, p_bc, s.p_ref).astype(float)
    seeds = np.array([e.q_seed if e.q_seed else s.q_init for e in net.edges])
    q = np.where(fixed, q_fix, seeds)
    r_floor_frac = np.abs(seeds) * s.q_eps_frac + 1e-12

    alpha_p, alpha_q = s.alpha_p, s.alpha_q
    history: list[tuple[float, float]] = []
    rising = 0

    a_arr = np.zeros(m)
    b_arr = np.zeros(m)
    dp_src = np.zeros(m)

    # Komponenten mit gekoppelten Kanten (z.B. T-Stück: ζ hängt vom
    # Volumenstromverhältnis der Geschwisterkanten ab) erhalten vor jeder
    # Koeffizientenauswertung ihre eigenen Kantenflüsse (Picard-nachgeführt).
    coupled: list[tuple[object, list[int]]] = []
    _seen: dict[int, list[int]] = {}
    for e in net.edges:
        if hasattr(e.component, "pre_coefficients"):
            key = id(e.component)
            if key not in _seen:
                _seen[key] = []
                coupled.append((e.component, _seen[key]))
            _seen[key].append(e.index)

    mass_res = mom_res = np.inf
    for it in range(1, s.max_iter + 1):
        # 1. Koeffizienten beim aktuellen Q auswerten
        for comp, idxs in coupled:
            comp.pre_coefficients([float(q[i]) for i in idxs], fluid)
        for e in net.edges:
            c = e.coeff_fn(q[e.index], fluid)
            a_arr[e.index], b_arr[e.index], dp_src[e.index] = c.a, c.b, c.dp_source

        r_floor = np.maximum(b_arr * r_floor_frac, 1e-3)  # min. 1e-3 Pa/(m³/s)
        jac = np.maximum(a_arr + 2.0 * b_arr * np.abs(q), r_floor)

        # 2. Impulsprädiktor (Newton-Inkrement der Kantenimpulsgleichung)
        dp_nodes = p[[e.node_from for e in net.edges]] - p[[e.node_to for e in net.edges]]
        mom_defect = dp_nodes + dp_src - (a_arr * q + b_arr * q * np.abs(q))
        q_star = q + alpha_q * mom_defect / jac
        q_star[fixed] = q_fix[fixed]

        # 3. Druckkorrektur-System (gleiche Jacobi-Steigung → Schur-Komplement)
        d = 1.0 / jac
        d[fixed] = 0.0
        K = (A @ sp.diags(d) @ A.T).tolil()
        r = sources - A @ q_star
        for i in np.flatnonzero(pinned):
            K.rows[i] = [i]
            K.data[i] = [1.0]
            r[i] = 0.0
        K = K.tocsc()
        # Spalten der gepinnten Knoten eliminieren (Symmetrie ist hier egal,
        # p' dort ist ohnehin 0, aber wir halten das System sauber):
        # -> stattdessen genügt Zeilen-Pinning + r=0, da p'_pinned = 0 folgt.

        # Jacobi-Skalierung gegen die riesigen SI-Größenordnungen
        diag = K.diagonal()
        scale = 1.0 / np.sqrt(np.maximum(np.abs(diag), 1e-30))
        S = sp.diags(scale)
        p_corr = S @ spsolve((S @ K @ S).tocsc(), scale * r)

        # 4. Korrektur & Update
        dpc = p_corr[[e.node_from for e in net.edges]] - p_corr[[e.node_to for e in net.edges]]
        q_new = q_star + d * dpc
        q_new[fixed] = q_fix[fixed]
        p = p + alpha_p * p_corr
        p[pinned] = p_bc[pinned]
        q = q_new

        # 5. Residuen (relativ)
        q_scale = max(float(np.max(np.abs(q))), 1e-9)
        mass_vec = (A @ q - sources)[~pinned]
        mass_res = float(np.max(np.abs(mass_vec))) / q_scale if mass_vec.size else 0.0

        dp_nodes = p[[e.node_from for e in net.edges]] - p[[e.node_to for e in net.edges]]
        mom_vec = dp_nodes + dp_src - (a_arr * q + b_arr * q * np.abs(q))
        mom_vec[fixed] = 0.0
        dp_scale = max(float(np.max(np.abs(dp_src))),
                       float(np.max(np.abs(a_arr * q + b_arr * q * np.abs(q)))), 1e3)
        mom_res = float(np.max(np.abs(mom_vec))) / dp_scale
        history.append((mass_res, mom_res))

        if not np.isfinite(mass_res) or not np.isfinite(mom_res):
            raise ConvergenceError(
                f"Hydraulik-Solver divergiert (NaN/Inf in Iteration {it}).", history)

        if mass_res < s.tol_mass_rel and mom_res < s.tol_mom_rel:
            return HydraulicState(p, q, it, mass_res, mom_res, True, history)

        # Divergenz-Wächter: steigt der Impulsdefekt 5× in Folge, Relaxation
        # halbieren (bis minimal 0.1)
        if len(history) > 1 and history[-1][1] > history[-2][1]:
            rising += 1
            if rising >= 5 and alpha_q > 0.1:
                alpha_p, alpha_q, rising = max(alpha_p / 2, 0.1), max(alpha_q / 2, 0.1), 0
        else:
            rising = 0

    raise ConvergenceError(
        f"Hydraulik-Solver nicht konvergiert nach {s.max_iter} Iterationen "
        f"(Massendefekt {mass_res:.2e}, Impulsdefekt {mom_res:.2e}). "
        f"Tipp: alpha_p/alpha_q reduzieren oder Startwerte (q_nom) angeben.", history)
