#!/usr/bin/env python3
"""Rotary heat-recovery wheel — faithful port of heat_rec_wheel_calc_V5.m.

EN 16798-5-1 / IDA-ICE-derived rotor model, VKA-tool parametrization.
Covers the three rotor types used by the Energetikum VKA tool:
    'ROT_SORP'  sorption / enthalpy wheel   (high moisture transfer)
    'ROT_HYG'   hygroscopic / enthalpy wheel
    'ROT_NH'    condensation / non-hygroscopic wheel

The KVS (run-around coil) and PLATE (plate exchanger) branches of the original
tool are intentionally omitted — this skill targets rotor-based AHUs only.

Speed control: energy-optimal (Optimal Control) — `energy_control` picks the
rotor speed that meets the T- and x-setpoint bands at minimum heating/cooling
energy. (This is the only control the tool exposes.)

This module uses the h,x moist-air model from moist_air.py (NOT CoolProp), to
stay bit-faithful to the MATLAB reference. All temperatures °C, humidity ratios
kg/kg, pressures Pa, enthalpies kJ/kg (dry-air basis).
"""

from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from .moist_air import h as hx, T_from_hx, Ts, xs, ps as ps_moist

# =============================================================================
# Rotor constants (PARAMETER block of heat_rec_wheel_calc_V5.m)
# =============================================================================
_ROTOR_CONSTANTS = {
    "ROT_SORP": dict(eta_hr_nom=0.69, v_hr_nom=3.5, eta_xr_nom=0.69,
                     C1=-0.0665, C2=1.0, C3=1.0182, C4=0.0352, C5=0.276,
                     C6=16.4, C7=0.918, C8=0.0, C9=0.1, C10=-0.098, C11=1.0,
                     C12=1.0533, C13=80000.0, C14=15.0, e1=-2.7, e2=-4.0,
                     dx_e_nom=0.005),
    "ROT_HYG":  dict(eta_hr_nom=0.67, v_hr_nom=3.5, eta_xr_nom=0.42,
                     C1=-0.0684, C2=1.0, C3=1.0182, C4=0.0352, C5=0.276,
                     C6=129.0, C7=0.476, C8=23.8, C9=0.1, C10=-0.152, C11=1.0,
                     C12=1.0533, C13=80000.0, C14=15.0, e1=-2.7, e2=-4.0,
                     dx_e_nom=0.005),
    "ROT_NH":   dict(eta_hr_nom=0.69, v_hr_nom=3.5, eta_xr_nom=0.3,
                     C1=-0.0643, C2=1.0, C3=1.0182, C4=0.0352, C5=0.276,
                     C6=248.0, C7=-0.24, C8=0.0, C9=0.1, C10=-0.2, C11=1.0,
                     C12=1.0533, C13=80000.0, C14=15.0, e1=-2.7, e2=-4.0,
                     dx_e_nom=0.005),
}

# Fan-location factor f_ODA_hr (only the rotor-relevant combinations kept)
_FODA = {
    ("UP_HR", "UP_HR"): 1.0,
    ("UP_HR", "DOWN_HR"): 1.0,
    ("DOWN_HR", "UP_HR"): 0.909,
    ("DOWN_HR", "DOWN_HR"): 0.98,
}


@dataclass
class WheelResult:
    eta_hr: float          # actual sensible effectiveness (at given n_rot)
    eta_xr: float          # actual moisture effectiveness (at given n_rot)
    T_sup_out: float       # supply outlet temp after wheel [°C]
    x_sup_out: float       # supply outlet humidity ratio [kg/kg]
    T_eta_out: float       # exhaust outlet temp [°C]
    x_eta_out: float       # exhaust outlet humidity ratio [kg/kg]
    n_rot: float           # operating speed [rpm]
    # energy-optimized speed (the *_new fields from MATLAB)
    eta_hr_new: float
    eta_xr_new: float
    n_rot_new: float
    f_n_new: float
    f_n_x_new: float


def _eff_factors(c, q_V_SUP, q_V_ETA, v_hr_eff, n_rot, n_rot_max, f_ODA_min,
                 v_hr_N_para, T_e, T_ETA_dis_out, T_ETA_hr_in, x_ETA_hr_in,
                 T_ODA_preh, x_ODA_preh, q_V_ETA_ahu, q_V_SUP_ahu, p_atm,
                 heat_rec_type):
    """Compute the sensible and latent effectiveness correction factors.

    Faithful port of the factor block (lines ~115-230 of the .m file) for a
    single operating point (scalars). Returns a dict of the factors plus the
    base effectivenesses at the *given* n_rot.
    """
    eta_hr_nom = c["eta_hr_nom"]; eta_xr_nom = c["eta_xr_nom"]
    v_hr_nom = c["v_hr_nom"]

    # --- sensible: f_q (mass/volume-flow unbalance) -------------------------
    if q_V_SUP_ahu == 0 or q_V_ETA_ahu == 0:
        f_q = 0.0
    else:
        f_q = max(0.0, ((q_V_ETA_ahu - q_V_SUP_ahu) / (q_V_SUP_ahu * f_ODA_min) + 1.0)) ** 0.4

    # --- sensible: f_v (face velocity) --------------------------------------
    if v_hr_eff == 0:
        f_v = 0.0
    else:
        f_v = max(0.0, c["C1"] * (v_hr_eff * v_hr_N_para * f_ODA_min - v_hr_nom) + c["C2"])

    # --- sensible: f_n (rotation speed) -------------------------------------
    if n_rot == 0:
        f_n = 0.0
    else:
        f_n = max(0.0, c["C3"] - c["C4"] * (n_rot / n_rot_max + c["C5"]) ** c["e1"])

    # Physikalische Grenze (Abweichung vom MATLAB-Original): die empirischen
    # Korrekturfaktoren (insb. f_q bei stark unbalancierten Volumenströmen)
    # können ε > 1 liefern — die Zuluft würde wärmer als die Abluftquelle
    # (2. Hauptsatz). Übertragungsgrade daher auf ≤ 1 begrenzen.
    eta_hr = min(1.0, eta_hr_nom * f_q * f_v * f_n)

    # --- latent: condensation potential -------------------------------------
    # choose evap/cond streams by sign of (T_e - T_ETA_dis_out)
    if T_e > T_ETA_dis_out:
        T_calc_evap = T_ETA_hr_in
        x_calc_cond = x_ODA_preh
        q_calc_evap = q_V_ETA_ahu
        q_calc_cond = q_V_SUP_ahu
    else:
        T_calc_evap = T_ODA_preh
        x_calc_cond = x_ETA_hr_in
        q_calc_evap = q_V_SUP_ahu
        q_calc_cond = q_V_ETA_ahu

    # saturation humidity of the evaporating-side air (uses the 611.2/17.62
    # Magnus form from the .m, NOT the split ps — kept verbatim)
    p_e_sat = 611.2 * np.exp(17.62 * T_calc_evap / (243.12 + T_calc_evap))
    x_e_sat = 0.622 * p_e_sat / (p_atm - p_e_sat)

    if heat_rec_type in ("ROT_SORP", "ROT_NH"):
        f_dx_x = min(1.0, max(0.0, c["C6"] * (x_calc_cond - x_e_sat) + c["C7"]))
    else:  # ROT_HYG
        f_dx_x = min(1.0, max(0.0,
                              1.0 + c["C6"] * (x_calc_cond - x_e_sat - c["dx_e_nom"]),
                              c["C7"] + c["C8"] * (x_calc_cond - x_e_sat - c["dx_e_nom"])))

    # --- latent: f_q_x (flow unbalance) -------------------------------------
    if q_calc_evap == 0 or q_calc_cond == 0:
        f_q_x = 0.0
    else:
        f_q_x = max(0.0, 1.0 - c["C9"] * ((q_calc_cond - q_calc_evap) / (q_calc_evap * f_ODA_min)))

    # --- latent: f_v_x (face velocity) --------------------------------------
    if v_hr_eff == 0:
        f_v_x = 0.0
    else:
        f_v_x = max(0.0, c["C10"] * (v_hr_eff * v_hr_N_para * f_ODA_min - v_hr_nom) + c["C11"])

    # --- latent: f_n_x (rotation speed) -------------------------------------
    if n_rot == n_rot_max:
        f_n_x = 1.0
    elif n_rot == 0:
        f_n_x = 0.0
    else:
        f_n_x = max(0.0, c["C12"] - c["C13"] * ((n_rot / n_rot_max) * 20.0 + c["C14"]) ** c["e2"])

    eta_xr = min(1.0, eta_xr_nom * f_dx_x * f_q_x * f_v_x * f_n_x)

    return dict(f_q=f_q, f_v=f_v, f_n=f_n, eta_hr=eta_hr,
                f_dx_x=f_dx_x, f_q_x=f_q_x, f_v_x=f_v_x, f_n_x=f_n_x, eta_xr=eta_xr)


def energy_control(c, n_rot_max, f_q, f_v, f_dx_x, f_q_x, f_v_x,
                   T_ODA_preh, x_ODA_preh, T_ETA_hr_in, x_ETA_hr_in,
                   T_SUP_hr_req_min, T_SUP_hr_req_max,
                   x_SUP_hr_req_min_Tmin, x_SUP_hr_req_max_Tmin,
                   x_SUP_hr_req_min_Tmax, x_SUP_hr_req_max_Tmax,
                   h_ZUL_Soll_phi_min_T_min, h_ZUL_Soll_phi_min_T_max,
                   Bef_n_WRG, Bef_ZUL, h_H2O, KR_n_WRG, KR_Entf,
                   dT_VentuKanal_ZUL, p_atm):
    """Energy-optimized rotor speed control (WRG_Ctrl='Energy').

    Faithful port of the 'Energy' branch of heat_rec_wheel_calc_V5.m. Sweeps
    1001 rotor speeds, computes the supply outlet (T,x) at each, and selects
    the speed that satisfies the T- and x-setpoint bands at minimum downstream
    energy. Returns (eta_hr_new, eta_xr_new, n_rot_new, f_n_new, f_n_x_new).
    """
    from .matlab_find import (find_first, find_last, find_all,
                             argmax_last, argmax_first, argmin_last, argmin_first)

    # 1001-point speed grid (0 : n_rot_max/1000 : n_rot_max)
    n_rot_opt = np.linspace(0.0, n_rot_max, 1001)
    f_n_opt = np.maximum(0.0, c["C3"] - c["C4"] * (n_rot_opt / n_rot_max + c["C5"]) ** c["e1"])
    f_n_x_opt = np.maximum(0.0, c["C12"] - c["C13"] * ((n_rot_opt / n_rot_max) * 20.0 + c["C14"]) ** c["e2"])
    # physikalische Grenze ε ≤ 1 (wie in _eff_factors; s. Kommentar dort)
    eta_hr_opt = np.minimum(1.0, c["eta_hr_nom"] * f_q * f_v * f_n_opt)
    eta_xr_opt = np.minimum(1.0, c["eta_xr_nom"] * f_dx_x * f_q_x * f_v_x * f_n_x_opt)

    T_out = T_ODA_preh + eta_hr_opt * (T_ETA_hr_in - T_ODA_preh)
    x_out = x_ODA_preh + eta_xr_opt * (x_ETA_hr_in - x_ODA_preh)

    def pack(idx):
        return (eta_hr_opt[idx], eta_xr_opt[idx], n_rot_opt[idx],
                f_n_opt[idx], f_n_x_opt[idx])

    def pack_off():
        return (0.0, 0.0, 0.0, 0.0, 0.0)

    # ----------------------------------------------------------------- Case 1
    # Spray humidifier downstream of wheel (Bef_ZUL==1), outdoor too dry
    if Bef_n_WRG == 1 and Bef_ZUL == 1 and x_ODA_preh < x_SUP_hr_req_min_Tmin:
        dx_Bef_min = x_SUP_hr_req_min_Tmin - x_out
        dx_Bef_max = x_SUP_hr_req_min_Tmax - x_out
        T_vBef_min = T_from_hx(h_ZUL_Soll_phi_min_T_min - h_H2O * dx_Bef_min, x_out) + dT_VentuKanal_ZUL
        T_vBef_max = T_from_hx(h_ZUL_Soll_phi_min_T_max - h_H2O * dx_Bef_max, x_out) + dT_VentuKanal_ZUL

        imax = argmax_last(T_out); imin = argmin_last(T_out)
        if T_ODA_preh < T_vBef_min[0] and np.max(T_out) > T_vBef_min[imax]:
            if np.max(T_out) <= T_vBef_max[imax]:
                idx = argmax_last(T_out)
            else:
                f = find_first(T_out > T_vBef_max)
                idx = (f - 1) if f is not None else None
        elif T_ODA_preh < T_vBef_min[0] and np.max(T_out) <= T_vBef_min[imax]:
            idx = argmax_last(T_out)
        elif T_ODA_preh > T_vBef_max[0] and np.min(T_out) < T_vBef_max[imin]:
            if np.max(T_out) >= T_vBef_min[imin]:
                idx = argmin_last(T_out)
            else:
                f = find_first(T_out < T_vBef_min)
                idx = (f - 1) if f is not None else None
        elif T_ODA_preh > T_vBef_max[0] and np.min(T_out) >= T_vBef_max[imin]:
            idx = argmin_first(T_out)
        else:
            idx = find_last((T_out <= T_vBef_max) & (T_out >= T_vBef_min))
        return pack(idx) if idx is not None else pack_off()

    # ----------------------------------------------------------------- Case 2
    # Humidifier type 2 downstream, outdoor too dry (energy check incl. dehumid)
    if Bef_n_WRG == 1 and Bef_ZUL == 2 and x_ODA_preh < x_SUP_hr_req_min_Tmin:
        dx_Bef_min = x_SUP_hr_req_min_Tmin - x_out
        dx_Bef_max = x_SUP_hr_req_min_Tmax - x_out
        T_vBef_min = T_from_hx(h_ZUL_Soll_phi_min_T_min - h_H2O * dx_Bef_min, x_out) + dT_VentuKanal_ZUL
        T_vBef_max = T_from_hx(h_ZUL_Soll_phi_min_T_max - h_H2O * dx_Bef_max, x_out) + dT_VentuKanal_ZUL

        h_Entf = np.zeros_like(x_out)
        m_dehum = x_out > x_SUP_hr_req_max_Tmin
        h_Entf[m_dehum] = hx(T_out[m_dehum], x_out[m_dehum])

        dT = np.maximum(T_vBef_min - T_out, T_out - T_vBef_max)
        dT[m_dehum] = np.maximum(T_SUP_hr_req_min - T_out[m_dehum], T_out[m_dehum] - T_SUP_hr_req_max)

        if np.max(x_out) > x_SUP_hr_req_max_Tmax:
            h_KR_out = hx(Ts(x_SUP_hr_req_max_Tmax, p_atm), x_SUP_hr_req_max_Tmax)
        else:
            h_KR_out = h_Entf

        en_check_max = np.maximum(0.0, dx_Bef_max * h_H2O) + 1.0 * np.maximum(0.0, dT) + np.maximum(0.0, h_Entf - h_KR_out)
        en_check_max[en_check_max < 0] = 0.0

        imax = argmax_last(T_out); imin = argmin_last(T_out)
        if T_ODA_preh < T_vBef_min[0] and np.max(T_out) > T_vBef_min[imax]:
            idx = argmin_first(en_check_max)
            if KR_n_WRG == 0:
                if T_SUP_hr_req_min == T_SUP_hr_req_max and x_SUP_hr_req_min_Tmin == x_SUP_hr_req_max_Tmax:
                    f = find_first(T_out > T_vBef_min); idx = (f - 1) if f is not None else None
                else:
                    idx = find_first(T_out >= T_vBef_min)
        elif T_ODA_preh < T_vBef_min[0] and np.max(T_out) <= T_vBef_min[imax]:
            idx = argmin_first(en_check_max)
            if KR_n_WRG == 0:
                idx = argmax_last(T_out)
        elif T_ODA_preh > T_vBef_max[0] and np.min(T_out) < T_vBef_max[imin]:
            idx = argmin_first(en_check_max)
            if KR_n_WRG == 0:
                if T_SUP_hr_req_min == T_SUP_hr_req_max and x_SUP_hr_req_min_Tmin == x_SUP_hr_req_max_Tmax:
                    f = find_first(T_out < T_vBef_max); idx = (f - 1) if f is not None else None
                else:
                    idx = find_first(T_out <= T_vBef_max)
        elif T_ODA_preh > T_vBef_max[0] and np.min(T_out) >= T_vBef_max[imin]:
            idx = argmin_first(en_check_max)
            if KR_n_WRG == 0:
                idx = argmin_last(T_out)
        else:
            if np.max(x_out) < x_SUP_hr_req_min_Tmax:
                idx = len(x_out) - 1
            elif np.max(x_out) > x_SUP_hr_req_min_Tmax:
                idx = find_last(x_out <= x_SUP_hr_req_min_Tmax)
            else:
                idx = argmin_first(en_check_max)
        return pack(idx) if idx is not None else pack_off()

    # ----------------------------------------------------------------- Case 3
    # Outdoor too humid, cooling coil dehumidifies
    if x_ODA_preh > x_SUP_hr_req_max_Tmax and KR_n_WRG == 1 and KR_Entf == 1:
        if np.min(x_out) > x_SUP_hr_req_max_Tmax:
            if np.min(x_out) - 0.03 / 1000 > x_SUP_hr_req_max_Tmax:
                h_all = hx(T_out, x_out)
                idx = argmin_first(h_all)
            else:
                idx = argmin_first(x_out)
        else:
            idx = find_first(x_out <= x_SUP_hr_req_max_Tmax)
        return pack(idx) if idx is not None else pack_off()

    # ----------------------------------------------------------------- Case 4
    # Default: temperature-band control with humidity side-constraint
    dTk = dT_VentuKanal_ZUL
    def x_in_band(lo, hi):
        return find_all((x_out <= hi) & (x_out >= lo))

    idx = None  # None -> off (MATLAB index_opt=0 sentinel)
    if np.max(T_out) > (T_SUP_hr_req_min + dTk) and T_ODA_preh < T_SUP_hr_req_min:
        f = find_first(T_out >= (T_SUP_hr_req_min + dTk))
        xf = x_out[f] if f is not None else np.nan
        if xf > x_SUP_hr_req_max_Tmin or xf < x_SUP_hr_req_min_Tmin:
            if x_ODA_preh > x_SUP_hr_req_max_Tmax or x_ODA_preh < x_SUP_hr_req_min_Tmin:
                idx = find_first(T_out >= (T_SUP_hr_req_min + dTk))
            else:
                nn = x_in_band(x_SUP_hr_req_min_Tmin, x_SUP_hr_req_max_Tmin)
                sub = nn[T_out[nn] >= (T_SUP_hr_req_min + dTk)] if nn.size else nn
                idx = int(np.min(sub)) if sub.size else None
        else:
            idx = find_first(T_out >= (T_SUP_hr_req_min + dTk))
    elif np.max(T_out) < (T_SUP_hr_req_min + dTk) and T_ODA_preh < T_SUP_hr_req_min:
        imax = argmax_first(T_out)
        xf = x_out[imax]
        if xf > x_SUP_hr_req_max_Tmin or xf < x_SUP_hr_req_min_Tmin:
            if x_ODA_preh > x_SUP_hr_req_max_Tmax or x_ODA_preh < x_SUP_hr_req_min_Tmin:
                idx = argmax_first(T_out)
            else:
                nn = x_in_band(x_SUP_hr_req_min_Tmin, x_SUP_hr_req_max_Tmin)
                sub = nn[T_out[nn] == np.max(T_out[nn])] if nn.size else nn
                idx = int(np.min(sub)) if sub.size else None
        else:
            idx = argmax_first(T_out)
    elif np.min(T_out) < (T_SUP_hr_req_max + dTk) and T_ODA_preh > T_SUP_hr_req_max:
        f = find_first(T_out <= (T_SUP_hr_req_max + dTk))
        xf = x_out[f] if f is not None else np.nan
        if xf > x_SUP_hr_req_max_Tmax or xf < x_SUP_hr_req_min_Tmax:
            if x_ODA_preh > x_SUP_hr_req_max_Tmax or x_ODA_preh < x_SUP_hr_req_min_Tmin:
                idx = find_first(T_out <= (T_SUP_hr_req_max + dTk))
            else:
                nn = x_in_band(x_SUP_hr_req_min_Tmax, x_SUP_hr_req_max_Tmax)
                sub = nn[T_out[nn] <= (T_SUP_hr_req_max + dTk)] if nn.size else nn
                idx = int(np.min(sub)) if sub.size else None
        else:
            idx = find_first(T_out <= (T_SUP_hr_req_max + dTk))
    elif np.min(T_out) > (T_SUP_hr_req_max + dTk) and T_ODA_preh > T_SUP_hr_req_max:
        imin = argmin_first(T_out)
        xf = x_out[imin]
        if xf > x_SUP_hr_req_max_Tmax or xf < x_SUP_hr_req_min_Tmax:
            if x_ODA_preh > x_SUP_hr_req_max_Tmax or x_ODA_preh < x_SUP_hr_req_min_Tmin:
                idx = argmin_first(T_out)
            else:
                nn = x_in_band(x_SUP_hr_req_min_Tmax, x_SUP_hr_req_max_Tmax)
                sub = nn[T_out[nn] == np.min(T_out[nn])] if nn.size else nn
                idx = int(np.min(sub)) if sub.size else None
        else:
            idx = argmin_first(T_out)
    elif T_ODA_preh > (T_SUP_hr_req_max + dTk) and T_ETA_hr_in > (T_SUP_hr_req_max + dTk):
        if T_ETA_hr_in < T_ODA_preh:
            imin = argmin_first(T_out)
            xf = x_out[imin]
            if xf > x_SUP_hr_req_max_Tmax or xf < x_SUP_hr_req_min_Tmax:
                if x_ODA_preh > x_SUP_hr_req_max_Tmax or x_ODA_preh < x_SUP_hr_req_min_Tmin:
                    idx = argmin_first(T_out)
                else:
                    nn = x_in_band(x_SUP_hr_req_min_Tmax, x_SUP_hr_req_max_Tmax)
                    sub = nn[T_out[nn] == np.min(T_out[nn])] if nn.size else nn
                    idx = int(np.min(sub)) if sub.size else None
            else:
                idx = argmin_first(T_out)
        else:
            idx = None
    else:
        idx = None

    if idx is None:
        return pack_off()
    return pack(idx)


if __name__ == "__main__":
    # smoke test: factors for a sorption wheel, balanced, full speed
    c = _ROTOR_CONSTANTS["ROT_SORP"]
    f = _eff_factors(c, q_V_SUP=10000, q_V_ETA=10000, v_hr_eff=1.0, n_rot=20,
                     n_rot_max=20, f_ODA_min=1.0, v_hr_N_para=c["v_hr_nom"],
                     T_e=33.0, T_ETA_dis_out=24.0, T_ETA_hr_in=24.0,
                     x_ETA_hr_in=0.0103, T_ODA_preh=33.0, x_ODA_preh=0.0176,
                     q_V_ETA_ahu=10000, q_V_SUP_ahu=10000, p_atm=1e5,
                     heat_rec_type="ROT_SORP")
    print("Sorption wheel factors (balanced, n_rot=20):")
    for k, v in f.items():
        print(f"  {k:8s} = {v:.5f}")
