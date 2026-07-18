#!/usr/bin/env python3
"""Kreislaufverbundsystem (run-around coil, WRG=3) — faithful port of the
WRG==3 branch of the VKA main script (lines ~1144-1565, ABL pass + ZUL pass).

A counterflow heat-exchanger pair (supply / exhaust) coupled by a circulating
water loop. Only SENSIBLE heat is transferred (x unchanged). The intermediate
medium volume flow is swept over 1000 points and the energy-optimal operating
point is chosen against the supply T- and x-setpoint bands — exactly mirroring
the rotor 'Energy' control, but the actuator is the pump flow, not wheel speed.

All temperatures °C, humidity ratios kg/kg, flows m3/h, enthalpies kJ/kg.
"""
from __future__ import annotations
import numpy as np
from .moist_air import h as hx, T_from_hx, x as x_ma, ps as ps_ma
from .matlab_find import find_first, find_last, argmax_first, argmin_first


def _T_for_x_at_phi(x_val, phi, p):
    """Solve x(T,phi)=x_val for T on [0,100] via MATLAB fminbnd objective."""
    from scipy.optimize import minimize_scalar
    return float(minimize_scalar(lambda T: (x_val - x_ma(T, phi, p)) ** 2,
                                 bounds=(0.0, 100.0), method="bounded").x)


def _counterflow_rwz(NTU, Cstar):
    return ((1 - np.exp(-NTU * (1 - Cstar)))
            / (1 - Cstar * np.exp(-NTU * (1 - Cstar))))


def _nominal_UA(RWZ_N, V_air_N, V_M_N, rho_M, cp_M):
    """Design UA [kW/K] of one counterflow exchanger (air vs medium)."""
    C_air = V_air_N / 3600.0 * 1.2 * 1.0
    C_M = V_M_N / 3600.0 * rho_M * cp_M
    if C_air == C_M:
        NTU_N = RWZ_N / (1 - RWZ_N)
    else:
        Cstar = C_air / C_M
        NTU_N = np.log((RWZ_N - 1) / (RWZ_N * Cstar - 1)) / (Cstar - 1)
    return NTU_N * C_air, C_air, C_M


def _select_index(T_in, x_in, T_nn, sp, Bef_n_WRG, Bef_ZUL, KR_n_WRG, KR_Entf,
                  h_H2O, dTk):
    """Energy-optimal medium-flow index (0-based) or None (inactive).
    Faithful port of the index_KVS cascade (MATLAB lines ~1292-1534)."""
    p = sp.p_atm
    point = (sp.T_min == sp.T_max
             and sp.x_min_Tmin == sp.x_max_Tmax)   # "kein Sollwertbereich"
    Tlo = sp.T_min + dTk
    Thi = sp.T_max + dTk
    mx = np.max(T_nn); mn = np.min(T_nn)

    def fl(thr):  # find first T_nn >= thr
        return find_first(T_nn >= thr)

    def fh(thr):  # find first T_nn <= thr
        return find_first(T_nn <= thr)

    # ---- generic 4-branch pattern (lo,hi thresholds; hi-branch "passend" thr) ----
    def pattern(lo, hi, lo_passend=None, hi_passend=None,
                lo_point=None, hi_point=None):
        lo_p = lo if lo_passend is None else lo_passend
        hi_p = hi if hi_passend is None else hi_passend
        lo_pt = lo if lo_point is None else lo_point
        hi_pt = hi if hi_point is None else hi_point
        if T_in < lo and mx > lo:
            return (fl(lo_pt) - 1) if point else fl(lo_p)
        elif T_in < lo and mx <= lo:
            return argmax_first(T_nn)
        elif T_in > hi and mn < hi:
            return (fh(hi_pt) - 1) if point else fh(hi_p)
        elif T_in > hi and mn >= hi:
            return argmin_first(T_nn)
        else:
            return None

    # ================= Case A: too dry (x_in < x_min_Tmin) ==================
    if x_in < sp.x_min_Tmin:
        dx_min = sp.x_min_Tmin - x_in
        dx_max = sp.x_min_Tmax - x_in
        T_vBef_min = T_from_hx(sp.h_min_Tmin - h_H2O * dx_min, x_in) + dTk
        T_vBef_max = T_from_hx(sp.h_min_Tmax - h_H2O * dx_max, x_in) + dTk
        if Bef_n_WRG == 1 and Bef_ZUL == 1:          # A1 spray after WRG
            return pattern(T_vBef_min, T_vBef_max)
        elif Bef_n_WRG == 1 and Bef_ZUL == 2:        # A2 steam after WRG
            if T_in < T_vBef_min and mx > T_vBef_min:
                return (fl(T_vBef_min) - 1) if point else fl(T_vBef_min)
            elif T_in < T_vBef_min and mx <= T_vBef_min:
                return argmax_first(T_nn)
            elif T_in > T_vBef_max and mn < T_vBef_max:
                if point:
                    return fh(T_vBef_max) - 1
                if mn < T_vBef_min:   # closer to T_vBef_min -> less humidification
                    return find_last(T_nn >= T_vBef_min)
                return argmin_first(T_nn)
            elif T_in > T_vBef_max and mn >= T_vBef_max:
                return argmin_first(T_nn)
            else:
                return None
        else:                                        # A3 no humidifier
            return pattern(Tlo, Thi)

    # ============ Case B: left x-band (x_min_Tmin..x_min_Tmax) ==============
    if sp.x_min_Tmin <= x_in <= sp.x_min_Tmax:
        T_phimin = _T_for_x_at_phi(x_in, sp.phi_min, p) + dTk
        if T_in < Tlo and mx > Tlo:
            return (fl(Tlo) - 1) if point else fl(Tlo)
        elif T_in < Tlo and mx <= Tlo:
            return argmax_first(T_nn)
        elif T_in > T_phimin and mn < T_phimin:
            return (fh(Thi) - 1) if point else fh(T_phimin)
        elif T_in > T_phimin and mn >= T_phimin:
            return argmin_first(T_nn)
        else:
            return None

    # ====== Case C: too humid + dehumidifying cooler -> max cooling =========
    if x_in > sp.x_max_Tmax and KR_n_WRG == 1 and KR_Entf == 1:
        return argmin_first(T_nn)

    # ============ Case D: right x-band (x_max_Tmin..x_max_Tmax) =============
    if sp.x_max_Tmin <= x_in <= sp.x_max_Tmax:
        T_phimax = _T_for_x_at_phi(x_in, sp.phi_max, p) + dTk
        if T_in < T_phimax and mx > T_phimax:
            return (fl(Tlo) - 1) if point else fl(T_phimax)
        elif T_in < T_phimax and mx <= T_phimax:
            return argmax_first(T_nn)
        elif T_in > Thi and mn < Thi:
            return (fh(Thi) - 1) if point else fh(Thi)
        elif T_in > Thi and mn >= Thi:
            return argmin_first(T_nn)
        else:
            return None

    # ===================== Case E: temperature only ========================
    return pattern(Tlo, Thi)


def kvs_recovery(T_oda, x_oda, T_eta, x_eta, V_sup, V_exh, sp,
                 V_M_N, rho_M, cp_M, RWZ_ZUL_N, RWZ_ABL_N,
                 V_ZUL_N, V_ABL_N, Bef_ZUL, Bef_n_WRG, KR_n_WRG, KR_Entf,
                 h_H2O, dTk=0.0):
    """Run-around coil. Supply enters at (T_oda,x_oda), exhaust at (T_eta,x_eta).
    Returns (T_sup_out, x_sup_out, T_eta_out, RWZ_ges, V_M_opt). x is unchanged
    (sensible-only). RWZ_ges = (T_sup_out-T_oda)/(T_eta-T_oda) or NaN."""
    # nominal design UA for both exchangers
    UA_ABL_N, C_air_ABL_N, C_M_N = _nominal_UA(RWZ_ABL_N, V_ABL_N, V_M_N, rho_M, cp_M)
    UA_ZUL_N, C_air_ZUL_N, _ = _nominal_UA(RWZ_ZUL_N, V_ZUL_N, V_M_N, rho_M, cp_M)
    # NOTE: MATLAB uses C_air_ABL_N for the ZUL nominal UA in the C*!=1 branch
    # (line 1174). With equal design flows this is identical; kept faithful:
    if C_air_ZUL_N != C_M_N:
        Cstar = C_air_ZUL_N / C_M_N
        NTU_ZUL_N = np.log((RWZ_ZUL_N - 1) / (RWZ_ZUL_N * Cstar - 1)) / (Cstar - 1)
        UA_ZUL_N = NTU_ZUL_N * C_air_ABL_N

    C_dot_L_ABL = V_exh / 3600.0 * 1.2
    C_dot_L_ZUL = V_sup / 3600.0 * 1.2
    if C_dot_L_ABL == 0 or C_dot_L_ZUL == 0:
        return T_oda, x_oda, T_eta, 0.0, 0.0

    # 1000-point medium-flow grid (step .. V_M_N)
    V_M_nn = np.arange(1, 1001) * (V_M_N / 1000.0)
    C_dot_M = V_M_nn / 3600.0 * rho_M * cp_M

    UA_ABL_nn = UA_ABL_N * (V_exh / V_ABL_N) ** 0.4 * (V_M_nn / V_M_N) ** 0.4
    UA_ZUL_nn = UA_ZUL_N * (V_sup / V_ZUL_N) ** 0.4 * (V_M_nn / V_M_N) ** 0.4

    mask_ABL = C_dot_L_ABL != C_dot_M
    Cstar_ABL = np.where(mask_ABL, C_dot_L_ABL / C_dot_M, 1.0)
    NTU_ABL = UA_ABL_nn / C_dot_L_ABL
    RWZ_L_ABL = np.where(mask_ABL, _counterflow_rwz(NTU_ABL, Cstar_ABL),
                         NTU_ABL / (NTU_ABL + 1))

    mask_ZUL = C_dot_L_ZUL != C_dot_M
    Cstar_ZUL = np.where(mask_ZUL, C_dot_L_ZUL / C_dot_M, 1.0)
    NTU_ZUL = UA_ZUL_nn / C_dot_L_ZUL
    # MATLAB overwrites the ZUL RWZ where C_dot_L_ABL != C_dot_M (line 1274):
    RWZ_L_ZUL = np.where(mask_ABL, _counterflow_rwz(NTU_ZUL, Cstar_ZUL),
                         NTU_ZUL / (NTU_ZUL + 1))

    RWZ_W_ABL = RWZ_L_ABL * (C_dot_L_ABL / C_dot_M)
    RWZ_W_ZUL = RWZ_L_ZUL * (C_dot_L_ZUL / C_dot_M)

    # medium temperatures (loop losses neglected) and resulting outlet temps
    T_out_M_ZUL = ((RWZ_W_ABL / RWZ_W_ZUL - RWZ_W_ABL) * T_eta + T_oda) \
        / (1 + RWZ_W_ABL / RWZ_W_ZUL - RWZ_W_ABL)
    T_in_M_ABL = T_out_M_ZUL
    T_out_M_ABL = T_in_M_ABL + RWZ_W_ABL * (T_eta - T_in_M_ABL)
    T_in_M_ZUL = T_out_M_ABL

    T_ABL_nn = T_eta - RWZ_L_ABL * (T_eta - T_in_M_ABL)
    T_ZUL_nn = T_oda + RWZ_L_ZUL * (T_in_M_ZUL - T_oda)

    idx = _select_index(T_oda, x_oda, T_ZUL_nn, sp, Bef_n_WRG, Bef_ZUL,
                        KR_n_WRG, KR_Entf, h_H2O, dTk)
    if idx is None:
        return T_oda, x_oda, T_eta, np.nan, 0.0
    T_sup_out = float(T_ZUL_nn[idx])
    T_eta_out = float(T_ABL_nn[idx])
    denom = (T_eta - T_oda)
    RWZ_ges = (T_sup_out - T_oda) / denom if denom != 0 else np.nan
    return T_sup_out, x_oda, T_eta_out, RWZ_ges, float(V_M_nn[idx])
