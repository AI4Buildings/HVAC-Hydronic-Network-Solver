"""Hydraulik-Solver gegen analytische Netzlösungen."""
import math

import pytest

import hydraulik as h
from hydraulik import SolverSettings
from hydraulik.friction import kv_to_b


def make_loop(pump_kwargs):
    """Pumpe + Regelventil als einzelner Kreis."""
    net = h.Network()
    net.add(h.Pump("pu", **pump_kwargs))
    net.add(h.ControlValve("v1", kvs_m3h=2.5, opening=1.0))
    net.connect("pu.out", "v1.in")
    net.connect("v1.out", "pu.in")
    return net


def test_constant_dp_pump_single_resistance():
    # Q = sqrt(Δp_netto / b_gesamt); interner Pumpenwiderstand eingerechnet
    net = make_loop(dict(mode="constant_dp", dp_kPa=50, q_nom_m3h=2.0))
    r = net.solve()
    rho = net.fluid.rho
    b_v = kv_to_b(2.5, rho)
    b_p = 0.05 * 50e3 / (2.0 / 3600) ** 2
    q_ref = math.sqrt(50e3 / (b_v + b_p))
    assert r["v1"].q_m3h == pytest.approx(q_ref * 3600, rel=1e-6)
    assert r.converged


def test_constant_flow_pump_dp_is_result():
    # Bei Konstant-Volumenstrom-Pumpe ist Δp Ergebnis: Δp_pumpe = b_v·Q²
    net = make_loop(dict(mode="constant_flow", q_m3h=1.8))
    r = net.solve()
    rho = net.fluid.rho
    q = 1.8 / 3600
    assert r["v1"].q_m3h == pytest.approx(1.8, rel=1e-9)
    # Druckerhöhung der Pumpe = Druckverlust des Ventils
    assert -r["pu"].dp_kPa == pytest.approx(kv_to_b(2.5, rho) * q**2 / 1e3, rel=1e-6)


def test_series_resistances_add():
    net = h.Network()
    net.add(h.Pump("pu", mode="constant_dp", dp_kPa=40, q_nom_m3h=2.0))
    net.add(h.ControlValve("v1", kvs_m3h=2.0))
    net.add(h.ControlValve("v2", kvs_m3h=3.0))
    net.connect("pu.out", "v1.in")
    net.connect("v1.out", "v2.in")
    net.connect("v2.out", "pu.in")
    r = net.solve()
    rho = net.fluid.rho
    b_tot = kv_to_b(2.0, rho) + kv_to_b(3.0, rho) + 0.05 * 40e3 / (2.0 / 3600) ** 2
    assert r["v1"].q_m3h == pytest.approx(math.sqrt(40e3 / b_tot) * 3600, rel=1e-6)


def test_parallel_branches_flow_split():
    """Zwei parallele Ventile: gleiche Druckdifferenz, Q_i ∝ Kv_i."""
    net = h.Network()
    net.add(h.Pump("pu", mode="constant_flow", q_m3h=3.0))
    net.add(h.ControlValve("v1", kvs_m3h=2.0))
    net.add(h.ControlValve("v2", kvs_m3h=4.0))
    net.connect("pu.out", "v1.in", "v2.in")
    net.connect("v1.out", "v2.out", "pu.in")
    r = net.solve()
    q1, q2 = r["v1"].q_m3h, r["v2"].q_m3h
    assert q1 + q2 == pytest.approx(3.0, rel=1e-9)
    # gleiches Δp → Q ∝ Kv
    assert q2 / q1 == pytest.approx(2.0, rel=1e-6)
    assert r["v1"].dp_kPa == pytest.approx(r["v2"].dp_kPa, rel=1e-6)


def test_three_parallel_radiator_branches_mass_balance():
    net = h.Network(fluid=h.water_at(60))
    net.add(h.Pump("pu", mode="constant_dp", dp_kPa=25, q_nom_m3h=1.5))
    net.add(h.Manifold("vert", n_ports=3))
    net.add(h.Manifold("samm", n_ports=3))
    for i in (1, 2, 3):
        net.add(h.Radiator(f"hk{i}", q_nom_kW=1.0 + 0.5 * i))
        net.connect(f"vert.s{i}", f"hk{i}.in")
        net.connect(f"hk{i}.out", f"samm.s{i}")
    net.connect("pu.out", "vert.main")
    net.connect("samm.main", "pu.in")
    r = net.solve()
    q_sum = sum(r[f"hk{i}"].q_m3h for i in (1, 2, 3))
    assert q_sum == pytest.approx(r["pu"].q_m3h, rel=1e-8)
    # alle Stränge sehen dieselbe Druckdifferenz
    dps = [r[f"hk{i}"].dp_kPa for i in (1, 2, 3)]
    assert max(dps) == pytest.approx(min(dps), rel=1e-6)


def test_open_ends_pressure_pressure():
    """Offenes Rohr zwischen zwei Druckrandbedingungen: Q = sqrt(Δp/b)."""
    net = h.Network()
    net.add(h.OpenEnd("e1", bc="pressure", p_kPa=300, t_supply_C=70))
    net.add(h.OpenEnd("e2", bc="pressure", p_kPa=200))
    net.add(h.ControlValve("v1", kvs_m3h=2.5))
    net.connect("e1.port", "v1.in")
    net.connect("v1.out", "e2.port")
    r = net.solve()
    q_ref = math.sqrt(100e3 / kv_to_b(2.5, net.fluid.rho))
    assert r["v1"].q_m3h == pytest.approx(q_ref * 3600, rel=1e-6)
    # eintretendes Wasser trägt die Zulauftemperatur durch das Netz
    assert r["v1"].t_out_C == pytest.approx(70.0, abs=1e-6)


def test_open_ends_flow_in_pressure_out():
    net = h.Network()
    net.add(h.OpenEnd("zu", bc="flow", q_m3h=1.2, t_supply_C=60))
    net.add(h.OpenEnd("ab", bc="pressure", p_kPa=100))
    net.add(h.Pipe("r1", length_m=20, d_inner_mm=20))
    net.connect("zu.port", "r1.in")
    net.connect("r1.out", "ab.port")
    r = net.solve()
    assert r["r1"].q_m3h == pytest.approx(1.2, rel=1e-9)
    assert r["r1"].t_in_C == pytest.approx(60.0, abs=1e-9)


def test_unbalanced_fixed_flows_raise():
    """Zwei Konstant-Volumenstrom-Pumpen in Reihe mit verschiedenen Sollwerten."""
    net = h.Network()
    net.add(h.Pump("p1", mode="constant_flow", q_m3h=1.0))
    net.add(h.Pump("p2", mode="constant_flow", q_m3h=2.0))
    net.connect("p1.out", "p2.in")
    net.connect("p2.out", "p1.in")
    with pytest.raises(h.SingularNetworkError):
        net.solve()


def test_closed_valve_blocks_exactly():
    """opening = 0 wirkt als Randbedingung Q = 0 (kein Leckage-Kv)."""
    net = make_loop(dict(mode="constant_dp", dp_kPa=30, q_nom_m3h=1.0))
    net.components["v1"].opening = 0.0
    r = net.solve()
    assert r.converged
    assert r["v1"].q_m3h == pytest.approx(0.0, abs=1e-12)
    assert r["pu"].q_m3h == pytest.approx(0.0, abs=1e-12)


def test_nearly_closed_valve_leakage_floor():
    """Im Regelbereich (opening > 0) greift der Kennlinien-Floor Kvs/Rangeability."""
    net = make_loop(dict(mode="constant_dp", dp_kPa=30, q_nom_m3h=1.0))
    net.components["v1"].opening = 1e-6
    r = net.solve()
    assert r.converged
    assert 0.0 < r["v1"].q_m3h < 0.1


def test_constant_flow_pump_against_closed_valve_raises():
    """Konstantstrom-Pumpe gegen geschlossenes Ventil: stationär unmöglich →
    klare Fehlermeldung statt Divergenz."""
    net = make_loop(dict(mode="constant_flow", q_m3h=1.0))
    net.components["v1"].opening = 0.0
    with pytest.raises(h.SingularNetworkError):
        net.solve()


def test_mixing_valve_end_position_blocks_bypass():
    """Mischventil in Endlage (opening = 1): B-Pfad sperrt exakt."""
    net = h.Network()
    net.add(h.Pump("pu", mode="constant_dp", dp_kPa=30, q_nom_m3h=1.0))
    net.add(h.MixingValve3Way("mv", kvs_m3h=4.0, opening=1.0, characteristic="linear"))
    net.add(h.ControlValve("va", kvs_m3h=3.0))   # Ast vor Tor A
    net.add(h.ControlValve("vb", kvs_m3h=3.0))   # Ast vor Tor B
    net.connect("pu.out", "va.in", "vb.in")
    net.connect("va.out", "mv.a")
    net.connect("vb.out", "mv.b")
    net.connect("mv.ab", "pu.in")
    r = net.solve()
    assert r["mv:b"].q_m3h == pytest.approx(0.0, abs=1e-12)
    assert r["mv:a"].q_m3h == pytest.approx(r["pu"].q_m3h, rel=1e-9)


def test_valve_sweep_monotone():
    flows = []
    for op in (0.2, 0.4, 0.6, 0.8, 1.0):
        net = h.Network()
        net.add(h.Pump("pu", mode="constant_dp", dp_kPa=30, q_nom_m3h=1.0))
        net.add(h.ControlValve("v1", kvs_m3h=2.5, opening=op, characteristic="linear"))
        net.connect("pu.out", "v1.in")
        net.connect("v1.out", "pu.in")
        flows.append(net.solve()["v1"].q_m3h)
    assert all(f2 > f1 for f1, f2 in zip(flows, flows[1:]))


def test_bad_initial_guess_still_converges():
    net = make_loop(dict(mode="constant_dp", dp_kPa=80, q_nom_m3h=0.3))
    r = net.solve(SolverSettings(q_init=5.0))  # absurd großer Start (5 m³/s)
    assert r.converged


def test_validation_unknown_port_and_component():
    net = h.Network()
    net.add(h.Pump("pu", mode="constant_dp", dp_kPa=30))
    net.add(h.ControlValve("v1", kvs_m3h=2.5))
    net.connect("pu.out", "v1.inn")     # Tippfehler Port
    net.connect("v2.out", "pu.in")      # unbekannte Komponente
    with pytest.raises(h.NetworkValidationError) as exc:
        net.solve()
    msg = str(exc.value)
    assert "v1.in" in msg          # Vorschlag für Port-Tippfehler
    assert "v1" in msg and "v2" in msg


def test_check_valve_forward_flow():
    """Rückschlagklappe in Durchlassrichtung: wirkt wie Kv-Widerstand."""
    net = h.Network()
    net.add(h.Inflow("zu", t_set_C=60, p_kPa=50))
    net.add(h.CheckValve("rk", kvs_m3h=6.3))
    net.add(h.Outflow("ab", p_kPa=0))
    net.connect("zu.port", "rk.in")
    net.connect("rk.out", "ab.port")
    r = net.solve()
    q_ref = math.sqrt(50e3 / kv_to_b(6.3, net.fluid.rho))
    assert r["rk"].q_m3h == pytest.approx(q_ref * 3600, rel=1e-6)


def test_check_valve_blocks_reverse_flow():
    """Treibendes Druckgefälle GEGEN die Pfeilrichtung: Klappe sperrt
    (Restleckage = Vorwärtsstrom/√block_factor = 1/1000)."""
    net = h.Network()
    net.add(h.Inflow("zu", t_set_C=60, p_kPa=50))
    net.add(h.CheckValve("rk", kvs_m3h=6.3))
    net.add(h.Outflow("ab", p_kPa=0))
    net.connect("zu.port", "rk.out")       # rückwärts angeströmt
    net.connect("rk.in", "ab.port")
    r = net.solve()
    q_fwd = math.sqrt(50e3 / kv_to_b(6.3, net.fluid.rho)) * 3600
    assert r["rk"].q_m3h < 0                                    # Richtung out→in
    assert abs(r["rk"].q_m3h) == pytest.approx(q_fwd / 1000, rel=1e-3)


def test_check_valve_selects_open_branch():
    """Zwei antiparallele Klappen: nur die richtig orientierte führt Durchfluss."""
    net = h.Network()
    net.add(h.Inflow("zu", t_set_C=60, p_kPa=30))
    net.add(h.Outflow("ab", p_kPa=0))
    net.add(h.CheckValve("rk_auf", kvs_m3h=4.0))
    net.add(h.CheckValve("rk_zu", kvs_m3h=4.0))
    net.connect("zu.port", "rk_auf.in", "rk_zu.out")   # rk_zu falsch herum
    net.connect("rk_auf.out", "rk_zu.in", "ab.port")
    r = net.solve()
    assert r["rk_auf"].q_m3h > 0
    assert abs(r["rk_zu"].q_m3h) < r["rk_auf"].q_m3h / 500


def test_ball_valve_open_negligible_resistance():
    """Kugelhahn offen (Default-Kvs 1000): praktisch widerstandsfrei."""
    import hydraulik as h
    doc = {
        "components": {
            "pu": {"type": "pump", "mode": "constant_flow", "q_m3h": 2.0},
            "kh": {"type": "ball_valve"},
            "rv": {"type": "control_valve", "kvs_m3h": 4.0},
        },
        "connections": [["pu.out", "kh.in"], ["kh.out", "rv.in"], ["rv.out", "pu.in"]],
    }
    r = h.load(doc).solve(thermal=False)
    assert r.converged
    # Δp = (2/1000)²·1e5·ρ/1000 ≈ 0.4 Pa — vernachlässigbar gegen das Ventil
    assert abs(r["kh"].dp_kPa) < 1e-3
    assert r["kh"].q_m3h == pytest.approx(2.0, abs=1e-9)


def test_ball_valve_closed_blocks_exactly():
    """Kugelhahn zu: V̇ = 0 als Randbedingung, Parallelzweig übernimmt alles."""
    import hydraulik as h
    doc = {
        "components": {
            "pu": {"type": "pump", "mode": "constant_dp", "dp_kPa": 30, "q_nom_m3h": 2.0},
            "kh": {"type": "ball_valve", "closed": True},
            "rv1": {"type": "control_valve", "kvs_m3h": 4.0},
            "rv2": {"type": "control_valve", "kvs_m3h": 4.0},
        },
        "connections": [
            ["pu.out", "kh.in", "rv2.in"],
            ["kh.out", "rv1.in"],
            ["rv1.out", "rv2.out", "pu.in"],
        ],
    }
    r = h.load(doc).solve(thermal=False)
    assert r.converged
    assert r["kh"].q_m3h == pytest.approx(0.0, abs=1e-12)
    assert abs(r["rv2"].q_m3h) > 0.1
