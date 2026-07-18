#!/usr/bin/env python3
"""High-level simulation driver for the VKA AHU tool.

THE one agent-facing function is `simulate(...)` — scalars OR vectors in, arrays
out. It runs the validated EN 16798-5-1 plant model and returns the heating /
cooling / humidifier loads, the achieved supply-air state and period energy
totals. Pass measured/assumed temperature + humidity directly; the tool does the
psychrometry with its own h,x model (do NOT use CoolProp).

    from .simulate import simulate

The other functions (simulate_point/series/weather, build_config) and the JSON
CLI remain as internal/optional helpers but are not the recommended entry point;
see SKILL.md.

Humidity units: in `simulate`, rel. humidity is PERCENT (e.g. 60, not 0.6) and
absolute humidity is g/kg; outputs carry the unit in the name (x_sup_gkg,
phi_sup_pct, *_kW). (Low-level run_plant returns x in kg/kg.)
"""
from __future__ import annotations
import sys, os, json, math, warnings
import numpy as np
from .vka_chain import Setpoints                                   # noqa: E402
from .vka_plant import PlantConfig, OperatingPoint, run_plant      # noqa: E402
from . import moist_air as ma                                            # noqa: E402

ROTORS = ("ROT_SORP", "ROT_HYG", "ROT_NH")


# ---------------------------------------------------------------- config build
def build_config(spec: dict) -> PlantConfig:
    """Map a friendly plant spec (dict) to a PlantConfig.

    The rotor always uses the energy-optimal control (Optimal Control) — it picks
    the speed that meets the supply setpoint at minimum heating/cooling energy.

    spec keys (all optional except 'wrg'):
      wrg            'ROT_SORP'|'ROT_HYG'|'ROT_NH'|'KVS'|'PLATE'
      components     subset of ['VHR','KR','NHR']  (default ['KR','NHR'])
      humidifier     'none'|'spray'|'steam'
      frost          'none'|'preheater'|'bypass'   (bypass needs a VHR)
      T_FS           frost limit [C]               (default -3.0)
      adiab_exhaust  bool   (only ROT_HYG/ROT_NH)
      recirculation_m3h  Umluft-Bypass flow [m3/h]
      SFP, f_rec, V_nom_m3h, eta_bef, T_H2O
      eta_hr_N, eta_xr_N         (rotor reference effectiveness, 0=norm)
      RWZ_N, V_M_KVS_N, V_WT_N   (KVS/plate design)
      order          explicit zul_order (else derived)
    """
    wrg = spec["wrg"]
    is_rotor = wrg in ROTORS
    comps = set(spec.get("components", ["KR", "NHR"]))
    hum = spec.get("humidifier", "none")
    frost = spec.get("frost", "none")
    bef_type = {"spray": 1, "steam": 2}.get(hum, 1)
    fs_type = {"preheater": 1, "bypass": 2}.get(frost, 1)
    has_VHR = "VHR" in comps or frost == "bypass"
    V_WT = float(spec.get("V_WT_N", spec.get("V_nom_m3h", 10000.0)))
    rwz = float(spec.get("RWZ_N", 0.76 if wrg == "KVS" else 0.6))
    cfg = PlantConfig(
        heat_rec_type=wrg if is_rotor else "ROT_NH",
        wrg="ROTOR" if is_rotor else wrg,
        eta_hr_N=float(spec.get("eta_hr_N", 0.0)),
        eta_xr_N=float(spec.get("eta_xr_N", 0.0)),
        SFP=float(spec.get("SFP", 1250.0)), f_rec=float(spec.get("f_rec", 0.6)),
        has_VHR=has_VHR, has_KR="KR" in comps, has_NHR="NHR" in comps,
        has_spray=hum != "none", bef_type=bef_type,
        has_FS=frost != "none", fs_type=fs_type, T_FS_Grenz=float(spec.get("T_FS", -3.0)),
        has_adiab_exhaust=bool(spec.get("adiab_exhaust", False)),
        has_uml=float(spec.get("recirculation_m3h", 0)) > 0,
        V_UML_m3h=float(spec.get("recirculation_m3h", 0)),
        exhaust_fan_before_wrg=(wrg == "KVS"),
        eta_bef=float(spec.get("eta_bef", 0.9)),
        T_H2O=float(spec.get("T_H2O", 100.0 if bef_type == 2 else 15.0)),
        V_nom_m3h=float(spec.get("V_nom_m3h", 10000.0)),
        V_M_KVS_N=float(spec.get("V_M_KVS_N", 2.5)),
        RWZ_KVS_ZUL_N=rwz, RWZ_KVS_ABL_N=rwz, V_ZUL_KVS_N=V_WT, V_ABL_KVS_N=V_WT,
        RWZ_WT_N=rwz, V_ZUL_WT_N=V_WT, V_ABL_WT_N=V_WT,
        zul_order=spec.get("order") or _order(wrg, has_VHR, comps, hum, frost,
                                              float(spec.get("recirculation_m3h", 0))))
    return cfg


def _order(wrg, has_VHR, comps, hum, frost, uml):
    o = ["Vent_ZUL"]
    if frost != "none":
        o.append("FS")
    o.append("WRG")
    if uml > 0:
        o.append("UML_Byp")
    if has_VHR:
        o.append("VHR")
    if hum == "steam":
        o.append("Bef")
    if "KR" in comps:
        o.append("KR")
    if hum == "spray":
        o.append("Bef")
    if "NHR" in comps:
        o.append("NHR")
    return o


def _sp(d):
    return Setpoints(float(d.get("T_min", 22)), float(d.get("T_max", 24)),
                     float(d.get("phi_min", 0.40)), float(d.get("phi_max", 0.55)), 1e5)


# ---------------------------------------------------------------- runs
def simulate_point(spec, setpoints, T_AUL, phi_AUL, T_ABL, phi_ABL,
                   V_sup_m3h, V_exh_m3h=None):
    cfg = build_config(spec); sp = _sp(setpoints)
    op = OperatingPoint(T_AUL, phi_AUL, T_ABL, phi_ABL, V_sup_m3h, V_exh_m3h)
    return run_plant(cfg, sp, [op])[0]


def simulate_series(spec, setpoints, records, room, V_sup_m3h, V_exh_m3h=None,
                    dt_h=1.0):
    """records: list of dict(time,T,RH) (outdoor). room: dict(T_ABL,phi_ABL) or
    a parallel list. Returns dict(steps=[...per-hour...], summary={...})."""
    cfg = build_config(spec); sp = _sp(setpoints)
    ops = []
    for i, r in enumerate(records):
        rm = room[i] if isinstance(room, list) else room
        ops.append(OperatingPoint(r["T"], r["RH"], float(rm["T_ABL"]),
                                  float(rm["phi_ABL"]), V_sup_m3h, V_exh_m3h))
    res = run_plant(cfg, sp, ops)
    steps = []
    for r, op, rr in zip(records, ops, res):
        steps.append(dict(time=r["time"], T_AUL=op.T_AUL, phi_AUL=op.phi_AUL,
                          T_sup=rr["T_sup"], x_sup=rr["x_sup"] * 1000,
                          phi_sup=rr["phi_sup"] * 100,
                          **{k: rr.get(k, 0.0) for k in
                             ("Q_FS", "Q_WRG", "Q_VHR", "Q_KR", "Q_Bef", "Q_NHR",
                              "m_dot_Bef")}))
    return dict(steps=steps, summary=_aggregate(steps, sp, spec, V_sup_m3h,
                                                V_exh_m3h or V_sup_m3h, dt_h))


def simulate(plant, T_AUL, hum_AUL, T_ABL, hum_ABL,
             T_sup_min, T_sup_max, hum_sup_min, hum_sup_max,
             V_sup_m3h, V_exh_m3h=None,
             humidity="rh", supply_band="phi", p_atm=1e5, dt_h=1.0):
    """THE one AHU-simulation function — scalars OR vectors in, arrays out.

    This is the only function an agent needs. It runs the validated EN 16798-5-1
    plant model. Do NOT compute humidity yourself (no CoolProp): pass measured
    temperature + humidity directly and read the results.

    INPUTS — each may be a scalar (constant) or a 1-D array (time series). Arrays
    are broadcast to the longest length N (so you can mix one constant setpoint
    with hourly weather, etc.).

      Outdoor air  AUL : T_AUL [°C], hum_AUL
      Room/exhaust ABL : T_ABL [°C], hum_ABL
        unit of hum_AUL/hum_ABL via ``humidity``:
          "rh" (default): relative humidity in PERCENT (e.g. 60)  ← sensors/weather
          "x"           : absolute humidity in g/kg (e.g. 8.5)
      Supply target BAND : T_sup_min/T_sup_max [°C] + hum_sup_min/hum_sup_max
        unit of the humidity band via ``supply_band``:
          "phi" (default): relative-humidity band in PERCENT (e.g. 40..55) —
                           model-native, exact.
          "x"            : absolute-humidity band in g/kg — APPROXIMATE (mapped to
                           a φ-band at T_mid); exact only when T_sup_min==T_sup_max.
      V_sup_m3h / V_exh_m3h : supply / exhaust volume flow [m³/h] (exhaust→supply
                              if omitted). A timestep with flow == 0 means the
                              plant is OFF: all loads are 0 and the supply state is
                              returned as NaN (no air delivered). Negative flow is
                              rejected.
      dt_h : hours per timestep (for the energy totals; default 1 h).

    plant : dict — which components are built (see SKILL.md), e.g.
      {"wrg":"ROT_SORP","components":["VHR","KR","NHR"],
       "humidifier":"steam","eta_hr_N":0.778,"eta_xr_N":0.807,"SFP":3430}

    Returns a dict of numpy arrays (one value per timestep), units in the names:
        T_sup_C, x_sup_gkg, phi_sup_pct,
        Q_heat_VHR_kW, Q_heat_NHR_kW, Q_heat_FS_kW, Q_heat_total_kW,
        Q_cool_KR_kW, Q_recovery_WRG_kW, Q_humid_Bef_kW, water_kg_h,
        eta_hr, eta_xr, n_rot, RWZ_KVS
    plus 'totals' (period sums): heating_kWh, cooling_kWh, recovery_kWh,
        humid_kWh, water_kg, hours (whole period), operating_hours (flow>0).

    Raises ValueError on an invalid setpoint band (e.g. φ_min>φ_max, φ out of
    range, T_min>T_max) instead of silently returning wrong numbers.
    """
    if humidity not in ("rh", "x"):
        raise ValueError(f"humidity must be 'rh' or 'x', got {humidity!r}")
    if supply_band not in ("phi", "x"):
        raise ValueError(f"supply_band must be 'phi' or 'x', got {supply_band!r}")
    cfg = build_config(plant)
    Ve_in = V_sup_m3h if V_exh_m3h is None else V_exh_m3h
    # volume flows broadcast like every other input (scalar OR per-timestep array)
    cols = [T_AUL, hum_AUL, T_ABL, hum_ABL, T_sup_min, T_sup_max,
            hum_sup_min, hum_sup_max, V_sup_m3h, Ve_in]
    n = max(np.atleast_1d(np.asarray(c, float)).size for c in cols)

    def arr(v):
        a = np.atleast_1d(np.asarray(v, float))
        return np.full(n, a[0]) if a.size == 1 else a
    Ta, ha, Tb, hb = arr(T_AUL), arr(hum_AUL), arr(T_ABL), arr(hum_ABL)
    Tlo, Thi = arr(T_sup_min), arr(T_sup_max)
    hlo, hhi = arr(hum_sup_min), arr(hum_sup_max)
    Vs, Ve = arr(V_sup_m3h), arr(Ve_in)

    # --- input validation (clear error beats silent garbage) ---------------
    if np.any(Tlo > Thi + 1e-9):
        raise ValueError("supply band: T_sup_min must be <= T_sup_max")
    if np.any(hlo > hhi + 1e-12):
        raise ValueError(f"supply band: hum_sup_min must be <= hum_sup_max "
                         f"(supply_band={supply_band!r})")
    if humidity == "rh" and (np.any(ha < 0) or np.any(ha > 100)
                             or np.any(hb < 0) or np.any(hb > 100)):
        raise ValueError("humidity='rh': hum_AUL/hum_ABL must be in 0..100 %")
    if humidity == "x" and (np.any(ha < 0) or np.any(hb < 0)):
        raise ValueError("humidity='x': hum_AUL/hum_ABL (g/kg) must be >= 0")
    if supply_band == "phi" and (np.any(hlo < 0) or np.any(hhi > 100)):
        raise ValueError("supply_band='phi': hum_sup_min/max must be in 0..100 %")
    if supply_band == "x" and np.any(hlo < 0):
        raise ValueError("supply_band='x': hum_sup_min/max (g/kg) must be >= 0")
    if np.any(Vs < 0) or np.any(Ve < 0):
        raise ValueError("V_sup_m3h / V_exh_m3h must be >= 0 "
                         "(0 = plant off for that timestep)")

    # AUL/ABL absolute humidity [kg/kg]
    x_aul = (ha * 1e-3) if humidity == "x" else ma.x(Ta, ha / 100.0, p_atm)
    x_abl = (hb * 1e-3) if humidity == "x" else ma.x(Tb, hb / 100.0, p_atm)

    # supply band -> phi_min/phi_max [fraction]
    if supply_band == "x":
        T_mid = 0.5 * (Tlo + Thi)
        phi_lo = np.asarray(ma.phi(T_mid, hlo * 1e-3, p_atm), float)
        phi_hi = np.asarray(ma.phi(T_mid, hhi * 1e-3, p_atm), float)
    else:
        phi_lo = np.asarray(hlo / 100.0, float)
        phi_hi = np.asarray(hhi / 100.0, float)

    # φ floor: φ_min ≈ 0 is unphysical → clamp to 1 % (with a warning).
    PHI_FLOOR = 0.01
    if np.any(phi_lo < PHI_FLOOR):
        warnings.warn(
            f"supply humidity: phi_min < {PHI_FLOOR*100:.0f}% is not physical; "
            f"clamped to {PHI_FLOOR*100:.0f}%.", stacklevel=2)
        phi_lo = np.maximum(phi_lo, PHI_FLOOR)
        phi_hi = np.maximum(phi_hi, phi_lo)
    # Near-saturation upper band drives the energy-optimal rotor to run slow —
    # this is FAITHFUL to the reference tool (verified vs MATLAB), not a bug.
    if np.any(phi_hi >= 0.98):
        warnings.warn(
            "supply humidity band reaches ~saturation (phi_max ~100%): the "
            "energy-optimal rotor control then runs slow / recovery drops (this "
            "matches the reference tool). To REPRODUCE a measured supply state, "
            "pin the humidity instead (hum_sup_min == hum_sup_max = measured); "
            "for comfort design use a realistic band (e.g. 40..60 %).",
            stacklevel=2)

    const_band = all(np.ptp(a) == 0 for a in (Tlo, Thi, phi_lo, phi_hi))

    # V_dot == 0 → plant OFF for that timestep: no airflow, every load is 0 and the
    # supply state is undefined (NaN — no air is delivered). Only the active hours
    # are actually simulated; off hours are skipped (no division by zero flow).
    on = (Vs > 0) & (Ve > 0)
    OFF = dict(T_sup=np.nan, x_sup_gkg=np.nan, phi_sup_pct=np.nan,
               m_water_kg_h=0.0, eta_hr=np.nan, eta_xr=np.nan, n_rot=0.0,
               RWZ_KVS_ges=np.nan)  # Q_* keys absent → col() reads them as 0.0

    def op_i(i):
        return OperatingPoint(float(Ta[i]), 0.0, float(Tb[i]), 0.0,
                              float(Vs[i]), float(Ve[i]),
                              x_AUL=float(x_aul[i]), x_ABL=float(x_abl[i]))
    on_idx = [i for i in range(n) if on[i]]
    res = [dict(OFF) for _ in range(n)]
    if const_band:
        if on_idx:
            sp = Setpoints(float(Tlo[0]), float(Thi[0]), float(phi_lo[0]),
                           float(phi_hi[0]), p_atm)
            rr = run_plant(cfg, sp, [op_i(i) for i in on_idx])
            for k, i in enumerate(on_idx):
                res[i] = rr[k]
    else:
        for i in on_idx:
            sp = Setpoints(float(Tlo[i]), float(Thi[i]), float(phi_lo[i]),
                           float(phi_hi[i]), p_atm)
            res[i] = run_plant(cfg, sp, [op_i(i)])[0]

    def col(fn):
        return np.array([fn(r) for r in res], float)
    out = dict(
        T_sup_C=col(lambda r: r["T_sup"]),
        x_sup_gkg=col(lambda r: r["x_sup_gkg"]),
        phi_sup_pct=col(lambda r: r["phi_sup_pct"]),
        Q_heat_VHR_kW=col(lambda r: r.get("Q_VHR", 0.0)),
        Q_heat_NHR_kW=col(lambda r: r.get("Q_NHR", 0.0)),
        Q_heat_FS_kW=col(lambda r: r.get("Q_FS", 0.0)),
        Q_heat_total_kW=col(lambda r: r.get("Q_VHR", 0.0) + r.get("Q_NHR", 0.0)
                            + r.get("Q_FS", 0.0)),
        Q_cool_KR_kW=col(lambda r: r.get("Q_KR", 0.0)),
        Q_recovery_WRG_kW=col(lambda r: r.get("Q_WRG", 0.0)),
        Q_humid_Bef_kW=col(lambda r: r.get("Q_Bef", 0.0)),
        water_kg_h=col(lambda r: r["m_water_kg_h"]),
        eta_hr=col(lambda r: r["eta_hr"]), eta_xr=col(lambda r: r["eta_xr"]),
        n_rot=col(lambda r: r["n_rot"]),
        RWZ_KVS=col(lambda r: r["RWZ_KVS_ges"]))
    # Stationszustände (Zustand NACH jedem order-Token) für Schema-Anzeigen;
    # Zusatzausgabe ohne Einfluss auf die validierten Größen oben.
    out["chain"] = [{"T": r.get("chain_T"), "x_gkg": r.get("chain_x_gkg")}
                    for r in res]
    out["T_eta_wheel_C"] = col(lambda r: r.get("T_eta_wheel", np.nan))
    out["x_eta_wheel_gkg"] = col(lambda r: r.get("x_eta_wheel", np.nan) * 1000.0)
    out["totals"] = dict(
        heating_kWh=float(np.nansum(out["Q_heat_total_kW"]) * dt_h),
        cooling_kWh=float(np.nansum(out["Q_cool_KR_kW"]) * dt_h),
        recovery_kWh=float(np.nansum(out["Q_recovery_WRG_kW"]) * dt_h),
        humid_kWh=float(np.nansum(out["Q_humid_Bef_kW"]) * dt_h),
        water_kg=float(np.nansum(out["water_kg_h"]) * dt_h),
        hours=float(n * dt_h),
        operating_hours=float(len(on_idx) * dt_h))
    return out


# Backwards-compatible alias (internal callers/tests); the SKILL documents only
# `simulate`.
simulate_vectors = simulate


def simulate_weather(spec, setpoints, lat, lon, start, end, room,
                     V_sup_m3h, V_exh_m3h=None, source="open-meteo",
                     tz="Europe/Vienna"):
    from weather import fetch
    rec = fetch(lat, lon, start, end, source, tz)
    out = simulate_series(spec, setpoints, rec, room, V_sup_m3h, V_exh_m3h)
    out["weather"] = dict(source=source, lat=lat, lon=lon, start=start, end=end,
                          n=len(rec))
    return out


# ---------------------------------------------------------------- aggregation
def _aggregate(steps, sp, spec, V_sup, V_exh, dt_h):
    n = len(steps)
    def s(k):
        return sum(st[k] for st in steps)
    E = {c: round(s("Q_" + c) * dt_h, 2) for c in ("FS", "VHR", "NHR", "KR", "WRG", "Bef")}
    heating = round((E["FS"] + E["VHR"] + E["NHR"]) , 2)
    SFP = float(spec.get("SFP", 1250.0))
    fan_kWh = round((SFP / 1000.0) * (V_sup / 3600.0 + V_exh / 3600.0) * n * dt_h, 2)
    water_l = round(s("m_dot_Bef") * 3600.0 * dt_h, 1)   # ~1 kg = 1 liter
    in_T = sum(1 for st in steps if sp.T_min - 1e-3 <= st["T_sup"] <= sp.T_max + 1e-3)
    in_p = sum(1 for st in steps
               if sp.phi_min * 100 - 0.5 <= st["phi_sup"] <= sp.phi_max * 100 + 0.5)
    in_band = sum(1 for st in steps
                  if sp.T_min - 1e-3 <= st["T_sup"] <= sp.T_max + 1e-3
                  and sp.phi_min * 100 - 0.5 <= st["phi_sup"] <= sp.phi_max * 100 + 0.5)
    return dict(
        n_steps=n, hours=round(n * dt_h, 1),
        energy_kWh=dict(heating_total=heating, heating_FS=E["FS"],
                        heating_VHR=E["VHR"], heating_NHR=E["NHR"],
                        cooling_KR=E["KR"], recovery_WRG=E["WRG"],
                        humidifier_Bef=E["Bef"], fan_electrical=fan_kWh),
        humidifier_water_liter=water_l,
        peak_kW=dict((c, round(max((st["Q_" + c] for st in steps), default=0.0), 2))
                     for c in ("FS", "VHR", "NHR", "KR")),
        comfort=dict(hours_T_in_band=in_T, hours_phi_in_band=in_p,
                     hours_both_in_band=in_band,
                     pct_both_in_band=round(100.0 * in_band / n, 1) if n else 0.0),
        supply=dict(T_mean=round(s("T_sup") / n, 2) if n else 0,
                    T_min=round(min((st["T_sup"] for st in steps), default=0), 2),
                    T_max=round(max((st["T_sup"] for st in steps), default=0), 2),
                    phi_mean=round(s("phi_sup") / n, 1) if n else 0))


# ---------------------------------------------------------------- CLI
def _run_config(cfg):
    plant = cfg["plant"]; setp = cfg.get("setpoints", {})
    flows = cfg.get("flows", {})
    Vs = float(flows.get("V_sup_m3h", 10000)); Ve = flows.get("V_exh_m3h")
    Ve = float(Ve) if Ve is not None else None
    if "point" in cfg:                       # single operating point
        p = cfg["point"]; rm = cfg.get("room", {"T_ABL": 24, "phi_ABL": 55})
        r = simulate_point(plant, setp, p["T_AUL"], p["phi_AUL"],
                           rm["T_ABL"], rm["phi_ABL"], Vs, Ve)
        return {"point": {k: (v if not isinstance(v, dict) else v) for k, v in r.items()
                          if k not in ("chain_T", "chain_x")},
                "chain_T": r["chain_T"], "chain_x": {k: v * 1000 for k, v in r["chain_x"].items()}}
    loc = cfg["location"]; per = cfg["period"]
    rm = cfg.get("room", {"T_ABL": 24, "phi_ABL": 55})
    out = simulate_weather(plant, setp, loc["lat"], loc["lon"], per["start"],
                           per["end"], rm, Vs, Ve,
                           per.get("source", "open-meteo"),
                           per.get("tz", "Europe/Vienna"))
    return out


def main():
    if len(sys.argv) < 2:
        print("usage: python simulate.py <config.json> [--csv hourly.csv] [--out summary.json]")
        sys.exit(1)
    cfg = json.load(open(sys.argv[1]))
    out = _clean(_run_config(cfg))
    csv_path = _arg("--csv"); out_path = _arg("--out")
    if "steps" in out and csv_path:
        with open(csv_path, "w") as f:
            cols = list(out["steps"][0].keys())
            f.write(",".join(cols) + "\n")
            for st in out["steps"]:
                f.write(",".join(f"{st[c]:.5g}" if isinstance(st[c], float) else str(st[c])
                                 for c in cols) + "\n")
        print(f"hourly CSV -> {csv_path} ({len(out['steps'])} rows)")
    summary = {k: v for k, v in out.items() if k != "steps"}
    txt = json.dumps(summary, indent=2, ensure_ascii=False)
    if out_path:
        open(out_path, "w").write(txt)
        print(f"summary -> {out_path}")
    else:
        print(txt)


def _arg(flag):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else None


def _clean(o):
    """Recursively replace NaN/Inf with None so the output is valid JSON."""
    if isinstance(o, float):
        return None if (math.isnan(o) or math.isinf(o)) else o
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_clean(v) for v in o]
    return o


if __name__ == "__main__":
    main()
