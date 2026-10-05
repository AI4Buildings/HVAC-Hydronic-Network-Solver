"""Regressionstests aus der Solver-Prüfung (Oktober 2026).

Jeder Test gegen eine UNABHÄNGIGE Referenz (Bisektion auf dem Komponenten-
modell bzw. geschlossene Lösung) — nicht gegen Solver-Zahlen.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

import hydraulik as h
from hydraulik import friction

W50 = h.water_at(50)
OIL = h.Fluid("oel", 870.0, 3e-2, 2000.0)


def _pipe_dp(q, length, d, fluid, rough=7e-6):
    a, b = friction.pipe_coefficients(q, length, d, rough, 0.0, fluid.rho, fluid.mu)
    return a * q + b * q * abs(q)


def _bisect(f, lo, hi, n=200):
    """Nullstelle einer monoton steigenden Funktion f auf [lo, hi]."""
    for _ in range(n):
        mid = 0.5 * (lo + hi)
        if f(mid) > 0.0:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


# --- B5: Konvergenz darf nicht mit veralteten Koeffizienten gemeldet werden -----

@pytest.mark.parametrize("d_mm, q_m3h", [(80, 20), (80, 40), (150, 60), (50, 10)])
def test_parallele_rohre_turbulente_aufteilung(d_mm, q_m3h):
    """Konstantstrom-Pumpe speist zwei parallele Rohre (10 m / 40 m). Rohre ohne
    Startwert beginnen laminar; die Lösung muss trotzdem die (turbulente)
    Gleichgewichtsaufteilung Δp1(q1) = Δp2(Q − q1) sein, nicht die laminare."""
    net = h.Network(fluid=W50)
    net.add(h.Pump("pu", mode="constant_flow", q_m3h=q_m3h))
    net.add(h.Pipe("r1", length_m=10, d_inner_mm=d_mm))
    net.add(h.Pipe("r2", length_m=40, d_inner_mm=d_mm))
    net.connect("pu.out", "r1.in", "r2.in")
    net.connect("r1.out", "r2.out", "pu.in")
    r = net.solve(thermal=False)
    Q, d = q_m3h / 3600.0, d_mm / 1e3
    q1 = _bisect(lambda x: _pipe_dp(x, 10, d, W50) - _pipe_dp(Q - x, 40, d, W50), 0.0, Q)
    assert r["r1"].q_m3h == pytest.approx(q1 * 3600.0, rel=1e-5)
    assert r["r1"].dp_kPa == pytest.approx(r["r2"].dp_kPa, rel=1e-6)


@pytest.mark.parametrize("dp_kpa", [50, 60, 67, 70, 80, 100, 120, 150, 180, 220])
@pytest.mark.parametrize("q_start", [0.3, 10.0, 30.0])
def test_oelkreis_uebergangsbereich_eindeutig(dp_kpa, q_start):
    """Ideale Δp-Pumpe + Rohr, Öl, Arbeitspunkt im laminar-turbulenten Übergang
    (Re ≈ 1900…3500): Konvergenz (B6) und dieselbe, exakte Lösung für jeden
    Startwert (B5)."""
    net = h.Network(fluid=OIL)
    pu = net.add(h.Pump("pu", mode="constant_dp", dp_kPa=dp_kpa, q_nom_m3h=q_start,
                        dp_internal_frac=1e-4))
    net.add(h.Pipe("r", length_m=100, d_inner_mm=50))
    net.connect("pu.out", "r.in")
    net.connect("r.out", "pu.in")
    r = net.solve(thermal=False)
    b_int = pu._b_internal()
    q = _bisect(lambda x: _pipe_dp(x, 100, 0.05, OIL) + b_int * x * x - dp_kpa * 1e3, 0.0, 1.0)
    assert r["r"].q_m3h == pytest.approx(q * 3600.0, rel=1e-5)


def test_konvergenz_nur_mit_konsistenten_koeffizienten():
    """Bei gemeldeter Konvergenz erfüllt der zurückgegebene Zustand die Impuls-
    gleichung mit den BEI DIESEM Zustand ausgewerteten Koeffizienten."""
    from hydraulik.solver.hydraulic import solve_hydraulics
    net = h.Network(fluid=W50)
    net.add(h.Pump("pu", mode="constant_flow", q_m3h=40))
    for k, length in enumerate((10, 25, 40)):
        net.add(h.Pipe(f"r{k}", length_m=length, d_inner_mm=80))
    net.connect("pu.out", "r0.in", "r1.in", "r2.in")
    net.connect("r0.out", "r1.out", "r2.out", "pu.in")
    comp = net.compile()
    hyd = solve_hydraulics(comp)
    assert hyd.converged
    for e in comp.edges:
        if e.is_fixed:
            continue
        q = float(hyd.q[e.index])
        c = e.coeff_fn(q, comp.fluid)
        res = hyd.p[e.node_from] - hyd.p[e.node_to] + c.dp_source - (c.a * q + c.b * q * abs(q))
        assert abs(res) < 1e-3, (e.name, res)


# --- B8: Eigenschleifen und antriebslose Maschen (doppelte Nullstelle) ---------

def _haupt_kreis(net):
    net.add(h.Pump("pu", mode="constant_dp", dp_kPa=120, q_nom_m3h=5))
    net.add(h.FlowResistance("r", c_Pa_m3h2=4000))
    net.connect("pu.out", "r.in")
    net.connect("r.out", "pu.in")


@pytest.mark.parametrize("q_init", [5e-5, 2e-3, 1e-2])
def test_kurzgeschlossenes_passives_bauteil_fuehrt_keinen_strom(q_init):
    """Ein- und Austritt am selben Knoten: Δp = 0 ⇒ V̇ = 0 exakt (nicht der
    halbierte Startwert, bei dem das globale Residuum schon klein ist)."""
    from hydraulik.solver.settings import SolverSettings
    net = h.Network(fluid=W50)
    _haupt_kreis(net)
    net.add(h.Link("ks"))
    net.add(h.HeatPump("wp", mode="target_t_out", t_out_set_C=45, q_nom_m3h=10))
    net.connect("ks.in", "ks.out", "r.in")        # Link kurzgeschlossen
    net.connect("wp.in", "wp.out", "pu.in")       # Erzeuger kurzgeschlossen
    r = net.solve(SolverSettings(q_init=q_init), thermal=False)
    assert abs(r["ks"].q_m3h) < 1e-6 and abs(r["wp"].q_m3h) < 1e-6
    b_int_m3h = 0.05 * 120e3 / 5 ** 2                  # interner Widerstand in Pa/(m³/h)²
    assert r["r"].q_m3h == pytest.approx(math.sqrt(120e3 / (4000 + b_int_m3h)), rel=1e-6)


def test_kurzgeschlossene_pumpe_zirkuliert_exakt():
    """Pumpe mit Ein- und Austritt am selben Knoten: dp = b_int·V̇² ⇒ V̇ = √(dp/b_int)."""
    net = h.Network(fluid=W50)
    pu = net.add(h.Pump("pu", mode="constant_dp", dp_kPa=30, q_nom_m3h=2))
    net.connect("pu.in", "pu.out")
    r = net.solve(thermal=False)
    assert r["pu"].q_m3h == pytest.approx(math.sqrt(30e3 / pu._b_internal()) * 3600, rel=1e-9)


@pytest.mark.parametrize("q_init", [5e-5, 2e-3, 1e-2])
def test_antriebslose_masche_ohne_kreisstroemung(q_init):
    """Passive Masche C–D (zwei verschiedene Rohre) nur über einen Knoten am
    Pumpenkreis: kein Antrieb ⇒ Kreisströmung 0 für jeden Startwert."""
    from hydraulik.solver.settings import SolverSettings
    net = h.Network(fluid=W50)
    _haupt_kreis(net)
    net.add(h.Pipe("m1", length_m=5, d_inner_mm=26))
    net.add(h.Pipe("m2", length_m=20, d_inner_mm=20))
    net.add(h.Link("an"))
    net.connect("r.out", "an.in")
    net.connect("an.out", "m1.in", "m2.out")
    net.connect("m1.out", "m2.in")
    r = net.solve(SolverSettings(q_init=q_init), thermal=False)
    assert abs(r["m1"].q_m3h) < 1e-6 and abs(r["m2"].q_m3h) < 1e-6 and abs(r["an"].q_m3h) < 1e-6
