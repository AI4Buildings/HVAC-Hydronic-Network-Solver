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
