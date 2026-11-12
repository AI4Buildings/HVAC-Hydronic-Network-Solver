"""Fehlerbehebungsrunde 2026-11 – Rechenkern (Netzaufbau, Druckränder,
Sensoren, Heizkörper, Bericht). Die Tests bilden die reproduzierten Befunde
der Projektanalyse vom 2026-11-12 ab und schlugen vor der Korrektur fehl;
einzige Ausnahme ist der Knotenpunkt-Test, der die Grenze der neuen
Sensorregel sichert (mehrere Leitungen an der Messstelle → Knotendruck)."""
import math

import pytest

import hydraulik as h

W = h.water_at(50.0)
RHO = W.rho


def _area(d):
    return math.pi * d * d / 4.0


def _sensor(r, name):
    return next(s for s in r.sensors if s.name == name).readings


def _knotendruck(net, r, ref):
    """Knotendruck [kPa] am Anschluss `ref` (Knotenbezeichnungen kürzen ab
    drei Ports ab – daher über die Knotenelemente des kompilierten Netzes)."""
    idx = next(n.index for n in net.compile().nodes if ref in n.elements)
    return r.nodes[idx].p_kPa


# ---------------------------------------------------------------- Netzaufbau

def test_komponentenname_mit_punkt_wird_gerechnet():
    """Namen wie „HK 1.OG" (Editor lässt sie zu): Portreferenzen werden am
    LETZTEN Punkt getrennt – früher KeyError('HK 1') im Netzaufbau."""
    doc = {
        "components": {
            "Pumpe": {"type": "pump", "mode": "constant_dp", "dp_kPa": 20, "q_nom_m3h": 1},
            "HK 1.OG": {"type": "radiator", "q_nom_kW": 2},
            "wp": {"type": "heat_pump", "mode": "target_t_out", "t_out_set_C": 60},
        },
        "connections": [["Pumpe.out", "HK 1.OG.in"], ["HK 1.OG.out", "wp.in"],
                        ["wp.out", "Pumpe.in"]],
    }
    r = h.load(doc).solve()
    assert r["HK 1.OG"].q_m3h > 0.0
    assert r["HK 1.OG"].q_dot_kW < 0.0


def test_name_mit_punkt_neben_gleichnamigem_praefix():
    """Rohre 's' und 's.x': der Sensor an 's.x.out' gehört zu 's.x' (nicht
    zum Präfix 's') und liest dessen Geschwindigkeit."""
    net = h.Network(fluid=W)
    net.add(h.Inflow("zu", t_set_C=50, q_m3h=2.0))
    net.add(h.Pipe("s", length_m=5, d_inner_mm=40))
    net.add(h.Pipe("s.x", length_m=5, d_inner_mm=20))
    net.add(h.Outflow("ab", p_kPa=100))
    net.add(h.PressureSensor("ps"))
    net.connect("zu.port", "s.in"); net.connect("s.out", "s.x.in")
    net.connect("s.x.out", "ab.port"); net.connect("ps.port", "s.x.out")
    r = net.solve(thermal=False)
    w = 2.0 / 3600.0 / _area(0.020)
    assert _sensor(r, "ps")["p_dyn_kPa"] * 1e3 == pytest.approx(RHO * w * w / 2.0, rel=1e-9)
    assert _sensor(r, "ps")["p_kPa"] == pytest.approx(100.0, abs=5e-4)


def test_unbekannter_port_bei_name_mit_punkt_nennt_die_komponente():
    net = h.Network(fluid=W)
    net.add(h.Pump("pu", mode="constant_dp", dp_kPa=20))
    net.add(h.Pipe("r.1", length_m=5, d_inner_mm=20))
    net.connect("pu.out", "r.1.inn"); net.connect("r.1.out", "pu.in")
    with pytest.raises(h.NetworkValidationError) as exc:
        net.compile()
    assert "Komponente 'r.1'" in str(exc.value) and "Meinten Sie 'r.1.in'" in str(exc.value)


@pytest.mark.parametrize("typ", [["pipe"], 5, True, {"a": 1}])
def test_type_muss_zeichenkette_sein(typ):
    """Früher roher TypeError (unhashable / not iterable) aus Registry/difflib."""
    doc = {"components": {"x": {"type": typ, "length_m": 1, "d_inner_mm": 20},
                          "p": {"type": "pump", "mode": "constant_dp", "dp_kPa": 20}},
           "connections": [["p.out", "x.in"], ["x.out", "p.in"]]}
    with pytest.raises(h.NetworkValidationError, match="'type' muss eine Zeichenkette sein"):
        h.load(doc)


def test_reservierte_schluessel_name_und_self_gesammelt():
    """`name:` bzw. `self:` in einer Komponente: früher TypeError „multiple
    values for argument"; jetzt gesammelt mit den übrigen Parameterfehlern."""
    doc = {"components": {"pu": {"type": "pump", "name": "Hauptpumpe", "self": 1,
                                 "mode": "constant_dp", "dp_kPa": 20, "foo": 1}},
           "connections": [["pu.out", "pu.in"]]}
    with pytest.raises(h.NetworkValidationError) as exc:
        h.load(doc)
    msg = str(exc.value)
    assert "Unbekannter Parameter 'name'" in msg and "Schlüssel des Eintrags" in msg
    assert "Unbekannter Parameter 'self'" in msg
    assert "Unbekannter Parameter 'foo'" in msg


def test_port_nur_mit_sich_selbst_verbunden_ist_unverbunden():
    """[x.out, x.out] ließ das Rohrende früher unbemerkt offen."""
    net = h.Network(fluid=W)
    net.add(h.Pump("p", mode="constant_dp", dp_kPa=20))
    net.add(h.Pipe("x", length_m=1, d_inner_mm=20))
    net.connect("p.out", "x.in"); net.connect("x.out", "x.out"); net.connect("p.in", "p.in")
    with pytest.raises(h.NetworkValidationError) as exc:
        net.compile()
    msg = str(exc.value)
    assert "Port 'x.out' ist mit nichts verbunden" in msg
    assert "Port 'p.in' ist mit nichts verbunden" in msg


def test_p_ref_setzt_den_automatischen_referenzdruck():
    """settings.p_ref ist laut README/Einstellungen der Referenzdruck
    geschlossener Kreise – früher fest 150 kPa, p_ref wirkungslos."""
    def netz():
        net = h.Network(fluid=W)
        net.add(h.Pump("p", mode="constant_dp", dp_kPa=20, q_nom_m3h=2))
        net.add(h.Pipe("r", length_m=10, d_inner_mm=20))
        net.connect("p.out", "r.in"); net.connect("r.out", "p.in")
        return net
    r0 = netz().solve(thermal=False)
    assert max(n.p_kPa for n in r0.nodes) == pytest.approx(150.0)
    assert any("Referenzdruck 150 kPa" in n for n in r0.notices)
    r1 = netz().solve(h.SolverSettings(p_ref=300e3), thermal=False)
    assert max(n.p_kPa for n in r1.nodes) == pytest.approx(300.0)
    assert any("Referenzdruck 300 kPa" in n for n in r1.notices)
    # nur das Niveau verschiebt sich, Volumenstrom und Differenzen bleiben
    assert r1["r"].q_m3h == pytest.approx(r0["r"].q_m3h, rel=1e-9)
    assert r1["r"].dp_kPa == pytest.approx(r0["r"].dp_kPa, rel=1e-9)


# ------------------------------------------------- Druckrand und Messanschluss

def _rand_netz(t_ref_an):
    net = h.Network(fluid=W)
    net.add(h.Inflow("zu", t_set_C=60, p_kPa=5))
    net.add(h.Pipe("r1", length_m=2, d_inner_mm=20))
    net.add(h.EnergyMeter("wmz"))
    net.add(h.Pipe("r2", length_m=2, d_inner_mm=20))
    net.add(h.Outflow("ab", p_kPa=0))
    net.connect("zu.port", "r1.in"); net.connect("r1.out", "wmz.in")
    net.connect("wmz.out", "r2.in"); net.connect("r2.out", "ab.port")
    net.connect("wmz.t_ref", t_ref_an)
    return net.solve()


def test_wmz_fuehler_am_druckrand_ist_rueckwirkungsfrei():
    """Der Fühleranschluss t_ref des Wärmemengenzählers ist eine Anzapfung.
    Am Knoten eines statischen Druckrands zählte er als zweites Bauteil – der
    Rand galt dann als Knotendruck und der Volumenstrom fiel um 10,6 %."""
    mitte, rand = _rand_netz("r1.out"), _rand_netz("zu.port")
    assert rand["r1"].q_m3h == pytest.approx(mitte["r1"].q_m3h, rel=1e-9)
    assert any(c.name == "zu:dyn" for c in rand.components)
    assert not any("Knotenpunkt mehrerer Bauteile" in n for n in rand.notices)


def test_notiz_druckrand_unabhaengig_von_der_baureihenfolge():
    """Die Verbindungsgruppe eines Ports hängt nicht davon ab, ob ein
    verschmelzendes Bauteil (Verteiler) vor oder nach dem Rand gebaut wird –
    früher erschien der Knotenpunkt-Hinweis nur bei einer der Reihenfolgen."""
    def netz(verteiler_zuerst):
        net = h.Network(fluid=W)
        teile = [lambda: h.Manifold("vt", n_ports=2),
                 lambda: h.Inflow("zu", t_set_C=50, p_kPa=20)]
        for mk in (teile if verteiler_zuerst else teile[::-1]):
            net.add(mk())
        net.add(h.Pipe("r1", length_m=5, d_inner_mm=20)); net.add(h.Pipe("r2", length_m=5, d_inner_mm=20))
        net.add(h.Outflow("ab", p_kPa=0))
        net.connect("zu.port", "vt.s1"); net.connect("vt.main", "r1.in"); net.connect("vt.s2", "r2.in")
        net.connect("r1.out", "r2.out", "ab.port")
        return net.solve(thermal=False)
    a, b = netz(True), netz(False)
    assert a.notices == b.notices
    assert a["r1"].q_m3h == pytest.approx(b["r1"].q_m3h, rel=1e-12)


# ------------------------------------------------------------- Drucksensoren

def test_drucksensor_am_randanschluss_zeigt_den_statischen_randdruck():
    """Rand 5 kPa statisch am Rohrende: der Sensor zeigt 5 kPa – egal ob die
    Messleitung am Rohr oder (wie im Editor) am Rand-Anschluss hängt. Früher
    am Rand-Anschluss der Totaldruck (7,45 kPa)."""
    net = h.Network(fluid=W)
    net.add(h.Inflow("zu", t_set_C=60, p_kPa=5)); net.add(h.Pipe("r1", length_m=2, d_inner_mm=20))
    net.add(h.Outflow("ab", p_kPa=0))
    net.add(h.PressureSensor("ps_rand")); net.add(h.PressureSensor("ps_rohr"))
    net.connect("zu.port", "r1.in"); net.connect("r1.out", "ab.port")
    net.connect("ps_rand.port", "zu.port"); net.connect("ps_rohr.port", "r1.in")
    r = net.solve(thermal=False)
    w = r["r1"].q_m3h / 3600.0 / _area(0.020)
    for name in ("ps_rand", "ps_rohr"):
        s = _sensor(r, name)
        assert s["p_kPa"] == pytest.approx(5.0, abs=5e-4)
        assert s["p_dyn_kPa"] * 1e3 == pytest.approx(RHO * w * w / 2.0, rel=1e-9)


def test_drucksensor_am_bauteilport_nutzt_die_einzige_leitung():
    """Editor-Topologie: Leitungen sind eigene Verbindungseinträge, die
    Messleitung hängt am Bauteilport (Ventil). Liegt dort genau EINE Leitung
    mit bekanntem Querschnitt, gilt deren Geschwindigkeit (wie beim Rand)."""
    net = h.Network(fluid=W)
    net.add(h.Inflow("zu", t_set_C=60, q_m3h=1.5))
    net.add(h.Conduit("l1", length_m=2, d_inner_mm=20)); net.add(h.ControlValve("v", kvs_m3h=10))
    net.add(h.Conduit("l2", length_m=2, d_inner_mm=32)); net.add(h.Outflow("ab", p_kPa=0))
    net.add(h.PressureSensor("ps_ein")); net.add(h.PressureSensor("ps_aus"))
    net.add(h.PressureDiffSensor("dps"))
    net.connect("zu.port", "l1.in"); net.connect("l1.out", "v.in")
    net.connect("v.out", "l2.in"); net.connect("l2.out", "ab.port")
    net.connect("ps_ein.port", "v.in"); net.connect("ps_aus.port", "v.out")
    net.connect("dps.plus", "v.in"); net.connect("dps.minus", "v.out")
    r = net.solve(thermal=False)
    q = 1.5 / 3600.0
    dyn_ein, dyn_aus = RHO * (q / _area(0.020)) ** 2 / 2.0, RHO * (q / _area(0.032)) ** 2 / 2.0
    p_ein = _knotendruck(net, r, "v.in") * 1e3
    p_aus = _knotendruck(net, r, "v.out") * 1e3
    assert _sensor(r, "ps_ein")["p_dyn_kPa"] * 1e3 == pytest.approx(dyn_ein, rel=1e-9)
    assert _sensor(r, "ps_ein")["p_kPa"] * 1e3 == pytest.approx(p_ein - dyn_ein, rel=1e-9)
    assert _sensor(r, "ps_aus")["p_kPa"] * 1e3 == pytest.approx(p_aus - dyn_aus, rel=1e-9)
    # Differenzdruck statisch: Totaldruckdifferenz minus Differenz der Staudrücke
    assert _sensor(r, "dps")["dp_kPa"] * 1e3 == pytest.approx(
        (p_ein - dyn_ein) - (p_aus - dyn_aus), rel=1e-9)


def test_drucksensor_am_knotenpunkt_mehrerer_leitungen_bleibt_knotendruck():
    """Zwei Leitungen mit Querschnitt an der Messstelle: Geschwindigkeit nicht
    definiert – Knotendruck ohne p_dyn (wie beim Druckrand am Knotenpunkt)."""
    net = h.Network(fluid=W)
    net.add(h.Inflow("z1", t_set_C=50, q_m3h=1.0)); net.add(h.Inflow("z2", t_set_C=50, q_m3h=2.0))
    net.add(h.Pipe("r1", length_m=5, d_inner_mm=20)); net.add(h.Pipe("r2", length_m=5, d_inner_mm=32))
    net.add(h.ControlValve("v", kvs_m3h=10)); net.add(h.Outflow("ab", p_kPa=100))
    net.add(h.PressureSensor("ps"))
    net.connect("z1.port", "r1.in"); net.connect("z2.port", "r2.in")
    net.connect("r1.out", "r2.out", "v.in"); net.connect("v.out", "ab.port")
    net.connect("ps.port", "v.in")
    r = net.solve(thermal=False)
    assert _sensor(r, "ps") == {"p_kPa": pytest.approx(_knotendruck(net, r, "v.in"), abs=1e-12)}


def test_drucksensor_mit_d_inner_am_volumenstrom_rand():
    """d_inner am Sensor, Messleitung am Zulauf mit Volumenstrom-Vorgabe (das
    Randbauteil hat keine Kante): früher p_dyn = 0 gemeldet; jetzt der
    Volumenstrom des einzigen durchströmten Anschlusses der Messstelle."""
    net = h.Network(fluid=W)
    net.add(h.Inflow("zu", t_set_C=60, q_m3h=2.5)); net.add(h.ControlValve("v", kvs_m3h=10))
    net.add(h.Outflow("ab", p_kPa=0)); net.add(h.PressureSensor("ps", d_inner_mm=20))
    net.connect("zu.port", "v.in"); net.connect("v.out", "ab.port"); net.connect("ps.port", "zu.port")
    r = net.solve(thermal=False)
    w = 2.5 / 3600.0 / _area(0.020)
    p_knoten = _knotendruck(net, r, "v.in")
    assert _sensor(r, "ps")["p_dyn_kPa"] * 1e3 == pytest.approx(RHO * w * w / 2.0, rel=1e-9)
    assert _sensor(r, "ps")["p_kPa"] == pytest.approx(p_knoten - RHO * w * w / 2e3, abs=1e-9)


# ------------------------------------------------------- Heizkörper, Bericht

def test_heizkoerper_feste_leistung_null_ohne_nennleistung():
    """q_prescribed = 0 ohne q_nom/kv/c: früher Division durch 0 im
    Default-Widerstand, gemeldet als „außerhalb des Modellbereichs"."""
    with pytest.raises(h.ComponentParamError, match="q_nom_kW"):
        h.Radiator("hk", q_prescribed_kW=0)
    # mit Kv (oder q_nom) ist „Heizkörper aus" ein gültiger Fall
    net = h.Network(fluid=W)
    net.add(h.Pump("p", mode="constant_dp", dp_kPa=20, q_nom_m3h=1))
    net.add(h.Radiator("hk", q_prescribed_kW=0, kv_m3h=1.0))
    net.add(h.IdealStorage("sp", t_set_C=60))
    net.connect("p.out", "hk.in"); net.connect("hk.out", "sp.in"); net.connect("sp.out", "p.in")
    r = net.solve()
    assert r["hk"].q_m3h > 0.0
    assert r["hk"].q_dot_kW == pytest.approx(0.0, abs=1e-12)
    assert r["hk"].t_out_C == pytest.approx(60.0, abs=1e-6)


def test_bericht_sensorzeilen_beschriftung():
    """Volumenstromsensor: „m_dot=… kg/s" (früher „m_dot_kg="); Drucksensor
    ohne bekannte Geschwindigkeit wird als Knotendruck ausgewiesen."""
    net = h.Network(fluid=W)
    net.add(h.Inflow("zu", t_set_C=50, q_m3h=1.5)); net.add(h.FlowSensor("fs"))
    net.add(h.ControlValve("v", kvs_m3h=2.5)); net.add(h.Pipe("r", length_m=5, d_inner_mm=20))
    net.add(h.Outflow("ab", p_kPa=150))
    net.add(h.PressureSensor("ps_knoten")); net.add(h.PressureSensor("ps_rohr"))
    net.connect("zu.port", "fs.in"); net.connect("fs.out", "v.in"); net.connect("v.out", "r.in")
    net.connect("r.out", "ab.port")
    net.connect("ps_knoten.port", "fs.in"); net.connect("ps_rohr.port", "r.out")
    rep = net.solve(thermal=False).report()
    zeile = {ln.split()[0]: ln for ln in rep.splitlines() if ln.startswith(("fs ", "ps_"))}
    assert "m_dot=" in zeile["fs"] and "m_dot_kg" not in zeile["fs"]
    assert "Knotendruck" in zeile["ps_knoten"]
    assert "statisch" in zeile["ps_rohr"] and "p_dyn=" in zeile["ps_rohr"]
