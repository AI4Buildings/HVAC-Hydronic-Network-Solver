"""Einheiten-Audit: Alternative Eingabe-Einheiten (Suffixe) müssen exakt auf
dieselben SI-Werte und damit identische Ergebnisse führen — geprüft für
Heiz-/Kühlregister und alle Wärmeabgabesysteme sowie Pumpe/Widerstände.

Jeder Testfall rechnet dieselbe Physik zweimal: einmal mit den HVAC-üblichen
Einheiten, einmal mit den Alternativ-Suffixen, und fordert bitgleiche
(rel < 1e-12) Übereinstimmung der Leistungen und Temperaturen.
"""
import pytest

import hydraulik as h


def _solve(comp: dict, fluid: dict | None = None):
    doc = {
        "components": {
            "zu": {"type": "inflow", "t_set_C": 60.0, "q_m3h": 1.8},
            "x": comp,
            "ab": {"type": "outflow", "p_kPa": 150},
        },
        "connections": [["zu.port", "x.in"], ["x.out", "ab.port"]],
    }
    if fluid:
        doc["fluid"] = fluid
    r = h.load(doc).solve()
    assert r.converged
    return r["x"]


def _same(a, b):
    assert a.q_dot_kW == pytest.approx(b.q_dot_kW, rel=1e-12)
    assert a.t_out_C == pytest.approx(b.t_out_C, rel=1e-12)
    assert a.dp_kPa == pytest.approx(b.dp_kPa, rel=1e-12)
    assert a.q_m3h == pytest.approx(b.q_m3h, rel=1e-12)


def test_heating_coil_unit_suffixes():
    """Register: ṁ_Luft kg/s ↔ kg/h, V̇_ref m³/h ↔ l/s ↔ m³/s, C Pa/(m³/h)² ↔
    Pa/(m³/s)², UA_ref W/K — alles muss identisch rechnen."""
    a = _solve({"type": "heating_coil", "ua_ref_W_K": 551.0, "n": 0.4,
                "m_dot_air_kg_s": 1.505, "m_dot_air_ref_kg_s": 1.505,
                "q_w_ref_m3h": 2.9, "t_air_in_C": 13.1,
                "arrangement": "crossflow_unmixed", "c_Pa_m3h2": 416.2})
    b = _solve({"type": "heating_coil", "ua_ref_W_K": 551.0, "n": 0.4,
                "m_dot_air_kg_h": 1.505 * 3600.0, "m_dot_air_ref_kg_h": 1.505 * 3600.0,
                "q_w_ref_l_s": 2.9 * 1000.0 / 3600.0, "t_air_in_C": 13.1,
                "arrangement": "crossflow_unmixed", "c_Pa_m3s2": 416.2 * 3600.0 ** 2})
    c = _solve({"type": "heating_coil", "ua_ref_W_K": 551.0, "n": 0.4,
                "m_dot_air_kg_s": 1.505, "m_dot_air_ref_kg_s": 1.505,
                "q_w_ref_m3s": 2.9 / 3600.0, "t_air_in_C": 13.1,
                "arrangement": "crossflow_unmixed", "c_Pa_m3h2": 416.2})
    _same(a, b)
    _same(a, c)
    assert a.extras["ua_eff_W_K"] == pytest.approx(
        551.0 * ((1.8 / 2.9)) ** 0.4, rel=1e-9)   # Wasserstrom 1.8 vs ref 2.9


def test_cooling_coil_greybox_unit_suffixes():
    """Greybox: UA*_wet kg/s ↔ kg/h; q_prescribed kW ↔ W."""
    base = {"type": "cooling_coil", "ua_ref_W_K": 2958.78, "n": 0.3737,
            "rh_air_in": 0.6, "m_dot_air_kg_s": 1.4682, "t_air_in_C": 24.0,
            "q_w_ref_m3h": 5.0}
    fl = {"rho": 999.9, "mu": 1.3e-3, "cp": 4186.0}

    def wet(extra):
        d = dict(base)
        d.update(extra)
        doc = {"fluid": fl, "components": {
                   "zu": {"type": "inflow", "t_set_C": 8.0, "q_m3h": 4.8},
                   "x": d, "ab": {"type": "outflow", "p_kPa": 150}},
               "connections": [["zu.port", "x.in"], ["x.out", "ab.port"]]}
        r = h.load(doc).solve()
        return r["x"]

    a = wet({"ua_star_wet_kg_s": 2.2928})
    b = wet({"ua_star_wet_kg_h": 2.2928 * 3600.0})
    assert a.extras["betrieb"] == b.extras["betrieb"] == "nass"
    assert a.q_dot_kW == pytest.approx(b.q_dot_kW, rel=1e-12)
    assert a.extras["kondensat_kg_h"] == pytest.approx(b.extras["kondensat_kg_h"], rel=1e-12)

    p1 = _solve({"type": "cooling_coil", "q_prescribed_kW": 12.0,
                 "m_dot_air_kg_s": 1.5, "t_air_in_C": 26.0})
    p2 = _solve({"type": "cooling_coil", "q_prescribed_W": 12000.0,
                 "m_dot_air_kg_s": 1.5, "t_air_in_C": 26.0})
    _same(p1, p2)


def test_radiator_unit_suffixes():
    """Heizkörper: Q̇_nom kW ↔ W; C-Wert in beiden Einheiten."""
    a = _solve({"type": "radiator", "q_nom_kW": 5.0, "t_sup_nom_C": 70,
                "t_ret_nom_C": 55, "t_room_C": 20, "c_Pa_m3h2": 3000.0})
    b = _solve({"type": "radiator", "q_nom_W": 5000.0, "t_sup_nom_C": 70,
                "t_ret_nom_C": 55, "t_room_C": 20,
                "c_Pa_m3s2": 3000.0 * 3600.0 ** 2})
    _same(a, b)


def test_floor_heating_unit_suffixes():
    """FBH: Fläche m², k W/(m²K), Rohrlänge m ↔ mm, d_innen m ↔ mm."""
    a = _solve({"type": "floor_heating", "area_m2": 18.0, "k_W_m2K": 5.5,
                "t_room_C": 20, "length_m": 90.0, "d_inner_m": 0.016})
    b = _solve({"type": "floor_heating", "area_m2": 18.0, "k_W_m2K": 5.5,
                "t_room_C": 20, "length_mm": 90000.0, "d_inner_mm": 16.0})
    _same(a, b)


def test_pump_and_resistance_unit_suffixes():
    """Pumpe: Δp kPa ↔ Pa ↔ bar ↔ mbar; Fluss-RB m³/h ↔ l/s ↔ l/min ↔ m³/s."""
    def loop(pu, zu_q):
        doc = {"components": {
                   "zu": {"type": "inflow", "t_set_C": 60.0, **zu_q},
                   "rv": {"type": "control_valve", "kvs_m3h": 4.0},
                   "ab": {"type": "outflow", "p_kPa": 150}},
               "connections": [["zu.port", "rv.in"], ["rv.out", "ab.port"]]}
        doc["components"]["zu"].update(pu)
        return h.load(doc).solve(thermal=False)["rv"]

    variants = [{"q_m3h": 1.8}, {"q_l_s": 0.5}, {"q_l_min": 30.0}, {"q_m3s": 5e-4}]
    results = [loop({}, v) for v in variants]
    for r in results[1:]:
        assert r.q_m3h == pytest.approx(results[0].q_m3h, rel=1e-12)

    def dp_loop(key, val):
        doc = {"components": {
                   "pu": {"type": "pump", "mode": "constant_dp", key: val, "q_nom_m3h": 1},
                   "rv": {"type": "control_valve", "kvs_m3h": 4.0}},
               "connections": [["pu.out", "rv.in"], ["rv.out", "pu.in"]]}
        return h.load(doc).solve(thermal=False)["rv"].q_m3h

    q0 = dp_loop("dp_kPa", 30.0)
    assert dp_loop("dp_Pa", 30000.0) == pytest.approx(q0, rel=1e-12)
    assert dp_loop("dp_bar", 0.3) == pytest.approx(q0, rel=1e-12)
    assert dp_loop("dp_mbar", 300.0) == pytest.approx(q0, rel=1e-12)


def test_display_defaults_hvac_units():
    """Bevorzugte Anzeigeeinheiten (erster Suffix) sind die HVAC-üblichen —
    ein leeres Editorfeld zeigt m³/h, kPa, kW statt m³/s, Pa, W."""
    from hydraulik.params import UNIT_GROUPS
    assert next(iter(UNIT_GROUPS["flow"])) == "m3h"
    assert next(iter(UNIT_GROUPS["pressure"])) == "kPa"
    assert next(iter(UNIT_GROUPS["power"])) == "kW"
    assert next(iter(UNIT_GROUPS["quad_resistance"])) == "Pa_m3h2"
    # Faktoren: exakte Kontrolle der Umrechnung nach SI
    assert UNIT_GROUPS["flow"]["m3h"] == pytest.approx(1 / 3600)
    assert UNIT_GROUPS["flow"]["l_s"] == pytest.approx(1e-3)
    assert UNIT_GROUPS["flow"]["l_min"] == pytest.approx(1e-3 / 60)
    assert UNIT_GROUPS["massflow"]["kg_h"] == pytest.approx(1 / 3600)
    assert UNIT_GROUPS["quad_resistance"]["Pa_m3h2"] == pytest.approx(3600.0 ** 2)
    assert UNIT_GROUPS["lin_resistance"]["Pa_m3h"] == pytest.approx(3600.0)
