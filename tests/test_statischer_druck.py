"""Druckbegriff: Knotendrücke sind Totaldrücke (alle Bauteile rechnen
Totaldruckverluste); Druckrandbedingungen und Drucksensoren arbeiten mit dem
STATISCHEN Überdruck am Anschluss, p_statisch = p_Knoten − ρw²/2, w aus dem
Querschnitt der angeschlossenen Leitung bzw. d_inner."""
import math

import pytest

import hydraulik as h

W = h.water_at(50.0)
RHO = W.rho


def _area(d):
    return math.pi * d * d / 4.0


def _pipe_dp(pipe, q):
    c = pipe.hydraulic_coefficients(q, W)
    return c.a * q + c.b * q * abs(q)


def _bisect(f, lo, hi, n=200):
    flo = f(lo)
    for _ in range(n):
        mid = 0.5 * (lo + hi)
        fm = f(mid)
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _sensor(r, name):
    return next(s for s in r.sensors if s.name == name).readings


def test_auslass_statisch_und_sensor_am_rohrende():
    """Fester Zulauf durch DN20 auf einen Auslass mit 150 kPa statisch: der
    Sensor am Rohrende zeigt exakt 150 kPa, der Knoten (Totaldruck) liegt um
    ρw²/2 höher; die Übergangskante 'ab:dyn' trägt genau ρw²/2."""
    q = 2.0 / 3600.0
    w = q / _area(0.020)
    net = h.Network(fluid=W)
    net.add(h.Inflow("zu", t_set_C=60, q_m3h=2.0))
    net.add(h.Pipe("r", length_m=10, d_inner_mm=20))
    net.add(h.Outflow("ab", p_kPa=150))
    net.add(h.PressureSensor("p_aus")); net.add(h.PressureSensor("p_ein"))
    net.connect("zu.port", "r.in"); net.connect("r.out", "ab.port")
    net.connect("p_aus.port", "r.out"); net.connect("p_ein.port", "r.in")
    r = net.solve()
    dyn = RHO * w * w / 2.0
    s_out = _sensor(r, "p_aus")
    assert s_out["p_kPa"] == pytest.approx(150.0, abs=5e-4)   # Impulstoleranz 1e-6·Druckmaßstab
    assert s_out["p_dyn_kPa"] * 1e3 == pytest.approx(dyn, rel=1e-9)
    assert r["ab:dyn"].dp_kPa * 1e3 == pytest.approx(dyn, rel=1e-6)
    # gleicher Querschnitt: statische Differenz über das Rohr = Rohrverlust
    s_in = _sensor(r, "p_ein")
    assert (s_in["p_kPa"] - 150.0) * 1e3 == pytest.approx(_pipe_dp(net.components["r"], q),
                                                          rel=1e-6)
    assert r["r"].t_out_C == pytest.approx(60.0, abs=1e-9)        # Thermik unverändert


def test_druckgetrieben_gleiche_nennweite_dynamik_hebt_sich_auf():
    """160 → 150 kPa statisch über DN20: ρw²/2 tritt an beiden Rändern gleich
    auf, der Volumenstrom folgt allein aus dem Rohrverlust (Bisektion)."""
    net = h.Network(fluid=W)
    net.add(h.Inflow("zu", t_set_C=50, p_kPa=160))
    net.add(h.Pipe("r", length_m=20, d_inner_mm=20))
    net.add(h.Outflow("ab", p_kPa=150))
    net.connect("zu.port", "r.in"); net.connect("r.out", "ab.port")
    r = net.solve(thermal=False)
    pipe = net.components["r"]
    q_ref = _bisect(lambda q: _pipe_dp(pipe, q) - 10e3, 1e-7, 0.01)
    assert r["r"].q_m3h / 3600.0 == pytest.approx(q_ref, rel=1e-6)


def test_reduzierung_statische_randdruecke_und_differenzdruck():
    """160 kPa (DN32) → 150 kPa (DN20) statisch: Bilanz der Totaldrücke
    p1 + ρw1²/2 − (p2 + ρw2²/2) = Σ Rohrverluste; der Differenzdrucksensor
    über die Reduzierung zeigt die statische Differenz 10 kPa."""
    net = h.Network(fluid=W)
    net.add(h.Inflow("zu", t_set_C=50, p_kPa=160))
    net.add(h.Pipe("r1", length_m=5, d_inner_mm=32))
    net.add(h.Pipe("r2", length_m=5, d_inner_mm=20))
    net.add(h.Outflow("ab", p_kPa=150))
    net.add(h.PressureDiffSensor("dp"))
    net.connect("zu.port", "r1.in"); net.connect("r1.out", "r2.in"); net.connect("r2.out", "ab.port")
    net.connect("dp.plus", "r1.in"); net.connect("dp.minus", "r2.out")
    r = net.solve(thermal=False)
    r1, r2 = net.components["r1"], net.components["r2"]

    def bilanz(q):
        w1, w2 = q / _area(0.032), q / _area(0.020)
        return (10e3 + RHO * (w1 * w1 - w2 * w2) / 2.0) - _pipe_dp(r1, q) - _pipe_dp(r2, q)
    q_ref = _bisect(bilanz, 1e-7, 0.02)
    assert r["r1"].q_m3h / 3600.0 == pytest.approx(q_ref, rel=1e-6)
    assert _sensor(r, "dp")["dp_kPa"] == pytest.approx(10.0, abs=5e-4)


def test_sensor_ohne_querschnitt_und_mit_d_inner():
    """An einem Ventilanschluss ist kein Querschnitt bekannt: statisch =
    Knotendruck (kein p_dyn); mit d_inner wird der Ventildurchfluss genutzt."""
    def netz(**kw):
        net = h.Network(fluid=W)
        net.add(h.Inflow("zu", t_set_C=50, q_m3h=1.5))
        net.add(h.ControlValve("v", kvs_m3h=2.5))
        net.add(h.Outflow("ab", p_kPa=150))
        net.add(h.PressureSensor("ps", **kw))
        net.connect("zu.port", "v.in"); net.connect("v.out", "ab.port"); net.connect("ps.port", "v.in")
        return net.solve(thermal=False)
    r0 = netz()
    node = next(n for n in r0.nodes if "v.in" in n.label)
    assert _sensor(r0, "ps") == {"p_kPa": pytest.approx(node.p_kPa, abs=1e-12)}
    r1 = netz(d_inner_mm=25)
    w = 1.5 / 3600.0 / _area(0.025)
    assert _sensor(r1, "ps")["p_dyn_kPa"] * 1e3 == pytest.approx(RHO * w * w / 2.0, rel=1e-9)
    assert _sensor(r1, "ps")["p_kPa"] == pytest.approx(node.p_kPa - RHO * w * w / 2e3, abs=1e-9)


def test_randbedingung_am_knotenpunkt_ohne_dynamik_mit_d_inner_wieder():
    """Rand an einem Knotenpunkt zweier Leitungen: die Geschwindigkeit am Rand
    ist nicht definiert — p gilt als Knotendruck (Hinweis, keine
    Übergangskante); mit d_inner wird der dynamische Anteil angesetzt."""
    def netz(**kw):
        net = h.Network(fluid=W)
        net.add(h.Inflow("z1", t_set_C=50, q_m3h=1.0)); net.add(h.Inflow("z2", t_set_C=50, q_m3h=2.0))
        net.add(h.Pipe("r1", length_m=5, d_inner_mm=20)); net.add(h.Pipe("r2", length_m=5, d_inner_mm=32))
        net.add(h.Outflow("ab", p_kPa=150, **kw))
        net.connect("z1.port", "r1.in"); net.connect("z2.port", "r2.in")
        net.connect("r1.out", "r2.out", "ab.port")
        return net.solve(thermal=False)
    r = netz()
    assert any("Knotenpunkt mehrerer Bauteile" in n for n in r.notices)
    assert not any(c.name == "ab:dyn" for c in r.components)
    node = next(n for n in r.nodes if "ab.port" in n.label)
    assert node.p_kPa == pytest.approx(150.0, abs=1e-9)
    r2 = netz(d_inner_mm=50)
    assert not any("Knotenpunkt mehrerer Bauteile" in n for n in r2.notices)
    w = 3.0 / 3600.0 / _area(0.050)
    assert r2["ab:dyn"].dp_kPa * 1e3 == pytest.approx(RHO * w * w / 2.0, rel=1e-6)


def test_widerspruechliche_druckraender_am_selben_knoten_bleiben_fehler():
    """Zwei Druckränder mit verschiedenem Wert am selben Knoten: Validierungs-
    fehler wie bisher (keine Übergangskante, die den Widerspruch verdeckt)."""
    net = h.Network(fluid=W)
    net.add(h.Inflow("zu", t_set_C=50, p_kPa=170)); net.add(h.Outflow("ab", p_kPa=150))
    net.add(h.Pipe("r", length_m=5, d_inner_mm=20)); net.add(h.Outflow("ab2", p_kPa=140))
    net.connect("zu.port", "ab.port", "r.in"); net.connect("r.out", "ab2.port")
    with pytest.raises(h.NetworkValidationError, match="Widersprüchliche Druck-Randbedingungen"):
        net.solve(thermal=False)


def test_auslauf_ins_freie_und_rueckstroemung():
    """Auslauf ins Freie (p = 0 statisch): der Strahl verlässt das Rohr mit
    ρw²/2 (Austrittsverlust). Rückströmung über einen Auslass: auch dann
    statisch = vorgegebener Druck, Knoten = p + ρw²/2."""
    net = h.Network(fluid=W)
    net.add(h.Inflow("zu", t_set_C=50, q_m3h=3.0))
    net.add(h.Pipe("r", length_m=2, d_inner_mm=26))
    net.add(h.OpenEnd("frei", bc="pressure", p_kPa=0.0))
    net.connect("zu.port", "r.in"); net.connect("r.out", "frei.port")
    r = net.solve(thermal=False)
    w = 3.0 / 3600.0 / _area(0.026)
    assert r["frei:dyn"].dp_kPa * 1e3 == pytest.approx(RHO * w * w / 2.0, rel=1e-6)
    # Rückströmung: Auslass 160 kPa speist über das Rohr in einen 150-kPa-Rand
    net2 = h.Network(fluid=W)
    net2.add(h.Outflow("a", p_kPa=160)); net2.add(h.Pipe("r", length_m=20, d_inner_mm=20))
    net2.add(h.Outflow("b", p_kPa=150)); net2.add(h.PressureSensor("ps"))
    net2.connect("a.port", "r.in"); net2.connect("r.out", "b.port"); net2.connect("ps.port", "r.in")
    r2 = net2.solve(thermal=False)
    assert r2["r"].q_m3h > 0.0
    assert _sensor(r2, "ps")["p_kPa"] == pytest.approx(160.0, abs=5e-4)
