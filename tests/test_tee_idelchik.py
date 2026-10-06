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
    # gerader Pfad der Trennung: Tabellenabszisse Q_st/Q_c (Diagramm 7-21,
    # idelchik_t_stueck_verteilung_llm.md Tab. 5) — hier 0.6 → ζ_c.st = 0.51
    zeta_st = idelchik.zeta_straight(1.0 - x, False)
    assert zeta_st == pytest.approx(0.51)
    exp_ab = zeta_st * rho * w_c ** 2 / 2 + rho * (w_st ** 2 - w_c ** 2) / 2
    exp_ac = (idelchik.zeta_side(x, r_a, False) * rho * w_c ** 2 / 2
              + rho * (w_s ** 2 - w_c ** 2) / 2)
    by = {s.name: s.readings["dp_kPa"] * 1e3 for s in r.sensors}
    # Netz rechnet mit Totaldruck: die Sensoren messen den Idelchik-Verlust
    # direkt (kombinierte Kante trägt nur die quasi-ideale Restkante ~0.05 Pa)
    assert by["pd_ab"] == pytest.approx(zeta_st * rho * w_c ** 2 / 2, abs=0.2)
    assert by["pd_ac"] == pytest.approx(idelchik.zeta_side(x, r_a, False) * rho * w_c ** 2 / 2,
                                        abs=0.2)
    # statische Anschlussdrücke (Ergebnis) treffen die Bernoulli-Handrechnung
    ps = {k: r[f"t1:{k}"].extras["p_static_port_kPa"] * 1e3 for k in "abc"}
    assert ps["a"] - ps["b"] == pytest.approx(exp_ab, abs=0.2)
    assert ps["a"] - ps["c"] == pytest.approx(exp_ac, abs=0.2)
    assert r["t1:c"].extras["v_m_s"] == pytest.approx(w_s, rel=1e-9)
    # Plausibilität: Abzweig verliert deutlich mehr als der gerade Durchgang;
    # im geraden Auslauf kann der Bernoulli-Rückgewinn den ζ-Verlust statisch
    # (fast) kompensieren (Diffusorwirkung, w_st < w_c)
    assert exp_ac > exp_ab
    assert exp_ac > 0


def test_vereinigung_handrechnung_mit_druckgewinn():
    """Sammlung mit kleinem Abzweiganteil (x = 0.1, r_A = 1): ζ_c.s = −0.65 —
    der Seitenstrang GEWINNT Totaldruck (Injektorwirkung). Der Solver muss
    konvergieren (nachgeführte Druckquelle) und die Handrechnung treffen:
    im Netz als Totaldruck, an den Anschlüssen statisch."""
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
    # Totaldruck im Netz: Sensoren messen ζ·ρw_c²/2 — beim Seitenstrang negativ
    assert by["pd_cb"] == pytest.approx(zeta_s * rho * w_c ** 2 / 2, abs=0.2)
    assert by["pd_cb"] < 0.0
    assert by["pd_ab"] == pytest.approx(idelchik.zeta_straight(x, True) * rho * w_c ** 2 / 2,
                                        abs=0.2)
    # statische Anschlussdrücke treffen die Bernoulli-Handrechnung
    ps = {k: r[f"t1:{k}"].extras["p_static_port_kPa"] * 1e3 for k in "abc"}
    assert ps["c"] - ps["b"] == pytest.approx(exp_cb, abs=0.2)
    assert ps["a"] - ps["b"] == pytest.approx(exp_ab, abs=0.2)


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


# --- Regimewechsel: stetige Kennlinie, Tabellen nur im Buchbereich (Solver-Prüfung B7)

def _tee(d_run=0.040, d_branch=0.025):
    return h.Tee("t", d_run_m=d_run, d_branch_m=d_branch)


def _S(tee, q, rho=988.0):
    return tee._pressures(list(q), rho)[0]


@pytest.mark.parametrize("k", [0, 1, 2])
@pytest.mark.parametrize("d_branch", [0.012, 0.025, 0.040])
def test_kennlinie_stetig_beim_vorzeichenwechsel_eines_schenkels(k, d_branch):
    """Jeder Regimewechsel liegt bei Schenkelstrom 0. Die statischen Port-
    drücke dürfen dort nicht springen (vorher: Sprung um bis zu ~ρw², keine
    Lösung zwischen den Sprungwerten → Stillstand bei V̇ = 0 oder NaN)."""
    tee = _tee(d_branch=d_branch)
    T = 2.0 / 3600.0
    i, j = [m for m in range(3) if m != k]
    def state(qk):
        q = [0.0] * 3
        q[k], q[i], q[j] = qk, T - qk / 2, -T - qk / 2
        return q
    scale = 988.0 * (T / (math.pi * 0.012 ** 2 / 4)) ** 2      # größter Staudruck
    for eps in (1e-9, 1e-12):
        sp, sm = _S(tee, state(eps * T)), _S(tee, state(-eps * T))
        assert max(abs(a - b) for a, b in zip(sp, sm)) <= 1e-6 * scale
    # stetig auch an der Überblendgrenze x = 0.1
    d = 0.1 * T / (1 - 0.05)
    for sgn in (1, -1):
        inner = _S(tee, state(sgn * d * (1 - 1e-9)))
        outer = _S(tee, state(sgn * d * (1 + 1e-9)))
        assert max(abs(a - b) for a, b in zip(inner, outer)) <= 1e-6 * scale


def test_tabellen_nur_im_buchbereich(monkeypatch):
    """Das T-Stück wertet die Idelchik-Tabellen nur für x ∈ [0.1, 1] aus —
    keine Klemmung, keine Extrapolation."""
    seen = []
    zs, zt = idelchik.zeta_side, idelchik.zeta_straight
    monkeypatch.setattr(idelchik, "zeta_side", lambda x, r, c: (seen.append(x), zs(x, r, c))[1])
    monkeypatch.setattr(idelchik, "zeta_straight", lambda x, c: (seen.append(x), zt(x, c))[1])
    tee = _tee()
    T = 1.0 / 3600.0
    for k in range(3):
        i, j = [m for m in range(3) if m != k]
        for f in [-1.5, -1.0, -0.5, -0.2, -0.1, -0.05, -1e-6, 0.0, 1e-6, 0.05, 0.1, 0.2, 0.5, 1.0, 1.5]:
            q = [0.0] * 3
            q[k], q[i], q[j] = f * T, T - f * T / 2, -T - f * T / 2
            _S(tee, q)
    assert seen and min(seen) >= 0.1 - 1e-12 and max(seen) <= 1.0 + 1e-12


def test_druckabtastung_ueber_den_regimewechsel():
    """Verteilung → Vereinigung am Abzweig: Druck am Abzweigende wird über den
    statischen Druck des Hauptstrangs geführt; V̇_c wechselt das Vorzeichen.
    Jeder Punkt muss konvergieren und V̇_c fällt monoton mit p_c (vorher: keine
    Lösung im Sprungintervall der Kennlinie)."""
    rho = h.water_at(50.0).rho
    w = 2.0 / 3600.0 / (math.pi * 0.032 ** 2 / 4)
    dyn = rho * w * w / 2.0
    q_c = []
    for f in [-2.0, -1.0, -0.5, -0.2, -0.05, 0.0, 0.05, 0.2, 0.5, 1.0, 2.0]:
        doc = {"components": {
                   "zu": {"type": "inflow", "t_set_C": 50.0, "q_m3h": 2.0},
                   "t1": {"type": "tee", "d_run_mm": 32.0, "d_branch_mm": 25.0},
                   "ab_b": {"type": "outflow", "p_kPa": 150.0},
                   "ab_c": {"type": "outflow", "p_kPa": 150.0 + f * dyn / 1e3}},
               "connections": [["zu.port", "t1.a"], ["t1.b", "ab_b.port"],
                               ["t1.c", "ab_c.port"]]}
        r = h.load(doc).solve(thermal=False)
        assert r.converged
        q_c.append(-r["t1:c"].q_m3h)                    # Abfluss über c positiv
    flows = q_c
    assert all(b <= a + 1e-9 for a, b in zip(flows, flows[1:]))
    assert flows[0] > 0.0 > flows[-1]                  # Vorzeichenwechsel überstrichen


def test_kurzgeschlossene_schenkel_werden_abgelehnt():
    """Zwei Schenkel eines Idelchik-T-Stücks am selben Knoten: Validierungs-
    fehler mit Abhilfe (ohne Durchmesser bleibt das ideale T-Stück erlaubt)."""
    def doc(tee):
        return {"components": {
                    "zu": {"type": "inflow", "t_set_C": 50.0, "q_m3h": 2.0},
                    "t1": {"type": "tee", **tee},
                    "ab": {"type": "outflow", "p_kPa": 150.0}},
                "connections": [["zu.port", "t1.a"], ["t1.b", "t1.c", "ab.port"]]}
    with pytest.raises(h.NetworkValidationError, match="Schenkel 'b' und 'c' liegen am selben"):
        h.load(doc({"d_run_mm": 32.0, "d_branch_mm": 25.0})).solve(thermal=False)
    assert h.load(doc({})).solve(thermal=False).converged
