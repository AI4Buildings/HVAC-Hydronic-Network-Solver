"""Sensoren: rückwirkungsfreie Messstellen + BEMS-Datenpunkt-Zuordnung."""
import pytest

import hydraulik as h


def _heizkreis(mit_sensoren: bool) -> dict:
    comps = {
        "pu": {"type": "pump", "mode": "constant_dp", "dp_kPa": 15, "q_nom_m3h": 0.5},
        "hk": {"type": "radiator", "q_nom_kW": 5, "t_sup_nom_C": 70,
               "t_ret_nom_C": 55, "t_room_C": 20, "kv_m3h": 2},
        "sp": {"type": "ideal_storage", "t_set_C": 70, "p_out_kPa": 200},
    }
    conns = [["sp.out", "pu.in"], ["pu.out", "hk.in"]]
    if mit_sensoren:
        comps["wmz"] = {"type": "energy_meter", "q_nom_m3h": 100,
                        "bems_id_q_dot": "FHPinkEgk'AS03-2099203_PXC03'MtrHC'Mtr02'PrPwr-AI45",
                        "bems_key": "EXP_HK_WMZ", "description": "WMZ Heizkreis, Einbau RL"}
        comps["tf_vl"] = {"type": "temperature_sensor",
                          "bems_id": "FHPinkEgk'AS03-2099203_PXC03'MtrHC'Mtr02'TFI-AI47"}
        comps["pf"] = {"type": "pressure_sensor"}
        comps["pdf"] = {"type": "pressure_diff_sensor"}
        comps["vf"] = {"type": "flow_sensor", "q_nom_m3h": 100}
        conns += [["pu.out", "tf_vl.port", "wmz.t_ref", "pdf.plus"],
                  ["sp.out", "pf.port", "pdf.minus"],
                  ["hk.out", "wmz.in"], ["wmz.out", "vf.in"], ["vf.out", "sp.in"]]
    else:
        conns += [["hk.out", "sp.in"]]
    return {"components": comps, "connections": conns}


def test_sensors_rueckwirkungsfrei():
    """Fühler ändern nichts; WMZ/V̇-Sensor mit großzügigem q_nom vernachlässigbar."""
    r0 = h.load(_heizkreis(False)).solve()
    r1 = h.load(_heizkreis(True)).solve()
    assert r1.converged
    assert r1["hk"].q_m3h == pytest.approx(r0["hk"].q_m3h, rel=1e-4)
    assert r1["hk"].q_dot_kW == pytest.approx(r0["hk"].q_dot_kW, rel=1e-4)


def test_sensor_messwerte_konsistent():
    """WMZ misst die Kreisleistung (= Heizkörperabgabe), Fühler die Knotenwerte,
    Δp-Sensor die Pumpenförderhöhe, V̇-Sensor den Umlaufvolumenstrom."""
    r = h.load(_heizkreis(True)).solve()
    by_name = {s.name: s for s in r.sensors}
    assert by_name["wmz"].readings["q_dot_kW"] == pytest.approx(-r["hk"].q_dot_kW, rel=1e-6)
    assert by_name["wmz"].readings["t_ref_C"] == pytest.approx(r["hk"].t_in_C, abs=1e-9)
    assert by_name["wmz"].readings["t_leitung_C"] == pytest.approx(r["hk"].t_out_C, abs=1e-9)
    assert by_name["tf_vl"].readings["t_C"] == pytest.approx(r["hk"].t_in_C, abs=1e-9)
    assert by_name["pdf"].readings["dp_kPa"] == pytest.approx(-r["pu"].dp_kPa, rel=1e-9)
    assert by_name["vf"].readings["q_m3h"] == pytest.approx(r["hk"].q_m3h, rel=1e-9)
    # Drucksensor: Speicher-Ausgang ist der Druckanker (200 kPa ü) minus Pumpensog? —
    # nein: pf sitzt AM Ankerknoten selbst
    assert by_name["pf"].readings["p_kPa"] == pytest.approx(200.0, abs=1e-6)


def test_bems_ids_in_yaml_und_ergebnis():
    """Aedifion-IDs (mit Apostrophen!) überleben YAML-Text → Ergebnis-JSON;
    Stellventile und Pumpen akzeptieren bems_id/bems_key/description."""
    yaml_text = """\
components:
  pu: {type: pump, mode: constant_flow, q_m3h: 1,
       bems_id: "FHPinkEgk'AS01-2099201_PXC01'HC'HCGen'Hpu'Sec'PuP2'Cmd-BO69",
       bems_id_w_elek: "FHPinkEgk'AS01-2099201_PXC01'E'MtrEl'MtrEl06-pulseConverter9",
       bems_key: EXP_HP_SEK_PUMP_P2}
  rv: {type: control_valve, kvs_m3h: 4, opening: 0.5,
       bems_id: "FHPinkEgk'AS04'HC'VTFBHS12'MxCrt'Vlv-AO12",
       description: "Stellventil FBH Büro 12"}
  tf: {type: temperature_sensor,
       bems_id: "FHPinkEgk'AS04-2099204_PXC04'HC'VTFBHS12'MxCrt'TFl-AI254",
       bems_key: EXP_ROOM_12_FBH_T_VL, description: "Vorlauf FBH Büro 12 (OG)"}
connections:
  - [pu.out, rv.in, tf.port]
  - [rv.out, pu.in]
"""
    net = h.load(yaml_text)
    assert net.components["pu"].bems_id.endswith("Cmd-BO69")
    assert net.components["rv"].description == "Stellventil FBH Büro 12"
    r = net.solve(thermal=False)
    d = r.to_dict()
    (tf,) = d["sensors"]
    assert tf["bems"]["bems_id"] == "FHPinkEgk'AS04-2099204_PXC04'HC'VTFBHS12'MxCrt'TFl-AI254"
    assert tf["bems"]["bems_key"] == "EXP_ROOM_12_FBH_T_VL"
    assert "Sensor" in r.report() and "EXP_ROOM_12_FBH_T_VL" in r.report()
