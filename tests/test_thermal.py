"""Energiegleichung gegen analytische Referenzen."""
import math

import pytest

import hydraulik as h
from hydraulik.components.coils import CP_AIR


def test_mixing_valve_temperature_blend():
    """Mischtemperatur = massenstromgewichtetes Mittel der Zuströme."""
    net = h.Network()
    net.add(h.OpenEnd("heiss", bc="flow", q_m3h=1.0, t_supply_C=80))
    net.add(h.OpenEnd("kalt", bc="flow", q_m3h=0.5, t_supply_C=40))
    net.add(h.MixingValve3Way("mv", kvs_m3h=4.0, opening=0.5))
    net.add(h.OpenEnd("aus", bc="pressure", p_kPa=100))
    net.add(h.Pipe("r1", length_m=2, d_inner_mm=25))
    net.connect("heiss.port", "mv.a")
    net.connect("kalt.port", "mv.b")
    net.connect("mv.ab", "r1.in")
    net.connect("r1.out", "aus.port")
    r = net.solve()
    t_mix = (1.0 * 80 + 0.5 * 40) / 1.5
    assert r["r1"].t_in_C == pytest.approx(t_mix, abs=1e-6)
    assert r["r1"].q_m3h == pytest.approx(1.5, rel=1e-9)


def test_pipe_heat_loss_exponential():
    """T_aus = T_amb + (T_ein − T_amb)·exp(−U'·L/(ṁ·cp))"""
    net = h.Network()
    net.add(h.OpenEnd("zu", bc="flow", q_m3h=0.5, t_supply_C=70))
    net.add(h.OpenEnd("ab", bc="pressure", p_kPa=100))
    net.add(h.Pipe("r1", length_m=50, d_inner_mm=20, u_linear_W_mK=0.35, t_amb_C=10))
    net.connect("zu.port", "r1.in")
    net.connect("r1.out", "ab.port")
    r = net.solve()
    m_dot = 0.5 / 3600 * net.fluid.rho
    t_ref = 10 + (70 - 10) * math.exp(-0.35 * 50 / (m_dot * net.fluid.cp))
    assert r["r1"].t_out_C == pytest.approx(t_ref, abs=1e-9)
    # Energiebilanz: Q̇_Kante = ṁ·cp·(T_aus − T_ein)
    assert r["r1"].q_dot_kW * 1e3 == pytest.approx(m_dot * net.fluid.cp * (t_ref - 70), rel=1e-9)


def _radiator_loop(q_pump_m3h, t_supply=75.0):
    net = h.Network(fluid=h.water_at(70))
    net.add(h.Pump("pu", mode="constant_flow", q_m3h=q_pump_m3h))
    net.add(h.HeatPump("kessel", mode="target_t_out", t_out_set_C=t_supply, q_nom_m3h=q_pump_m3h))
    net.add(h.Radiator("hk", q_nom_kW=2.0, t_sup_nom_C=75, t_ret_nom_C=65, t_room_C=20, n=1.3))
    net.connect("kessel.out", "pu.in")
    net.connect("pu.out", "hk.in")
    net.connect("hk.out", "kessel.in")
    return net


def test_radiator_nominal_point():
    """Bei Nennmassenstrom und Nennvorlauf muss der HK Q_nom und t_ret_nom liefern."""
    fluid = h.water_at(70)
    m_dot_nom = 2000.0 / (fluid.cp * 10.0)
    q_nom_m3h = m_dot_nom / fluid.rho * 3600
    r = _radiator_loop(q_nom_m3h).solve()
    assert r["hk"].q_dot_kW == pytest.approx(-2.0, abs=0.001)   # 2 kW aus dem Wasser
    assert r["hk"].t_out_C == pytest.approx(65.0, abs=0.05)
    assert r["hk"].t_in_C == pytest.approx(75.0, abs=1e-6)


def test_radiator_half_flow_reference():
    """Halber Massenstrom: Referenzlösung durch unabhängige Fixpunktiteration."""
    fluid = h.water_at(70)
    m_dot_nom = 2000.0 / (fluid.cp * 10.0)
    m_half = m_dot_nom / 2
    r = _radiator_loop(m_half / fluid.rho * 3600).solve()

    # unabhängige Referenz: Q̇ = Q̇n·(ΔTlm/ΔTlm,n)^n und Q̇ = ṁcp(75 − T_out)
    dtlm_n = 10.0 / math.log(55.0 / 45.0)
    t_out = 65.0
    for _ in range(200):
        dtlm = (75.0 - t_out) / math.log(55.0 / (t_out - 20.0))
        q = 2000.0 * (dtlm / dtlm_n) ** 1.3
        t_out = 75.0 - q / (m_half * fluid.cp)
    assert r["hk"].t_out_C == pytest.approx(t_out, abs=0.01)


def test_radiator_prescribed_q_override():
    net = _radiator_loop(0.2)
    net.components["hk"].q_prescribed = 1500.0
    r = net.solve()
    fluid = net.fluid
    m_dot = 0.2 / 3600 * fluid.rho
    assert r["hk"].t_in_C - r["hk"].t_out_C == pytest.approx(1500.0 / (m_dot * fluid.cp), rel=1e-6)
    assert r["hk"].q_dot_kW == pytest.approx(-1.5, rel=1e-9)


def test_floor_heating_exponential():
    net = h.Network()
    net.add(h.OpenEnd("zu", bc="flow", q_m3h=0.3, t_supply_C=35))
    net.add(h.OpenEnd("ab", bc="pressure", p_kPa=100))
    net.add(h.FloorHeatingLoop("fbh", area_m2=15, k_W_m2K=6.0, t_room_C=21,
                               length_m=75, d_inner_mm=12))
    net.connect("zu.port", "fbh.in")
    net.connect("fbh.out", "ab.port")
    r = net.solve()
    m_dot = 0.3 / 3600 * net.fluid.rho
    t_ref = 21 + (35 - 21) * math.exp(-6.0 * 15 / (m_dot * net.fluid.cp))
    assert r["fbh"].t_out_C == pytest.approx(t_ref, abs=1e-9)


def test_cooling_coil_entu_counterflow():
    """ε-NTU-Gegenstrom gegen Handrechnung; Kühlregister: Luft erwärmt Wasser."""
    net = h.Network(fluid=h.water_at(10))
    net.add(h.OpenEnd("zu", bc="flow", q_m3h=1.0, t_supply_C=6))
    net.add(h.OpenEnd("ab", bc="pressure", p_kPa=100))
    net.add(h.CoolingCoil("kr", ua_W_K=800, m_dot_air_kg_s=1.2, t_air_in_C=28, kv_m3h=3.0))
    net.connect("zu.port", "kr.in")
    net.connect("kr.out", "ab.port")
    r = net.solve()

    fluid = net.fluid
    c_w = 1.0 / 3600 * fluid.rho * fluid.cp
    c_a = 1.2 * CP_AIR
    c_min, c_max = min(c_w, c_a), max(c_w, c_a)
    ntu = 800 / c_min
    cr = c_min / c_max
    e = math.exp(-ntu * (1 - cr))
    eps = (1 - e) / (1 - cr * e)
    q_ref = eps * c_min * (28 - 6)
    assert r["kr"].q_dot_kW * 1e3 == pytest.approx(q_ref, rel=1e-6)
    assert r["kr"].extras["t_luft_aus_C"] == pytest.approx(28 - q_ref / c_a, rel=1e-6)


def test_hydraulic_separator_mixing():
    """Sekundärstrom > Primärstrom: Sekundärvorlauf wird durch Rücklauf verdünnt.

    T_sec_VL = (ṁ_prim·T_prim_VL + (ṁ_sec − ṁ_prim)·T_sec_RL) / ṁ_sec
    """
    net = h.Network()
    net.add(h.Pump("pu_p", mode="constant_flow", q_m3h=1.0))
    net.add(h.HeatPump("wp", mode="target_t_out", t_out_set_C=50, q_nom_m3h=1.0))
    net.add(h.HydraulicSeparator("weiche", q_nom_m3h=2.0))
    net.add(h.Pump("pu_s", mode="constant_flow", q_m3h=1.5))
    net.add(h.Radiator("hk", q_nom_kW=6, t_sup_nom_C=50, t_ret_nom_C=43, t_room_C=20))
    net.connect("wp.out", "pu_p.in")
    net.connect("pu_p.out", "weiche.prim_in")
    net.connect("weiche.prim_out", "wp.in")
    net.connect("weiche.sec_out", "pu_s.in")
    net.connect("pu_s.out", "hk.in")
    net.connect("hk.out", "weiche.sec_in")
    r = net.solve()

    # Transferstrom in der vertikalen Kante: 0.5 m³/h von unten nach oben
    q_vert = r["weiche:vertikal"].q_m3h
    assert abs(q_vert) == pytest.approx(0.5, rel=1e-6)
    t_prim_vl = r["wp"].t_out_C
    t_sec_rl = r["hk"].t_out_C
    t_sec_vl_ref = (1.0 * t_prim_vl + 0.5 * t_sec_rl) / 1.5
    assert r["hk"].t_in_C == pytest.approx(t_sec_vl_ref, abs=1e-6)


def test_hydraulic_separator_balanced_no_transfer():
    net = h.Network()
    net.add(h.Pump("pu_p", mode="constant_flow", q_m3h=1.5))
    net.add(h.HeatPump("wp", mode="prescribed_q", q_dot_kW=5, q_nom_m3h=1.5))
    net.add(h.HydraulicSeparator("weiche"))
    net.add(h.Pump("pu_s", mode="constant_flow", q_m3h=1.5))
    net.add(h.Radiator("hk", q_nom_kW=6, t_sup_nom_C=50, t_ret_nom_C=43, t_room_C=20))
    net.connect("wp.out", "pu_p.in")
    net.connect("pu_p.out", "weiche.prim_in")
    net.connect("weiche.prim_out", "wp.in")
    net.connect("weiche.sec_out", "pu_s.in")
    net.connect("pu_s.out", "hk.in")
    net.connect("hk.out", "weiche.sec_in")
    r = net.solve()
    assert r["weiche:vertikal"].q_m3h == pytest.approx(0.0, abs=1e-6)


def test_buffer_storage_mixes_and_loses_heat():
    """Puffer als Mischknoten mit UA-Verlust: T = (ṁcp·T_zu + UA·T_amb)/(ṁcp + UA)."""
    net = h.Network()
    net.add(h.OpenEnd("zu", bc="flow", q_m3h=0.4, t_supply_C=60))
    net.add(h.OpenEnd("ab", bc="pressure", p_kPa=100))
    net.add(h.BufferStorage("puffer", n_ports=2, ua_W_K=15, t_amb_C=15))
    net.add(h.Pipe("r1", length_m=1, d_inner_mm=25))
    net.connect("zu.port", "puffer.p1")
    net.connect("puffer.p2", "r1.in")
    net.connect("r1.out", "ab.port")
    r = net.solve()
    c = 0.4 / 3600 * net.fluid.rho * net.fluid.cp
    t_ref = (c * 60 + 15 * 15) / (c + 15)
    assert r["r1"].t_in_C == pytest.approx(t_ref, abs=1e-9)


def test_global_energy_balance_closed_loop():
    net = _radiator_loop(0.15)
    r = net.solve()
    assert abs(r.energy_imbalance_W) < 1.0


def test_stagnant_branch_flagged():
    """Toter Strang (Ventil zu): Temperatur bleibt Startwert, Knoten markiert."""
    net = h.Network()
    net.add(h.Pump("pu", mode="constant_dp", dp_kPa=30, q_nom_m3h=1.0))
    net.add(h.HeatPump("wp", mode="target_t_out", t_out_set_C=45, q_nom_m3h=1.0))
    net.add(h.Radiator("hk", q_nom_kW=5, t_sup_nom_C=45, t_ret_nom_C=40))
    net.connect("wp.out", "pu.in")
    net.connect("pu.out", "hk.in")
    net.connect("hk.out", "wp.in")
    r = net.solve()
    assert r.converged


def test_emitters_accept_c_value():
    """Wärmeabgabesysteme mit C-Wert statt Kv: dp = C·V̇² exakt (dichteunabhängig)."""
    net = h.Network()
    net.add(h.Inflow("zu", t_set_C=70, q_m3h=2.0))
    net.add(h.Radiator("hk", q_prescribed_kW=4, c_Pa_m3h2=3000))
    net.add(h.Outflow("ab", p_kPa=0))
    net.connect("zu.port", "hk.in")
    net.connect("hk.out", "ab.port")
    r = net.solve()
    assert r["hk"].dp_kPa == pytest.approx(3000 * 2.0**2 / 1e3, rel=1e-6)


def test_floor_heating_with_c_value():
    """FBH mit konzentriertem C statt Rohrmodell (length entfällt dann)."""
    net = h.Network()
    net.add(h.Inflow("zu", t_set_C=35, q_m3h=0.3))
    net.add(h.FloorHeatingLoop("fbh", area_m2=15, k_W_m2K=6.0, t_room_C=21, c_Pa_m3h2=44000))
    net.add(h.Outflow("ab", p_kPa=0))
    net.connect("zu.port", "fbh.in")
    net.connect("fbh.out", "ab.port")
    r = net.solve()
    assert r["fbh"].dp_kPa == pytest.approx(44000 * 0.3**2 / 1e3, rel=1e-6)
    # Thermik unverändert (exponentielles Modell)
    m_dot = 0.3 / 3600 * net.fluid.rho
    t_ref = 21 + (35 - 21) * math.exp(-6.0 * 15 / (m_dot * net.fluid.cp))
    assert r["fbh"].t_out_C == pytest.approx(t_ref, abs=1e-9)


def test_coil_with_c_value():
    net = h.Network()
    net.add(h.Inflow("zu", t_set_C=6, q_m3h=1.0))
    net.add(h.CoolingCoil("kr", ua_W_K=800, m_dot_air_kg_s=1.2, t_air_in_C=28, c_Pa_m3h2=8000))
    net.add(h.Outflow("ab", p_kPa=0))
    net.connect("zu.port", "kr.in")
    net.connect("kr.out", "ab.port")
    r = net.solve()
    assert r["kr"].dp_kPa == pytest.approx(8.0, rel=1e-6)


def test_emitters_reject_kv_and_c_together():
    with pytest.raises(h.ComponentParamError):
        h.Radiator("hk", q_nom_kW=2, kv_m3h=2, c_Pa_m3h2=1000)
    with pytest.raises(h.ComponentParamError):
        h.HeatingCoil("hr", ua_W_K=500, m_dot_air_kg_s=1, t_air_in_C=20,
                      kv_m3h=2, c_Pa_m3h2=1000)
    with pytest.raises(h.ComponentParamError):
        h.FloorHeatingLoop("fbh", area_m2=10, length_m=50, c_Pa_m3h2=1000)  # beides
    with pytest.raises(h.ComponentParamError):
        h.FloorHeatingLoop("fbh", area_m2=10)                               # keines


def test_slow_recirculation_converges_beyond_sweep_limit():
    """Großes Rezirkulationsverhältnis (Bypass-Umlauf ≫ Zustrom) → Kontraktions-
    faktor nahe 1. Der Solver muss über max_iter_thermal hinaus weiterrechnen,
    solange der Fehler nachweislich fällt (Trendprüfung) — und darf das NICHT
    als Drift eines isolierten Umlaufs fehlklassifizieren."""
    import hydraulik as h
    doc = {
        "components": {
            "zu": {"type": "inflow", "t_set_C": 70, "q_m3h": 0.05},   # kleiner Zustrom
            "pu": {"type": "pump", "mode": "constant_flow", "q_m3h": 1.0},  # großer Umlauf
            "hk": {"type": "radiator", "q_nom_kW": 3, "t_sup_nom_C": 70,
                   "t_ret_nom_C": 55, "t_room_C": 20, "kv_m3h": 100},
            "ab": {"type": "outflow", "p_kPa": 150},
            "abz": {"type": "tee"},
        },
        "connections": [["zu.port", "pu.in"], ["pu.out", "hk.in"],
                        ["hk.out", "abz.a"], ["abz.b", "pu.in"], ["abz.c", "ab.port"]],
    }
    settings = h.load_settings({"settings": {"max_iter_thermal": 60}})
    r = h.load(doc).solve(settings)
    assert r.converged
    assert abs(r.energy_imbalance_W) < 1.0


def test_coil_partload_ua_correction():
    """Teillast-UA nach Gl. (4.2): halber Wasserstrom → UA·0.5^n; Ergebnis
    deckt sich mit der manuellen ε-NTU-Rechnung (Gegenstrom)."""
    import math
    import hydraulik as h

    def solve(q_m3h):
        doc = {
            "components": {
                "zu": {"type": "inflow", "t_set_C": 60.0, "q_m3h": q_m3h},
                "hr": {"type": "heating_coil", "ua_W_K": 800.0, "n": 0.4,
                       "q_w_ref_m3h": 2.0, "m_dot_air_kg_s": 1.2,
                       "t_air_in_C": 15.0, "arrangement": "counterflow"},
                "ab": {"type": "outflow", "p_kPa": 150},
            },
            "connections": [["zu.port", "hr.in"], ["hr.out", "ab.port"]],
        }
        net = h.load(doc)
        return net.solve(), net.fluid

    r, fluid = solve(1.0)                    # halber Referenzstrom
    ua_exp = 800.0 * 0.5 ** 0.4
    assert r["hr"].extras["ua_W_K"] == pytest.approx(ua_exp, rel=1e-9)
    # manuelle ε-NTU-Gegenprobe
    c_w = 1.0 / 3600 * fluid.rho * fluid.cp
    c_a = 1.2 * 1006.0
    c_min, c_max = min(c_w, c_a), max(c_w, c_a)
    ntu = ua_exp / c_min
    cr = c_min / c_max
    e = math.exp(-ntu * (1 - cr))
    eps = (1 - e) / (1 - cr * e)
    q_exp = eps * c_min * (15.0 - 60.0)      # Q̇ ins Wasser (negativ: Heizfall)
    assert r["hr"].q_dot_kW == pytest.approx(q_exp / 1e3, rel=1e-6)
    # Referenzstrom → exakt UA_ref
    r2, _ = solve(2.0)
    assert r2["hr"].extras["ua_W_K"] == pytest.approx(800.0, rel=1e-9)


def test_cooling_coil_greybox_wet_vs_skill_reference():
    """Greybox-Kühlregister gegen die Referenzvorhersage des Skills
    cooling-coil-greybox (FläktGroup H241611, kalibriert: UA_dry 2.959 kW/K,
    UA*_wet 2.293 kg/s, n 0.374; Punkt 24 °C/60 %, Wasser 8 °C, 4800 kg/h):
    Q = 27.59 kW, Kondensat 13.37 kg/h, Luftaustritt 11.84 °C (nass)."""
    import hydraulik as h
    rho = 999.9
    doc = {
        "fluid": {"rho": rho, "mu": 1.3e-3, "cp": 4186.0},
        "components": {
            "zu": {"type": "inflow", "t_set_C": 8.0, "q_m3h": 4800.0 / rho},
            "kr": {"type": "cooling_coil", "ua_W_K": 2958.78, "n": 0.3737,
                   "ua_star_wet_kg_s": 2.2928, "rh_air_in": 0.6,
                   "m_dot_air_kg_s": 1.4682, "m_dot_air_ref_kg_s": 1.4682,
                   "q_w_ref_m3h": 5.0, "t_air_in_C": 24.0},
            "ab": {"type": "outflow", "p_kPa": 150},
        },
        "connections": [["zu.port", "kr.in"], ["kr.out", "ab.port"]],
    }
    r = h.load(doc).solve()
    kr = r["kr"]
    assert kr.extras["betrieb"] == "nass"
    assert kr.q_dot_kW == pytest.approx(27.59, rel=0.015)          # Wärme INS Wasser
    assert kr.extras["kondensat_kg_h"] == pytest.approx(13.37, rel=0.05)
    assert kr.extras["t_luft_aus_C"] == pytest.approx(11.84, abs=0.2)
    assert abs(r.energy_imbalance_W) < 1.0


def test_cooling_coil_greybox_dry_regime_matches_sensible():
    """Trockene Luft (niedrige rF) → Greybox wählt das Trockenmodell und
    liefert exakt das sensible ε-NTU-Ergebnis; Kondensat = 0."""
    import hydraulik as h

    def doc(greybox):
        kr = {"type": "cooling_coil", "ua_W_K": 2000.0, "n": 0.4,
              "m_dot_air_kg_s": 1.5, "t_air_in_C": 26.0}
        if greybox:
            kr.update({"ua_star_wet_kg_s": 1.5, "rh_air_in": 0.15})
        return {"components": {
                    "zu": {"type": "inflow", "t_set_C": 16.0, "q_m3h": 2.0},
                    "kr": kr, "ab": {"type": "outflow", "p_kPa": 150}},
                "connections": [["zu.port", "kr.in"], ["kr.out", "ab.port"]]}

    r_dry = h.load(doc(True)).solve()
    r_ref = h.load(doc(False)).solve()
    assert r_dry["kr"].extras["betrieb"] == "trocken"
    assert r_dry["kr"].extras["kondensat_kg_h"] == 0.0
    assert r_dry["kr"].q_dot_kW == pytest.approx(r_ref["kr"].q_dot_kW, rel=1e-9)


def test_coil_q_prescribed_ohne_ua():
    """Feste Leistung braucht keinen UA-Wert; ganz ohne beides → klare Meldung."""
    import hydraulik as h
    doc = {"components": {
               "zu": {"type": "inflow", "t_set_C": 60.0, "q_m3h": 1.0},
               "hr": {"type": "heating_coil", "q_prescribed_kW": -10.0,
                      "m_dot_air_kg_s": 1.2, "t_air_in_C": 15.0},
               "ab": {"type": "outflow", "p_kPa": 150}},
           "connections": [["zu.port", "hr.in"], ["hr.out", "ab.port"]]}
    r = h.load(doc).solve()
    assert r["hr"].q_dot_kW == pytest.approx(-10.0, rel=1e-9)
    del doc["components"]["hr"]["q_prescribed_kW"]
    with pytest.raises(h.NetworkValidationError) as exc:
        h.load(doc)
    assert "ua_W_K" in str(exc.value)
