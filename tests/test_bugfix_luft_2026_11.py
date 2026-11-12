"""Luftseite: Befunde der Fehlersuche 2026-11 — Adapter, Loader, raumgekoppelte
Rechnung (simulate_room) und Kern-API.

Die Abweichungen vom MATLAB-Original L13–L16 (Sprühbefeuchter-Lücke,
Frostschutz-Massenstrom, KVS-Maske, Heizregister-Toleranz) stehen bei den
übrigen L-Befunden in test_air_vka_pruefung.py. Geprüft werden Bilanzen und
Invarianten, keine Regressionszahlen.
"""
import copy
import warnings

import numpy as np
import pytest

import hydraulik as h
from hydraulik.air import load_air, solve_air
from hydraulik.air import adapter as luft_adapter
from hydraulik.air.vka import moist_air as ma
from hydraulik.air.vka import simulate, simulate_room
from hydraulik.air.vka.simulate import simulate_weather

from test_air_vka import _doc


def _kette(zul, abl, comps):
    """Schema aus zwei Strängen (Namenslisten inkl. Randkomponenten); die
    WRG-Ports werden je Strang gesetzt."""
    conns = []
    for seite, seq in (("zul", zul), ("abl", abl)):
        for a, b in zip(seq[:-1], seq[1:]):
            pa = f"{a}.{seite}_out" if comps[a]["type"] == "wrg" else f"{a}.out"
            pb = f"{b}.{seite}_in" if comps[b]["type"] == "wrg" else f"{b}.in"
            conns.append([pa, pb])
    return {"components": copy.deepcopy(comps), "connections": conns}


def _rand(t_aul=-5.0, rh_aul=80.0, t_abl=22.0, rh_abl=40.0, v=4500, wrg="PLATE", **wrg_extra):
    return {
        "aul": {"type": "aussenluft", "t_C": t_aul, "rh": rh_aul},
        "wrg1": {"type": "wrg", "typ": wrg, **wrg_extra},
        "zul": {"type": "zuluft", "v_m3h": v, "regelung": "band", "t_min_C": 20,
                "t_max_C": 22, "rh_min": 30, "rh_max": 60},
        "abl": {"type": "abluft_raum", "t_C": t_abl, "rh": rh_abl},
        "fol": {"type": "fortluft"},
    }


def _h(t, x_gkg):
    return float(ma.h(t, x_gkg / 1000.0))


# =========================== raumgekoppelte Rechnung ============================

SORP = {"wrg": "ROT_SORP", "components": ["VHR", "KR", "NHR"], "eta_hr_N": 0.778,
        "eta_xr_N": 0.807, "SFP": 0, "V_nom_m3h": 4500,
        "order": ["Vent_ZUL", "WRG", "VHR", "KR", "NHR"]}
SORP_DAMPF = dict(SORP, humidifier="steam",
                  order=["Vent_ZUL", "WRG", "VHR", "Bef", "KR", "NHR"])
RAUM = dict(T_room=22.0, room_rh_min=40, room_rh_max=55, T_sup_min=20, T_sup_max=23,
            V_sup_m3h=4500)


def test_raum_ohne_feuchtequelle_bleibt_bei_aussenluftfeuchte():
    """Sorptionsrotor ohne Befeuchter und ohne Feuchtelast: es gibt keine
    Feuchtequelle, der Raum stellt sich auf die Außenluftfeuchte ein (12 %).
    Vorher: Raum 40,3 %, Zuluft 6,7 g/kg bei 2,0 g/kg Außenluft — der Kern
    hatte die Abluft mit 47 % angesetzt."""
    o = simulate_room(SORP, -5.0, 80.0, moisture_g_h=0.0, **RAUM)
    x_aul = float(ma.rh_to_x(-5.0, 80.0))
    assert float(o["x_sup_gkg"][0]) == pytest.approx(x_aul, abs=0.02)
    assert float(o["room_rh_pct"][0]) == pytest.approx(float(ma.x_to_rh(22.0, x_aul)), abs=0.2)
    assert float(o["water_kg_h"][0]) == 0.0
    assert not o["room_band_ok"][0] and o["T_band_ok"][0]
    assert float(o["rh_residual_pct"][0]) <= 0.25


@pytest.mark.parametrize("last", [1000.0, 2000.0, 4000.0])
def test_raum_feuchtebilanz_rotor_ohne_befeuchter(last):
    """Stationäre Feuchtebilanz mit Rotor: x_Zuluft = x_AUL + η_x·(x_Raum − x_AUL)
    und x_Raum = x_Zuluft + dx  ⇒  x_Raum − x_AUL = dx/(1 − η_x).
    Vorher (2 kg/h): Raum 40,0 % gemeldet, Bilanz verlangt 21 %."""
    o = simulate_room(SORP, -5.0, 80.0, moisture_g_h=last, **RAUM)
    x_aul = float(ma.rh_to_x(-5.0, 80.0))
    dx = last / (4500 * 1.2)
    x_raum = float(ma.rh_to_x(22.0, float(o["room_rh_pct"][0])))
    eta_x = float(o["eta_xr"][0])
    assert x_raum == pytest.approx(float(o["x_sup_gkg"][0]) + dx, abs=1e-3)
    assert x_raum - x_aul == pytest.approx(dx / (1.0 - eta_x), rel=0.02)


@pytest.mark.parametrize("t_aul, rh_aul, last", [(-5.0, 80.0, 2000.0), (-5.0, 80.0, 0.0),
                                                (2.0, 70.0, 1000.0), (-12.0, 60.0, 3000.0)])
def test_raum_befeuchterwasser_passt_zur_raumfeuchte(t_aul, rh_aul, last):
    """Mit Dampfbefeuchter: die gemeldete Wassermenge deckt genau die Lücke
    zwischen Rotoraustritt (aus der GEMELDETEN Raumfeuchte und η_x) und
    Zuluftfeuchte. Vorher (−5 °C/80 %, 2 kg/h): 0,35 kg/h gemeldet, 3,1 kg/h
    nötig — der Kandidat rechnete mit einer feuchteren Abluft als dem Raum."""
    o = simulate_room(SORP_DAMPF, t_aul, rh_aul, moisture_g_h=last, **RAUM)
    assert o["room_band_ok"][0] and o["T_band_ok"][0]
    x_aul = float(ma.rh_to_x(t_aul, rh_aul))
    x_raum = float(ma.rh_to_x(22.0, float(o["room_rh_pct"][0])))
    x_zul, t_zul = float(o["x_sup_gkg"][0]), float(o["T_sup_C"][0])
    x_wrg = x_aul + float(o["eta_xr"][0]) * (x_raum - x_aul)
    m = 4500 / 3600 * 1e5 / (287.0 * (273.0 + t_zul))
    assert float(o["water_kg_h"][0]) == pytest.approx(m * (x_zul - x_wrg) * 3.6,
                                                     rel=0.03, abs=0.08)


def test_raum_reihe_aus_und_datenluecke():
    """Zeitreihe: V = 0 → Anlage aus (Lasten 0, Zustand NaN, wie simulate());
    NaN-Eingabe → Datenlücke (alles NaN); beides keine Betriebsstunde."""
    o = simulate_room(SORP_DAMPF, [-5.0, 30.0, np.nan, 12.0], [80.0, 60.0, 50.0, 60.0],
                      moisture_g_h=1000.0, **dict(RAUM, V_sup_m3h=[4500, 4500, 4500, 0]))
    assert o["totals"]["operating_hours"] == 2.0
    assert np.isnan(o["Q_heat_total_kW"][2]) and np.isnan(o["T_sup_C"][2])
    assert o["Q_heat_total_kW"][3] == 0.0 and np.isnan(o["T_sup_C"][3])
    assert list(o["room_band_ok"]) == [True, True, False, False]


def test_raum_feuchte_in_gkg_wie_in_prozent():
    """humidity="x": die Abluftfeuchte wurde als g/kg gelesen, obwohl die
    Raumfeuchte in % läuft (5 °C/60 %: Heizen 4,2 kW über "rh", 11,3 kW über
    das gleichwertige "x")."""
    spec = dict(SORP_DAMPF, SFP=2500)
    for t, rh in ((5.0, 60.0), (-5.0, 80.0), (30.0, 60.0)):
        a = simulate_room(spec, t, rh, moisture_g_h=1000.0, **RAUM)
        b = simulate_room(spec, t, float(ma.rh_to_x(t, rh)), moisture_g_h=1000.0,
                          humidity="x", **RAUM)
        for k in ("T_sup_C", "x_sup_gkg", "room_rh_pct", "Q_heat_total_kW", "Q_cool_KR_kW",
                  "water_kg_h"):
            assert float(b[k][0]) == pytest.approx(float(a[k][0]), abs=1e-6), k


def test_raum_reihenlaengen():
    """Skalares Wetter mit Feuchtelast-Reihe wird auf die Reihenlänge gebracht
    (vorher IndexError); unterschiedliche Längen → klare Meldung."""
    o = simulate_room(SORP_DAMPF, 5.0, 60.0, moisture_g_h=[0.0, 1000.0, 2000.0], **RAUM)
    assert len(o["T_sup_C"]) == 3 and np.all(np.diff(o["dx_load_gkg"]) > 0)
    with pytest.raises(ValueError, match="series lengths differ"):
        simulate_room(SORP_DAMPF, [5.0, 6.0, 7.0], [60.0, 50.0], moisture_g_h=0.0, **RAUM)


# ------------------------------ Adapter, Regelung raum ---------------------------

def _raum_doc(t_aul, rh_aul, last, wrg="ROT_SORP", bef=None, t_raum=22.0, norm=False):
    C = _rand(t_aul, rh_aul, wrg=wrg,
              **({"eta_hr_n": 0.778, "eta_xr_n": 0.807}
                 if wrg == "ROT_SORP" and not norm else {}))
    C["zul"] = {"type": "zuluft", "v_m3h": 4500, "regelung": "raum", "t_min_C": 20,
                "t_max_C": 23, "raum_rh_min": 40, "raum_rh_max": 55, "feuchtelast": last}
    C["abl"] = {"type": "abluft_raum", "t_C": t_raum}
    C.update({"ven": {"type": "ventilator_luft", "sfp": 1000.0},
              "vhr": {"type": "vorheizer"}, "kr": {"type": "kuehler"},
              "nhr": {"type": "nachheizer"}})
    zul = ["aul", "ven", "wrg1", "vhr"]
    if bef:
        C["bef"] = {"type": "befeuchter", "typ": bef}
        zul += ["bef", "kr"] if bef == "steam" else ["kr", "bef"]
    else:
        zul.append("kr")
    return _kette(zul + ["nhr", "zul"], ["abl", "wrg1", "fol"], C), zul + ["nhr", "zul"]


@pytest.mark.parametrize("wrg, t_aul, last, rh_soll", [("PLATE", -5.0, 2000.0, 14.4),
                                                      ("ROT_HYG", -10.0, 1000.0, None),
                                                      ("ROT_SORP", -5.0, 0.0, 12.1)])
def test_raum_band_nicht_erreichbar_zustand_und_hinweis(wrg, t_aul, last, rh_soll):
    """Raumfeuchteband ohne Befeuchter nicht erreichbar: der bilanzkonsistente
    Zustand wird geliefert, mit Hinweis — nie ok: true mit lauter null
    (vorher: alle Werte null, hinweise leer; Sorptionsrotor: 40 % erfunden)."""
    doc, _ = _raum_doc(t_aul, 80.0, last, wrg=wrg)
    r = solve_air(doc)
    assert r["ok"] and r["stationen"]
    assert None not in r["zuluft"].values()
    assert None not in r["leistungen"].values()
    assert r["raum"]["band_erreicht"] is False and r["raum"]["rh_pct"] < 40.0
    if rh_soll is not None:
        assert r["raum"]["rh_pct"] == pytest.approx(rh_soll, abs=0.3)
    hinweis = [s for s in r["hinweise"] if "Raumfeuchteband nicht erreichbar" in s]
    assert len(hinweis) == 1 and "zu trocken" in hinweis[0]
    assert f"{r['raum']['rh_pct']:.1f}".replace(".", ",") in hinweis[0]
    # Feuchtebilanz Raum: x_Raum = x_Zuluft + Feuchtelast/ṁ
    x_raum = float(ma.rh_to_x(22.0, r["raum"]["rh_pct"]))
    assert x_raum == pytest.approx(r["zuluft"]["x_gkg"] + last / (4500 * 1.2), abs=5e-3)


def test_raum_band_erreicht_ohne_hinweis():
    doc, _ = _raum_doc(-5.0, 80.0, 2000.0, bef="steam")
    r = solve_air(doc)
    assert r["raum"]["band_erreicht"] is True
    assert 40.0 - 0.2 <= r["raum"]["rh_pct"] <= 55.0 + 0.2
    assert not any("Raumfeuchteband" in s or "Sollband" in s for s in r["hinweise"])


@pytest.mark.parametrize("t_aul, rh_aul, last, bef, t_raum, norm", [
    (-5.0, 80.0, 2000.0, "steam", 22.0, False),   # vorher: Stationen 3,29 kg/h, Lasten 0,35
    (19.6, 57.7, 0.0, None, 22.0, True),          # vorher: Stationen 18,8 + 17,8 kW, Lasten 0/0
    (4.2, 43.8, 0.0, "spray", 26.0, False),
    (2.9, 75.1, 1000.0, "steam", 26.0, True),
])
def test_raum_stationen_beschreiben_denselben_betriebspunkt(t_aul, rh_aul, last, bef, t_raum,
                                                           norm):
    """Die Stationskarte gehört zum selben Betriebspunkt wie die Lasten:
    ṁ·Δh bzw. ṁ·Δx zwischen den Stationen ergibt die gemeldeten Leistungen
    (vorher gepinnter Nachlauf mit anderer Abluftfeuchte)."""
    doc, zul = _raum_doc(t_aul, rh_aul, last, bef=bef, t_raum=t_raum, norm=norm)
    r = solve_air(doc)
    st, K = r["stationen"], r["komponenten"]
    m = 4500 / 3600 * 1e5 / (287.0 * (273.0 + r["zuluft"]["t_C"]))
    nach = dict(zip(zul[:-1], zul[1:]))
    for n in ("vhr", "nhr", "kr", "bef"):
        if n not in doc["components"]:
            continue
        ein, aus = st[f"{n}.in"], st[f"{nach[n]}.in"]
        dh = _h(aus["t_C"], aus["x_gkg"]) - _h(ein["t_C"], ein["x_gkg"])
        dx = (aus["x_gkg"] - ein["x_gkg"]) / 1000.0
        if n == "kr":
            soll, ist = -m * dh + m * dx * 4.18 * aus["t_C"], K[n]["q_kuehlen_kW"]
        elif n == "bef":
            soll, ist = m * dx * 3600.0, K[n]["wasser_kg_h"]
        else:
            soll, ist = m * dh, K[n]["q_heizen_kW"]
        assert ist == pytest.approx(soll, rel=0.03, abs=0.1), n
    assert st["zul.in"]["t_C"] == pytest.approx(r["zuluft"]["t_C"], abs=1e-3)
    assert st["zul.in"]["x_gkg"] == pytest.approx(r["zuluft"]["x_gkg"], abs=1e-3)
    # Abluftstation = resultierender Raumzustand
    assert st["abl.out"]["phi_pct"] == pytest.approx(r["raum"]["rh_pct"], abs=0.02)


# ================================ KVS-Auslegung ==================================

def _kvs_doc(v, **wrg_extra):
    C = _rand(v=v, wrg="KVS", **wrg_extra)
    C.update({"ven": {"type": "ventilator_luft", "sfp": 1250}, "vhr": {"type": "vorheizer"},
              "kr": {"type": "kuehler"}, "nhr": {"type": "nachheizer"},
              "vab": {"type": "ventilator_luft", "sfp": 1250}})
    return _kette(["aul", "ven", "wrg1", "vhr", "kr", "nhr", "zul"],
                  ["abl", "vab", "wrg1", "fol"], C)


def test_kvs_unmoegliche_auslegung_wird_abgelehnt():
    """KVS-Vorlage mit 12 000 m³/h: ε·C_Luft ≥ C_Sole (2,5 m³/h Sole) — vorher
    NaN im Kern, WRG stillschweigend 0 kW, Heizlast 96,6 kW, kein Hinweis.
    Jetzt Validierungsfehler mit dem nötigen Sole-Nennvolumenstrom."""
    with pytest.raises(h.NetworkValidationError) as exc:
        solve_air(_kvs_doc(12000))
    text = str(exc.value)
    v_min = 0.76 * 12000 * 1.2 / (1000 * 4.19)
    assert "KVS-Auslegung nicht möglich" in text and "v_m_kvs" in text
    assert f"{v_min:.3f}".replace(".", ",") in text
    # knapp über dem Minimum und mit kleinerer Rückwärmzahl rechnet die Anlage
    for extra in ({"v_m_kvs_m3h": 1.05 * v_min}, {"rwz_n": 0.6}):
        r = solve_air(_kvs_doc(12000, **extra))
        assert r["leistungen"]["wrg_kW"] > 30.0


def test_kvs_rechnet_knapp_unter_der_grenze_weiter():
    """11 400 m³/h liegt noch unter der Grenze (11 486 m³/h): vorher lief die
    Gegenstromformel über (exp > 1e308) und die WRG fiel auf 0 kW."""
    r = solve_air(_kvs_doc(11400))
    assert r["leistungen"]["wrg_kW"] > 50.0
    assert not any("Numerische Warnung" in s for s in r["hinweise"])


def test_kvs_und_platte_melden_rueckwaermzahl():
    """KVS/Plattentauscher: unter eta_hr steht die erreichte Rückwärmzahl
    (vorher 0 bei arbeitender WRG); eta_xr und n_rot bleiben 0."""
    for typ, abl in (("KVS", ["abl", "vab", "wrg1", "fol"]), ("PLATE", ["abl", "wrg1", "fol"])):
        C = _rand(wrg=typ)
        C.update({"ven": {"type": "ventilator_luft", "sfp": 1250},
                  "nhr": {"type": "nachheizer"},
                  "vab": {"type": "ventilator_luft", "sfp": 1250}})
        if typ == "PLATE":
            del C["vab"]
        r = solve_air(_kette(["aul", "ven", "wrg1", "nhr", "zul"], abl, C))
        st, w = r["stationen"], r["komponenten"]["wrg1"]
        rwz = ((st["wrg1.zul_out"]["t_C"] - st["wrg1.zul_in"]["t_C"])
               / (st["wrg1.abl_in"]["t_C"] - st["wrg1.zul_in"]["t_C"]))
        assert w["q_wrg_kW"] > 5.0
        assert w["eta_hr"] == pytest.approx(rwz, abs=2e-3) and 0.3 < w["eta_hr"] < 1.0
        assert w["eta_xr"] == 0.0 and w["n_rot"] == 0.0


# ============================ gezeichnet ↔ gerechnet =============================

def _winter_platte(**extra):
    C = _rand()
    C.update(extra)
    return C


def test_doppelte_komponenten_werden_abgelehnt():
    """Zwei Nachheizer/Kühler/Vorheizer im Zuluftstrang: der Kern legt Lasten
    je Typ ab, die zweite überschrieb die erste (0,0 statt 13,1 kW Heizen,
    0 statt 46 kW Kühlen). Jetzt gesammelter Validierungsfehler."""
    C = _winter_platte(nhr1={"type": "nachheizer"}, nhr2={"type": "nachheizer"},
                       kr1={"type": "kuehler"}, kr2={"type": "kuehler"})
    doc = _kette(["aul", "wrg1", "kr1", "kr2", "nhr1", "nhr2", "zul"], ["abl", "wrg1", "fol"], C)
    with pytest.raises(h.NetworkValidationError) as exc:
        solve_air(doc)
    assert len(exc.value.messages) == 2
    text = str(exc.value)
    assert "'nachheizer'" in text and "nhr1, nhr2" in text
    assert "'kuehler'" in text and "kr1, kr2" in text
    # mehrere Ventilatoren bleiben zulässig (SFP addiert sich)
    C = _winter_platte(v1={"type": "ventilator_luft", "sfp": 500},
                       v2={"type": "ventilator_luft", "sfp": 700}, nhr={"type": "nachheizer"})
    r = solve_air(_kette(["aul", "v1", "wrg1", "nhr", "v2", "zul"], ["abl", "wrg1", "fol"], C))
    assert r["plant"]["SFP"] == pytest.approx(1200.0)


@pytest.mark.parametrize("lose", [
    {"type": "umluft", "v_m3h": 2000},          # vorher: Heizlast 5,4 → 1,3 kW
    {"type": "frostschutz", "modus": "bypass", "t_fs_C": 5},
    {"type": "kuehler"},
    {"type": "ventilator_luft", "sfp": 5000},
    {"type": "filter_luft"},
])
def test_nicht_angeschlossene_komponente_ohne_einfluss(lose):
    """Eine Komponente, die in keinem Strang liegt, wird nicht gerechnet:
    Ergebnis unverändert, Hinweis statt stillem Einfluss."""
    ref = solve_air(_doc())
    doc = _doc()
    doc["components"]["lose"] = lose
    r = solve_air(doc)
    assert r["leistungen"] == ref["leistungen"]
    assert r["stationen"] == ref["stationen"] and r["plant"] == ref["plant"]
    assert "lose" not in r["komponenten"]
    assert [s for s in r["hinweise"] if "'lose'" in s] == [
        f"'lose' ({lose['type']}) ist nicht angeschlossen und wird nicht berechnet."]
    assert not any("ist nicht angeschlossen" in s for s in ref["hinweise"])


def test_loser_befeuchter_neben_strang_ohne_befeuchter():
    doc = _doc()
    del doc["components"]["bef"]
    doc["connections"] = [c for c in doc["connections"]
                          if not any(p.startswith("bef.") for p in c)] + [["vhr.out", "kr.in"]]
    ref = solve_air(doc)
    doc["components"]["bef_lose"] = {"type": "befeuchter", "typ": "spray"}
    r = solve_air(doc)
    assert r["plant"]["humidifier"] == "none" and r["leistungen"] == ref["leistungen"]
    assert any("'bef_lose'" in s and "nicht angeschlossen" in s for s in r["hinweise"])


def test_zwei_zuluftventilatoren_station_bleibt_zuluftzustand():
    """Ventilator am Stranganfang UND am Strangende: der zweite setzte die
    Station auf „nach dem ersten Ventilator“ zurück (Zuluftstation −4,1 °C
    bei Zuluft 20,0 °C)."""
    C = _winter_platte(v1={"type": "ventilator_luft", "sfp": 900},
                       v2={"type": "ventilator_luft", "sfp": 900}, nhr={"type": "nachheizer"})
    C["wrg1"] = {"type": "wrg", "typ": "ROT_SORP"}
    r = solve_air(_kette(["aul", "v1", "wrg1", "nhr", "v2", "zul"], ["abl", "wrg1", "fol"], C))
    st = r["stationen"]
    assert st["zul.in"]["t_C"] == pytest.approx(r["zuluft"]["t_C"], abs=1e-3)
    assert st["zul.in"]["x_gkg"] == pytest.approx(r["zuluft"]["x_gkg"], abs=1e-3)
    assert st["v2.in"] == st["zul.in"]                      # zweiter Ventilator: kein Sprung
    assert st["wrg1.zul_in"]["t_C"] > -5.0                  # erster trägt die Ventilatorwärme


# ===================== Fachentscheidungen 2026-11 (Runde 2) ======================

def test_vorheizer_vor_der_wrg_wird_abgelehnt():
    """Der Vorheizer ist im Modell das Register NACH der WRG. Vor der WRG
    gezeichnet heizte er bis zum Zuluft-Sollwert vor und legte die Rückgewinnung
    still (−12 °C: 46,7 statt 8,1 kW Heizlast, WRG aus). Jetzt Validierungs-
    fehler mit Verweis auf 'frostschutz'."""
    C = _rand(t_aul=-12.0, wrg="ROT_SORP")
    C.update({"vhr": {"type": "vorheizer"}, "kr": {"type": "kuehler"},
              "nhr": {"type": "nachheizer"}, "nhr_abl": {"type": "nachheizer"}})
    falsch = _kette(["aul", "vhr", "wrg1", "kr", "nhr", "zul"],
                    ["abl", "wrg1", "nhr_abl", "fol"], C)
    with pytest.raises(h.NetworkValidationError) as exc:
        solve_air(falsch)
    text = str(exc.value)
    assert "'vhr' (vorheizer) liegt im Zuluftstrang vor der WRG" in text
    assert "'frostschutz'" in text and "preheater" in text
    assert "'nhr_abl'" in text and len(exc.value.messages) == 2        # gesammelt
    del C["nhr_abl"]
    richtig = solve_air(_kette(["aul", "wrg1", "vhr", "kr", "nhr", "zul"],
                               ["abl", "wrg1", "fol"], C))
    assert richtig["leistungen"]["wrg_kW"] > 30.0
    # Frostschutz vor der WRG bleibt der vorgesehene Weg
    C["fs"] = {"type": "frostschutz", "modus": "preheater", "t_fs_C": -3.0}
    r = solve_air(_kette(["aul", "fs", "wrg1", "vhr", "kr", "nhr", "zul"],
                         ["abl", "wrg1", "fol"], C))
    assert r["komponenten"]["fs"]["q_heizen_kW"] > 0.0


def _entfeuchter_doc(**kr_extra):
    """Platte + Kühler + Nachheizer; Zuluft fest 22,3 °C / 34,3 % (Taupunkt 5,84 °C)."""
    C = _rand(t_aul=25.0, rh_aul=60.0, t_abl=24.0, rh_abl=50.0)
    C["zul"] = {"type": "zuluft", "v_m3h": 4500, "regelung": "fest", "t_C": 22.3, "rh": 34.3}
    C.update({"kr": {"type": "kuehler", **kr_extra}, "nhr": {"type": "nachheizer"}})
    return _kette(["aul", "wrg1", "kr", "nhr", "zul"], ["abl", "wrg1", "fol"], C)


def test_kuehler_kaltwasser_vorlauftemperatur():
    """Neuer Parameter kuehler.t_kw (Schlüssel t_kw_C, Default 6 °C). Verlangt
    der Feuchte-Sollwert einen tieferen Taupunkt, entfeuchtet der Kühler bis zur
    Sättigung bei der Vorlauftemperatur und die Antwort trägt einen Hinweis
    (vorher: keine Entfeuchtung, Zuluft 70,6 % statt 34,3 %, kein Hinweis)."""
    r = solve_air(_entfeuchter_doc())
    assert r["plant"]["T_KW"] == 6.0
    x_s6 = float(ma.xs(6.0, 1e5)) * 1e3
    assert r["zuluft"]["x_gkg"] == pytest.approx(x_s6, abs=1e-4)
    assert r["stationen"]["nhr.in"]["t_C"] == pytest.approx(6.0, abs=1e-3)   # Kühleraustritt
    hinweis = [s for s in r["hinweise"] if "Kaltwasser-Vorlauftemperatur" in s]
    assert len(hinweis) == 1
    assert "'kr'" in hinweis[0] and "Taupunkt von 5,8 °C" in hinweis[0] and "6,0 °C" in hinweis[0]
    assert "t_kw_C" in hinweis[0]
    # tieferer Vorlauf: Sollwert erreicht, kein Hinweis
    r = solve_air(_entfeuchter_doc(t_kw_C=4.0))
    assert r["zuluft"]["phi_pct"] == pytest.approx(34.3, abs=0.05)
    assert not any("Kaltwasser" in s or "Sollband" in s for s in r["hinweise"])
    # höherer Vorlauf: entsprechend feuchter, mit Hinweis
    r = solve_air(_entfeuchter_doc(t_kw_C=12.0))
    assert r["zuluft"]["x_gkg"] == pytest.approx(float(ma.xs(12.0, 1e5)) * 1e3, abs=1e-4)
    assert any("Kaltwasser-Vorlauftemperatur (12,0 °C)" in s for s in r["hinweise"])
    # Bereichsgrenze des Parameters
    with pytest.raises(h.NetworkValidationError, match="t_kw_C"):
        load_air(_entfeuchter_doc(t_kw_C=40.0))


def test_kaltwasser_default_aendert_gewoehnliche_entfeuchtung_nicht():
    """Taupunkt des Sollbands (12,5 °C) über der Vorlauftemperatur: Ergebnis
    unabhängig von t_kw, kein Hinweis."""
    def doc(**kr_extra):
        C = _rand(t_aul=30.0, rh_aul=60.0, t_abl=26.0, rh_abl=50.0)
        C["zul"].update({"rh_min": 40, "rh_max": 55})
        C.update({"kr": {"type": "kuehler", **kr_extra}, "nhr": {"type": "nachheizer"}})
        return _kette(["aul", "wrg1", "kr", "nhr", "zul"], ["abl", "wrg1", "fol"], C)
    a, b = solve_air(doc()), solve_air(doc(t_kw_C=10.0))
    assert a["leistungen"] == b["leistungen"] and a["zuluft"] == b["zuluft"]
    assert a["zuluft"]["phi_pct"] == pytest.approx(55.0, abs=0.3)
    assert not any("Kaltwasser" in s for s in a["hinweise"] + b["hinweise"])


def _umluft_doc(zul, v_uml=3000.0, sfp=2000.0):
    C = _rand()
    C["zul"].update({"rh_min": 1, "rh_max": 90})
    C.update({"ven": {"type": "ventilator_luft", "sfp": sfp},
              "uml": {"type": "umluft", "v_m3h": v_uml}, "nhr": {"type": "nachheizer"}})
    return _kette(zul, ["abl", "wrg1", "fol"], C)


@pytest.mark.parametrize("v_uml", [1500.0, 3000.0, 4500.0])
def test_umluft_ventilator_nach_der_mischkammer(v_uml):
    """Anlage mit Umluft, Ventilator am Stranganfang gezeichnet: bilanziert
    wird er nach der Mischkammer. Die WRG sieht Außenluft ohne Ventilator-
    wärme, der Abschnitt nach der Umluft trägt Mischung + volle Ventilator-
    wärme f_rec·P_el (vorher kam nur der Außenluftanteil an; bei 100 % Umluft
    nichts), und der Hinweis nennt die Mischkammer."""
    r = solve_air(_umluft_doc(["aul", "ven", "wrg1", "uml", "nhr", "zul"], v_uml))
    st = r["stationen"]
    m = 4500 / 3600 * 1e5 / (287.0 * (273.0 + 21.0))
    q_fan = 0.6 * 2000.0 * 4500 / 3.6e6                                  # 1,5 kW
    assert r["komponenten"]["ven"]["p_el_kW"] == pytest.approx(2.5)      # SFP · V̇_Zuluft
    assert st["wrg1.zul_in"]["t_C"] == pytest.approx(-5.0)               # keine Ventilatorwärme
    t_misch = (st["uml.in"]["t_C"] * (4500 - v_uml) + 22.0 * v_uml) / 4500
    assert st["nhr.in"]["t_C"] == pytest.approx(t_misch + q_fan / m, abs=2e-3)
    assert st["nhr.in"]["v_m3h"] == pytest.approx(4500.0)
    assert st["uml.in"]["v_m3h"] == pytest.approx(4500.0 - v_uml)
    hinweis = [s for s in r["hinweise"] if "Ventilator" in s]
    assert len(hinweis) == 1 and "nach der Mischkammer" in hinweis[0]
    assert "Stranganfang" not in hinweis[0]
    # Energiebilanz der Zuluft über den ganzen Strang: Lasten + f_rec·P_el
    x_aul, x_abl = st["aul.out"]["x_gkg"], st["abl.out"]["x_gkg"]
    z = r["zuluft"]
    zugefuehrt = (m * _h(z["t_C"], z["x_gkg"]) - m * (4500 - v_uml) / 4500 * _h(-5.0, x_aul)
                  - m * v_uml / 4500 * _h(22.0, x_abl))
    lasten = r["leistungen"]["wrg_kW"] + r["leistungen"]["heizen_gesamt_kW"] \
        - r["leistungen"]["kuehlen_kW"]
    assert zugefuehrt == pytest.approx(lasten + q_fan, abs=0.15)


def test_umluft_ventilator_direkt_nach_der_mischkammer_gezeichnet():
    """Ist der Ventilator dort gezeichnet, wo der Kern ihn bilanziert, zeigen
    die Stationen Mischung und Ventilatorerwärmung getrennt, ohne Hinweis."""
    r = solve_air(_umluft_doc(["aul", "wrg1", "uml", "ven", "nhr", "zul"]))
    st = r["stationen"]
    m = 4500 / 3600 * 1e5 / (287.0 * (273.0 + 21.0))
    t_misch = (st["uml.in"]["t_C"] * 1500 + 22.0 * 3000) / 4500
    assert st["ven.in"]["t_C"] == pytest.approx(t_misch, abs=2e-3)
    assert st["nhr.in"]["t_C"] == pytest.approx(t_misch + 1.5 / m, abs=2e-3)
    assert not any("Ventilator" in s for s in r["hinweise"])
    # gleiche Rechnung wie mit dem Ventilator am Stranganfang
    ref = solve_air(_umluft_doc(["aul", "ven", "wrg1", "uml", "nhr", "zul"]))
    assert r["leistungen"] == ref["leistungen"] and r["zuluft"] == ref["zuluft"]


def test_umluft_fortluft_aus_wrg_bilanz():
    """Fortluftstation bei Umluft nach der WRG und Ventilator am Stranganfang:
    Feuchte- und Energiebilanz der WRG gegen den Außenluftzustand (der
    Kettenzustand „nach Ventilator“ gilt hier nach der Mischkammer)."""
    C = _rand(wrg="ROT_SORP")
    C.update({"ven": {"type": "ventilator_luft", "sfp": 2000.0},
              "uml": {"type": "umluft", "v_m3h": 2250.0}, "nhr": {"type": "nachheizer"}})
    r = solve_air(_kette(["aul", "ven", "wrg1", "uml", "nhr", "zul"], ["abl", "wrg1", "fol"], C))
    st = r["stationen"]
    zu_ein, zu_aus = st["wrg1.zul_in"], st["wrg1.zul_out"]
    ab_ein, ab_aus = st["wrg1.abl_in"], st["wrg1.abl_out"]
    assert zu_ein["v_m3h"] == ab_ein["v_m3h"] == pytest.approx(2250.0)
    assert ab_ein["x_gkg"] - ab_aus["x_gkg"] == pytest.approx(
        zu_aus["x_gkg"] - zu_ein["x_gkg"], abs=2e-3)
    assert (_h(ab_ein["t_C"], ab_ein["x_gkg"]) - _h(ab_aus["t_C"], ab_aus["x_gkg"])
            == pytest.approx(_h(zu_aus["t_C"], zu_aus["x_gkg"])
                             - _h(zu_ein["t_C"], zu_ein["x_gkg"]), abs=0.02))
    assert -5.0 < ab_aus["t_C"] < 22.0


def test_ohne_umluft_bleibt_der_hinweis_stranganfang():
    r = solve_air(_doc())                                    # Ventilator am Strangende
    hinweis = [s for s in r["hinweise"] if "Ventilator" in s]
    assert len(hinweis) == 1 and "Stranganfang" in hinweis[0] and "Mischkammer" not in hinweis[0]


# =================================== Meldungen ===================================

def test_strangfehler_richtungsbewusst():
    """Verkehrt herum angeschlossene Komponente, vertauschte Strangenden und an
    der WRG gekreuzte Stränge werden als solche benannt (vorher „läuft im
    Kreis“, „Port 'fol.out' ist nicht verbunden“ bzw. stillschweigend
    akzeptiert)."""
    d = _doc()
    d["connections"][0] = ["aul.out", "fil1.out"]
    d["connections"][1] = ["fil1.in", "wrg1.zul_in"]
    with pytest.raises(h.NetworkValidationError, match="'fil1'.*verkehrt herum angeschlossen"):
        solve_air(d)

    d = _doc()
    d["connections"][7] = ["ven_zul.out", "fol.in"]
    d["connections"][10] = ["wrg1.abl_out", "zul.in"]
    with pytest.raises(h.NetworkValidationError) as exc:
        solve_air(d)
    text = str(exc.value)
    assert "Zuluftstrang endet an 'fol' (fortluft)" in text
    assert "Abluftstrang endet an 'zul' (zuluft)" in text       # beide Stränge gemeldet

    d = _doc()
    d["connections"][1] = ["fil1.out", "wrg1.abl_in"]
    d["connections"][2] = ["wrg1.abl_out", "vhr.in"]
    d["connections"][9] = ["ven_abl.out", "wrg1.zul_in"]
    d["connections"][10] = ["wrg1.zul_out", "fol.in"]
    with pytest.raises(h.NetworkValidationError, match="an der WRG vertauscht"):
        solve_air(d)

    d = _doc()
    d["connections"][1] = ["fil1.out", "wrg1.zul_out"]
    d["connections"][2] = ["wrg1.zul_in", "vhr.in"]
    with pytest.raises(h.NetworkValidationError, match="WRG 'wrg1' ist verkehrt herum"):
        solve_air(d)


def test_bandgrenzen_deutsch_und_gesammelt():
    """Verkehrte Bandgrenzen: gesammelte deutsche Meldung des Loaders statt
    einzelner englischer Kernmeldung („supply band: T_sup_min must be …“)."""
    d = _doc()
    d["components"]["zul"].update({"t_min_C": 24, "t_max_C": 20, "rh_min": 60, "rh_max": 40})
    with pytest.raises(h.NetworkValidationError) as exc:
        load_air(d)
    assert len(exc.value.messages) == 2
    assert all("verkehrt herum" in m for m in exc.value.messages)
    assert "must be" not in str(exc.value)
    d = _doc(regelung="raum", t_min_C=20, t_max_C=23, raum_rh_min=60, raum_rh_max=40,
             feuchtelast=0.0)
    with pytest.raises(h.NetworkValidationError, match="Raumfeuchteband verkehrt herum"):
        load_air(d)


def test_kernfehler_werden_deutsch_gemeldet(monkeypatch):
    """Ein ValueError des Rechenkerns erreicht den Nutzer als deutscher
    Validierungsfehler (mit der Kernmeldung als Zusatz)."""
    def kaputt(*a, **k):
        raise ValueError("supply band: T_sup_min must be <= T_sup_max")
    monkeypatch.setattr(luft_adapter, "simulate", kaputt)
    with pytest.raises(h.NetworkValidationError) as exc:
        solve_air(_doc())
    text = str(exc.value)
    assert "Zuluft-Temperaturband verkehrt herum" in text and "Kernmeldung" in text
    assert "KVS-Auslegung physikalisch nicht möglich" in luft_adapter._kern_fehler(
        ValueError("KVS design point infeasible: …"))


def test_kernwarnungen_als_hinweise():
    """Warnungen des Kerns (Feuchte-Untergrenze, Band bis zur Sättigung)
    stehen als deutsche Hinweise in der Antwort statt nur auf stderr."""
    d = _doc()
    d["components"]["zul"].update({"rh_min": 0, "rh_max": 100})
    with warnings.catch_warnings():
        warnings.simplefilter("error")           # nichts darf nach außen dringen
        r = solve_air(d)
    assert any("unter 1 %" in s for s in r["hinweise"])
    assert any("Sättigung" in s for s in r["hinweise"])
    ref = solve_air(_doc())
    assert not any("Sättigung" in s or "unter 1 %" in s for s in ref["hinweise"])


def test_hinweis_wenn_sollband_nicht_erreicht():
    """Liegt der erreichte Zuluftzustand außerhalb des Sollbands (hier: nur
    Kühler, Entfeuchtung ohne Nachheizer), steht das in den Hinweisen."""
    C = _rand(t_aul=32.0, rh_aul=50.0, t_abl=26.0, rh_abl=50.0)
    C["zul"].update({"rh_min": 40, "rh_max": 55})
    C["kr"] = {"type": "kuehler"}
    r = solve_air(_kette(["aul", "wrg1", "kr", "zul"], ["abl", "wrg1", "fol"], C))
    assert r["zuluft"]["t_C"] < 20.0 - 1.0
    hinweis = [s for s in r["hinweise"] if "Zuluft-Sollband nicht erreicht" in s]
    assert len(hinweis) == 1
    assert f"{r['zuluft']['t_C']:.1f}".replace(".", ",") in hinweis[0]
    # im Band: kein Hinweis
    assert not any("Sollband nicht erreicht" in s for s in solve_air(_doc())["hinweise"])


# ==================================== Loader =====================================

@pytest.mark.parametrize("key", ["name", "self"])
def test_reservierte_schluessel_als_gesammelte_meldung(key):
    """'name:'/'self:' in einer Komponente: gesammelte Meldung „Unbekannter
    Parameter“ statt TypeError aus dem Konstruktoraufruf."""
    d = _doc()
    d["components"]["kr"][key] = "Kühler 1"
    d["components"]["aul"]["rh"] = 120                       # weiterer Fehler: gesammelt
    with pytest.raises(h.NetworkValidationError) as exc:
        load_air(d)
    text = str(exc.value)
    assert f"Komponente 'kr': Unbekannter Parameter '{key}' für Typ 'kuehler'" in text
    assert "Komponente 'aul': 'rh' = 120" in text
    assert len(exc.value.messages) == 2


def test_komponentennamen_mit_punkt():
    """Port-Referenzen werden am LETZTEN Punkt getrennt — Namen wie
    "VHR 1.OG" sind zulässig (vorher: unbekannte Komponente 'VHR 1')."""
    ref = solve_air(_doc())
    neu = {"vhr": "VHR 1.OG", "aul": "AUL.Nord", "wrg1": "WRG 2.1", "zul": "ZUL.EG"}
    d = _doc()
    d["components"] = {neu.get(n, n): c for n, c in d["components"].items()}
    d["components"]["T 1.1"] = {"type": "temperatursensor_luft"}

    def um(ref_):
        n, port = ref_.rsplit(".", 1)
        return f"{neu.get(n, n)}.{port}"
    d["connections"] = [[um(a), um(b)] for a, b in d["connections"]] \
        + [["T 1.1.port", "VHR 1.OG.out"]]
    plant = load_air(d)
    assert ("T 1.1.port", "VHR 1.OG.out") in plant.measurements
    r = solve_air(d)
    assert r["leistungen"] == ref["leistungen"]
    assert r["komponenten"]["VHR 1.OG"] == ref["komponenten"]["vhr"]
    assert r["stationen"]["VHR 1.OG.out"] == ref["stationen"]["vhr.out"]
    assert r["stationen"]["WRG 2.1.zul_in"] == ref["stationen"]["wrg1.zul_in"]
    # Tippfehler im Namen: Meldung mit Vorschlag
    d["connections"][0] = ["AUL.Nrod.out", "fil1.in"]
    with pytest.raises(h.NetworkValidationError, match="'AUL.Nrod'.*Meinten Sie 'AUL.Nord'"):
        load_air(d)


# ============================ Kern-API: simulate() ===============================

SPEC = {"wrg": "ROT_SORP", "components": ["VHR", "KR", "NHR"], "humidifier": "steam",
        "SFP": 2500, "V_nom_m3h": 4500}


def test_p_atm_gilt_fuer_dichte_und_gemeldete_feuchte():
    """p_atm ≠ 1e5: bisher nur für Sollwerte/Eingabefeuchten, nicht für Dichte
    und gemeldetes φ (8e4 Pa: Soll 50 % → gemeldet 62,5 %)."""
    o = simulate(SPEC, -5, 80, 22, 40, 20, 20, 50, 50, V_sup_m3h=4500, p_atm=8e4)
    assert float(o["phi_sup_pct"][0]) == pytest.approx(50.0, abs=0.1)
    # trockene Erwärmung: Leistung ∝ Massenstrom ∝ Druck
    nur_nhr = {"wrg": "PLATE", "components": ["NHR"], "SFP": 0, "RWZ_N": 0.5}
    q = [float(simulate(nur_nhr, -5, 0.5, 22, 0.5, 20, 22, 1, 90, V_sup_m3h=4500,
                        humidity="x", supply_band="phi", p_atm=p)["Q_heat_NHR_kW"][0])
         for p in (1e5, 8e4)]
    assert q[1] / q[0] == pytest.approx(0.8, rel=1e-6)


def test_reihenlaengen_und_leere_reihen():
    """Unterschiedlich lange bzw. leere Reihen: klare ValueError-Meldung statt
    IndexError/numpy-Broadcast-Fehler aus der Tiefe."""
    with pytest.raises(ValueError, match=r"series lengths differ.*hum_AUL \(2\)"):
        simulate(SPEC, [5.0, 6.0, 7.0], [60.0, 50.0], 22, 40, 20, 22, 40, 55, V_sup_m3h=4500)
    with pytest.raises(ValueError, match="T_AUL: empty series"):
        simulate(SPEC, [], 60.0, 22, 40, 20, 22, 40, 55, V_sup_m3h=4500)
    o = simulate(SPEC, [5.0], [60.0], [22.0], [40.0], 20, 22, 40, 55, V_sup_m3h=[4500])
    assert len(o["T_sup_C"]) == 1


@pytest.mark.parametrize("luecke", ["T_AUL", "hum_AUL", "V_sup_m3h", "hum_ABL"])
def test_datenluecke_gibt_nan_und_keine_betriebsstunde(luecke):
    """NaN in einer Eingabe: Zustand UND Lasten NaN, keine Betriebsstunde
    (vorher lief die Lücke als Betriebsstunde mit Last 0 durch)."""
    kw = dict(T_AUL=[5.0, 6.0, -5.0], hum_AUL=[60.0, 55.0, 80.0], T_ABL=22.0,
              hum_ABL=[40.0, 40.0, 40.0], V_sup_m3h=[4500.0, 4500.0, 4500.0])
    ref = simulate(SPEC, T_sup_min=20, T_sup_max=22, hum_sup_min=40, hum_sup_max=55, **kw)
    kw[luecke] = list(kw[luecke])
    kw[luecke][1] = float("nan")
    o = simulate(SPEC, T_sup_min=20, T_sup_max=22, hum_sup_min=40, hum_sup_max=55, **kw)
    for k in ("T_sup_C", "x_sup_gkg", "Q_heat_total_kW", "Q_cool_KR_kW", "Q_recovery_WRG_kW",
              "Q_humid_Bef_kW", "water_kg_h", "n_rot"):
        assert np.isnan(o[k][1]), k
        assert o[k][0] == ref[k][0] and o[k][2] == ref[k][2], k
    assert o["totals"]["operating_hours"] == 2.0 and o["totals"]["hours"] == 3.0
    assert o["totals"]["heating_kWh"] == pytest.approx(
        float(ref["Q_heat_total_kW"][0] + ref["Q_heat_total_kW"][2]))


def test_simulate_weather_meldet_fehlendes_modul():
    """Das Wetter-Modul liegt nur dem Skill bei: klare Meldung statt
    „No module named 'weather'“."""
    with pytest.raises(ImportError, match="ships only with the skill"):
        simulate_weather(SPEC, {}, 47.4, 16.1, "2026-01-01", "2026-01-02",
                         {"T_ABL": 22, "phi_ABL": 45}, 4500)
