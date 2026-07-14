"""link, source/ideal_storage und Mehrfach-Fluss-RB je Knoten."""
import pytest

import hydraulik as h


def _header_network(with_link: bool) -> h.SolutionResult:
    """Sammler mit Zapfstelle VOR einer stromab liegenden Einmischung.

    zu (2 m³/h, 60 °C) → N1 ─(link)→ N2 → v4 → ab (Druck-RB)
                          │            ↑
                        tap (1 m³/h)  zu2 (1 m³/h, 30 °C)

    Physik: Die Zapfstelle an N1 muss reines 60-°C-Wasser sehen; erst an N2
    mischt der 30-°C-Zustrom ein → dort (1·60 + 1·30)/2 = 45 °C.
    """
    net = h.Network()
    net.add(h.OpenEnd("zu", bc="flow", q_m3h=2.0, t_supply_C=60))
    net.add(h.Pump("tap", mode="constant_flow", q_m3h=1.0))
    net.add(h.OpenEnd("tap_end", bc="pressure", p_kPa=100))
    net.add(h.OpenEnd("zu2", bc="flow", q_m3h=1.0, t_supply_C=30))
    net.add(h.FlowResistance("v4", c_Pa_m3h2=100.0))
    net.add(h.OpenEnd("ab", bc="pressure", p_kPa=100))
    net.connect("tap.out", "tap_end.port")
    net.connect("v4.out", "ab.port")
    if with_link:
        net.add(h.Link("lk", q_nom_m3h=1.0))
        net.connect("zu.port", "tap.in", "lk.in")          # N1
        net.connect("lk.out", "zu2.port", "v4.in")         # N2
    else:
        net.connect("zu.port", "tap.in", "zu2.port", "v4.in")   # alles EIN Knoten
    return net.solve()


def test_link_separates_mixing_points():
    r = _header_network(with_link=True)
    assert r["tap"].t_in_C == pytest.approx(60.0, abs=1e-6)    # zapft VOR der Einmischung
    assert r["lk"].q_m3h == pytest.approx(1.0, rel=1e-6)
    assert r["v4"].t_in_C == pytest.approx(45.0, abs=1e-6)     # Mischung erst an N2


def test_without_link_everything_mixes():
    """Dokumentiert die Verschmelzungssemantik: ein Knoten = eine Mischtemperatur."""
    r = _header_network(with_link=False)
    t_mix = (2 * 60 + 1 * 30) / 3
    assert r["tap"].t_in_C == pytest.approx(t_mix, abs=1e-6)
    assert r["v4"].t_in_C == pytest.approx(t_mix, abs=1e-6)


def test_link_dp_negligible():
    r = _header_network(with_link=True)
    assert abs(r["lk"].dp_kPa) < 0.01   # ~1 Pa bei q_nom


def test_ideal_storage_sets_outlet_temp():
    """Quelle prägt t_set am Austritt auf; Rücklauf und Leistung sind Ergebnis."""
    net = h.Network()
    net.add(h.IdealStorage("quelle", t_set_C=70, q_nom_m3h=2.0))
    net.add(h.Pump("pu", mode="constant_flow", q_m3h=2.0))
    net.add(h.Radiator("last", q_prescribed_kW=10.0, kv_m3h=100))
    net.connect("quelle.out", "pu.in")
    net.connect("pu.out", "last.in")
    net.connect("last.out", "quelle.in")
    r = net.solve()
    fl = net.fluid
    dt = 10e3 / (2.0 / 3600 * fl.rho * fl.cp)
    assert r["last"].t_in_C == pytest.approx(70.0, abs=1e-9)
    assert r["quelle"].t_in_C == pytest.approx(70.0 - dt, abs=1e-6)
    assert r["quelle"].q_dot_kW == pytest.approx(10.0, rel=1e-6)
    assert abs(r["quelle"].dp_kPa) < 0.01           # quasi-ideal


def test_ideal_storage_via_yaml():
    doc = {
        "components": {
            "sp": {"type": "ideal_storage", "t_set_C": 70, "q_nom_m3h": 1.5},
            "pu": {"type": "pump", "mode": "constant_flow", "q_m3h": 1.5},
            "hk": {"type": "radiator", "q_prescribed_kW": 5, "kv_m3h": 100},
        },
        "connections": [["sp.out", "pu.in"], ["pu.out", "hk.in"], ["hk.out", "sp.in"]],
    }
    r = h.load(doc).solve()
    assert r["hk"].t_in_C == pytest.approx(70.0, abs=1e-9)
    assert abs(r.energy_imbalance_W) < 0.5


def test_multiple_flow_bcs_per_node_keep_their_temperatures():
    """Zwei Fluss-RB am selben Knoten: Enthalpie je RB, nicht 'last wins'."""
    net = h.Network()
    net.add(h.OpenEnd("zu1", bc="flow", q_m3h=1.0, t_supply_C=80))
    net.add(h.OpenEnd("zu2", bc="flow", q_m3h=1.0, t_supply_C=40))
    net.add(h.FlowResistance("v1", c_Pa_m3h2=100.0))
    net.add(h.OpenEnd("ab", bc="pressure", p_kPa=100))
    net.connect("zu1.port", "zu2.port", "v1.in")
    net.connect("v1.out", "ab.port")
    r = net.solve()
    assert r["v1"].q_m3h == pytest.approx(2.0, rel=1e-9)
    assert r["v1"].t_in_C == pytest.approx(60.0, abs=1e-9)     # (80+40)/2
    assert abs(r.energy_imbalance_W) < 1e-6


def test_ideal_storage_with_internal_dp():
    net = h.Network()
    net.add(h.IdealStorage("q1", t_set_C=50, dp_nom_kPa=10, q_nom_m3h=2.0))
    net.add(h.Pump("pu", mode="constant_flow", q_m3h=2.0))
    net.connect("q1.out", "pu.in")
    net.connect("pu.out", "q1.in")
    r = net.solve()
    assert r["q1"].dp_kPa == pytest.approx(10.0, rel=1e-6)


def test_source_with_prescribed_flow():
    """Allgemeine Quelle mit eingeprägtem Volumenstrom: treibt den Kreis
    ohne Pumpe; ihre Druckdifferenz ist Ergebnis (= Kreiswiderstand)."""
    net = h.Network()
    net.add(h.IdealStorage("q1", t_set_C=60, q_m3h=1.5))
    net.add(h.FlowResistance("w1", c_Pa_m3h2=8000))
    net.connect("q1.out", "w1.in")
    net.connect("w1.out", "q1.in")
    r = net.solve()
    assert r["w1"].q_m3h == pytest.approx(1.5, rel=1e-9)
    assert r["w1"].t_in_C == pytest.approx(60.0, abs=1e-9)
    assert -r["q1"].dp_kPa == pytest.approx(8000 * 1.5**2 / 1e3, rel=1e-6)


def test_source_with_pressure_anchor():
    """Quelle mit p_out verankert das Druckniveau (gauge) – kein Auto-Referenz-
    Hinweis, Austrittsknoten liegt exakt auf p_out."""
    net = h.Network()
    net.add(h.IdealStorage("sp", t_set_C=70, p_out_kPa=250))
    net.add(h.Pump("pu", mode="constant_flow", q_m3h=2.0))
    net.add(h.FlowResistance("w1", c_Pa_m3h2=5000))
    net.connect("sp.out", "pu.in")
    net.connect("pu.out", "w1.in")
    net.connect("w1.out", "sp.in")
    r = net.solve()
    assert not any("Referenzdruck" in n for n in r.notices)
    node = next(n for n in r.nodes if "sp.out" in n.label)
    assert node.p_kPa == pytest.approx(250.0, abs=1e-6)


def test_source_with_flow_and_pressure():
    """q und p_out kombinierbar: Flusszwang + Druckanker."""
    net = h.Network()
    net.add(h.IdealStorage("q1", t_set_C=45, q_m3h=1.0, p_out_kPa=100))
    net.add(h.FlowResistance("w1", c_Pa_m3h2=10000))
    net.connect("q1.out", "w1.in")
    net.connect("w1.out", "q1.in")
    r = net.solve()
    assert r["w1"].q_m3h == pytest.approx(1.0, rel=1e-9)
    node = next(n for n in r.nodes if "q1.out" in n.label)
    assert node.p_kPa == pytest.approx(100.0, abs=1e-6)


def test_inflow_flow_to_outflow_pressure():
    """Quelle (1 Anschluss): T + V̇; Gegenseite Quelle mit p (gauge)."""
    net = h.Network()
    net.add(h.Inflow("zu", t_set_C=65, q_m3h=1.2))
    net.add(h.FlowResistance("w1", c_Pa_m3h2=2000))
    net.add(h.Outflow("ab", p_kPa=0))                # Auslauf ins Freie (0 kPa ü)
    net.connect("zu.port", "w1.in")
    net.connect("w1.out", "ab.port")
    r = net.solve()
    assert r["w1"].q_m3h == pytest.approx(1.2, rel=1e-9)
    assert r["w1"].t_in_C == pytest.approx(65.0, abs=1e-9)
    node = next(n for n in r.nodes if "ab.port" in n.label)
    assert node.p_kPa == pytest.approx(0.0, abs=1e-6)


def test_inflow_outflow_pressure_pair():
    """Zwei Druck-Quellen: V̇ folgt aus Δp = C·V̇²."""
    net = h.Network()
    net.add(h.Inflow("zu", t_set_C=80, p_kPa=200))
    net.add(h.Outflow("ab", p_kPa=100))
    net.add(h.FlowResistance("w1", c_Pa_m3h2=10000))
    net.connect("zu.port", "w1.in")
    net.connect("w1.out", "ab.port")
    r = net.solve()
    assert r["w1"].q_m3h == pytest.approx((100e3 / 10000) ** 0.5, rel=1e-6)
    assert r["w1"].t_out_C == pytest.approx(80.0, abs=1e-6)


def test_inflow_outflow_require_exactly_one_bc():
    with pytest.raises(h.ComponentParamError):
        h.Inflow("q1", t_set_C=50)                          # weder q noch p
    with pytest.raises(h.ComponentParamError):
        h.Inflow("q1", t_set_C=50, q_m3h=1.0, p_kPa=100)    # beides
    with pytest.raises(h.ComponentParamError):
        h.Outflow("a1")
    with pytest.raises(h.ComponentParamError):
        h.Outflow("a1", p_kPa=0, q_m3h=1.0)


def test_outflow_with_prescribed_extraction():
    """Outflow mit Entnahme-V̇: erzwingt die Ausströmmenge; Druck ist Ergebnis."""
    net = h.Network()
    net.add(h.Inflow("zu", t_set_C=70, p_kPa=80))
    net.add(h.FlowResistance("w1", c_Pa_m3h2=4000))
    net.add(h.Outflow("ab", q_m3h=2.5))
    net.connect("zu.port", "w1.in")
    net.connect("w1.out", "ab.port")
    r = net.solve()
    assert r["w1"].q_m3h == pytest.approx(2.5, rel=1e-9)
    assert r["w1"].t_out_C == pytest.approx(70.0, abs=1e-9)
    node = next(n for n in r.nodes if "ab.port" in n.label)
    assert node.p_kPa == pytest.approx(80 - 4000 * 2.5**2 / 1e3, rel=1e-6)


def test_cap_blocks_branch():
    """Dichtes Endstück: Abzweig führt exakt V̇ = 0, Hauptstrang unbeeinflusst."""
    net = h.Network()
    net.add(h.Inflow("zu", t_set_C=60, p_kPa=50))
    net.add(h.Outflow("ab", p_kPa=0))
    net.add(h.FlowResistance("w1", c_Pa_m3h2=5000))
    net.add(h.Tee("t1"))
    net.add(h.FlowResistance("stich", c_Pa_m3h2=1000))   # abgestöpselter Strang
    net.add(h.Cap("es1"))
    net.connect("zu.port", "w1.in")
    net.connect("w1.out", "t1.a")
    net.connect("t1.b", "ab.port")
    net.connect("t1.c", "stich.in")
    net.connect("stich.out", "es1.port")
    r = net.solve()
    assert r["stich"].q_m3h == pytest.approx(0.0, abs=1e-9)
    assert r["w1"].q_m3h == pytest.approx((50e3 / 5000) ** 0.5, rel=1e-6)
    # Sackknoten: Druck = Abzweigdruck (kein Verlust bei V̇=0), thermisch stagnierend
    node = next(n for n in r.nodes if "es1.port" in n.label)
    tee = next(n for n in r.nodes if "t1.a" in n.label)
    assert node.p_kPa == pytest.approx(tee.p_kPa, abs=1e-6)
    assert node.stagnant


def _conduit_case(**params):
    net = h.Network()
    net.add(h.Inflow("zu", t_set_C=60, p_kPa=50))
    net.add(h.Conduit("lt1", **params))
    net.add(h.Outflow("ab", p_kPa=0))
    net.connect("zu.port", "lt1.in")
    net.connect("lt1.out", "ab.port")
    return net.solve()


def test_conduit_ideal_default():
    """Ideale Verbindungsleitung in Serie mit realem Widerstand: ihr Δp ist
    vernachlässigbar, der Durchfluss wird vom Widerstand bestimmt."""
    net = h.Network()
    net.add(h.Inflow("zu", t_set_C=60, p_kPa=50))
    net.add(h.FlowResistance("w1", c_Pa_m3h2=5000))
    net.add(h.Conduit("lt1"))
    net.add(h.Outflow("ab", p_kPa=0))
    net.connect("zu.port", "w1.in")
    net.connect("w1.out", "lt1.in")
    net.connect("lt1.out", "ab.port")
    r = net.solve()
    assert r["w1"].q_m3h == pytest.approx((50e3 / 5000) ** 0.5, rel=1e-3)
    assert abs(r["lt1"].dp_kPa) < 0.02          # quasi-ideal (~1 Pa bei q_nom)
    assert "v_m_s" not in r["lt1"].extras       # kein Schein-v ohne Rohrmodell


def test_conduit_c_value():
    r = _conduit_case(c_Pa_m3h2=5000)
    assert r["lt1"].q_m3h == pytest.approx((50e3 / 5000) ** 0.5, rel=1e-6)


def test_conduit_design_point():
    r1 = _conduit_case(c_Pa_m3h2=20000)
    r2 = _conduit_case(dp_kPa=20, q_m3h=1.0)    # C = 20000
    assert r1["lt1"].q_m3h == pytest.approx(r2["lt1"].q_m3h, rel=1e-6)


def test_conduit_pipe_mode_matches_pipe_component():
    """Rohrmodus der Verbindungsleitung ≡ Pipe-Komponente (inkl. Wärmeverlust)."""
    def run(cls, name, **kw):
        net = h.Network()
        net.add(h.Inflow("zu", t_set_C=70, q_m3h=0.5))
        net.add(cls(name, length_m=50, d_inner_mm=20, u_linear_W_mK=0.35, t_amb_C=10, **kw))
        net.add(h.Outflow("ab", p_kPa=0))
        net.connect("zu.port", f"{name}.in")
        net.connect(f"{name}.out", "ab.port")
        return net.solve()[name]
    a, b = run(h.Conduit, "lt1"), run(h.Pipe, "ro1")
    assert a.dp_kPa == pytest.approx(b.dp_kPa, rel=1e-9)
    assert a.t_out_C == pytest.approx(b.t_out_C, abs=1e-9)


def test_conduit_rejects_multiple_modes():
    with pytest.raises(h.ComponentParamError):
        h.Conduit("lt1", c_Pa_m3h2=1000, length_m=10)
    with pytest.raises(h.ComponentParamError):
        h.Conduit("lt1", dp_kPa=10)                 # q fehlt


def test_conduit_pipes_liste_mehrere_abschnitte():
    """pipes-Liste: mehrere Rohrabschnitte in Reihe = Summe der Einzelrohre,
    hydraulisch (a+b je Abschnitt) und thermisch (Gesamtlänge)."""
    import hydraulik as h

    def netz(comps_lt):
        doc = {"components": {
                   "zu": {"type": "inflow", "t_set_C": 70.0, "q_m3h": 1.5},
                   **comps_lt,
                   "ab": {"type": "outflow", "p_kPa": 150}},
               "connections": [["zu.port", "lt1.in"]] + comps_lt["_conn"] + [["_last.out", "ab.port"]]}
        del doc["components"]["_conn"]
        return doc

    seg1 = dict(length_m=12.0, d_inner_mm=26.0, zeta=2.0)
    seg2 = dict(length_m=8.0, d_inner_mm=20.0, zeta=1.5)
    # Variante A: EIN conduit mit pipes-Liste
    doc_a = {"components": {
                 "zu": {"type": "inflow", "t_set_C": 70.0, "q_m3h": 1.5},
                 "lt1": {"type": "conduit", "pipes": [seg1, seg2],
                         "u_linear_W_mK": 0.35, "t_amb_C": 15.0},
                 "ab": {"type": "outflow", "p_kPa": 150}},
             "connections": [["zu.port", "lt1.in"], ["lt1.out", "ab.port"]]}
    # Variante B: zwei einzelne Rohr-conduits in Reihe (Referenz)
    doc_b = {"components": {
                 "zu": {"type": "inflow", "t_set_C": 70.0, "q_m3h": 1.5},
                 "lt1": {"type": "conduit", **seg1, "u_linear_W_mK": 0.35, "t_amb_C": 15.0},
                 "lt2": {"type": "conduit", **seg2, "u_linear_W_mK": 0.35, "t_amb_C": 15.0},
                 "ab": {"type": "outflow", "p_kPa": 150}},
             "connections": [["zu.port", "lt1.in"], ["lt1.out", "lt2.in"],
                             ["lt2.out", "ab.port"]]}
    ra = h.load(doc_a).solve()
    rb = h.load(doc_b).solve()
    assert ra["lt1"].dp_kPa == pytest.approx(rb["lt1"].dp_kPa + rb["lt2"].dp_kPa, rel=1e-9)
    assert ra["lt1"].t_out_C == pytest.approx(rb["lt2"].t_out_C, rel=1e-9)
    assert ra["lt1"].q_dot_kW == pytest.approx(rb["lt1"].q_dot_kW + rb["lt2"].q_dot_kW, rel=1e-9)


def test_conduit_pipes_validierung():
    """pipes + length gleichzeitig → Fehler; fehlerhafte Abschnitte → klare Meldung."""
    import hydraulik as h
    with pytest.raises(h.NetworkValidationError) as exc:
        h.load({"components": {
                    "zu": {"type": "inflow", "t_set_C": 70, "q_m3h": 1},
                    "lt1": {"type": "conduit", "length_m": 5,
                            "pipes": [{"length_m": 3}]},
                    "ab": {"type": "outflow", "p_kPa": 150}},
                "connections": [["zu.port", "lt1.in"], ["lt1.out", "ab.port"]]})
    assert "Rohrmodell" in str(exc.value)
    with pytest.raises(h.NetworkValidationError) as exc:
        h.load({"components": {
                    "zu": {"type": "inflow", "t_set_C": 70, "q_m3h": 1},
                    "lt1": {"type": "conduit", "pipes": [{"d_inner_mm": 26}]},
                    "ab": {"type": "outflow", "p_kPa": 150}},
                "connections": [["zu.port", "lt1.in"], ["lt1.out", "ab.port"]]})
    assert "pipes[0]" in str(exc.value) and "length" in str(exc.value)
