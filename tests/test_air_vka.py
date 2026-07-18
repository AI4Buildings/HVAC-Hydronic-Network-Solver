"""Luftseite: Loader + Adapter auf den integrierten VKA-Rechenkern.

Der Adapter muss exakt das liefern, was ein direkter simulate()-Aufruf mit
identischer Konfiguration (inkl. gezeichneter Reihenfolge) ergibt.
"""
import pytest

import hydraulik as h
from hydraulik.air import load_air, solve_air
from hydraulik.air.vka import simulate


def _doc(regelung="band", **zul_extra):
    zul = {"type": "zuluft", "v_m3h": 1359, "regelung": regelung}
    if regelung == "band":
        zul.update({"t_min_C": 18, "t_max_C": 22, "rh_min": 40, "rh_max": 55})
    zul.update(zul_extra)
    return {
        "components": {
            "aul": {"type": "aussenluft", "t_C": 30.0, "rh": 50.0,
                    "bems": [{"id": "FHPinkEgk'AS20'TOaDct301-AI37",
                              "key": "TZ_T_AUL_301"}]},
            "fil1": {"type": "filter_luft"},
            "wrg1": {"type": "wrg", "typ": "ROT_SORP", "eta_hr_n": 0.778,
                     "eta_xr_n": 0.807},
            "vhr": {"type": "vorheizer"},
            "bef": {"type": "befeuchter", "typ": "steam"},
            "kr": {"type": "kuehler"},
            "nhr": {"type": "nachheizer"},
            "ven_zul": {"type": "ventilator_luft", "sfp": 1715},
            "zul": zul,
            "abl": {"type": "abluft_raum", "t_C": 24.0, "rh": 53.0},
            "ven_abl": {"type": "ventilator_luft", "sfp": 1715},
            "fol": {"type": "fortluft"},
        },
        "connections": [
            ["aul.out", "fil1.in"], ["fil1.out", "wrg1.zul_in"],
            ["wrg1.zul_out", "vhr.in"], ["vhr.out", "bef.in"],
            ["bef.out", "kr.in"], ["kr.out", "nhr.in"],
            ["nhr.out", "ven_zul.in"], ["ven_zul.out", "zul.in"],
            ["abl.out", "ven_abl.in"], ["ven_abl.out", "wrg1.abl_in"],
            ["wrg1.abl_out", "fol.in"],
        ],
    }


def test_adapter_deckt_sich_mit_direktem_kernaufruf():
    r = solve_air(_doc())
    assert r["ok"] and r["regelung"] == "band"
    # Ventilatorposition wird an den Stranganfang normiert (validierter Pfad
    # des Kerns); die Konditionierungsreihenfolge folgt der Zeichnung.
    assert r["plant"]["order"] == ["Vent_ZUL", "WRG", "VHR", "Bef", "KR", "NHR"]
    assert any("Stranganfang" in h for h in r["hinweise"])
    ref = simulate({"wrg": "ROT_SORP", "components": ["VHR", "KR", "NHR"],
                    "humidifier": "steam", "eta_hr_N": 0.778, "eta_xr_N": 0.807,
                    "SFP": 3430, "V_nom_m3h": 4500.0,
                    "order": ["Vent_ZUL", "WRG", "VHR", "Bef", "KR", "NHR"]},
                   30.0, 50.0, 24.0, 53.0, 18, 22, 40, 55, V_sup_m3h=1359)
    assert r["leistungen"]["kuehlen_kW"] == pytest.approx(float(ref["Q_cool_KR_kW"][0]), abs=1e-5)
    assert r["zuluft"]["t_C"] == pytest.approx(float(ref["T_sup_C"][0]), abs=1e-6)
    assert r["zuluft"]["phi_pct"] == pytest.approx(float(ref["phi_sup_pct"][0]), abs=1e-6)
    # Komponenten-Zuordnung für Tooltips
    assert r["komponenten"]["kr"]["q_kuehlen_kW"] == r["leistungen"]["kuehlen_kW"]
    assert r["komponenten"]["wrg1"]["eta_hr"] is not None
    assert r["komponenten"]["ven_zul"]["p_el_kW"] == pytest.approx(
        r["leistungen"]["ventilatoren_el_kW"] / 2, abs=1e-5)
    # BEMS-Metadaten bleiben an der Komponente (LLM-Weiterverarbeitung)
    plant = load_air(_doc())
    assert plant.components["aul"].bems[0]["key"] == "TZ_T_AUL_301"


def test_regelung_fest_pinnt_zuluftzustand():
    r = solve_air(_doc(regelung="fest", t_C=20.0, rh=50.0))
    assert r["zuluft"]["t_C"] == pytest.approx(20.0, abs=1e-6)
    assert r["zuluft"]["phi_pct"] == pytest.approx(50.0, abs=1e-3)


def test_regelung_raum_gekoppelt():
    r = solve_air(_doc(regelung="raum", t_min_C=20, t_max_C=23,
                       raum_rh_min=40, raum_rh_max=55, feuchtelast=720.0))
    assert r["ok"] and r["regelung"] == "raum"
    assert 40.0 - 1.5 <= r["raum"]["rh_pct"] <= 55.0 + 1.5
    assert 20.0 - 0.3 <= r["zuluft"]["t_C"] <= 23.0 + 0.3


def test_luft_fehlerpfade():
    doc = _doc()
    del doc["components"]["zul"]
    doc["connections"] = [c for c in doc["connections"] if "zul.in" not in c]
    with pytest.raises(h.NetworkValidationError) as exc:
        solve_air(doc)
    assert "zuluft" in str(exc.value)

    doc = _doc()
    doc["components"]["x1"] = {"type": "ventilator"}      # Tippfehler
    with pytest.raises(h.NetworkValidationError) as exc:
        solve_air(doc)
    assert "ventilator_luft" in str(exc.value)

    doc = _doc()
    doc["components"]["abl"].pop("rh")                    # Abluftfeuchte fehlt
    with pytest.raises(h.NetworkValidationError) as exc:
        solve_air(doc)
    assert "Abluftfeuchte" in str(exc.value)

    doc = _doc()
    doc["connections"].append(["fil1.out", "kr.in"])      # Port doppelt
    with pytest.raises(h.NetworkValidationError) as exc:
        solve_air(doc)
    assert "genau einmal" in str(exc.value)


def test_zuluft_modusvalidierung():
    with pytest.raises(h.NetworkValidationError) as exc:
        load_air({"components": {"zul": {"type": "zuluft", "v_m3h": 1000,
                                         "regelung": "fest"}},
                  "connections": [["zul.in", "zul.in"]]})
    assert "'t' fehlt" in str(exc.value)


def test_wrg_läuft_energieoptimal_im_winter():
    """Regression: Rotor muss im Winterfall laufen (die unnormierte Reihenfolge
    mit Ventilator am Ende ließ die Energy-Regelung auf n_rot = 0 zurückfallen)."""
    doc = _doc()
    doc["components"]["aul"].update({"t_C": -5.0, "rh": 80.0})
    doc["components"]["abl"].update({"t_C": 22.0, "rh": 40.0})
    doc["components"]["zul"].update({"t_min_C": 20, "t_max_C": 22})
    doc["components"]["zul"]["v_m3h"] = 4500
    r = solve_air(doc)
    assert r["komponenten"]["wrg1"]["q_wrg_kW"] > 30.0
    assert r["komponenten"]["wrg1"]["n_rot"] > 1.0
    assert r["leistungen"]["heizen_gesamt_kW"] < 10.0


def test_luft_sensoren_als_anzapfung():
    """Fühler (T, Tφ, φ, p, Δp, V̇, WMZ, kWh) sind Anzapfungen mit Messleitung:
    sie liegen NICHT im Strang, dürfen bereits verbundene Kanal-Anschlüsse
    anzapfen und ändern das Rechenergebnis nicht."""
    ref = solve_air(_doc())
    doc = _doc()
    doc["components"]["tf1"] = {"type": "kombisensor_luft",
                                "bems": [{"key": "TZ_T_ZUL", "id": "x'AI1"}]}
    doc["components"]["pd1"] = {"type": "differenzdrucksensor_luft"}
    doc["connections"] += [
        ["tf1.port", "nhr.out"],          # zapft bereits verbundenen Port an
        ["pd1.plus", "fil1.in"],
        ["pd1.minus", "fil1.out"],
    ]
    r = solve_air(doc)
    assert r["leistungen"]["kuehlen_kW"] == ref["leistungen"]["kuehlen_kW"]
    assert r["zuluft"] == ref["zuluft"]
    # doppelte Messleitung am selben Sensoranschluss → klare Meldung
    doc["connections"].append(["tf1.port", "kr.out"])
    with pytest.raises(h.NetworkValidationError) as exc:
        solve_air(doc)
    assert "Messanschluss" in str(exc.value) or "Sensoranschluss" in str(exc.value)


def test_stationszustaende_je_kanalabschnitt():
    """Leitungs-Tooltips: payload['stationen'] liefert ϑ/x/φ/V̇ je Kanal-
    Anschluss; Fortluft folgt aus der WRG-Bilanz (Wärme + Feuchte)."""
    r = solve_air(_doc())
    st = r["stationen"]
    assert st["aul.out"]["t_C"] == pytest.approx(30.0)
    assert st["fil1.in"] == st["aul.out"]              # beide Enden eines Abschnitts
    assert st["zul.in"]["t_C"] == pytest.approx(r["zuluft"]["t_C"], abs=1e-3)
    assert st["zul.in"]["v_m3h"] == pytest.approx(1359.0)
    # WRG-Bilanz: Feuchteabgabe der Zuluft landet in der Fortluft
    dx_zul = st["aul.out"]["x_gkg"] - st["wrg1.zul_out"]["x_gkg"]
    dx_fol = st["fol.in"]["x_gkg"] - st["wrg1.abl_in"]["x_gkg"]
    assert dx_fol == pytest.approx(dx_zul, abs=2e-3)   # gleiche Massenströme
    # Winter: Fortluft deutlich abgekühlt (Rückwärmung), aber über Außenluft
    doc = _doc()
    doc["components"]["aul"].update({"t_C": -5.0, "rh": 80.0})
    doc["components"]["abl"].update({"t_C": 22.0, "rh": 40.0})
    doc["components"]["zul"].update({"t_min_C": 20, "t_max_C": 22})
    rw = solve_air(doc)
    assert -5.0 < rw["stationen"]["fol.in"]["t_C"] < 10.0
    # raumgekoppelt: Stationskarte über gepinnten Nachlauf ebenfalls vorhanden
    rr = solve_air(_doc(regelung="raum", t_min_C=20, t_max_C=23,
                        raum_rh_min=40, raum_rh_max=55, feuchtelast=720.0))
    assert rr["stationen"]["zul.in"]["t_C"] == pytest.approx(
        rr["zuluft"]["t_C"], abs=1e-3)


def test_kvs_und_plattentauscher_auslegung():
    """Skill-Parität: Plattentauscher-Rückwärmzahl (rwz_n) und KVS-Sole-
    Nennvolumenstrom (v_m_kvs) erreichen den Kern; Defaults = Kern-Defaults."""
    def doc(**wrg_extra):
        d = _doc()
        d["components"]["aul"].update({"t_C": -5.0, "rh": 80.0})
        d["components"]["abl"].update({"t_C": 22.0, "rh": 40.0})
        d["components"]["wrg1"] = {"type": "wrg", **wrg_extra}
        return d

    # Plattentauscher: bessere Rückwärmzahl → weniger Nachheizen
    r_norm = solve_air(doc(typ="PLATE"))
    r_gut = solve_air(doc(typ="PLATE", rwz_n=0.85))
    assert r_gut["leistungen"]["wrg_kW"] > r_norm["leistungen"]["wrg_kW"] + 2.0
    assert r_gut["leistungen"]["heizen_gesamt_kW"] < r_norm["leistungen"]["heizen_gesamt_kW"] - 2.0
    ref = simulate({"wrg": "PLATE", "components": ["VHR", "KR", "NHR"],
                    "humidifier": "steam", "RWZ_N": 0.85, "SFP": 3430,
                    "V_nom_m3h": 4500.0,
                    "order": ["Vent_ZUL", "WRG", "VHR", "Bef", "KR", "NHR"]},
                   -5.0, 80.0, 22.0, 40.0, 18, 22, 40, 55, V_sup_m3h=1359)
    assert r_gut["leistungen"]["heizen_gesamt_kW"] == pytest.approx(
        float(ref["Q_heat_total_kW"][0]), abs=1e-5)

    # KVS: größerer Sole-Nennvolumenstrom → schlechtere Übertragung
    r_kvs = solve_air(doc(typ="KVS", rwz_n=0.70))
    r_kvs10 = solve_air(doc(typ="KVS", rwz_n=0.70, v_m_kvs_m3h=10.0))
    assert r_kvs10["leistungen"]["wrg_kW"] < r_kvs["leistungen"]["wrg_kW"]
    ref10 = simulate({"wrg": "KVS", "components": ["VHR", "KR", "NHR"],
                      "humidifier": "steam", "RWZ_N": 0.70, "V_M_KVS_N": 10.0,
                      "SFP": 3430, "V_nom_m3h": 4500.0,
                      "order": ["Vent_ZUL", "WRG", "VHR", "Bef", "KR", "NHR"]},
                     -5.0, 80.0, 22.0, 40.0, 18, 22, 40, 55, V_sup_m3h=1359)
    assert r_kvs10["leistungen"]["heizen_gesamt_kW"] == pytest.approx(
        float(ref10["Q_heat_total_kW"][0]), abs=1e-5)


def test_wrg_uebertragungsgrad_physikalisch_begrenzt():
    """Regression: Bei stark unbalancierten Volumenströmen (ABL >> ZUL) trieb
    der empirische Unbalance-Faktor f_q den Übertragungsgrad über 1 — die
    Zuluft verließ die WRG wärmer als die Abluftquelle (2. Hauptsatz).
    Jetzt gilt ε ≤ 1, der WRG-Austritt bleibt ≤ max(T_AUL, T_ABL)."""
    doc = _doc()
    doc["components"]["aul"].update({"t_C": -5.0, "rh": 80.0})
    doc["components"]["abl"].update({"t_C": 22.0, "rh": 40.0})
    doc["components"]["zul"].update({"t_min_C": 20, "t_max_C": 22})
    doc["components"]["zul"]["v_m3h"] = 4500
    doc["components"]["abl"]["v_m3h"] = 10000
    r = solve_air(doc)
    assert r["komponenten"]["wrg1"]["eta_hr"] <= 1.0
    assert r["stationen"]["wrg1.zul_out"]["t_C"] <= 22.0 + 1e-6
    assert r["zuluft"]["t_C"] <= 22.0 + 0.3          # Sollband eingehalten
    assert any("Volumenstromverhältnis" in h for h in r["hinweise"])
    # balanciert: unverändert unter der Grenze, kein Hinweis
    doc["components"]["abl"].pop("v_m3h")
    rb = solve_air(doc)
    assert rb["komponenten"]["wrg1"]["eta_hr"] == pytest.approx(0.777978, abs=1e-4)
    assert not any("Volumenstromverhältnis" in h for h in rb["hinweise"])


def test_abluft_volumenstrom_eingabe():
    """Der Abluft-Volumenstrom wird bei 'abluft_raum' angegeben; fortluft.v
    bleibt als Altbestand gültig (gleiches Ergebnis), Widerspruch → Fehler."""
    doc = _doc()
    doc["components"]["abl"]["v_m3h"] = 2000
    r_abl = solve_air(doc)
    doc = _doc()
    doc["components"]["fol"]["v_m3h"] = 2000
    r_fol = solve_air(doc)
    assert r_abl["leistungen"] == r_fol["leistungen"]
    doc["components"]["abl"]["v_m3h"] = 1500          # widersprüchlich
    with pytest.raises(h.NetworkValidationError) as exc:
        solve_air(doc)
    assert "abluft_raum" in str(exc.value)


def test_gea_vollklima_winterfall():
    """Editor-Vorlage 'GEA Vollklima Energetikum': Winter-Auslegungsfall der
    Gerätedokumentation GEA CAIRplus SX 096.064 IVBV (Nr. 165225_463):
    AUL −15 °C/90 %, ABL 22 °C/45 %, 4500/4500 m³/h, Zuluft 24,6 °C/53,6 %.
    Referenzwerte lt. Datenblatt: Rotor-Austritt 13,8 °C/6,1 g/kg,
    Dampfbefeuchter ~22–23 kg/h (max. 23), VHR-Bilanzpunkt ~15,3 kW."""
    doc = {
        "components": {
            "aul1": {"type": "aussenluft", "t_C": -15, "rh": 90},
            "fil1": {"type": "filter_luft"},
            "ven_zul1": {"type": "ventilator_luft", "sfp": 1830},
            "wrg1": {"type": "wrg", "typ": "ROT_SORP", "eta_hr_n": 0.778,
                     "eta_xr_n": 0.807, "v_nom_m3h": 4500},
            "vhr1": {"type": "vorheizer"},
            "bef1": {"type": "befeuchter", "typ": "steam"},
            "kr1": {"type": "kuehler"},
            "nhr1": {"type": "nachheizer"},
            "zul1": {"type": "zuluft", "v_m3h": 4500, "regelung": "fest",
                     "t_C": 24.6, "rh": 53.6},
            "abl1": {"type": "abluft_raum", "t_C": 22, "rh": 45, "v_m3h": 4500},
            "fil2": {"type": "filter_luft"},
            "ven_abl1": {"type": "ventilator_luft", "sfp": 1430},
            "fol1": {"type": "fortluft"},
        },
        "connections": [
            ["aul1.out", "fil1.in"], ["fil1.out", "ven_zul1.in"],
            ["ven_zul1.out", "wrg1.zul_in"], ["wrg1.zul_out", "vhr1.in"],
            ["vhr1.out", "bef1.in"], ["bef1.out", "kr1.in"],
            ["kr1.out", "nhr1.in"], ["nhr1.out", "zul1.in"],
            ["abl1.out", "fil2.in"], ["fil2.out", "wrg1.abl_in"],
            ["wrg1.abl_out", "ven_abl1.in"], ["ven_abl1.out", "fol1.in"],
        ],
    }
    r = solve_air(doc)
    st = r["stationen"]
    assert st["wrg1.zul_out"]["t_C"] == pytest.approx(13.8, abs=0.5)
    assert st["wrg1.zul_out"]["x_gkg"] == pytest.approx(6.1, abs=0.3)
    assert r["leistungen"]["befeuchter_wasser_kg_h"] == pytest.approx(22.5, abs=1.0)
    assert r["leistungen"]["befeuchter_wasser_kg_h"] <= 23.0    # Hyline-Maximum
    assert r["komponenten"]["vhr1"]["q_heizen_kW"] == pytest.approx(15.0, abs=1.0)
    assert r["komponenten"]["vhr1"]["q_heizen_kW"] <= 16.5      # Auslegungsleistung
    assert r["leistungen"]["kuehlen_kW"] == 0.0
    assert r["zuluft"]["t_C"] == pytest.approx(24.6, abs=1e-3)
    assert r["zuluft"]["phi_pct"] == pytest.approx(53.6, abs=0.1)
    assert st["ven_abl1.in"]["t_C"] == pytest.approx(-6.8, abs=2.0)   # Fortluft
