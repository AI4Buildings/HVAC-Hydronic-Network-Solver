"""Stationäre Energiegleichung: Upwind-Advektion auf dem gelösten Strömungsfeld.

Läuft strikt nach der Hydraulik. Gauss-Seidel-Sweeps: je Kante liefert das
Komponentenmodell T_aus = f(T_upwind, |ṁ|); je Knoten ideale Mischung aller
Zuströme plus Randzuflüsse und UA-Verluste. Geschlossene Kreise sind
Kontraktionen (|∂T_aus/∂T_ein| ≤ 1), daher konvergiert die Fixpunktiteration.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..exceptions import ConvergenceError
from ..fluids import Fluid
from ..network import CompiledNetwork
from .hydraulic import HydraulicState
from .settings import SolverSettings


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

    q = hyd.q
    m_dot = fluid.rho * q                       # signiert, in Kanten-Definitionsrichtung
    up = np.where(q >= 0.0, [e.node_from for e in net.edges], [e.node_to for e in net.edges])
    down = np.where(q >= 0.0, [e.node_to for e in net.edges], [e.node_from for e in net.edges])
    m_abs = np.abs(m_dot)

    # Umgebungszufluss an Druck-Randknoten: Nettoabfluss über Kanten kommt aus
    # der Umgebung (Vorzeichen aus Kontinuität)
    a_q = np.zeros(n)
    for e in net.edges:
        a_q[e.node_from] += q[e.index]
        a_q[e.node_to] -= q[e.index]
    env_in = np.zeros(n)                         # [m³/s] aus der Umgebung ins Netz
    for nd in net.nodes:
        if nd.pinned and not nd.is_auto_ref:
            env_in[nd.index] = a_q[nd.index] - nd.flow_bc

    t_node = np.full(n, s.t_init)
    t_out = np.full(m, s.t_init)
    q_dot = np.zeros(m)
    extras: list[dict] = [{} for _ in range(m)]

    incoming: list[list[int]] = [[] for _ in range(n)]
    for e in net.edges:
        if m_abs[e.index] >= s.m_dot_eps:
            incoming[down[e.index]].append(e.index)

    converged = False
    it = 0
    alpha = 1.0
    t_prev = t_node.copy()
    t_prev2 = None
    t_half = None                       # Schnappschuss zur Drift-Erkennung
    for it in range(1, s.max_iter_thermal + 1):
        delta = 0.0
        for e in net.edges:
            i = e.index
            if m_abs[i] < s.m_dot_eps or e.thermal_fn is None:
                # tote/adiabate Kante: Durchreichen ohne Wärmestrom
                t_out[i] = t_node[up[i]]
                q_dot[i] = 0.0
                continue
            res = e.thermal_fn(t_node[up[i]], m_abs[i], fluid)
            t_out[i], q_dot[i], extras[i] = res.t_out, res.q_dot, res.extras

        for nd in net.nodes:
            i = nd.index
            num = 0.0
            den = 0.0
            for ei in incoming[i]:
                num += m_abs[ei] * fluid.cp * t_out[ei]
                den += m_abs[ei] * fluid.cp
            for qb, tb in nd.bc_supplies:       # jede Fluss-RB mit eigener Zulauftemperatur
                if qb > 0.0:
                    c_bc = fluid.rho * qb * fluid.cp
                    num += c_bc * tb
                    den += c_bc
            if env_in[i] > 0.0 and nd.t_supply is not None:   # Zustrom über Druck-RB
                c_bc = fluid.rho * env_in[i] * fluid.cp
                num += c_bc * nd.t_supply
                den += c_bc
            if nd.ua > 0.0:
                num += nd.ua * nd.t_amb
                den += nd.ua
            if den > 1e-12:
                t_new = alpha * (num / den) + (1.0 - alpha) * t_node[i]
                delta = max(delta, abs(t_new - t_node[i]))
                t_node[i] = t_new
        if delta < s.tol_t:
            converged = True
            break
        if t_prev2 is not None and delta > s.tol_t:
            step2 = float(np.max(np.abs(t_node - t_prev2)))
            # Periode-2-Grenzzyklus (Eigenwert ≈ −1, z.B. durch die q_max-Klemme
            # eines Erzeugers): Feld kehrt nach 2 Sweeps fast zurück → dämpfen.
            # (Stagnierende Advektionsfronten erfüllen das nicht: dort wandert
            # die Front weiter, |T_k − T_{k−2}| bleibt groß.)
            if step2 < 0.1 * delta and alpha > 0.3:
                alpha = max(alpha / 2.0, 0.3)
        t_prev2 = t_prev
        t_prev = t_node.copy()
        if it == s.max_iter_thermal // 2:
            t_half = t_node.copy()

    if not converged:
        # Drift-Erkennung: hat sich das Feld seit der Halbzeit weit bewegt,
        # obwohl der Sweep-Fehler stagniert, sinkt/steigt ein thermisch
        # isolierter Umlauf mit fester Leistung ohne Grenze.
        drifting = (t_half is not None
                    and float(np.max(np.abs(t_node - t_half))) > 10.0 * delta)
        hint = ""
        if drifting:
            hint = (" Die Temperaturen driften mit konstanter Rate: vermutlich zirkuliert "
                    "ein Teilkreis thermisch isoliert (kein Zustrom, kein UA-Verlust) mit "
                    "fest vorgegebener Leistung (q_prescribed/prescribed_q) – dafür existiert "
                    "keine stationäre Lösung. Abhilfe: UA-Verlust angeben, physikalisches "
                    "Wärmeübertragermodell verwenden oder nur hydraulisch rechnen "
                    "(net.solve(thermal=False)).")
        raise ConvergenceError(
            f"Thermik-Solver nicht konvergiert nach {s.max_iter_thermal} Sweeps "
            f"(max. Temperaturänderung {delta:.2e} K).{hint}")

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
