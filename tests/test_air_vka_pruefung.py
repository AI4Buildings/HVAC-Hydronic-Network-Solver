"""Lüftungskern: Befunde L1–L12 der Solver-Prüfung 2026-10 und L13–L19 der
Prüfung 2026-11 (Abweichungen vom MATLAB-Original, je mit physikalischer
Begründung).

Geprüft werden Invarianten (Energie-/Feuchtebilanz je Komponente, 2. Haupt-
satz, Sollband, Mischungsgrenzen) statt Regressionszahlen; die validierten
Betriebspunkte deckt tests/test_air_vka_matlab.py ab.
"""
import itertools

import numpy as np
import pytest

import hydraulik as h
from hydraulik.air import solve_air
from hydraulik.air.vka import moist_air as ma
from hydraulik.air.vka import simulate
from hydraulik.air.vka.heat_rec_wheel import _ROTOR_CONSTANTS, _eff_factors
from hydraulik.air.vka.kvs import _counterflow_rwz, kvs_recovery
from hydraulik.air.vka.simulate import build_config
from hydraulik.air.vka.vka_chain import (DX_ENTF_TOL, Setpoints, adiabatic_cooler, cooler,
                                        plate_recovery, preheater, reheater,
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


# --- Übersättigung: Umluftmischung und Kühler ----------------------------------

def test_kuehler_heizt_nie():
    """Übersättigter Eintritt (Nebel, x > x_s(T)): der Entfeuchtungszweig
    lag über T_ein — der Kühler 'entfeuchtete' durch Heizen (negative Last)."""
    T_in, x_in = 3.0, 12.0e-3                        # > x_max (10,2 g/kg), x_s(3 °C) ≈ 4,7
    T, x, Q = cooler(T_in, x_in, 1.0, SP, 0.0)
    assert Q >= 0.0 and T <= T_in + 1e-12


def test_umluftmischung_nicht_uebersaettigt_und_lasten_positiv():
    """Feuchte Umluft (26 °C/100 %) in Frostluft (−15 °C): die Mischung wäre
    übersättigt; der Überschuss kondensiert bei gleicher Enthalpie."""
    spec = {"wrg": "KVS", "components": ["VHR", "KR", "NHR"], "frost": "bypass",
            "recirculation_m3h": 2000}
    o = simulate(spec, -15, 100, 26, 100, 20, 22, 40, 55, V_sup_m3h=5000, V_exh_m3h=5000)
    ch = o["chain"][0]
    T, x = ch["T"]["UML_Byp"], ch["x_gkg"]["UML_Byp"] / 1000.0
    assert x <= float(ma.xs(T, 1e5)) + 1e-12
    assert float(o["Q_cool_KR_kW"][0]) >= 0.0 and float(o["Q_heat_total_kW"][0]) >= 0.0


# --- L13: Sprühbefeuchter-Lücke über T_max ---------------------------------------

def test_spruehbefeuchter_keine_luecke_ueber_tmax():
    """T_ein > T_max, x im linken Feuchteband, h ≤ h(T_max, φ_min): adiabate
    Befeuchtung bis zur φ_min-Linie führt ins Band (vorher griff kein Zweig:
    23,67 °C/32 % blieben unbehandelt, die Nachbarn mit 5,80 g/kg bzw. 21,9 °C
    wurden befeuchtet)."""
    sp = Setpoints(20, 22, 0.40, 0.55)
    hw = 4.18 * 15
    T_in, x_in = 23.67, 5.94e-3
    assert sp.x_min_Tmin < x_in < sp.x_min_Tmax and _h(T_in, x_in) < sp.h_min_Tmax
    T, x, mw, Q = spray_humidifier(T_in, x_in, 1.0, sp, 0.0, 0.9, hw)
    assert mw > 0.0
    assert sp.T_min <= T <= sp.T_max + 0.05
    assert float(ma.phi(T, x, 1e5)) >= sp.phi_min - 5e-3
    assert _h(T, x) - _h(T_in, x_in) == pytest.approx(Q, abs=1e-6)       # adiabat
    assert mw == pytest.approx(x - x_in, rel=1e-9)


def test_spruehbefeuchter_streifen_ueber_h_min_tmax():
    """Zweig 2 im 0,01-kJ/kg-Streifen über h(T_max, φ_min): bis zur φ_min-Linie
    befeuchten (vorher nur bis x_min(T_min): Zuluft 2 K über T_max)."""
    sp = Setpoints(20, 22, 0.40, 0.55)
    x_in = 5.58e-3
    T_in = float(ma.T_from_hx(sp.h_min_Tmax + 0.005, x_in))
    T, x, mw, Q = spray_humidifier(T_in, x_in, 1.0, sp, 0.0, 0.9, 4.18 * 15)
    assert T <= sp.T_max + 0.15          # Wasserenthalpie: wenige Hundertstel K
    assert float(ma.phi(T, x, 1e5)) >= sp.phi_min - 5e-3


@pytest.mark.parametrize("wrg", ["PLATE", "KVS", "ROT_HYG", "ROT_SORP"])
def test_heisse_trockene_luft_mit_spruehbefeuchter_im_band(wrg):
    """Vollklima mit Sprühbefeuchter in Standardreihenfolge: heiße, trockene
    Außenluft erreicht die Zuluft nicht unbehandelt (vorher z.B. Platte,
    36,5 °C/12,5 %: Zuluft 28,3 °C/20 % bei Band 18–24 °C/35–60 %, Lasten 0)."""
    spec = {"wrg": wrg, "components": ["VHR", "KR", "NHR"], "humidifier": "spray",
            "SFP": 2500, "V_nom_m3h": 4500, "V_M_KVS_N": 1.2}
    TT, RR = np.meshgrid(np.arange(20.0, 38.1, 1.5), np.arange(10.0, 40.1, 2.5))
    o = simulate(spec, TT.ravel(), RR.ravel(), 22, 40, 18, 24, 35, 60, V_sup_m3h=4500)
    assert float(np.max(o["T_sup_C"])) <= 24.0 + 0.2
    assert float(np.min(o["phi_sup_pct"])) >= 35.0 - 1.5
    assert float(np.max(o["phi_sup_pct"])) <= 60.0 + 1.5


# --- L14: Frostschutz-Vorerhitzer vor der Umluftbeimischung -----------------------

def test_frostschutz_leistung_mit_umluft_stromab():
    """Q_FS = Außenluftstrom · Δh — der Frostschutz vor der Umluftbeimischung
    erwärmt nur den Außenluftanteil (vorher voller Zuluftstrom: 22,3 statt
    7,4 kW bei 3000 von 4500 m³/h Umluft)."""
    spec = dict(wrg="PLATE", components=["NHR"], frost="preheater", T_FS=0.0, SFP=0,
                recirculation_m3h=3000, order=["Vent_ZUL", "FS", "WRG", "UML_Byp", "NHR"])
    r = run_plant(build_config(spec), Setpoints(20, 22, 0.40, 0.55),
                  [OperatingPoint(-15.0, 80, 22, 30, 4500)])[0]
    m_aussen = r["m_dot"] * (4500 - 3000) / 4500
    # Eintritt des Frostschutzes = Außenluft (SFP = 0; mit Umluft sitzt der
    # Ventilator ohnehin nach der Mischkammer, L19)
    x_aul = float(ma.x(-15.0, 0.80, 1e5))
    dh = _h(r["chain_T"]["FS"], r["chain_x"]["FS"]) - _h(-15.0, x_aul)
    assert r["chain_T"]["FS"] == pytest.approx(0.0)
    assert r["Q_FS"] == pytest.approx(m_aussen * dh, rel=1e-9)
    # ohne Umluft unverändert: voller Zuluftstrom
    spec0 = dict(spec, recirculation_m3h=0, order=["Vent_ZUL", "FS", "WRG", "NHR"])
    r0 = run_plant(build_config(spec0), Setpoints(20, 22, 0.40, 0.55),
                   [OperatingPoint(-15.0, 80, 22, 30, 4500)])[0]
    assert r0["Q_FS"] == pytest.approx(r0["m_dot"] * dh, rel=1e-9)


# --- L15: KVS — Maske der Zuluftseite, kein NaN im Mediumstrom-Raster ------------

def _kvs(v_sup, v_exh, v_m=2.5):
    return kvs_recovery(-5.0, 2e-3, 22.0, 6.6e-3, v_sup, v_exh, Setpoints(20, 22, 0.4, 0.55),
                        v_m, 1000, 4.19, 0.6, 0.6, v_sup, v_sup, 0, 0, 0, 1, 62.7)


@pytest.mark.parametrize("v", [1257, 2095, 2514, 4190, 5028, 7123, 8380])
def test_kvs_rasterkollision_schaltet_nicht_ab(v):
    """C_Luft,ZUL == C_Medium an einem Rasterpunkt bei V̇_zu ≠ V̇_ab: MATLAB wählt
    die Zuluft-Formel über die ABLUFT-Maske → 0/0 = NaN → ganze KVS aus
    (4190/4000 m³/h: WRG 0 statt 17 kW). Jetzt stetig zu den Nachbarn."""
    c_m = np.arange(1, 1001) * (2.5 / 1000.0) / 3600.0 * 1000 * 4.19
    assert np.any(c_m == v / 3600.0 * 1.2)                   # exakte Kollision
    T, _, _, rwz, v_m = _kvs(v, 0.9 * v)
    lo, hi = _kvs(v - 1, 0.9 * (v - 1))[0], _kvs(v + 1, 0.9 * (v + 1))[0]
    assert np.isfinite(rwz) and v_m > 0.0
    assert min(lo, hi) - 0.02 <= T <= max(lo, hi) + 0.02
    assert T > -5.0 + 5.0                                     # WRG arbeitet


def test_kvs_gegenstromformel_ohne_ueberlauf():
    """exp(NTU·(C*−1)) > 1e308 gab inf/inf = NaN; die überlauffreie Form liefert
    den Grenzwert 1/C* (und dieselben Werte, wo nichts überläuft)."""
    with np.errstate(all="raise"):
        big = _counterflow_rwz(np.array([5.0, 2.0]), np.array([500.0, 800.0]))
    assert np.all(np.isfinite(big))
    assert big == pytest.approx([1 / 500.0, 1 / 800.0], rel=1e-9)
    ntu, c = np.array([0.5, 1.5, 3.0]), np.array([0.4, 1.3, 2.0])
    ref = (1 - np.exp(-ntu * (1 - c))) / (1 - c * np.exp(-ntu * (1 - c)))
    assert np.array_equal(_counterflow_rwz(ntu, c), ref)       # bitgleich


def test_kvs_unmoegliche_auslegung_wird_gemeldet():
    """ε·C_Luft ≥ C_Medium: früher log(negativ) = NaN → WRG stillschweigend 0."""
    with pytest.raises(ValueError, match="KVS design point infeasible"):
        kvs_recovery(-5.0, 2e-3, 22.0, 6.6e-3, 12000, 12000, Setpoints(20, 22, 0.4, 0.55),
                     2.5, 1000, 4.19, 0.76, 0.76, 12000, 12000, 0, 0, 0, 1, 62.7)


# --- L16: Toleranzfenster des Kühlers in den Heizregistern -----------------------

def test_heizregister_im_toleranzfenster_des_kuehlers():
    """x_max < x ≤ x_max + Toleranz: der Kühler entfeuchtet nicht (Toleranz),
    die Heizregister heizten aber nur bis T_min (Zuluft 20 °C/62 % bei Band
    20–22 °C/40–55 %). Jetzt bis T_max; φ liegt höchstens um die Toleranz
    über φ_max."""
    sp = Setpoints(20, 22, 0.40, 0.55)
    x = sp.x_max_Tmax + 0.5 * DX_ENTF_TOL
    assert cooler(17.0, x, 1.0, sp, 0.0)[2] == 0.0             # Kühler: Toleranz
    T, xo, Q = reheater(17.0, x, 1.0, sp, 0.0)
    assert T == pytest.approx(sp.T_max) and xo == x
    assert Q == pytest.approx(_h(T, x) - _h(17.0, x), abs=1e-9)
    assert float(ma.phi(T, x, 1e5)) <= sp.phi_max + 3e-3
    T, xo, Q = preheater(17.0, x, 1.0, sp, 0.0, 0.9, 62.7, Bef_after=False,
                         NHR_after=True, KR_after=True)
    assert T == pytest.approx(sp.T_max)
    # knapp darunter unverändert (MATLAB-Pfad): φ_max-Linie
    T_in_band = reheater(17.0, sp.x_max_Tmax - 1e-6, 1.0, sp, 0.0)[0]
    assert T_in_band == pytest.approx(sp.T_max, abs=0.01)


@pytest.mark.parametrize("wrg", ["ROT_NH", "PLATE", "KVS"])
def test_zuluftfeuchte_im_band_am_rand_der_entfeuchtung(wrg):
    """Außenluftfeuchte im Toleranzfenster knapp über x_max (16,5 °C/77,5 %):
    Zuluft im Feuchteband (vorher 20,0 °C/62,2 %)."""
    spec = {"wrg": wrg, "components": ["VHR", "KR", "NHR"], "humidifier": "steam",
            "SFP": 2500, "V_nom_m3h": 4500, "V_M_KVS_N": 1.2}
    rh = np.arange(76.0, 79.01, 0.05)
    o = simulate(spec, 16.5, rh, 22, 40, 20, 22, 40, 55, V_sup_m3h=4500)
    assert float(np.max(o["phi_sup_pct"])) <= 55.0 + 0.5
    assert float(np.min(o["T_sup_C"])) >= 20.0 - 1e-6


# --- L17: Feuchteübertragung des Rotors nach der Richtung des Feuchtegefälles ----

def _x_s_rotor(T):
    """Sättigungsfeuchte wie im Rotormodell (Magnus 611,2/17,62)."""
    p_s = 611.2 * np.exp(17.62 * T / (243.12 + T))
    return 0.622 * p_s / (1e5 - p_s)


@pytest.mark.parametrize("fall, T_oda, x_oda, T_eta, x_eta, spender", [
    ("Winter: kälter und trockener", -5.0, 2e-3, 22.0, 7e-3, "eta"),
    ("Sommer: wärmer und feuchter", 32.0, 16e-3, 24.0, 10e-3, "oda"),
    ("mild-feucht: kälter, aber feuchter", 20.0, 13e-3, 22.0, 7e-3, "oda"),
    ("heiß-trocken: wärmer, aber trockener", 32.0, 5e-3, 24.0, 10e-3, "eta"),
])
def test_rotor_feuchtezuordnung_nach_feuchtegefaelle(fall, T_oda, x_oda, T_eta, x_eta, spender):
    """Spender ist der Strom mit der höheren absoluten Feuchte, Empfänger der
    andere: Kondensationspotential = x_Spender − x_s(T_Empfänger); die
    Volumenströme folgen derselben Zuordnung. MATLAB ordnete nach dem
    Temperaturvorzeichen zu — im Winter- und Sommerfall dasselbe, im
    mild-feuchten und heiß-trockenen Fall vertauscht."""
    c = _ROTOR_CONSTANTS["ROT_SORP"]
    v_sup, v_eta = 10000.0, 8000.0
    f = _eff_factors(c, v_sup, v_eta, 1.0, 20.0, 20.0, 1.0, c["v_hr_nom"], T_oda, T_eta,
                     T_eta, x_eta, T_oda, x_oda, v_eta, v_sup, 1e5, "ROT_SORP")
    x_sp, T_emp, q_sp, q_emp = ((x_oda, T_eta, v_sup, v_eta) if spender == "oda"
                                else (x_eta, T_oda, v_eta, v_sup))
    assert f["f_dx_x"] == pytest.approx(
        min(1.0, max(0.0, c["C6"] * (x_sp - _x_s_rotor(T_emp)) + c["C7"])), abs=1e-12)
    assert f["f_q_x"] == pytest.approx(1.0 - c["C9"] * (q_sp - q_emp) / q_emp, abs=1e-12)
    assert 0.0 < f["f_dx_x"] < 1.0                           # nicht geklemmt: aussagekräftig


_VOLLKLIMA = {"components": ["VHR", "KR", "NHR"], "humidifier": "steam", "SFP": 3430,
              "V_nom_m3h": 4500, "order": ["Vent_ZUL", "WRG", "VHR", "Bef", "KR", "NHR"]}


@pytest.mark.parametrize("wrg", ["ROT_SORP", "ROT_HYG"])
def test_rotor_stetig_ueber_t_aul_gleich_t_abl(wrg):
    """Feuchte Außenluft (90 %), T_AUL läuft durch T_ABL = 22 °C: Rotoraustritt
    und Lasten bleiben stetig. Vorher sprang eta_xr an dieser Stelle (Sorptions-
    rotor 0,58 → 0,70), der Rotoraustritt um 1,0 g/kg und die Summe aus Kühl-
    und Nachheizleistung von 32,9 auf 0,6 kW."""
    T = np.arange(21.5, 22.5001, 0.02)
    o = simulate(dict(_VOLLKLIMA, wrg=wrg), T, 90.0, 22.0, 40.0, 20, 22, 40, 55, V_sup_m3h=4500)
    last = o["Q_heat_total_kW"] + o["Q_cool_KR_kW"] + o["Q_humid_Bef_kW"]
    x_wrg = np.array([c["x_gkg"]["WRG"] for c in o["chain"]])
    assert float(np.max(np.abs(np.diff(last)))) < 0.5
    assert float(np.max(np.abs(np.diff(x_wrg)))) < 0.1
    assert float(np.max(np.abs(np.diff(o["eta_xr"])))) < 0.02
    assert float(np.max(np.abs(np.diff(o["x_sup_gkg"])))) < 0.05


@pytest.mark.parametrize("wrg", ["ROT_SORP", "ROT_HYG", "ROT_NH"])
@pytest.mark.parametrize("T_aul", [15.0, 28.0])
def test_rotor_stetig_ueber_x_aul_gleich_x_abl(wrg, T_aul):
    """Am Wechsel der Zuordnung (x_AUL = x_ABL) ist der Feuchteübertrag
    eta_xr·(x_ABL − x_AUL) null: eta_xr darf dort springen, der Rotoraustritt
    und die Lasten nicht; Feuchte fließt immer dem Gefälle nach."""
    x_abl = float(ma.rh_to_x(24.0, 45.0))
    x_aul = x_abl + np.arange(-60, 61) * 0.01
    o = simulate({"wrg": wrg, "components": ["KR", "NHR"], "SFP": 0, "V_nom_m3h": 4500},
                 T_aul, x_aul, 24.0, x_abl, 20, 24, 30, 65, V_sup_m3h=4500, humidity="x")
    dx = np.array([c["x_gkg"]["WRG"] for c in o["chain"]]) - x_aul      # Übertrag an die Zuluft
    last = o["Q_heat_total_kW"] + o["Q_cool_KR_kW"]
    assert abs(float(dx[60])) < 1e-9                         # x_AUL = x_ABL: kein Übertrag
    assert np.all(dx[:60] >= -1e-12) and np.all(dx[61:] <= 1e-12)        # dem Gefälle nach
    assert np.all(np.abs(dx) <= np.abs(x_abl - x_aul) + 1e-12)           # nie über das Gefälle hinaus
    assert float(np.max(np.abs(np.diff(dx)))) < 0.02
    assert float(np.max(np.abs(np.diff(last)))) < 0.2


# --- L18: Kaltwasser-Vorlauftemperatur des Kühlers -------------------------------

def test_kuehler_entfeuchtet_bis_zur_kaltwassergrenze():
    """Verlangt x_max einen Taupunkt unter der Kaltwasser-Vorlauftemperatur,
    entfeuchtet der Kühler bis zur Sättigung bei dieser Temperatur (vorher gar
    nicht: Zuluft 12,1 statt 5,9 g/kg). Bilanz wie im normalen Entfeuchtungs-
    zweig; nie über x_ein, nie heizend."""
    sp = Setpoints(22.3, 22.3, 0.343, 0.343)                 # x_max 5,81 g/kg, Taupunkt 5,84 °C
    T_tau = float(ma.Ts(sp.x_max_Tmax, 1e5))
    assert 5.0 < T_tau < 6.0
    T_in, x_in = 25.0, 12e-3

    def bilanz(T, x):
        return _h(T_in, x_in) - _h(T, x) + (x - x_in) * 4.18 * T

    T, x, Q = cooler(T_in, x_in, 1.0, sp, 0.0)               # Default 6 °C: an der Grenze
    assert T == 6.0 and x == pytest.approx(float(ma.xs(6.0, 1e5)), rel=1e-12)
    assert sp.x_max_Tmax < x < x_in and Q == pytest.approx(bilanz(T, x), abs=1e-9)
    T, x, Q = cooler(T_in, x_in, 1.0, sp, 0.0, T_Entf_bnd=4.0)           # tiefer: Sollwert erreicht
    assert T == pytest.approx(T_tau) and x == sp.x_max_Tmax
    T, x, Q = cooler(T_in, x_in, 1.0, sp, 0.0, T_Entf_bnd=12.0)          # höher: früher begrenzt
    assert T == 12.0 and x == pytest.approx(float(ma.xs(12.0, 1e5)), rel=1e-12)
    assert Q == pytest.approx(bilanz(T, x), abs=1e-9) and Q > 0.0
    # Grenze scheidet nichts ab (x_s(T_KW) ≥ x_ein): nur sensible Kühlung auf T_max
    T, x, Q = cooler(T_in, 7e-3, 1.0, sp, 0.0, T_Entf_bnd=12.0)
    assert T == pytest.approx(22.3) and x == 7e-3 and Q > 0.0
    # Luft kälter als das Kaltwasser: der Kühler heizt nie
    T, x, Q = cooler(5.9, 7e-3, 1.0, sp, 0.0)
    assert (T, x, Q) == (5.9, 7e-3, 0.0)


def test_kaltwasser_vorlauf_ohne_einfluss_ueber_dem_taupunkt():
    """Liegt der Taupunkt von x_max über der Vorlauftemperatur, ändert sie
    nichts (Default 6 °C = bisheriges Verhalten)."""
    sp = Setpoints(20, 22, 0.40, 0.55)                       # Taupunkt 12,5 °C
    ref = cooler(30.0, 14e-3, 1.0, sp, 0.0)
    assert ref[1] == sp.x_max_Tmax
    for t_kw in (-5.0, 6.0, 10.0, 12.4):
        assert cooler(30.0, 14e-3, 1.0, sp, 0.0, T_Entf_bnd=t_kw) == ref


def test_kaltwasser_vorlauf_als_anlagenparameter():
    """Anlagenparameter T_KW (simulate/Skill-API): Zuluft-Sollwert 22,3 °C/34,3 %
    (Taupunkt 5,84 °C). Default 6 °C: Sättigung bei 6 °C und Kennzeichen
    KR_dew_limited; 4 °C: Sollwert erreicht; 12 °C: entsprechend feuchter."""
    spec = {"wrg": "PLATE", "components": ["KR", "NHR"], "SFP": 0, "V_nom_m3h": 4500}

    def lauf(**extra):
        return simulate(dict(spec, **extra), 25.0, 60.0, 24.0, 50.0, 22.3, 22.3, 34.3, 34.3,
                        V_sup_m3h=4500)
    o = lauf()
    assert float(o["x_sup_gkg"][0]) == pytest.approx(float(ma.xs(6.0, 1e5)) * 1e3, rel=1e-9)
    assert float(o["KR_dew_limited"][0]) == 1.0 and float(o["T_sup_C"][0]) == pytest.approx(22.3)
    assert float(o["Q_cool_KR_kW"][0]) > 40.0                # vorher 3,2 kW (nur sensibel)
    o = lauf(T_KW=4.0)
    assert float(o["phi_sup_pct"][0]) == pytest.approx(34.3, abs=0.05)
    assert float(o["KR_dew_limited"][0]) == 0.0
    o = lauf(T_KW=12.0)
    assert float(o["x_sup_gkg"][0]) == pytest.approx(float(ma.xs(12.0, 1e5)) * 1e3, rel=1e-9)
    assert float(o["KR_dew_limited"][0]) == 1.0
    # Band mit Taupunkt 12,5 °C: T_KW bis 12 °C ohne Einfluss, 14 °C begrenzt
    band = dict(T_AUL=30.0, hum_AUL=60.0, T_ABL=26.0, hum_ABL=50.0, T_sup_min=20, T_sup_max=22,
                hum_sup_min=40, hum_sup_max=55, V_sup_m3h=4500)
    a, b = simulate(spec, **band), simulate(dict(spec, T_KW=12.0), **band)
    for k in ("T_sup_C", "x_sup_gkg", "Q_cool_KR_kW", "Q_heat_total_kW", "KR_dew_limited"):
        assert float(a[k][0]) == float(b[k][0]), k
    c = simulate(dict(spec, T_KW=14.0), **band)
    assert float(c["x_sup_gkg"][0]) == pytest.approx(float(ma.xs(14.0, 1e5)) * 1e3, rel=1e-9)
    assert float(c["Q_cool_KR_kW"][0]) < float(a["Q_cool_KR_kW"][0])


def test_rotorregelung_haelt_feuchteband_unabhaengig_vom_kaltwasser():
    """Die Rotorregelung zielt weiter auf x_max, auch wenn der Kühler wegen
    einer hohen Kaltwasser-Vorlauftemperatur nicht entfeuchten könnte: sie
    drosselt den Rotor, statt die Zuluft über das Feuchteband zu befeuchten."""
    spec = {"wrg": "ROT_SORP", "components": ["VHR", "KR", "NHR"], "humidifier": "steam",
            "SFP": 0, "V_nom_m3h": 4500}
    a = simulate(dict(spec, T_KW=6.0), -5.0, 20.0, 24.0, 75.0, 20, 22, 40, 55, V_sup_m3h=4500)
    b = simulate(dict(spec, T_KW=16.0), -5.0, 20.0, 24.0, 75.0, 20, 22, 40, 55, V_sup_m3h=4500)
    assert float(b["n_rot"][0]) == float(a["n_rot"][0]) < 20.0
    assert float(b["phi_sup_pct"][0]) <= 55.0 + 0.5 and float(b["KR_dew_limited"][0]) == 0.0


# --- L19: Zuluftventilator nach der Mischkammer (Anlagen mit Umluft) -------------

def _umluft_anlage(v_uml, sfp=2000.0, comps=("NHR",), frost="none", order=None):
    spec = dict(wrg="PLATE", components=list(comps), SFP=sfp, recirculation_m3h=v_uml,
                frost=frost, T_FS=0.0,
                order=order or ["Vent_ZUL", "WRG", "UML_Byp"] + list(comps))
    r = run_plant(build_config(spec), Setpoints(20, 22, 0.40, 0.55),
                  [OperatingPoint(-5.0, 80, 22, 40, 4500)])[0]
    return r, float(ma.x(-5.0, 0.80, 1e5)), float(ma.x(22.0, 0.40, 1e5))


@pytest.mark.parametrize("v_uml", [1500.0, 3000.0, 4500.0])
def test_ventilatorwaerme_nach_der_mischkammer(v_uml):
    """Mit Umluft fördert der Zuluftventilator den vollen Zuluftstrom nach der
    Mischkammer; f_rec·P_el geht vollständig in die Mischluft (vorher am
    Stranganfang nur der Außenluftanteil: 0,5 von 1,5 kW bei 3000 von 4500 m³/h,
    bei 100 % Umluft nichts)."""
    r, x_aul, x_abl = _umluft_anlage(v_uml)
    m, m_aussen, m_uml = r["m_dot"], r["m_dot"] * (4500 - v_uml) / 4500, r["m_dot"] * v_uml / 4500
    q_fan = 0.6 * 2000.0 / 1000.0 * 4500 / 3600                          # f_rec·P_el = 1,5 kW
    ch_T, ch_x = r["chain_T"], r["chain_x"]
    assert r["Q_fan"] == pytest.approx(q_fan)
    # Ventilator direkt nach der Mischung, auf dem vollen Massenstrom
    assert m * 1.0 * (ch_T["Vent_ZUL"] - ch_T["UML_Byp"]) == pytest.approx(q_fan, rel=1e-9)
    assert ch_T["UML_Byp"] == pytest.approx(
        (ch_T["WRG"] * (4500 - v_uml) + 22.0 * v_uml) / 4500, abs=1e-9)
    # WRG vor der Mischung sieht Außenluft OHNE Ventilatorwärme
    assert r["Q_WRG"] == pytest.approx(m_aussen * (_h(ch_T["WRG"], x_aul) - _h(-5.0, x_aul)),
                                       abs=1e-9)
    # Energiebilanz der Zuluft über den ganzen Strang (Mischung nach T: ±0,1 kW)
    zugefuehrt = m * _h(r["T_sup"], r["x_sup"]) - m_aussen * _h(-5.0, x_aul) - m_uml * _h(22.0, x_abl)
    lasten = r["Q_WRG"] + r.get("Q_NHR", 0.0)
    assert zugefuehrt == pytest.approx(lasten + q_fan * (1 + 1.85 * ch_x["UML_Byp"]), abs=0.12)


def test_volle_umluft_gibt_raumluft_plus_ventilatorerwaermung():
    r, _, x_abl = _umluft_anlage(4500.0, comps=())
    assert r["x_sup"] == pytest.approx(x_abl, rel=1e-12)
    assert r["T_sup"] == pytest.approx(22.0 + 1.5 / r["m_dot"], rel=1e-12)   # vorher exakt 22,0


def test_frostschutz_vor_der_mischkammer_sieht_aussenluft():
    """Frostschutz und WRG vor der Mischung: Eintritt ist die Außenluft ohne
    Ventilatorwärme (vorher Außenluft + Ventilatorerwärmung)."""
    r, x_aul, _ = _umluft_anlage(3000.0, frost="preheater",
                                 order=["Vent_ZUL", "FS", "WRG", "UML_Byp", "NHR"])
    m_aussen = r["m_dot"] * 1500 / 4500
    assert r["chain_T"]["FS"] == pytest.approx(0.0)
    assert r["Q_FS"] == pytest.approx(m_aussen * (_h(0.0, x_aul) - _h(-5.0, x_aul)), rel=1e-9)


def test_ohne_umluft_ventilator_am_stranganfang():
    """Anlagen ohne Umluft bleiben auf dem validierten Pfad."""
    r, x_aul, _ = _umluft_anlage(0.0, order=["Vent_ZUL", "WRG", "NHR"])
    assert r["chain_T"]["Vent_ZUL"] == pytest.approx(-5.0 + 1.5 / r["m_dot"], rel=1e-12)
    assert r["Q_WRG"] == pytest.approx(
        r["m_dot"] * (_h(r["chain_T"]["WRG"], x_aul) - _h(r["chain_T"]["Vent_ZUL"], x_aul)),
        abs=1e-9)
