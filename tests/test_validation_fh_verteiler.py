"""Validierung gegen die FH-Burgenland-Übung „Modellierung Verteiler" (06.11.2021).

Referenz: unabhängiges Excel-Modell (Übung Verteiler _LSG.pdf) für die
Umlenk-/Einspritzschaltung aus examples/06. Das Referenzmodell verwendet
ideale Δp-Pumpen und die Kv-Formel ohne Dichtekorrektur → hier ρ = 1000
und dp_internal_frac = 1e-4.

Aufgabe 2/3 (Kennlinien) sind rein hydraulisch; im Fall „Einspritzventil zu"
existiert wegen der fest vorgegebenen Verbraucherleistung im dann isolierten
Sekundärkreis keine stationäre Temperaturlösung → thermal=False.
"""
from pathlib import Path

import pytest

import hydraulik as h

EXAMPLE = Path(__file__).parent.parent / "examples" / "06_umlenk_einspritz_flow_resistance.yaml"

# Referenzlösung Aufgabe 1 (Volllast) [m³/h]
REF_VOLLLAST = {"ts1": 1.472, "ts2": 0.7361, "ts3": 0.0, "ts4": 0.7361,
                "ts5": 0.7363, "ts6": 0.7363, "ts7": 0.4313, "ts8": 1.168}


def make_net(mv=1.0, dv=1.0):
    net = h.load(EXAMPLE)
    net.fluid = h.Fluid("rho1000", rho=1000.0, mu=0.6e-3, cp=4180.0)
    for pu in ("pu_haupt", "pu_sek"):
        net.components[pu].dp_internal_frac = 1e-4
    net.components["mv1"].opening = mv
    net.components["dv1"].opening = dv
    return net


def solve(mv=1.0, dv=1.0):
    return make_net(mv, dv).solve(thermal=False)


def test_aufgabe1_volllast_volumenstroeme():
    r = solve()
    for ts, v_ref in REF_VOLLLAST.items():
        if v_ref == 0.0:
            assert abs(r[ts].q_m3h) < 1e-6, ts
        else:
            assert r[ts].q_m3h == pytest.approx(v_ref, rel=2e-3), ts


def test_aufgabe5_ventilautoritaet():
    """a_v = Δp_Ventil / Δp_variabler Zweig, am Volllastpunkt (Referenzdefinition)."""
    r = solve()
    dp_uls_zweig = r["ts4"].dp_kPa + r["ea1"].dp_kPa + r["mv1:a"].dp_kPa
    dp_ess_zweig = r["ts5"].dp_kPa + r["ts6"].dp_kPa + r["dv1"].dp_kPa
    assert r["mv1:a"].dp_kPa / dp_uls_zweig == pytest.approx(0.2381, abs=0.001)
    assert r["dv1"].dp_kPa / dp_ess_zweig == pytest.approx(0.1938, abs=0.001)


def test_aufgabe2_kennlinien_anker():
    """ULS-Ventil 100 → 0 %, ESS Volllast. Ankerwerte aus den Lösungsplots (S. 2/3)."""
    base = solve()
    r50 = solve(mv=0.5)
    r0 = solve(mv=0.0)
    assert r50["ts2"].q_m3h / base["ts2"].q_m3h == pytest.approx(1.21, abs=0.02)   # Peak Summentor
    assert r50["ts6"].q_m3h / base["ts6"].q_m3h == pytest.approx(0.89, abs=0.02)   # Einbruch Einspritzstrom
    assert r50["ts7"].q_m3h / base["ts7"].q_m3h == pytest.approx(1.19, abs=0.02)   # Anstieg Kurzschluss
    # Aufgabe 9: hydraulischer Abgleich Regel-/Bypasstor → V2(0 %) = V2(100 %)
    assert r0["ts2"].q_m3h / base["ts2"].q_m3h == pytest.approx(1.0, abs=0.005)
    # komplementäre Aufteilung bei H = 50 %
    assert r50["ts3"].q_m3h + r50["ts4"].q_m3h == pytest.approx(r50["ts2"].q_m3h, rel=1e-9)


def test_aufgabe3_kennlinien_anker():
    """ESS-Ventil 100 → 0 %, ULS Volllast. Ankerwerte aus den Lösungsplots (S. 4/5)."""
    base = solve()
    e0 = solve(dv=0.0)
    assert e0["ts4"].q_m3h / base["ts4"].q_m3h == pytest.approx(1.40, abs=0.02)    # Verbraucherstrang ULS
    assert e0["ts7"].q_m3h / base["ts7"].q_m3h == pytest.approx(2.70, abs=0.03)    # Kurzschluss = V8
    assert abs(e0["ts6"].q_m3h) < 1e-6                                              # Einspritzung dicht
    assert e0["ts8"].q_m3h / base["ts8"].q_m3h == pytest.approx(1.0, abs=0.01)     # Sekundärstrom ≈ konstant
    assert e0["ts7"].q_m3h == pytest.approx(e0["ts8"].q_m3h, rel=1e-9)             # V7 = V8 bei V6 = 0


def test_isolierter_kreis_mit_fester_leistung_meldet_drift():
    """ESS-Ventil zu + q_prescribed am Verbraucher: stationär keine
    Temperaturlösung → verständliche Fehlermeldung statt stiller Divergenz."""
    with pytest.raises(h.ConvergenceError) as exc:
        make_net(dv=0.0).solve()
    assert "thermisch isoliert" in str(exc.value)
    assert "thermal=False" in str(exc.value)


def test_thermal_false_liefert_hydraulik():
    r = solve(dv=0.0)
    assert r.converged
    assert any("Thermik nicht berechnet" in n for n in r.notices)
