"""T-Stück mit Idelchik-Druckverlust (Diagramme 7-10/7-21): Handrechnungs-
verifikation für Trennung und Vereinigung inkl. Bernoulli-Umrechnung
Totaldruck → statischer Knotendruck und Druckgewinn (negatives ζ)."""
import math

import pytest

import hydraulik as h
from hydraulik.components import idelchik


def test_tabellen_stuetzstellen():
    """Direkte Stützstellen der Buchtabellen (keine Interpolation)."""
    assert idelchik.zeta_side(1.0, 1.0, converging=True) == pytest.approx(2.30)
    assert idelchik.zeta_side(0.1, 0.09, converging=True) == pytest.approx(-0.50)
    assert idelchik.zeta_side(1.0, 0.44, converging=True) == pytest.approx(9.60)
    assert idelchik.zeta_side(0.1, 0.09, converging=False) == pytest.approx(2.80)
    assert idelchik.zeta_side(0.5, 0.35, converging=False) == pytest.approx(2.73)
    assert idelchik.zeta_straight(0.7, converging=False) == pytest.approx(0.49)
    assert idelchik.zeta_straight(1.0, converging=True) == pytest.approx(1.00)
    # Klemmen an den Rändern
    assert idelchik.zeta_side(0.05, 0.02, converging=False) == pytest.approx(2.80)


def _netz(d_run_mm, d_branch_mm, q_in, q_b=None, q_c=None):
    """inflow → tee.a; b/c wahlweise mit festem Abfluss bzw. Druckanker;
    Differenzdrucksensoren messen die statischen Knotendifferenzen."""
    comps = {
        "zu": {"type": "inflow", "t_set_C": 50.0, "q_m3h": q_in},
        "t1": {"type": "tee", "d_run_mm": d_run_mm, "d_branch_mm": d_branch_mm},
        "pd_ab": {"type": "pressure_diff_sensor"},
        "pd_ac": {"type": "pressure_diff_sensor"},
        "ab_b": {"type": "outflow", **({"q_m3h": q_b} if q_b else {"p_kPa": 150})},
        "ab_c": {"type": "outflow", **({"q_m3h": q_c} if q_c else {"p_kPa": 150})},
    }
    conns = [["zu.port", "t1.a", "pd_ab.plus", "pd_ac.plus"],
             ["t1.b", "ab_b.port", "pd_ab.minus"],
             ["t1.c", "ab_c.port", "pd_ac.minus"]]
    return h.load({"components": comps, "connections": conns}).solve(thermal=False)


def test_trennung_handrechnung():
    """Verteilung: a kombiniert (einströmend), x = Q_c-Abzweig/Q_gesamt = 0.4."""
    d_run, d_branch = 0.032, 0.025
    q_in, q_branch = 2.0, 0.8                    # m³/h; gerader Auslauf 1.2
    r = _netz(32.0, 25.0, q_in, q_b=None, q_c=q_branch)
    assert r.converged
    fluid = h.water_at(50.0)
    rho = fluid.rho
    f_run = math.pi * d_run ** 2 / 4
    f_branch = math.pi * d_branch ** 2 / 4
    q_c = q_in / 3600.0
    x = q_branch / q_in
    r_a = f_branch / f_run
    w_c = q_c / f_run
    w_st = (q_in - q_branch) / 3600.0 / f_run
    w_s = q_branch / 3600.0 / f_branch
    # statische Differenz (Trennung, c→Ast): p_a − p_leg = ζ·ρw_c²/2 + ρ(w_leg² − w_c²)/2
    exp_ab = (idelchik.zeta_straight(x, False) * rho * w_c ** 2 / 2
              + rho * (w_st ** 2 - w_c ** 2) / 2)
    exp_ac = (idelchik.zeta_side(x, r_a, False) * rho * w_c ** 2 / 2
              + rho * (w_s ** 2 - w_c ** 2) / 2)
    by = {s.name: s.readings["dp_kPa"] * 1e3 for s in r.sensors}
    # kombinierte Kante trägt nur die quasi-ideale Restkante (~0.05 Pa)
    assert by["pd_ab"] == pytest.approx(exp_ab, abs=0.2)
    assert by["pd_ac"] == pytest.approx(exp_ac, abs=0.2)
    # Plausibilität: Abzweig verliert deutlich mehr als der gerade Durchgang;
    # im geraden Auslauf kann der Bernoulli-Rückgewinn den ζ-Verlust statisch
    # (fast) kompensieren (Diffusorwirkung, w_st < w_c)
    assert exp_ac > exp_ab
    assert exp_ac > 0


def test_vereinigung_handrechnung_mit_druckgewinn():
    """Sammlung mit kleinem Abzweiganteil (x = 0.1, r_A = 1): ζ_c.s = −0.65 —
    der Seitenstrang GEWINNT Totaldruck (Injektorwirkung). Der Solver muss
    konvergieren (nachgeführte Druckquelle) und die Handrechnung treffen."""
    d = 0.032
    doc = {"components": {
               "zu_a": {"type": "inflow", "t_set_C": 50.0, "q_m3h": 1.8},
               "zu_c": {"type": "inflow", "t_set_C": 50.0, "q_m3h": 0.2},
               "t1": {"type": "tee", "d_run_mm": 32.0, "d_branch_mm": 32.0},
               "pd_cb": {"type": "pressure_diff_sensor"},
               "pd_ab": {"type": "pressure_diff_sensor"},
               "ab_b": {"type": "outflow", "p_kPa": 150}},
           "connections": [["zu_a.port", "t1.a", "pd_ab.plus"],
                           ["zu_c.port", "t1.c", "pd_cb.plus"],
                           ["t1.b", "ab_b.port", "pd_cb.minus", "pd_ab.minus"]]}
    r = h.load(doc).solve(thermal=False)
    assert r.converged
    fluid = h.water_at(50.0)
    rho = fluid.rho
    f = math.pi * d ** 2 / 4
    q_c = 2.0 / 3600.0
    x = 0.1
    w_c = q_c / f
    w_s = 0.2 / 3600.0 / f
    w_st = 1.8 / 3600.0 / f
    zeta_s = idelchik.zeta_side(x, 1.0, True)
    assert zeta_s == pytest.approx(-0.65)          # Totaldruck-GEWINN (Injektor)
    # Sammlung, Ast→c: p_leg − p_c = ζ·ρw_c²/2 + ρ(w_c² − w_leg²)/2
    exp_cb = zeta_s * rho * w_c ** 2 / 2 + rho * (w_c ** 2 - w_s ** 2) / 2
    exp_ab = (idelchik.zeta_straight(x, True) * rho * w_c ** 2 / 2
              + rho * (w_c ** 2 - w_st ** 2) / 2)
    by = {s.name: s.readings["dp_kPa"] * 1e3 for s in r.sensors}
    assert by["pd_cb"] == pytest.approx(exp_cb, abs=0.2)
    assert by["pd_ab"] == pytest.approx(exp_ab, abs=0.2)


def test_tee_ohne_durchmesser_bleibt_idealer_knoten():
    """Default unverändert: ohne d-Angaben ein Knoten (kein Druckverlust);
    deckt zugleich den Randfall eines Netzes ganz ohne Kanten ab."""
    doc = {"components": {
               "zu": {"type": "inflow", "t_set_C": 50.0, "q_m3h": 2.0},
               "t1": {"type": "tee"},
               "pd": {"type": "pressure_diff_sensor"},
               "ab_b": {"type": "outflow", "p_kPa": 150},
               "ab_c": {"type": "outflow", "q_m3h": 0.8}},
           "connections": [["zu.port", "t1.a", "pd.plus"],
                           ["t1.b", "ab_b.port", "pd.minus"],
                           ["t1.c", "ab_c.port"]]}
    r = h.load(doc).solve(thermal=False)
    assert r.converged
    (pd,) = [s for s in r.sensors if s.name == "pd"]
    assert pd.readings["dp_kPa"] == pytest.approx(0.0, abs=1e-9)


def test_tee_validierung():
    with pytest.raises(h.NetworkValidationError) as exc:
        h.load({"components": {"t1": {"type": "tee", "d_run_mm": 25.0}},
                "connections": [["t1.a", "t1.b"], ["t1.c", "t1.a"]]})
    assert "gemeinsam" in str(exc.value)
    with pytest.raises(h.NetworkValidationError) as exc:
        h.load({"components": {"t1": {"type": "tee", "d_run_mm": 25.0, "d_branch_mm": 32.0}},
                "connections": [["t1.a", "t1.b"], ["t1.c", "t1.a"]]})
    assert "d_branch" in str(exc.value)
