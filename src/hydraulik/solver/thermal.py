"""Stationäre Energiegleichung: Upwind-Advektion auf dem gelösten Strömungsfeld.

Läuft strikt nach der Hydraulik. Unbekannte sind die Knotentemperaturen T.
Je Kante liefert das Komponentenmodell T_aus = f(T_upwind, |ṁ|), je Knoten
gilt ideale Mischung aller Zuströme plus Randzuflüsse und UA-Verluste:

    T = G(T),   G_j(T) = (Σ_zu ṁ_e·cp·f_e(T_up(e)) + b_j) / D_j

Gelöst wird F(T) = G(T) − T = 0 mit einem Newton-Verfahren auf der
Knotenbilanz: ∂G/∂T ist dünn besetzt (ein Eintrag je durchströmter Kante,
Steigung ∂T_aus/∂T_ein per Differenzenquotient — generisch für jedes
Komponentenmodell, ohne neuen Vertrag), das System (I − ∂G/∂T)·δ = F wird
direkt gelöst. Für lineare Netze (Rohre, Mischung, Speicher) ist EIN Schritt
exakt — unabhängig vom Rezirkulationsverhältnis, an dem die frühere
Gauss-Seidel-Iteration (Kontraktionsfaktor nahe 1) hunderte Sweeps brauchte;
nichtlineare Modelle (Heizkörper, Register, Klemmen) konvergieren quadratisch.

Globalisierung (Levenberg–Marquardt mit Vertrauensbereich Δ): Ist die
Linearisierung singulär oder der Newton-Schritt länger als Δ — typisch an
Umläufen aus lauter Kanten mit Steigung 1 (Erzeuger an der q_max-Klemme,
Heizkörper „aus" am Startfeld, feste Leistungen) — wird stattdessen
(I − ∂G/∂T + μI)·δ = F mit μ = |F|/Δ gelöst: im singulären Unterraum wird δ
zum gedämpften Fixpunktschritt, sonst bleibt es Newton. Armijo-Liniensuche
auf max|F| entscheidet über Annahme; da alle Modelle nicht-expansiv sind
(0 ≤ ∂T_aus/∂T_ein ≤ 1, Mischung konvex), kann ein kleiner gedämpfter
Schritt das Residuum nie vergrößern — die frühere Grenzzyklus-Dämpfung
entfällt. Ein gedämpfter Schritt, der das Residuum unverändert lässt, ist
ein Stillstand (G entlang δ affin-identisch): Bewegung ist dort frei, Δ
verdoppelt sich, bis eine Klemme verlassen ist; übersteigt die Verschiebung
1e6 K ohne Änderung, existiert keine stationäre Lösung (thermisch
isolierter Umlauf mit fester Leistung) — die Fehlermeldung nennt es beim
Namen. Wächst das Residuum in jede Richtung, schrumpft Δ (bzw. der nächste
Schritt wird gedämpft, wenn die Newton-Richtung selbst unbrauchbar war).
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
import scipy.sparse as sp
from scipy.sparse.linalg import MatrixRankWarning, spsolve

from ..exceptions import ComponentModelError, ConvergenceError, HydraulikError
from ..fluids import Fluid
from ..network import CompiledNetwork
from .hydraulic import HydraulicState
from .settings import SolverSettings

#: Differenzenschritt [K] für die Kantensteigung ∂T_aus/∂T_ein: absolut
#: 1e-3 K (robust gegen Modellrauschen, z.B. brentq-Toleranz), relativ
#: 1e-6·|T| bei großen Temperaturen (sonst dominiert die Gleitkomma-Rundung
#: die Steigung und macht singuläre Umläufe fälschlich regulär)
_FD_STEP = 1e-3
_FD_REL = 1e-6
#: Armijo-Liniensuche: kleinster Schrittfaktor (1, ½, ¼, …)
_LAMBDA_MIN = 1.0 / 128.0
#: Vertrauensbereich [K]: Startwert und Untergrenze (darunter gilt „festgefahren")
_TRUST_INIT = 1e3
_TRUST_MIN = 1e-9
#: Stillstand: gedämpfter Schritt ohne Abstieg, der das Residuum um höchstens
#: diesen Anteil wachsen lässt (Toleranz für Rundung bei großen Temperaturen)
_STALL_TOL = 1e-3
#: Kumulierte Verschiebung [K] aufeinanderfolgender Stillstand-Schritte, ab
#: der keine stationäre Lösung existiert (isolierter Umlauf mit fester Leistung)
_DRIFT_DISPLACEMENT_K = 1e6


@dataclass
class ThermalState:
    t_node: np.ndarray                # Knotentemperaturen [°C]
    t_edge_out: np.ndarray            # Austrittstemperatur je Kante (in Flussrichtung) [°C]
    q_dot_edge: np.ndarray            # Wärmestrom ins Wasser je Kante [W]
    edge_extras: list[dict]
    stagnant_nodes: list[int]
    iterations: int
    converged: bool
    energy_imbalance: float           # globale Bilanzabweichung [W]


def skipped_thermal(net: CompiledNetwork, settings: SolverSettings | None = None) -> ThermalState:
    """Neutraler Zustand für rein hydraulische Rechnungen (solve(thermal=False))."""
    s = settings or SolverSettings()
    n, m = len(net.nodes), len(net.edges)
    return ThermalState(t_node=np.full(n, s.t_init), t_edge_out=np.full(m, s.t_init),
                        q_dot_edge=np.zeros(m), edge_extras=[{} for _ in range(m)],
                        stagnant_nodes=[], iterations=0, converged=True,
                        energy_imbalance=0.0)


def solve_thermal(net: CompiledNetwork, hyd: HydraulicState,
                  settings: SolverSettings | None = None) -> ThermalState:
    s = settings or SolverSettings()
    fluid: Fluid = net.fluid
    n, m = len(net.nodes), len(net.edges)
    edges = net.edges

    q = np.asarray(hyd.q, dtype=float)
    m_abs = np.abs(fluid.rho * q)                       # |ṁ| je Kante
    n_from = np.array([e.node_from for e in edges], dtype=int)
    n_to = np.array([e.node_to for e in edges], dtype=int)
    fwd = q >= 0.0
    up = np.where(fwd, n_from, n_to)                    # stromauf liegender Knoten
    down = np.where(fwd, n_to, n_from)

    # Umgebungszufluss an Druck-Randknoten: Nettoabfluss über Kanten kommt aus
    # der Umgebung (Vorzeichen aus Kontinuität)
    a_q = np.zeros(n)
    np.add.at(a_q, n_from, q)
    np.add.at(a_q, n_to, -q)
    env_in = np.zeros(n)                                # [m³/s] aus der Umgebung ins Netz
    for nd in net.nodes:
        if nd.pinned and not nd.is_auto_ref:
            env_in[nd.index] = a_q[nd.index] - nd.flow_bc

    flowing = np.flatnonzero(m_abs >= s.m_dot_eps)      # Kanten, die Enthalpie transportieren
    modelled = [int(i) for i in flowing if edges[i].thermal_fn is not None]
    c_edge = m_abs * fluid.cp                           # Kapazitätsstrom je Kante [W/K]
    incoming: list[list[int]] = [[] for _ in range(n)]
    for i in flowing:
        incoming[down[i]].append(int(i))

    # Knotenbilanz: G_j = (Σ c_e·T_aus,e + b_j) / D_j
    den = np.zeros(n)
    b_const = np.zeros(n)
    for nd in net.nodes:
        i = nd.index
        den[i] += sum(c_edge[k] for k in incoming[i])
        for qb, tb in nd.bc_supplies:                   # jede Fluss-RB mit eigener Zulauftemperatur
            if qb > 0.0:
                c_bc = fluid.rho * qb * fluid.cp
                den[i] += c_bc
                b_const[i] += c_bc * tb
        if env_in[i] > 0.0 and nd.t_supply is not None:   # Zustrom über Druck-RB
            c_bc = fluid.rho * env_in[i] * fluid.cp
            den[i] += c_bc
            b_const[i] += c_bc * nd.t_supply
        if nd.ua > 0.0:
            den[i] += nd.ua
            b_const[i] += nd.ua * nd.t_amb
    free = den > 1e-12                                  # Knoten mit Bilanz; sonst T_init
    inv_den = np.zeros(n)
    inv_den[free] = 1.0 / den[free]

    def model(i: int, t_in: float, where: str):
        e = edges[i]
        try:
            return e.thermal_fn(float(t_in), float(m_abs[i]), fluid)
        except HydraulikError:
            raise
        except Exception as exc:                        # Modellfehler lesbar einhüllen
            raise ComponentModelError(
                e.name, e.component.type_name, "thermisches",
                f"T_ein = {float(t_in):.2f} °C, ṁ = {float(m_abs[i]):.3e} kg/s ({where})",
                exc) from exc

    def evaluate(t: np.ndarray, where: str):
        """G(T) samt Kantenergebnissen (T_aus, Q̇, extras) zum Feld t."""
        t_out = t[up].copy() if m else np.zeros(0)      # tote/adiabate Kanten: Durchreichen
        q_dot = np.zeros(m)
        extras: list[dict] = [{} for _ in range(m)]
        for i in modelled:
            res = model(i, t[up[i]], where)
            t_out[i], q_dot[i], extras[i] = res.t_out, res.q_dot, res.extras
        num = b_const.copy()
        if flowing.size:
            np.add.at(num, down[flowing], c_edge[flowing] * t_out[flowing])
        g = np.where(free, num * inv_den, t)
        return g, t_out, q_dot, extras

    def jacobian(t: np.ndarray, t_out: np.ndarray, where: str):
        """∂G/∂T: je durchströmter Kante c_e·f_e'/D_j an (down, up)."""
        rows, cols, vals = [], [], []
        for i in flowing:
            j, k = int(down[i]), int(up[i])
            if edges[i].thermal_fn is None:
                slope = 1.0
            else:
                h = max(_FD_STEP, _FD_REL * abs(float(t[k])))
                slope = (model(i, t[k] + h, where).t_out - t_out[i]) / h
            rows.append(j)
            cols.append(k)
            vals.append(c_edge[i] * slope * inv_den[j])
        return sp.coo_matrix((vals, (rows, cols)), shape=(n, n)).tocsc()

    def solve_linear(mat, rhs):
        """Direkte Lösung; None bei (numerisch) singulärer Matrix."""
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", MatrixRankWarning)
            try:
                x = np.atleast_1d(spsolve(mat, rhs))
            except RuntimeError:                        # exakt singulär
                return None
        return x if np.all(np.isfinite(x)) else None

    identity = sp.identity(n, format="csc")
    t = np.full(n, float(s.t_init))
    g, t_out, q_dot, extras = evaluate(t, "Startfeld")
    F = g - t
    err = float(np.max(np.abs(F))) if n else 0.0
    trust = _TRUST_INIT                                 # Vertrauensbereich [K]
    A = None                                            # Linearisierung am aktuellen Feld
    force_lm = False                                    # Newton-Richtung zuletzt unbrauchbar
    stall_disp = 0.0                                    # Verschiebung seit letztem Abstieg [K]
    it = 0
    converged = err < s.tol_t
    while not converged and it < s.max_iter_thermal:
        it += 1
        where = f"Newton-Iteration {it}"

        # 1. Richtung: Newton-Schritt (I − ∂G/∂T)·δ = F; ist das System singulär
        #    (Umlauf aus lauter Steigung-1-Kanten, z.B. Erzeuger an der Klemme,
        #    Heizkörper „aus" am Startfeld) oder der Schritt größer als der
        #    Vertrauensbereich, Levenberg-Marquardt-Dämpfung (A + μI)·δ = F mit
        #    μ = |F|/Δ — im singulären Unterraum wird δ zum Fixpunktschritt.
        if A is None:
            A = identity - jacobian(t, t_out, where + ", Differenzenquotient")
        delta = None if force_lm else solve_linear(A, F)
        bounded = delta is None or float(np.max(np.abs(delta))) > trust
        if bounded:
            delta = solve_linear(A + (err / trust) * identity, F)
            if delta is None:
                raise ConvergenceError(
                    f"Thermik-Solver: Linearisierung in Iteration {it} auch gedämpft "
                    f"singulär (max. Bilanzabweichung {err:.2e} K).")
            nrm = float(np.max(np.abs(delta)))
            if nrm > trust:
                delta *= trust / nrm

        # 2. Armijo-Liniensuche auf max|F|. Ein voller GEDÄMPFTER Schritt, der
        #    das Residuum praktisch unverändert lässt, ist ein Stillstand: G ist
        #    entlang δ affin-identisch (Klemme oder isolierter Umlauf) — Bewegung
        #    ist dort frei, der Vertrauensbereich verdoppelt sich, bis die Klemme
        #    verlassen ist oder die Verschiebung jede Lösung ausschließt. (Für
        #    volle Newton-Schritte gilt das nicht: ein Wechsel zwischen zwei
        #    Klemmzuständen gleicher Abweichung wird per λ-Halbierung aufgelöst.)
        lam, outcome = 1.0, None
        while lam >= _LAMBDA_MIN:
            t_new = t + lam * delta
            g_new, t_out_new, q_dot_new, extras_new = evaluate(t_new, where)
            F_new = g_new - t_new
            err_new = float(np.max(np.abs(F_new)))
            if err_new <= (1.0 - 1e-4 * lam) * err:
                outcome = "abstieg"
                break
            if bounded and lam == 1.0 and err_new <= (1.0 + _STALL_TOL) * err:
                outcome = "stillstand"
                break
            lam *= 0.5
        if outcome is None:                             # Residuum wächst in jede Richtung
            if bounded:                                 # Vertrauensbereich zu groß
                trust *= 0.25
                if trust < _TRUST_MIN:
                    raise ConvergenceError(
                        f"Thermik-Solver festgefahren nach {it} Iterationen (max. "
                        f"Bilanzabweichung {err:.2e} K, kein Abstieg mehr möglich).")
            else:                                       # Newton-Richtung selbst unbrauchbar
                force_lm = True                         # (nahezu singulär): nächster Schritt gedämpft
            continue
        force_lm = False
        step = lam * float(np.max(np.abs(delta)))
        if outcome == "stillstand":
            stall_disp += step
            if stall_disp > _DRIFT_DISPLACEMENT_K:
                affected = [net.nodes[i].label for i in np.flatnonzero(np.abs(F) > s.tol_t)]
                raise ConvergenceError(
                    f"Thermik-Solver: keine stationäre Lösung — die Energiebilanz ändert sich "
                    f"auch bei beliebig großer Temperaturverschiebung nicht (Residuum {err:.2e} K "
                    f"konstant; Knoten: {', '.join(affected[:5])}"
                    f"{', …' if len(affected) > 5 else ''}). Vermutlich zirkuliert ein "
                    f"Teilkreis thermisch isoliert (kein Zustrom, kein UA-Verlust) mit fest "
                    f"vorgegebener Leistung (q_prescribed/prescribed_q, ggf. ein an q_max "
                    f"geklemmter Erzeuger) – dafür existiert keine stationäre Lösung. Abhilfe: "
                    f"UA-Verlust angeben, physikalisches Wärmeübertragermodell verwenden oder "
                    f"nur hydraulisch rechnen (net.solve(thermal=False)).")
            trust = 2.0 * max(trust, step)
        else:
            stall_disp = 0.0
            if bounded:
                trust = 2.0 * step                      # λ = 1: wachsen, sonst schrumpfen
            else:
                trust = max(trust, 2.0 * step)

        t, g, t_out, q_dot, extras, F, err = t_new, g_new, t_out_new, q_dot_new, extras_new, F_new, err_new
        A = None
        converged = err < s.tol_t

    if not converged:
        raise ConvergenceError(
            f"Thermik-Solver nicht konvergiert nach {it} Iterationen "
            f"(max. Bilanzabweichung {err:.2e} K). Abhilfe: settings.max_iter_thermal erhöhen.")

    t_node = t
    stagnant = [nd.index for nd in net.nodes
                if not incoming[nd.index]
                and (nd.flow_bc + env_in[nd.index]) <= 0.0 and nd.ua <= 0.0]

    # Globale Energiebilanz: Kantenwärmeströme + UA-Verluste + Randenthalpien
    balance = float(np.sum(q_dot))
    for nd in net.nodes:
        if nd.ua > 0.0:
            balance += nd.ua * (nd.t_amb - t_node[nd.index])
        for qb, tb in nd.bc_supplies:
            t_ref = tb if qb > 0.0 else t_node[nd.index]
            balance += fluid.rho * qb * fluid.cp * t_ref
        q_env = env_in[nd.index]
        if q_env > 0.0 and nd.t_supply is not None:
            balance += fluid.rho * q_env * fluid.cp * nd.t_supply
        elif q_env < 0.0:
            balance += fluid.rho * q_env * fluid.cp * t_node[nd.index]

    return ThermalState(t_node=t_node, t_edge_out=t_out, q_dot_edge=q_dot,
                        edge_extras=extras, stagnant_nodes=stagnant,
                        iterations=it, converged=converged,
                        energy_imbalance=balance)
