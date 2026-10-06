"""Lüftungskern: Befunde L1–L12 der Solver-Prüfung 2026-10 (Abweichungen vom
MATLAB-Original, je mit physikalischer Begründung).

Geprüft werden Invarianten (Energie-/Feuchtebilanz je Komponente, 2. Haupt-
satz, Sollband, Mischungsgrenzen) statt Regressionszahlen; die validierten
Betriebspunkte deckt tests/test_air_vka_matlab.py ab.
"""
import itertools

import pytest

import hydraulik as h
from hydraulik.air import solve_air
from hydraulik.air.vka import moist_air as ma
from hydraulik.air.vka import simulate
from hydraulik.air.vka.kvs import kvs_recovery
from hydraulik.air.vka.simulate import build_config
from hydraulik.air.vka.vka_chain import (Setpoints, adiabatic_cooler, cooler, plate_recovery,
                                        spray_humidifier, steam_humidifier)
from hydraulik.air.vka.vka_plant import OperatingPoint, plant_anlage1, plant_plate, run_plant

from test_air_vka import _doc, _gea_doc

SP = Setpoints(22, 24, 0.40, 0.55)


def _h(T, x):
    return float(ma.h(T, x))


# --- L1: Sprühbefeuchter — Austrittstemperatur zur richtigen Feuchte ------------

@pytest.mark.parametrize("cfg", [plant_anlage1(), plant_plate(),
                                 build_config(dict(wrg="ROT_HYG", components=["KR", "NHR"],
                                                   humidifier="spray"))],
                         ids=["anlage1", "platte", "rot_hyg"])
def test_spruehbefeuchter_bilanz_und_adiabat(cfg):
    """m·Δh über dem Befeuchter = Wasserenthalpie; adiabate Befeuchtung kühlt
    (vorher: Erwärmung um bis zu 7,1 kW 'aus dem Nichts')."""
    order = cfg.zul_order or ["Vent_ZUL", "WRG", "VHR", "KR", "Bef", "NHR"]
    prev = order[order.index("Bef") - 1]
    for Ta, ra, Tb in itertools.product(range(14, 36, 3), range(10, 60, 10), (22, 26)):
        r = run_plant(cfg, SP, [OperatingPoint(Ta, ra, Tb, 45, 10000, 10000)])[0]
        Ti, xi = r["chain_T"][prev], r["chain_x"][prev]
        To, xo = r["chain_T"]["Bef"], r["chain_x"]["Bef"]
        assert r["m_dot"] * (_h(To, xo) - _h(Ti, xi)) == pytest.approx(r.get("Q_Bef", 0.0), abs=0.01)
        assert To <= Ti + 1e-9


# --- L2: Kühler kühlt heiße, trockene Luft; Rotor schaltet nicht ab -------------

def test_kuehler_kuehlt_heisse_trockene_luft():
    T, x, Q = cooler(28.0, 6.8e-3, 1.0, SP, 0.0)
    assert Q > 0.0 and T <= SP.T_max + 1e-6
    assert SP.phi_min - 1e-3 <= float(ma.phi(T, x, 1e5)) <= SP.phi_max + 1e-3


@pytest.mark.parametrize("spec, Ta, ra", [
    ({"wrg": "ROT_NH", "components": ["VHR", "KR", "NHR"]}, 35.0, 20.0),
    ({"wrg": "ROT_NH", "components": ["VHR", "KR", "NHR"]}, 28.0, 30.0),
    ({"wrg": "PLATE", "components": ["KR", "NHR"]}, 28.0, 30.0),
    ({"wrg": "ROT_SORP", "components": ["KR", "NHR"]}, 32.0, 20.0),
])
def test_zuluft_im_sollband_bei_heisser_trockener_aussenluft(spec, Ta, ra):
    o = simulate(dict(spec, SFP=1250), Ta, ra, 24, 45, 22, 24, 40, 55, V_sup_m3h=10000)
    assert float(o["T_sup_C"][0]) <= 24.0 + 1e-3
    assert float(o["Q_cool_KR_kW"][0]) > 0.0


def test_rotor_schaltet_bei_heisser_trockener_luft_nicht_ab():
    """Hält keine Drehzahl die Feuchte im Band, wählt die Regelung die beste
    Temperatur statt abzuschalten (vorher: Rotor aus, Kühler 41,9 kW)."""
    spec = {"wrg": "ROT_NH", "components": ["VHR", "KR", "NHR"], "SFP": 1250}
    o = simulate(spec, 35.0, 20.0, 24, 45, 22, 24, 40, 55, V_sup_m3h=10000)
    assert float(o["eta_hr"][0]) > 0.5
    assert float(o["Q_cool_KR_kW"][0]) < 25.0


# --- L3: WRG-Übertragungsgrad durch das Kapazitätsstromverhältnis begrenzt ------

def _unbal(ve, typ="ROT_SORP", uml=None, Ta=-10.0):
    d = _doc()
    C = d["components"]
    C["aul"].update({"t_C": Ta, "rh": 80})
    C["abl"].update({"t_C": 22.0, "rh": 40, "v_m3h": ve})
    C["zul"].update({"v_m3h": 4500, "t_min_C": 20, "t_max_C": 22})
    C["wrg1"] = {"type": "wrg", "typ": typ, "v_nom_m3h": 4500,
                 **({"eta_hr_n": 0.778, "eta_xr_n": 0.807} if typ == "ROT_SORP" else {})}
    if uml:
        C["uml"] = {"type": "umluft", "v_m3h": uml}
        d["connections"] = [c for c in d["connections"] if c != ["fil1.out", "wrg1.zul_in"]] \
            + [["fil1.out", "uml.in"], ["uml.out", "wrg1.zul_in"]]
    return solve_air(d)


@pytest.mark.parametrize("ve, uml", [(2250, None), (3500, None), (4500, 600)])
@pytest.mark.parametrize("typ", ["ROT_SORP", "ROT_NH", "ROT_HYG", "PLATE", "KVS"])
def test_fortluft_zwischen_aussen_und_abluft(ve, uml, typ):
    """Wärme-/Feuchteübertrag ≤ was der kleinere Strom abgeben kann: die
    Fortluft liegt zwischen Außen- und Abluftzustand (vorher x = −2,37 g/kg,
    φ = −206 % bei V_ab/V_zu = 0,5)."""
    r = _unbal(ve, typ, uml)
    st = r["stationen"]
    x_aul = float(ma.rh_to_x(-10.0, 80, 1e5))
    x_abl = float(ma.rh_to_x(22.0, 40, 1e5))
    fol = st["fol.in"]
    assert x_aul - 1e-3 <= fol["x_gkg"] <= x_abl + 1e-3
    assert -10.0 - 1e-3 <= fol["t_C"] <= 22.0 + 1e-3
    zo = st["wrg1.zul_out"]
    assert zo["x_gkg"] <= float(ma.xs(zo["t_C"], 1e5)) * 1e3 + 1e-6      # nicht übersättigt


# --- L4: ohne gezeichneten Ventilator bleibt die WRG aktiv ----------------------

def test_wrg_ohne_ventilator_aktiv():
    d = _doc()
    C = d["components"]
    C["aul"].update({"t_C": -5.0, "rh": 80})
    C["abl"].update({"t_C": 22.0, "rh": 40})
    r_ref = solve_air(d)
    for n in ("ven_zul", "ven_abl"):
        del C[n]
    d["connections"] = [c for c in d["connections"] if not any(p.startswith("ven_") for p in c)] \
        + [["nhr.out", "zul.in"], ["abl.out", "wrg1.abl_in"]]
    r = solve_air(d)
    assert r["komponenten"]["wrg1"]["q_wrg_kW"] > 0.5 * r_ref["komponenten"]["wrg1"]["q_wrg_kW"]
    assert r["plant"].get("SFP", 0.0) == 0.0                 # keine Ventilatorwärme
    # ohne Ventilatorwärme mehr (nicht weniger) Heizleistung als mit
    assert r["leistungen"]["heizen_gesamt_kW"] >= r_ref["leistungen"]["heizen_gesamt_kW"] - 1e-6


# --- L5: Fortluftstation hinter einem Abluftventilator nach der WRG -------------

def test_fortluftstation_nach_wrg_mit_ventilator():
    """GEA-Vorlage: Abluftventilator NACH der WRG — die Fortluftstation zeigt
    den Fortluftzustand (vorher: Raumluft 22 °C/7,5 g/kg)."""
    r = solve_air(_gea_doc())
    st = r["stationen"]
    assert st["fol1.in"]["t_C"] < 5.0                        # Fortluft, nicht Raumluft (22 °C)
    assert st["fol1.in"]["x_gkg"] < st["wrg1.abl_in"]["x_gkg"]


# --- L6: Vorheizer meldet seine Leistung ----------------------------------------

def test_vorheizer_meldet_leistung():
    cfg = build_config(dict(wrg="PLATE", components=["VHR", "NHR"], RWZ_N=0.3, SFP=1250))
    r = run_plant(cfg, Setpoints(20, 22, 0.40, 0.55), [OperatingPoint(13.0, 95, 22, 60, 10000)])[0]
    o = cfg.zul_order or []
    Ti, xi = r["chain_T"][o[o.index("VHR") - 1]], r["chain_x"][o[o.index("VHR") - 1]]
    To, xo = r["chain_T"]["VHR"], r["chain_x"]["VHR"]
    assert To > Ti + 0.1
    assert r["Q_VHR"] == pytest.approx(r["m_dot"] * (_h(To, xo) - _h(Ti, xi)), abs=1e-6)


# --- L7: KVS-Ventilatorwärme = f_rec · P_el -------------------------------------

def test_kvs_ventilatorwaerme_nicht_doppelt():
    cfg = build_config(dict(wrg="KVS", components=["KR", "NHR"], SFP=3000))
    r = run_plant(cfg, Setpoints(20, 22, 0.4, 0.55), [OperatingPoint(-5, 80, 22, 40, 10000)])[0]
    rho = 1e5 / (287.0 * (273.0 + 21.0))
    m = 10000 / 3600 * rho
    heat = m * (r["chain_T"]["Vent_ZUL"] - (-5.0)) + m * (r["T_eta_wheel"] - 22.0)
    p_el = 3000 / 1000 * 10000 / 3600
    assert heat == pytest.approx(0.6 * p_el, rel=1e-6)


# --- L8: adiabate Abluftkühlung befeuchtet und kühlt ----------------------------

def test_adiabate_abluftkuehlung_physikalisch():
    sp = Setpoints(20, 24, 0.40, 0.55)
    xb = float(ma.x(26.0, 0.45, 1e5))
    T, x, dx = adiabatic_cooler(26.0, xb, 29.0, 6.5e-3, sp, 0.9, KR_nAK=1)
    assert T <= 26.0 + 1e-9 and x >= xb - 1e-12 and dx >= 0.0
    assert _h(T, x) == pytest.approx(_h(26.0, xb), abs=1e-6)        # adiabat
    out = simulate({"wrg": "ROT_NH", "components": ["KR", "NHR"], "adiab_exhaust": True,
                    "SFP": 1250}, 28.0, 6.5, 26.0, 9.571, 20, 24, 40, 55,
                   V_sup_m3h=10000, humidity="x")
    assert float(out["T_eta_wheel_C"][0]) <= 26.0 + 1e-9
    assert float(out["x_eta_wheel_gkg"][0]) >= 9.571 - 1e-6


# --- L9: Eingabevalidierung -----------------------------------------------------

@pytest.mark.parametrize("typ, rwz", [("PLATE", 1.0), ("PLATE", 1.2), ("KVS", 1.0)])
def test_rueckwaermzahl_unter_eins(typ, rwz):
    d = _doc()
    d["components"]["wrg1"] = {"type": "wrg", "typ": typ, "rwz_n": rwz}
    with pytest.raises((h.ComponentParamError, h.NetworkValidationError), match="rwz_n"):
        solve_air(d)


def test_umluft_groesser_als_zuluft_abgelehnt():
    d = _doc()
    d["components"]["uml"] = {"type": "umluft", "v_m3h": 2000}
    d["connections"] = [c for c in d["connections"] if c != ["wrg1.zul_out", "vhr.in"]] \
        + [["wrg1.zul_out", "uml.in"], ["uml.out", "vhr.in"]]
    with pytest.raises(h.NetworkValidationError, match="Umluft"):
        solve_air(d)


def test_abluft_null_abgelehnt():
    d = _doc()
    d["components"]["abl"]["v_m3h"] = 0
    with pytest.raises(h.NetworkValidationError, match="Abluft"):
        solve_air(d)


# --- L10: Dampfbefeuchter-Wassermenge im Sättigungszweig ------------------------

def test_dampfbefeuchter_wassermenge_bilanz():
    sp = Setpoints(20, 22, 0.4, 0.55)
    hw = 2500.9 + 1.86 * 100
    T, x, mw, Q = steam_humidifier(2.0, 2.0e-3, 1.0, sp, 0.0, hw)
    assert mw == pytest.approx(x - 2.0e-3, rel=1e-9)
    assert Q == pytest.approx(_h(T, x) - _h(2.0, 2.0e-3), abs=1e-6)


# --- L11: aktive Komponenten außerhalb des Zuluftstrangs ------------------------

def test_befeuchter_im_abluftstrang_abgelehnt():
    d = _doc()
    C = d["components"]
    C["bef_abl"] = {"type": "befeuchter", "typ": "spray"}
    del C["bef"]
    d["connections"] = [c for c in d["connections"]
                        if not any(p.startswith("bef.") for p in c)
                        and c != ["ven_abl.out", "wrg1.abl_in"]] \
        + [["vhr.out", "kr.in"], ["ven_abl.out", "bef_abl.in"], ["bef_abl.out", "wrg1.abl_in"]]
    with pytest.raises(h.NetworkValidationError, match="bef_abl"):
        solve_air(d)


# --- L12: weitere ---------------------------------------------------------------

def test_kvs_index_null_heisst_aus():
    """MATLAB-Index 0 = aus; Python-Index −1 wählte den VOLLEN Mediumstrom."""
    sp = Setpoints(20, 20, 0.4, 0.4)                          # Punkt-Sollwert
    T, x, Te, RWZ, VM = kvs_recovery(19.999, 6.0e-3, 23.0, 7.0e-3, 10000, 10000, sp,
                                     2.5, 1000, 4.19, 0.76, 0.76, 10000, 10000,
                                     0, 0, 1, 1, 62.7)
    assert T <= 20.0 + 0.05


def test_ventilatorleistung_mit_abluftvolumenstrom():
    d = _doc()
    d["components"]["abl"]["v_m3h"] = 2700
    r = solve_air(d)
    p = (1715 * 1359 + 1715 * 2700) / 3600 / 1000
    assert r["leistungen"]["ventilatoren_el_kW"] == pytest.approx(p, rel=1e-6)
    assert r["komponenten"]["ven_abl"]["p_el_kW"] == pytest.approx(1715 * 2700 / 3.6e6, rel=1e-6)


def test_platte_kreuzstrom_auslegungspunkt_bei_unbalance():
    """Bei Auslegungsvolumenströmen muss die Auslegungs-Rückwärmzahl herauskommen
    (auch C* ≠ 1; Operator-Rangfolge der NTU-Umkehr)."""
    for vz, va in ((10000, 12500), (8000, 10000)):
        T, x, rwz = plate_recovery(0.0, 3e-3, 20.0, vz, va, 0.5, vz, va)
        assert rwz == pytest.approx(0.5, abs=1e-9)


def test_taupunkt_unter_null():
    assert float(ma.Ts(2.0e-3, 1e5)) == pytest.approx(-7.7, abs=0.3)
    T, x, mw, Q = spray_humidifier(-5.0, 1.0e-3, 1.0, Setpoints(20, 22, 0.4, 0.55), 0.0, 0.9,
                                   4.18 * 15)
    assert x <= float(ma.xs(T, 1e5)) + 1e-9                  # nicht übersättigt


# --- (b): Rotor-Auslegungsvolumenstrom, Übersättigungshinweis -------------------

def test_rotor_auslegung_default_zuluftvolumenstrom():
    d = _doc()
    d["components"]["wrg1"].pop("v_nom_m3h", None)
    r = solve_air(d)
    assert r["plant"]["V_nom_m3h"] == pytest.approx(1359.0)
