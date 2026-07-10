"""Teilstrecken-Gruppierung (ts-Label): Kettenauswertung + Konsistenzprüfung."""
import pytest

import hydraulik as h


def _loop():
    """Kreis: Quelle → [TS1: vl] → Heizkörper → [TS1: rl] → Pumpe → Quelle."""
    net = h.Network()
    net.add(h.IdealStorage("sp", t_set_C=70, q_nom_m3h=2.0))
    net.add(h.Pump("pu", mode="constant_flow", q_m3h=2.0))
    net.add(h.FlowResistance("vl", c_Pa_m3h2=1000.0, ts=1))
    net.add(h.FlowResistance("rl", c_Pa_m3h2=1000.0, ts=1))
    net.add(h.Radiator("hk", q_prescribed_kW=10, kv_m3h=100))
    net.connect("sp.out", "pu.in")
    net.connect("pu.out", "vl.in")
    net.connect("vl.out", "hk.in")
    net.connect("hk.out", "rl.in")
    net.connect("rl.out", "sp.in")
    return net


def test_ts_group_splits_into_vl_and_rl_segment():
    r = _loop().solve()
    segs = [s for s in r.teilstrecken if s.ts == "1"]
    assert len(segs) == 2                       # Vor- und Rücklaufabschnitt getrennt
    for s in segs:
        assert s.q_m3h == pytest.approx(2.0, rel=1e-6)
        assert s.dp_kPa == pytest.approx(1000.0 * 4 / 1e3, rel=1e-6)   # C·V̇²
        assert s.p_in_kPa - s.p_out_kPa == pytest.approx(s.dp_kPa, rel=1e-9)
    # Vorlaufabschnitt bei 70 °C, Rücklaufabschnitt um ΔT = Q̇/(ṁcp) kälter
    temps = sorted(s.t_in_C for s in segs)
    fl = h.WATER_DEFAULT
    dt = 10e3 / (2.0 / 3600 * fl.rho * fl.cp)
    assert temps[1] == pytest.approx(70.0, abs=1e-6)
    assert temps[0] == pytest.approx(70.0 - dt, abs=1e-3)


def test_ts_chain_orders_components_along_flow():
    net = _loop()
    net.components["hk"].ts = "1"               # ganze Kette einer TS zuordnen
    r = net.solve()
    segs = [s for s in r.teilstrecken if s.ts == "1"]
    assert len(segs) == 1
    assert segs[0].components == ["vl", "hk", "rl"]
    assert segs[0].t_in_C == pytest.approx(70.0, abs=1e-6)
    assert segs[0].t_out_C < 70.0               # nach dem Heizkörper


def test_ts_inconsistent_flow_warns():
    """Gruppe überspannt eine Verzweigung → unterschiedliche Volumenströme."""
    net = h.Network()
    net.add(h.Pump("pu", mode="constant_flow", q_m3h=3.0))
    net.add(h.FlowResistance("haupt", c_Pa_m3h2=100.0, ts="X"))
    net.add(h.FlowResistance("ast1", c_Pa_m3h2=1000.0, ts="X"))   # falsch: anderer V̇
    net.add(h.FlowResistance("ast2", c_Pa_m3h2=1000.0))
    net.connect("pu.out", "haupt.in")
    net.connect("haupt.out", "ast1.in", "ast2.in")
    net.connect("ast1.out", "ast2.out", "pu.in")
    r = net.solve()
    assert any("uneinheitliche Volumenströme" in n and "X" in n for n in r.notices)


def test_ts_label_via_yaml_and_report():
    doc = {
        "components": {
            "sp": {"type": "ideal_storage", "t_set_C": 60, "q_nom_m3h": 1.0},
            "pu": {"type": "pump", "mode": "constant_flow", "q_m3h": 1.0},
            "v1": {"type": "flow_resistance", "c_Pa_m3h2": 500, "ts": 3},
        },
        "connections": [["sp.out", "pu.in"], ["pu.out", "v1.in"], ["v1.out", "sp.in"]],
    }
    r = h.load(doc).solve()
    assert r.teilstrecken[0].ts == "3"
    assert "Teilstrecke" not in " ".join(r.notices)   # konsistent → kein Hinweis
    assert "TS" in r.report() and "v1" in r.report()
