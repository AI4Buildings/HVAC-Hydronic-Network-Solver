"""Unabhängiger Prüfer: rechnet eine Solver-Lösung aus den Komponentenmodellen nach.

Nutzt nur die öffentlichen Komponentenverträge (coeff_fn/thermal_fn) und eigene
Bilanzformeln — NICHT die Residuen des Solvers.
"""
from __future__ import annotations

import math

import numpy as np

from hydraulik.solver.settings import SolverSettings
from hydraulik.solver.thermal import solve_thermal
from hydraulik.solver.uniqueness import solve_hydraulics_checked


def solve_raw(net, settings=None, thermal=True):
    """Wie Network.solve, aber mit Rückgabe der Solver-Zustände (kompiliertes
    Netz, Hydraulik, Thermik) für die unabhängige Nachrechnung."""
    s = settings or SolverSettings()
    comp = net.compile()
    hyd = solve_hydraulics_checked(comp, s)
    th = solve_thermal(comp, hyd, s) if thermal else None
    return comp, hyd, th


def check_hydraulics(comp, hyd, tol_mass=1e-7, tol_mom=1e-5):
    """Kontinuität + Impuls mit NEU ausgewerteten Koeffizienten bei der Endlösung."""
    fluid = comp.fluid
    issues = []
    n = len(comp.nodes)
    q = np.asarray(hyd.q, float)
    p = np.asarray(hyd.p, float)
    # Kontinuität (nur freie Knoten; Druck-RB-Knoten nehmen den Rest auf)
    bal = np.zeros(n)
    for e in comp.edges:
        bal[e.node_from] -= q[e.index]
        bal[e.node_to] += q[e.index]
    qscale = max(float(np.max(np.abs(q))) if q.size else 0.0, 1e-9)
    for nd in comp.nodes:
        if nd.pinned:
            continue
        r = bal[nd.index] + nd.flow_bc
        if abs(r) > tol_mass * qscale + 1e-12:
            issues.append(f"Kontinuität verletzt an '{nd.label}': {r*3600:.3e} m³/h")
    # Impuls: Koeffizienten bei der Endlösung neu auswerten (inkl. pre_coefficients)
    coupled = {}
    for e in comp.edges:
        if hasattr(e.component, "pre_coefficients"):
            coupled.setdefault(id(e.component), (e.component, []))[1].append(e.index)
    for c, idxs in coupled.values():
        c.pre_coefficients([float(q[i]) for i in idxs], fluid)
    dps = []
    for e in comp.edges:
        c = e.coeff_fn(float(q[e.index]), fluid)
        dps.append(abs(c.dp_source))
        dps.append(abs(c.a * q[e.index] + c.b * q[e.index] * abs(q[e.index])))
    dpscale = max(max(dps) if dps else 0.0, 1e3)
    for e in comp.edges:
        if e.is_fixed:
            continue
        c = e.coeff_fn(float(q[e.index]), fluid)
        qi = float(q[e.index])
        res = (p[e.node_from] - p[e.node_to]) + c.dp_source - (c.a * qi + c.b * qi * abs(qi))
        if abs(res) > tol_mom * dpscale:
            issues.append(f"Impuls verletzt an '{e.name}': {res:.3e} Pa (Maßstab {dpscale:.3e})")
    return issues


def check_thermal(comp, hyd, th, s=None, tol_t=1e-4, tol_w=1.0):
    """Kantenmodelle neu auswerten, Knotenmischung und globale Bilanz unabhängig nachrechnen."""
    s = s or SolverSettings()
    fluid = comp.fluid
    issues = []
    q = np.asarray(hyd.q, float)
    t = np.asarray(th.t_node, float)
    m = np.abs(q) * fluid.rho
    n = len(comp.nodes)
    up = [e.node_from if q[e.index] >= 0 else e.node_to for e in comp.edges]
    down = [e.node_to if q[e.index] >= 0 else e.node_from for e in comp.edges]
    t_out = np.array([t[up[e.index]] for e in comp.edges], float)
    q_dot = np.zeros(len(comp.edges))
    for e in comp.edges:
        i = e.index
        if m[i] >= s.m_dot_eps and e.thermal_fn is not None:
            res = e.thermal_fn(float(t[up[i]]), float(m[i]), fluid)
            t_out[i], q_dot[i] = res.t_out, res.q_dot
        if abs(t_out[i] - th.t_edge_out[i]) > tol_t:
            issues.append(f"T_aus '{e.name}': Modell {t_out[i]:.6f} vs Solver {th.t_edge_out[i]:.6f}")
        if abs(q_dot[i] - th.q_dot_edge[i]) > max(tol_w, 1e-6 * abs(q_dot[i])):
            issues.append(f"Q̇ '{e.name}': Modell {q_dot[i]:.3f} vs Solver {th.q_dot_edge[i]:.3f} W")
    # Druck-RB-Umgebungszufluss aus Kontinuität
    net_out = np.zeros(n)
    for e in comp.edges:
        net_out[e.node_from] += q[e.index]
        net_out[e.node_to] -= q[e.index]
    # Knotenmischung: Σ Zufluss-Enthalpie = (Σ Zufluss + UA) · T_Knoten − UA·T_amb
    for nd in comp.nodes:
        j = nd.index
        num = den = 0.0
        for e in comp.edges:
            if down[e.index] == j and m[e.index] >= s.m_dot_eps:
                num += m[e.index] * fluid.cp * t_out[e.index]
                den += m[e.index] * fluid.cp
        for qb, tb in nd.bc_supplies:
            if qb > 0:
                num += fluid.rho * qb * fluid.cp * tb
                den += fluid.rho * qb * fluid.cp
        env = (net_out[j] - nd.flow_bc) if (nd.pinned and not nd.is_auto_ref) else 0.0
        if env > 0 and nd.t_supply is not None:
            num += fluid.rho * env * fluid.cp * nd.t_supply
            den += fluid.rho * env * fluid.cp
        if nd.ua > 0:
            num += nd.ua * nd.t_amb
            den += nd.ua
        if den > 1e-12:
            tmix = num / den
            if abs(tmix - t[j]) > tol_t:
                issues.append(f"Mischung '{nd.label}': {tmix:.6f} vs Solver {t[j]:.6f}")
    # globale Bilanz (unabhängig): Σ Q̇ + Σ UA(T_amb−T) + Σ Enthalpieströme über die Grenze
    bal = float(np.sum(q_dot))
    hin = 0.0
    for nd in comp.nodes:
        j = nd.index
        if nd.ua > 0:
            bal += nd.ua * (nd.t_amb - t[j])
        for qb, tb in nd.bc_supplies:
            bal += fluid.rho * qb * fluid.cp * (tb if qb > 0 else t[j])
            hin += abs(fluid.rho * qb * fluid.cp * tb)
        env = (net_out[j] - nd.flow_bc) if (nd.pinned and not nd.is_auto_ref) else 0.0
        if env > 0 and nd.t_supply is not None:
            bal += fluid.rho * env * fluid.cp * nd.t_supply
        elif env < 0:
            bal += fluid.rho * env * fluid.cp * t[j]
    scale = max(float(np.sum(np.abs(q_dot))), hin * 1e-9, 1.0)
    if abs(bal) > max(tol_w, 1e-6 * scale):
        issues.append(f"globale Energiebilanz {bal:.4f} W (Maßstab {scale:.1f} W)")
    if abs(bal - th.energy_imbalance) > max(tol_w, 1e-6 * scale):
        issues.append(f"Bilanz Solver {th.energy_imbalance:.4f} W vs unabhängig {bal:.4f} W")
    return issues
