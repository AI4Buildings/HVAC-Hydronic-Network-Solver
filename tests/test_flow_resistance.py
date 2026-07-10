"""flow_resistance: C-Wert-Widerstand gegen analytische Referenzen und
Kreuzvalidierung Beispiel 06 (C-Werte) vs. 05 (Kvs-Äquivalente)."""
import math
from pathlib import Path

import pytest

import hydraulik as h

EXAMPLES = Path(__file__).parent.parent / "examples"


def _flow_through(c_value_pa_m3h2=None, **params):
    """Offenes Rohr zwischen 300 und 200 kPa mit einem flow_resistance."""
    net = h.Network()
    net.add(h.OpenEnd("e1", bc="pressure", p_kPa=300))
    net.add(h.OpenEnd("e2", bc="pressure", p_kPa=200))
    if c_value_pa_m3h2 is not None:
        params["c_Pa_m3h2"] = c_value_pa_m3h2
    net.add(h.FlowResistance("ts", **params))
    net.connect("e1.port", "ts.in")
    net.connect("ts.out", "e2.port")
    return net.solve()


def test_dp_equals_c_q_squared():
    """Q[m³/h] = sqrt(dp / C) (dp = 100 kPa, C = 15000 Pa/(m³/h)²);
    Toleranz = Solver-Konvergenztoleranz (1e-6)."""
    r = _flow_through(15000)
    assert r["ts"].q_m3h == pytest.approx(math.sqrt(100e3 / 15000), rel=1e-6)


def test_si_variant_equivalent():
    """c_Pa_m3s2 = c_Pa_m3h2 · 3600² liefert dasselbe Ergebnis."""
    r1 = _flow_through(15000)
    r2 = _flow_through(c_Pa_m3s2=15000 * 3600.0 ** 2)
    assert r1["ts"].q_m3h == pytest.approx(r2["ts"].q_m3h, rel=1e-6)


def test_design_point_variant():
    """dp = 22 kPa bei 1 m³/h ⇒ C = 22000 Pa/(m³/h)²."""
    r1 = _flow_through(22000)
    r2 = _flow_through(dp_kPa=22, q_m3h=1.0)
    assert r1["ts"].q_m3h == pytest.approx(r2["ts"].q_m3h, rel=1e-6)


def test_linear_part():
    """dp = a·Q + C·Q² am Ergebnis nachrechenbar."""
    net = h.Network()
    net.add(h.OpenEnd("zu", bc="flow", q_m3h=1.5))
    net.add(h.OpenEnd("ab", bc="pressure", p_kPa=100))
    net.add(h.FlowResistance("ts", c_Pa_m3h2=8000, a_Pa_m3h=500))
    net.connect("zu.port", "ts.in")
    net.connect("ts.out", "ab.port")
    r = net.solve()
    dp_ref = 500 * 1.5 + 8000 * 1.5 ** 2   # Pa, in m³/h-Einheiten
    assert r["ts"].dp_kPa * 1e3 == pytest.approx(dp_ref, rel=1e-9)


def test_flow_reversal_sign_correct():
    """Umgekehrtes Druckgefälle → Q < 0 mit gleichem Betrag."""
    net = h.Network()
    net.add(h.OpenEnd("e1", bc="pressure", p_kPa=200))
    net.add(h.OpenEnd("e2", bc="pressure", p_kPa=300))
    net.add(h.FlowResistance("ts", c_Pa_m3h2=15000))
    net.connect("e1.port", "ts.in")
    net.connect("ts.out", "e2.port")
    r = net.solve()
    assert r["ts"].q_m3h == pytest.approx(-math.sqrt(100e3 / 15000), rel=1e-6)


@pytest.mark.parametrize("params,fragment", [
    ({}, "Widerstand fehlt"),
    ({"c_Pa_m3h2": 15000, "dp_kPa": 22, "q_m3h": 1.0}, "nicht beides"),
    ({"dp_kPa": 22}, "unvollständig"),
    ({"q_m3h": 1.0}, "unvollständig"),
    ({"c_Pa_m3h2": -5}, "positiv"),
    ({"dp_kPa": 22, "q_m3h": 0.0}, "positiv"),
])
def test_validation_errors(params, fragment):
    with pytest.raises(h.ComponentParamError) as exc:
        h.FlowResistance("ts", **params)
    assert fragment in str(exc.value)


def test_yaml_loader_collects_flow_resistance_errors():
    doc = {
        "components": {
            "ts1": {"type": "flow_resistance"},                       # Angabe fehlt
            "ts2": {"type": "flow_resistance", "c_pa_m3h2": 100},     # Suffix-Tippfehler
        },
        "connections": [["ts1.out", "ts2.in"]],
    }
    with pytest.raises(h.NetworkValidationError) as exc:
        h.load(doc)
    msg = str(exc.value)
    assert "c_Pa_m3h2" in msg     # Korrekturvorschlag bzw. gültiger Schlüssel genannt
    assert "ts1" in msg and "ts2" in msg


def test_example_06_matches_example_05_at_rho_1000():
    """Kreuzvalidierung: direkte C-Werte (06) vs. Kvs-Äquivalente (05).

    Die Kv-Definition enthält die Dichte (Δp = (Q/Kv)²·1e5·ρ/1000), die
    Umrechnung Kvs = sqrt(1e5/C) ist daher NUR bei ρ = 1000 kg/m³ exakt.
    Bei ρ = 1000 müssen beide Beispiele bis auf die Kvs-Rundung (4–6
    signifikante Stellen) übereinstimmen.
    """
    rho1000 = h.Fluid("wasser_rho1000", rho=1000.0, mu=0.6e-3, cp=4180.0)
    net05 = h.load(EXAMPLES / "05_umlenk_einspritz.yaml")
    net06 = h.load(EXAMPLES / "06_umlenk_einspritz_flow_resistance.yaml")
    net05.fluid = net06.fluid = rho1000
    r05, r06 = net05.solve(), net06.solve()
    for ts in [f"ts{i}" for i in range(1, 9)] + ["mv1:a", "mv1:b", "dv1", "erz"]:
        assert r06[ts].q_m3h == pytest.approx(r05[ts].q_m3h, rel=1e-4), ts
    assert r06.converged and abs(r06.energy_imbalance_W) < 1.0


def test_example_06_density_effect_vs_05():
    """Bei Wasser 45 °C (ρ = 990.1) liegen die Kv-basierten Widerstände in 05
    um Faktor ρ/1000 zu niedrig → Volumenströme dort ~0.2–0.5 % zu hoch.
    06 (dichteunabhängige C-Werte) ist die korrektere REGuA-Abbildung."""
    r05 = h.load(EXAMPLES / "05_umlenk_einspritz.yaml").solve()
    r06 = h.load(EXAMPLES / "06_umlenk_einspritz_flow_resistance.yaml").solve()
    assert r06["ts1"].q_m3h < r05["ts1"].q_m3h                     # systematisch kleiner
    assert r06["ts1"].q_m3h == pytest.approx(r05["ts1"].q_m3h, rel=6e-3)  # aber nahe
