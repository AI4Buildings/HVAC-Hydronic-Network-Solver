"""Regressionstests aus der Solver-Prüfung (Oktober 2026).

Jeder Test gegen eine UNABHÄNGIGE Referenz (Bisektion auf dem Komponenten-
modell bzw. geschlossene Lösung) — nicht gegen Solver-Zahlen.
"""
from __future__ import annotations

import math
from pathlib import Path

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


# --- B4: Greybox-Kühlregister bei kleinem Wasserstrom ---------------------------

@pytest.mark.parametrize("m_w", [0.5, 0.05, 5e-3, 1e-3, 4.3e-5, 3.9e-7, 1e-9])
def test_kuehlregister_greybox_kleiner_wasserstrom(m_w):
    """m* = ṁ_L·c_s/(ṁ_w·cp) ≫ 1: kein OverflowError; Q̇ endlich, Wasser höchstens
    bis zur Luft-Eintrittstemperatur erwärmt, Energiebilanz Wasser = Q̇."""
    c = h.CoolingCoil("kr", ua_ref_W_K=1500, ua_star_wet_kg_s=1.0, rh_air_in=0.6,
                      m_dot_air_kg_s=2.0, t_air_in_C=30)
    r = c.thermal_outlet(12.0, m_w, W50)
    assert math.isfinite(r.t_out) and math.isfinite(r.q_dot)
    assert 12.0 <= r.t_out <= 30.0 + 1e-9
    assert r.q_dot == pytest.approx(m_w * W50.cp * (r.t_out - 12.0), rel=1e-9, abs=1e-12)


def test_greybox_epsilon_stabile_form_gleich_originalformel():
    """Die stabile Umformung für m* > 1 ist algebraisch identisch."""
    from hydraulik.components.coils import _eps_counterflow
    for ntu in (0.1, 1.0, 3.0, 10.0):
        for m in (0.2, 0.9, 0.999999, 1.0, 1.000001, 1.5, 4.0, 30.0):
            if abs(1 - m) < 1e-9:
                ref = ntu / (1 + ntu)
            else:
                e = math.exp(-ntu * (1 - m))
                ref = (1 - e) / (1 - m * e)
            assert _eps_counterflow(ntu, m) == pytest.approx(ref, rel=1e-9)
    assert _eps_counterflow(5.0, 1e6) == pytest.approx(1e-6, rel=1e-9)    # ε* → 1/m*


# --- B1/B2: stille Verfälschung durch interne Referenzwiderstände ---------------

def _notices_for(net):
    return " | ".join(net.solve(thermal=False).notices)


def test_hinweis_pumpe_ohne_q_nom_verliert_foerderhoehe():
    net = h.Network(fluid=W50)
    net.add(h.Pump("pu", mode="constant_dp", dp_kPa=50))
    net.add(h.FlowResistance("r", c_Pa_m3h2=500))
    net.connect("pu.out", "r.in")
    net.connect("r.out", "pu.in")
    msg = _notices_for(net)
    assert "Pumpe 'pu'" in msg and "q_nom" in msg and "%" in msg
    # mit passendem q_nom: kein Hinweis (5 % Regularisierung ist dokumentiert)
    net2 = h.Network(fluid=W50)
    net2.add(h.Pump("pu", mode="constant_dp", dp_kPa=50, q_nom_m3h=10))
    net2.add(h.FlowResistance("r", c_Pa_m3h2=500))
    net2.connect("pu.out", "r.in")
    net2.connect("r.out", "pu.in")
    assert "Pumpe 'pu'" not in _notices_for(net2)


def test_hinweis_erzeuger_ohne_q_nom_grosser_innendruckverlust():
    net = h.Network(fluid=W50)
    net.add(h.HeatPump("wp", mode="target_t_out", t_out_set_C=45, q_max_kW=60))
    net.add(h.Pump("pu", mode="constant_dp", dp_kPa=60, q_nom_m3h=8.6))
    net.add(h.Radiator("hk", q_nom_kW=50, t_sup_nom_C=45, t_ret_nom_C=40))
    net.connect("wp.out", "pu.in")
    net.connect("pu.out", "hk.in")
    net.connect("hk.out", "wp.in")
    msg = _notices_for(net)
    assert "'wp'" in msg and "q_nom" in msg and "kPa" in msg
    net.components["wp"].q_nom = 8.6 / 3600.0                       # angegeben → ruhig
    net.components["wp"].given = net.components["wp"].given | {"q_nom"}
    assert "'wp'" not in _notices_for(net)


# --- B11: Greybox-Nassmodell darf den zweiten Hauptsatz nicht verletzen ---------

@pytest.mark.parametrize("rh", [0.5, 0.7, 0.912])
@pytest.mark.parametrize("m_w", [2.0, 0.5, 0.193, 0.05, 0.01, 1e-4])
@pytest.mark.parametrize("t_w_in", [6.0, 12.563, 18.0])
def test_kuehlregister_wasser_nie_ueber_feuchtkugelgrenze(rh, m_w, t_w_in):
    """Gegenstrom: das Wasser kann höchstens den Zustand im Gleichgewicht mit der
    eintretenden Luft erreichen, h_sat(T_w,aus) ≤ h_Luft,ein (Nassbetrieb) bzw.
    T_w,aus ≤ T_Luft,ein (trocken)."""
    from hydraulik.components.coils import h_moist, t_air_from_h_phi, x_from_rh
    c = h.CoolingCoil("kr", ua_ref_W_K=1500, ua_star_wet_kg_s=0.525, rh_air_in=rh,
                      m_dot_air_kg_s=2.0, t_air_in_C=23.098)
    r = c.thermal_outlet(t_w_in, m_w, h.water_at(20))
    t_star = t_air_from_h_phi(h_moist(23.098, x_from_rh(23.098, rh)), 1.0)
    limit = max(t_star, t_w_in) if r.extras.get("betrieb") == "nass" else 23.098
    assert r.t_out <= limit + 1e-6
    assert r.t_out <= 23.098 + 1e-9


# --- B12: Thermik-Fehlermeldung nennt die wahrscheinliche Ursache ----------------

def test_thermik_fehlermeldung_nennt_unplausible_spreizung():
    """Feste Leistung durch einen Kleinstdurchfluss (fast geschlossenes Ventil)
    im isolierten Teilkreis: die Fehlermeldung nennt die Kante mit der
    unplausiblen Temperaturspreizung."""
    from hydraulik.exceptions import ConvergenceError
    from hydraulik.solver.settings import SolverSettings
    net = h.Network(fluid=W50)
    net.add(h.Pump("pu", mode="constant_flow", q_m3h=0.0005))
    net.add(h.Radiator("hk", q_prescribed_kW=2.0, kv_m3h=2.0))
    net.connect("pu.out", "hk.in")
    net.connect("hk.out", "pu.in")
    with pytest.raises(ConvergenceError) as ei:
        net.solve(SolverSettings(max_iter_thermal=20))
    assert "'hk'" in str(ei.value)


# --- Netze gegen geschlossene Lösungen (unabhängige Referenzen) ------------------

@pytest.mark.parametrize("qp, qs", [(2.0, 3.0), (3.0, 2.0)])
def test_weiche_mischtemperaturen_beide_betriebsfaelle(qp, qs):
    net = h.Network(fluid=W50)
    net.add(h.IdealStorage("erz", t_set_C=70))
    net.add(h.Pump("pp", mode="constant_flow", q_m3h=qp))
    net.add(h.HydraulicSeparator("hw"))
    net.add(h.Pump("ps", mode="constant_flow", q_m3h=qs))
    net.add(h.IdealStorage("vb", t_set_C=40))
    net.connect("erz.out", "pp.in"); net.connect("pp.out", "hw.prim_in"); net.connect("hw.prim_out", "erz.in")
    net.connect("hw.sec_out", "ps.in"); net.connect("ps.out", "vb.in"); net.connect("vb.out", "hw.sec_in")
    r = net.solve()
    t_sec = (qp * 70 + (qs - qp) * 40) / qs if qs > qp else 70.0
    t_ret = 40.0 if qs > qp else (qs * 40 + (qp - qs) * 70) / qp
    assert r["ps"].t_in_C == pytest.approx(t_sec, abs=1e-6)
    assert r["erz"].t_in_C == pytest.approx(t_ret, abs=1e-6)


def test_heizkoerper_en442_gegen_eigene_nullstellensuche():
    qn, ts, tr, troom, n, q_m3h, t_vl = 2000.0, 55.0, 45.0, 20.0, 1.3, 0.12, 50.0
    net = h.Network(fluid=W50)
    net.add(h.IdealStorage("sp", t_set_C=t_vl))
    net.add(h.Pump("pu", mode="constant_flow", q_m3h=q_m3h))
    net.add(h.Radiator("hk", q_nom_W=qn, t_sup_nom_C=ts, t_ret_nom_C=tr, t_room_C=troom, n=n))
    net.connect("sp.out", "pu.in"); net.connect("pu.out", "hk.in"); net.connect("hk.out", "sp.in")
    r = net.solve()
    m, cp = q_m3h / 3600 * W50.rho, W50.cp
    dtn = (ts - tr) / math.log((ts - troom) / (tr - troom))

    def f(t):                                   # Energiebilanz minus EN-442-Leistung, steigend in t? → umdrehen
        dt = (t_vl - t) / math.log((t_vl - troom) / (t - troom))
        return qn * (dt / dtn) ** n - m * cp * (t_vl - t)
    t_ret = _bisect(f, troom + 1e-9, t_vl)
    assert r["hk"].t_out_C == pytest.approx(t_ret, abs=1e-6)


def test_waermepumpe_an_der_leistungsgrenze():
    net = h.Network(fluid=W50)
    net.add(h.HeatPump("wp", mode="target_t_out", t_out_set_C=55, q_max_kW=5, q_nom_m3h=1))
    net.add(h.Pump("pu", mode="constant_flow", q_m3h=1.0))
    net.add(h.IdealStorage("vb", t_set_C=30))
    net.connect("wp.out", "pu.in"); net.connect("pu.out", "vb.in"); net.connect("vb.out", "wp.in")
    r = net.solve()
    assert r["wp"].t_out_C == pytest.approx(30 + 5000 / (1 / 3600 * W50.rho * W50.cp), abs=1e-6)


def test_offenes_system_und_gesperrte_rueckschlagklappe():
    from hydraulik.friction import kv_to_b
    net = h.Network(fluid=W50)
    net.add(h.Inflow("zu", t_set_C=15, p_kPa=200)); net.add(h.FlowResistance("r", c_Pa_m3h2=5000))
    net.add(h.Outflow("ab", p_kPa=50))
    net.connect("zu.port", "r.in"); net.connect("r.out", "ab.port")
    r = net.solve()
    assert r["r"].q_m3h == pytest.approx(math.sqrt(150e3 / 5000), rel=1e-9)
    assert r["r"].t_out_C == pytest.approx(15.0, abs=1e-12)
    net = h.Network(fluid=W50)
    net.add(h.Inflow("zu", t_set_C=15, p_kPa=50)); net.add(h.CheckValve("rk", kvs_m3h=4))
    net.add(h.Outflow("ab", p_kPa=150))
    net.connect("zu.port", "rk.in"); net.connect("rk.out", "ab.port")
    r = net.solve(thermal=False)
    leak = -math.sqrt(100e3 / (1e6 * kv_to_b(4, W50.rho))) * 3600
    assert r["rk"].q_m3h == pytest.approx(leak, rel=1e-9)


def test_hinweis_weiche_ohne_q_nom_mit_spuerbarem_druckverlust():
    def netz(**kw):
        net = h.Network(fluid=W50)
        net.add(h.IdealStorage("erz", t_set_C=70))
        net.add(h.Pump("pp", mode="constant_dp", dp_kPa=40, q_nom_m3h=20))
        net.add(h.FlowResistance("rp", c_Pa_m3h2=80))
        net.add(h.HydraulicSeparator("hw", **kw))
        net.add(h.Pump("ps", mode="constant_dp", dp_kPa=30, q_nom_m3h=5))
        net.add(h.FlowResistance("rs", c_Pa_m3h2=1080))
        net.connect("erz.out", "pp.in"); net.connect("pp.out", "rp.in"); net.connect("rp.out", "hw.prim_in")
        net.connect("hw.prim_out", "erz.in"); net.connect("hw.sec_out", "ps.in"); net.connect("ps.out", "rs.in")
        net.connect("rs.out", "hw.sec_in")
        return " | ".join(net.solve().notices)
    assert "Weiche 'hw'" in netz() and "q_nom" in netz()
    assert "Weiche 'hw'" not in netz(q_nom_m3h=25)


@pytest.mark.parametrize("t_in", [-244.87, -100.0, -40.0, 95.0, 150.0, 1e4, -1e4])
def test_kuehlregister_greybox_endlich_fuer_jeden_eingang(t_in):
    """Newton-Zwischenschritte können absurde Temperaturen liefern; das Modell
    muss trotzdem endlich antworten (kein OverflowError)."""
    c = h.CoolingCoil("kr", ua_ref_W_K=1500, ua_star_wet_kg_s=1.0, rh_air_in=0.6,
                      m_dot_air_kg_s=2.0, t_air_in_C=30)
    for m_w in (1.0, 9.5e-7):
        r = c.thermal_outlet(t_in, m_w, W50)
        assert math.isfinite(r.t_out) and math.isfinite(r.q_dot)


def test_psychrometrie_im_gueltigkeitsbereich_unveraendert():
    from hydraulik.components.coils import p_ws
    for t in (-30.0, 0.0, 20.0, 60.0):
        assert p_ws(t) == pytest.approx(611.2 * math.exp(17.62 * t / (243.12 + t)), rel=1e-15)


# --- B3: 3-Wege-Mischventil, Kennlinie je Pfad -----------------------------------

def _mv_kv_total(**kw):
    """Gesamt-Kv (A‖B) aus der Simulation: beide Eingänge auf gleichem Druck."""
    net = h.Network(fluid=W50)
    net.add(h.Inflow("pa", t_set_C=60, p_kPa=100)); net.add(h.Inflow("pb", t_set_C=30, p_kPa=100))
    net.add(h.MixingValve3Way("mv", kvs_m3h=4.0, **kw)); net.add(h.Outflow("ab", p_kPa=0))
    net.connect("pa.port", "mv.a"); net.connect("pb.port", "mv.b"); net.connect("mv.ab", "ab.port")
    r = net.solve(thermal=False)
    q = r["mv:a"].q_m3h + r["mv:b"].q_m3h
    return q / math.sqrt(1.0 * 1000 / W50.rho)        # Kv = V̇ / √(Δp[bar]·1000/ρ)


def test_mischventil_standard_a_gleichprozentig_b_linear():
    """Herstellerkonvention (z.B. Siemens VXG, Belimo R3): A–AB gleichprozentig,
    B–AB linear → Gesamt-Kv bei Mittelstellung Kvs·(R^-0.5 + 0.5)."""
    kv = _mv_kv_total(opening=0.5)
    assert kv == pytest.approx(4.0 * (100 ** -0.5 + 0.5), rel=1e-6)
    # ausdrücklich beidseitig gleichprozentig: der starke Einbruch bleibt wählbar
    kv_eq = _mv_kv_total(opening=0.5, characteristic_b="equal_percentage")
    assert kv_eq == pytest.approx(4.0 * 2 * 100 ** -0.5, rel=1e-6)
    # linear/linear: konstanter Gesamt-Kv
    kv_lin = _mv_kv_total(opening=0.3, characteristic="linear", characteristic_b="linear")
    assert kv_lin == pytest.approx(4.0, rel=1e-6)


# --- B10: thermisch unbestimmte Umläufe werden gekennzeichnet ---------------------

def _umlauf(*, wp=False, verlust=False, t_init=20.0):
    from hydraulik.solver.settings import SolverSettings
    net = h.Network(fluid=W50)
    net.add(h.Pump("pu", mode="constant_flow", q_m3h=1.0))
    net.add(h.Pipe("r", length_m=20, d_inner_mm=20, **({"u_linear_W_mK": 0.3, "t_amb_C": 15} if verlust else {})))
    if wp:
        net.add(h.HeatPump("wp", mode="target_t_out", t_out_set_C=45, q_nom_m3h=1))
        net.connect("pu.out", "wp.in"); net.connect("wp.out", "r.in")
    else:
        net.connect("pu.out", "r.in")
    net.connect("r.out", "pu.in")
    return net.solve(SolverSettings(t_init=t_init))


def test_adiabater_umlauf_ohne_quelle_unbestimmt():
    r = _umlauf()
    assert any("nicht eindeutig" in n for n in r.notices)
    assert r["r"].t_out_C == pytest.approx(20.0)


@pytest.mark.parametrize("t_init", [20.0, 80.0])
def test_nur_heizende_wp_im_verlustfreien_kreis_unbestimmt(t_init):
    """Jede Temperatur ≥ Sollwert ist stationär (WP aus) → gekennzeichnet."""
    r = _umlauf(wp=True, t_init=t_init)
    assert any("nicht eindeutig" in n for n in r.notices)


def test_bestimmte_kreise_ohne_hinweis():
    assert not any("nicht eindeutig" in n for n in _umlauf(verlust=True).notices)
    assert not any("nicht eindeutig" in n for n in _umlauf(wp=True, verlust=True).notices)
    for path in sorted((Path(__file__).parent.parent / "examples").glob("*.yaml")):
        assert not any("nicht eindeutig" in n for n in h.load(path).solve(h.load_settings(path)).notices), path


# --- B14: fast geschlossenes Ventil parallel zu einer idealen Verbindung ---------

@pytest.mark.parametrize("opening", [1e-3, 0.02])
@pytest.mark.parametrize("fluid", [W50, h.Fluid("glykol", 1050.0, 4e-3, 3600.0)], ids=["wasser", "glykol"])
def test_fast_geschlossenes_ventil_konvergiert_zuegig(opening, fluid):
    """Gleichgewichtsstrom ~1e-7 m³/h liegt weit unter der Standard-Floor-
    Auflösung (0.1 % des Startwerts): Newton darf dort nicht künstlich gedämpft
    bleiben (vorher 415–487 Iterationen > max_iter 400)."""
    from hydraulik.solver.hydraulic import solve_hydraulics
    net = h.Network(fluid=fluid)
    net.add(h.Pump("pu", mode="constant_dp", dp_kPa=60, q_nom_m3h=3))
    net.add(h.FlowResistance("netz", c_Pa_m3h2=5000))
    net.add(h.Link("by"))
    net.add(h.ControlValve("rv", kvs_m3h=0.4, opening=opening, rangeability=1000))
    net.add(h.HeatPump("wp", mode="target_t_out", t_out_set_C=45, q_nom_m3h=19))
    net.add(h.Radiator("hk", q_nom_kW=2, t_sup_nom_C=55, t_ret_nom_C=45))
    net.connect("pu.out", "netz.in"); net.connect("netz.out", "by.in", "rv.in")
    net.connect("rv.out", "wp.in"); net.connect("wp.out", "hk.in"); net.connect("hk.out", "by.out", "pu.in")
    comp = net.compile()
    hyd = solve_hydraulics(comp)
    assert hyd.converged and hyd.iterations <= 60
    # Strang rv–wp–hk: Δp über dem Strang = Δp über dem Link; Strom aus der Kennlinie
    e = {x.name: x for x in comp.edges}
    q = float(hyd.q[e["rv"].index])
    dp_strang = sum(x.coeff_fn(q, fluid).b * q * abs(q) for x in (e["rv"], e["wp"], e["hk"]))
    dp_link = hyd.p[e["by"].node_from] - hyd.p[e["by"].node_to]
    # innerhalb der Impulstoleranz des Solvers (1e-6 · Druckmaßstab 60 kPa)
    assert abs(dp_strang - dp_link) <= 1e-6 * 60e3


# --- Thermik: Genauigkeitsgrenze statt Fehler, keine stationäre Lösung bleibt Fehler

def test_thermik_genauigkeitsgrenze_mit_hinweis_statt_fehler():
    """Endet die Iteration knapp über tol_t (≤ 100·tol_t, ohne Drift — typisch
    die Rundungsgrenze bei stark unterschiedlichen Kapazitätsströmen), wird die
    Lösung mit Genauigkeitshinweis geliefert statt verworfen."""
    from hydraulik.solver.settings import SolverSettings
    net = h.Network(fluid=W50)
    net.add(h.IdealStorage("sp", t_set_C=60))
    net.add(h.Pump("pu", mode="constant_flow", q_m3h=1.0))
    net.add(h.Radiator("hk", q_nom_kW=3, t_sup_nom_C=55, t_ret_nom_C=45))
    net.connect("sp.out", "pu.in"); net.connect("pu.out", "hk.in"); net.connect("hk.out", "sp.in")
    import re
    from hydraulik.exceptions import ConvergenceError
    # Residuum nach EINEM Newton-Schritt (Iterationsgrenze) aus der Meldung
    with pytest.raises(ConvergenceError) as exc:
        net.solve(SolverSettings(tol_t=1e-30, max_iter_thermal=1))
    rest = float(re.search(r"Bilanzabweichung ([0-9.e+-]+) K", str(exc.value)).group(1))
    assert rest > 0.0
    # Rest ≤ 100·tol_t → Ergebnis mit Genauigkeitshinweis
    r = net.solve(SolverSettings(tol_t=rest / 10.0, max_iter_thermal=1))
    assert r.converged
    assert any("Thermik" in n and "genau" in n for n in r.notices), r.notices
    # Rest > 100·tol_t → weiterhin Fehler
    with pytest.raises(ConvergenceError):
        net.solve(SolverSettings(tol_t=rest / 1000.0, max_iter_thermal=1))


def test_keine_stationaere_loesung_bleibt_fehler():
    from hydraulik.exceptions import ConvergenceError
    net = h.Network(fluid=W50)
    net.add(h.Pump("pu", mode="constant_flow", q_m3h=0.5))
    net.add(h.Radiator("hk", q_prescribed_kW=2.0, kv_m3h=2.0))
    net.connect("pu.out", "hk.in"); net.connect("hk.out", "pu.in")
    with pytest.raises(ConvergenceError, match="keine stationäre Lösung"):
        net.solve()


@pytest.mark.parametrize("q_umlauf_m3h", [50.0, 1500.0])
def test_drift_erkannt_trotz_grossem_umlauf(q_umlauf_m3h):
    """Kreis ohne Wärmequelle mit fest entziehendem Register und großem
    Umlauf über einen Link: das Bilanzresiduum ist winzig (~1e-4 K), bei
    |T| ~ 1e6 K erzeugt Rundung scheinbare Mini-Abstiege — die Drift muss
    trotzdem als „keine stationäre Lösung" erkannt werden (vorher:
    Iterationsgrenze ohne Diagnose)."""
    from hydraulik.exceptions import ConvergenceError
    net = h.Network(fluid=h.water_at(10))
    net.add(h.Pump("pu", mode="constant_flow", q_m3h=q_umlauf_m3h))
    net.add(h.Link("by"))
    net.add(h.HeatingCoil("reg", q_prescribed_kW=-2.24, kv_m3h=2.0, m_dot_air_kg_s=1.0, t_air_in_C=0.0))
    net.connect("pu.out", "by.in", "reg.in"); net.connect("by.out", "reg.out", "pu.in")
    with pytest.raises(ConvergenceError, match="keine stationäre Lösung"):
        net.solve()


# --- Heizkörper: stetig an der Raumtemperatur (Kampagne Seed 1727) -------------

@pytest.mark.parametrize("m_dot", [1e-4, 1.09e-2, 0.1])
def test_heizkoerper_stetig_an_der_raumtemperatur(m_dot):
    """Vorher galt der Heizkörper bis t_room + 0.01 K als 'aus' (T_aus = T_ein)
    und kühlte knapp darüber fast auf Raumtemperatur ab: Sprung bis 0.01 K →
    Thermik-Newton 'festgefahren'. T_aus muss stetig in T_ein sein und nie
    unter t_room fallen."""
    hk = h.Radiator("hk", q_nom_kW=2.0, t_sup_nom_C=55, t_ret_nom_C=45, t_room_C=20.0)
    f = lambda t: hk.thermal_outlet(t, m_dot, W50).t_out
    ts = [20.0 + d for d in (-1e-3, 0.0, 1e-9, 1e-7, 1e-5, 1e-3, 5e-3, 0.0099, 0.01, 0.0101, 0.02, 0.1, 1.0)]
    outs = [f(t) for t in ts]
    for t, o in zip(ts, outs):
        assert min(t, 20.0) - 1e-12 <= o <= t + 1e-12          # 2. Hauptsatz, keine Erwärmung
    for (t1, o1), (t2, o2) in zip(zip(ts, outs), zip(ts[1:], outs[1:])):
        assert abs(o2 - o1) <= abs(t2 - t1) + 1e-8             # Lipschitz 1: kein Sprung

