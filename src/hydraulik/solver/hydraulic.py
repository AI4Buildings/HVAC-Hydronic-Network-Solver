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

from ..exceptions import ComponentModelError, ConvergenceError, HydraulikError
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
    # Adaptiver Floor je Kante: bleibt eine Kante im Floor-Bereich bei stabiler
    # Strömungsrichtung (z.B. fast geschlossenes Ventil, Gleichgewichtsstrom
    # weit unter q_eps), schrumpft er je Iteration ×0.1 (bis 1e-4 des
    # Standards, nicht unter 1 Pa/(m³/s)) — sonst dämpfte der Floor Newton
    # dort bis zu tausendfach.
    # Ein Vorzeichenwechsel (pendelnde Rückschlagklappe) setzt ihn zurück.
    floor_frac = r_floor_frac.copy()

    alpha_p, alpha_q = s.alpha_p, s.alpha_q
    history: list[tuple[float, float]] = []
    rising = falling = 0

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

    def coefficients(e, q_e: float, it: int):
        try:
            return e.coeff_fn(q_e, fluid)
        except HydraulikError:
            raise
        except Exception as exc:                     # Modellfehler lesbar einhüllen
            raise ComponentModelError(
                e.name, e.component.type_name, "hydraulisches",
                f"V̇ = {float(q_e) * 3600:.4g} m³/h (Iteration {it})", exc) from exc

    n_from = np.array([e.node_from for e in net.edges], dtype=int)
    n_to = np.array([e.node_to for e in net.edges], dtype=int)

    # Eigenschleifen (Ein- und Austritt am selben Knoten, z.B. kurzgeschlossenes
    # Bauteil): Δp ≡ 0, die Kante ist vom Netz entkoppelt und ihre Gleichung
    # R(Q) = Δp_Quelle skalar. Exakt vorab lösen — im Netzverbund konvergiert
    # die doppelte Nullstelle passiver Kanten (b·Q|Q| = 0) nur linear und
    # bliebe beim Startwert-Bruchteil stehen.
    coupled_idx = {i for _, idxs in coupled for i in idxs}
    for e in net.edges:
        i = e.index
        if e.node_from == e.node_to and not fixed[i] and i not in coupled_idx:
            root = _self_loop_flow(e, float(seeds[i]), coefficients)
            if root is not None:
                fixed[i], q_fix[i], q[i] = True, root, root

    mass_res = mom_res = np.inf
    step_ok = False                                  # letzte Newton-Korrektur klein?
    updates = 0
    for it in range(1, s.max_iter + 2):
        # 1. Koeffizienten beim AKTUELLEN Q auswerten
        for comp, idxs in coupled:
            try:
                comp.pre_coefficients([float(q[i]) for i in idxs], fluid)
            except HydraulikError:
                raise
            except Exception as exc:                 # Modellfehler lesbar einhüllen
                raise ComponentModelError(
                    comp.name, comp.type_name, "hydraulisches",
                    f"Volumenströme {[round(float(q[i]) * 3600, 4) for i in idxs]} m³/h "
                    f"(Iteration {it})", exc) from exc
        for e in net.edges:
            c = coefficients(e, q[e.index], it)
            a_arr[e.index], b_arr[e.index], dp_src[e.index] = c.a, c.b, c.dp_source

        # 2. Residuen des aktuellen Zustands — mit den Koeffizienten DIESES
        #    Zustands. (Früher nach dem Update mit den Koeffizienten des alten Q:
        #    stark Q-abhängige Widerstände, z.B. Rohre mit laminarem Startwert,
        #    wurden so nach einem Schritt fälschlich als konvergiert gemeldet.)
        dp_nodes = p[n_from] - p[n_to]
        r_edge = a_arr * q + b_arr * q * np.abs(q)
        mom_defect = dp_nodes + dp_src - r_edge
        q_scale = max(float(np.max(np.abs(q))), 1e-9)
        mass_vec = (A @ q - sources)[~pinned]
        mass_res = float(np.max(np.abs(mass_vec))) / q_scale if mass_vec.size else 0.0
        mom_vec = np.where(fixed, 0.0, mom_defect)
        dp_scale = max(float(np.max(np.abs(dp_src))), float(np.max(np.abs(r_edge))), 1e3)
        mom_res = float(np.max(np.abs(mom_vec))) / dp_scale
        history.append((mass_res, mom_res))

        if not np.isfinite(mass_res) or not np.isfinite(mom_res):
            raise ConvergenceError(
                f"Hydraulik-Solver divergiert (NaN/Inf in Iteration {it}).", history)
        # Konvergenz: Residuen klein UND letzte Volumenstrom-Korrektur klein —
        # das Impulsresiduum allein (relativ zum GLOBALEN Druckmaßstab) legt
        # Ströme mit kleinem Δp nicht fest (widerstandsarme oder antriebslose
        # Maschen: dort konvergiert Newton nur linear).
        if mass_res < s.tol_mass_rel and mom_res < s.tol_mom_rel and step_ok:
            return HydraulicState(p, q, updates, mass_res, mom_res, True, history)
        if updates >= s.max_iter:
            break

        # Divergenz-Wächter: steigt der Impulsdefekt 5× in Folge, Relaxation
        # halbieren (bis minimal 0.1). Die Dämpfung ist nur vorübergehend: nach
        # 3 Abnahmen in Folge wieder verdoppeln (bis zum eingestellten Wert) —
        # sonst liefe Newton nach einer unruhigen Anlaufphase dauerhaft
        # gedämpft und konvergierte nur noch linear (≈ 1 % je Iteration).
        if len(history) > 1 and history[-1][1] > history[-2][1]:
            rising, falling = rising + 1, 0
            if rising >= 5 and alpha_q > 0.1:
                alpha_p, alpha_q, rising = max(alpha_p / 2, 0.1), max(alpha_q / 2, 0.1), 0
        else:
            rising, falling = 0, falling + 1
            if falling >= 3 and (alpha_q < s.alpha_q or alpha_p < s.alpha_p):
                alpha_p, alpha_q = min(alpha_p * 2, s.alpha_p), min(alpha_q * 2, s.alpha_q)
                falling = 0

        # 3. Jacobi-Steigung J = dR/dQ der Kantenimpulsgleichung. a + 2b|Q| ist
        #    exakt nur für Q-unabhängige a, b; hängen sie von Q ab (Reibungs-
        #    beiwert im laminar-turbulenten Übergang), unterschätzt sie die wahre
        #    Steigung bis Faktor ~3 → Newton schießt über und pendelt. Daher
        #    zusätzlich der Differenzenquotient aus dem Komponentenmodell
        #    (generisch, kein neuer Vertrag); das Maximum ist nie zu flach.
        r_floor = np.maximum(b_arr * floor_frac, 1e-3)    # min. 1e-3 Pa/(m³/s)
        jac = a_arr + 2.0 * b_arr * np.abs(q)
        h_fd = 1e-6 * np.maximum(np.abs(q), floor_frac)
        r_now = r_edge - dp_src
        for e in net.edges:
            i = e.index
            if fixed[i]:
                continue
            q_h = q[i] + h_fd[i]
            c = coefficients(e, q_h, it)
            slope = (c.a * q_h + c.b * q_h * abs(q_h) - c.dp_source - r_now[i]) / h_fd[i]
            if np.isfinite(slope) and slope > jac[i]:
                jac[i] = slope
        floor_limited = jac < r_floor
        jac = np.maximum(jac, r_floor)

        # 4. Impulsprädiktor (Newton-Inkrement der Kantenimpulsgleichung)
        q_star = q + alpha_q * mom_defect / jac
        q_star[fixed] = q_fix[fixed]

        # 5. Druckkorrektur-System (gleiche Jacobi-Steigung → Schur-Komplement)
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

        # 6. Korrektur & Update (Residuen prüft der nächste Iterationsanfang
        #    mit den dann neu ausgewerteten Koeffizienten)
        dpc = p_corr[n_from] - p_corr[n_to]
        q_new = q_star + d * dpc
        q_new[fixed] = q_fix[fixed]
        step_tol = np.maximum(1e-6 * max(float(np.max(np.abs(q_new))), 1e-9), 2.0 * floor_frac)
        step_ok = bool(np.all(np.abs(q_new - q) <= step_tol))
        flipped = np.sign(q_new) * np.sign(q) < 0.0
        # Untergrenze: 1e-4 des Standards, aber nie unter 1 Pa/(m³/s) als
        # Floor-Wert — widerstandsarme Kanten (Link, Sensor) behalten ihren
        # Standard-Floor: dort machte ein kleineres J das 1/J so groß, dass
        # Rundungsfehler der Druckkorrektur die Kontinuität verfehlen
        frac_min = np.maximum(r_floor_frac * 1e-4,
                              np.minimum(r_floor_frac, 1.0 / np.maximum(b_arr, 1e-300)))
        floor_frac = np.where(flipped, r_floor_frac,
                              np.where(floor_limited, np.maximum(floor_frac * 0.1, frac_min),
                                       floor_frac))
        p = p + alpha_p * p_corr
        p[pinned] = p_bc[pinned]
        q = q_new
        updates += 1

    raise ConvergenceError(
        f"Hydraulik-Solver nicht konvergiert nach {s.max_iter} Iterationen "
        f"(Massendefekt {mass_res:.2e}, Impulsdefekt {mom_res:.2e}). "
        f"Tipp: alpha_p/alpha_q reduzieren oder Startwerte (q_nom) angeben.", history)


def _self_loop_flow(e, seed: float, coefficients) -> float | None:
    """Volumenstrom einer Eigenschleifen-Kante: Nullstelle der monotonen
    Kantenkennlinie f(Q) = a·Q + b·Q·|Q| − Δp_Quelle (Δp der Knoten ist 0).
    Bisektion mit wachsendem Intervall; None, falls kein Vorzeichenwechsel
    (dann löst die normale Iteration)."""
    def f(q: float) -> float:
        c = coefficients(e, q, 0)
        return c.a * q + c.b * q * abs(q) - c.dp_source

    if f(0.0) == 0.0:
        return 0.0
    hi = max(abs(seed), 1e-6)
    sign = 1.0 if f(0.0) < 0.0 else -1.0             # Nullstelle bei Q > 0 bzw. Q < 0
    while sign * f(sign * hi) < 0.0:
        hi *= 2.0
        if hi > 1e3:                                 # > 3.6e6 m³/h: kein sinnvoller Fall
            return None
    lo_q, hi_q = (0.0, hi) if sign > 0 else (-hi, 0.0)
    for _ in range(200):
        mid = 0.5 * (lo_q + hi_q)
        if f(mid) < 0.0:
            lo_q = mid
        else:
            hi_q = mid
    return 0.5 * (lo_q + hi_q)
