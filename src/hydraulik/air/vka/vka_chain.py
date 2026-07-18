#!/usr/bin/env python3
"""VKA component chain — inline supply-air components from the main MATLAB script.

Ports the component-by-component state changes (lines ~608-2592 of
VKA_Effizienzbeurteilung) for a rotor-based AHU:

    Fan -> (Frost) -> Wheel(WRG) -> Pre-heater(VHR) -> Cooler(KR)
        -> Spray humidifier(Bef) -> Re-heater(NHR) -> Supply

Each component reads the current (T,x) state and adjusts it toward the supply
setpoint band (T 22-24 C, phi 40-55 %), exactly as the MATLAB inline blocks do.
Uses the h,x moist-air model (moist_air.py) for MATLAB-faithful results.

All component heat loads Q_dot are in kW (m_dot in kg/s, enthalpy in kJ/kg).
"""

from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np
from scipy.optimize import minimize_scalar
from . import moist_air as ma
from . import matlab_find
from .heat_rec_wheel import (_ROTOR_CONSTANTS, _eff_factors, energy_control,
                            _FODA)

H_EVAP = 2500.0  # h_H2O for spray/steam water enthalpy term (kJ/kg) — see note
# NOTE: the MATLAB script sets h_H2O depending on humidifier water temperature;
# for the spray humidifier it uses the evaporation enthalpy reference. The exact
# value is resolved per-call from the humidifier water temperature.


def _x_at_phi(T, phi, p):
    return ma.x(T, phi, p)


def _T_for_x_at_phi(x_val, phi, p):
    """Solve x(T,phi)=x_val for T on [0,100] (MATLAB fminbnd objective)."""
    def obj(T):
        return (x_val - ma.x(T, phi, p)) ** 2
    return float(minimize_scalar(obj, bounds=(0.0, 100.0), method="bounded").x)


@dataclass
class Setpoints:
    T_min: float
    T_max: float
    phi_min: float
    phi_max: float
    p_atm: float = 1e5
    # derived corner values
    x_min_Tmin: float = field(init=False)
    x_min_Tmax: float = field(init=False)
    x_max_Tmin: float = field(init=False)
    x_max_Tmax: float = field(init=False)
    h_min_Tmin: float = field(init=False)
    h_min_Tmax: float = field(init=False)
    h_max_Tmin: float = field(init=False)
    h_max_Tmax: float = field(init=False)

    def __post_init__(self):
        # Validate the band so an inverted/out-of-range setpoint raises a clear
        # error instead of silently producing wrong loads. phi is a FRACTION
        # [0,1] here (not percent). T_min==T_max (point setpoint) is allowed.
        if self.T_min > self.T_max + 1e-9:
            raise ValueError(f"Setpoints: T_min ({self.T_min}) > T_max "
                             f"({self.T_max})")
        if not (0.0 <= self.phi_min <= 1.0) or not (0.0 <= self.phi_max <= 1.0):
            raise ValueError("Setpoints: phi_min/phi_max must be in [0,1] "
                             f"(fraction, not %); got {self.phi_min}, "
                             f"{self.phi_max}")
        if self.phi_min > self.phi_max + 1e-12:
            raise ValueError(f"Setpoints: phi_min ({self.phi_min}) > phi_max "
                             f"({self.phi_max})")
        p = self.p_atm
        self.x_min_Tmin = ma.x(self.T_min, self.phi_min, p)
        self.x_min_Tmax = ma.x(self.T_max, self.phi_min, p)
        self.x_max_Tmin = ma.x(self.T_min, self.phi_max, p)
        self.x_max_Tmax = ma.x(self.T_max, self.phi_max, p)
        self.h_min_Tmin = ma.h(self.T_min, self.x_min_Tmin)
        self.h_min_Tmax = ma.h(self.T_max, self.x_min_Tmax)
        self.h_max_Tmin = ma.h(self.T_min, self.x_max_Tmin)
        self.h_max_Tmax = ma.h(self.T_max, self.x_max_Tmax)


def fan(T_in, x_in, V_dot_m3h, SFP, f_rec, T_set_mean, p_atm=1e5):
    """Supply/exhaust fan: temperature rise from recovered motor heat.

    rho = p/(287*(273+T_set_mean)); m = V/3600*rho;
    Q = f_rec * SFP/1000 * V/3600 [kW]; dT = Q/(1*m).
    Returns (T_out, x_out, m_dot_kg_s, Q_dot_kW, dT).
    """
    rho = p_atm / (287.0 * (273.0 + T_set_mean))
    m_dot = V_dot_m3h / 3600.0 * rho
    P_el_kW = (SFP / 1000.0) * (V_dot_m3h / 3600.0)
    Q_dot = f_rec * P_el_kW
    dT = Q_dot / (1.0 * m_dot) if m_dot > 0 else 0.0
    return T_in + dT, x_in, m_dot, Q_dot, dT


def wheel(T_oda, x_oda, T_eta, x_eta, T_aul, T_abl, V_sup, V_eta, V_nom,
          heat_rec_type, sp: Setpoints, dT_VentuKanal_ZUL,
          Bef_ZUL, Bef_n_WRG, KR_n_WRG, KR_Entf, h_H2O,
          sup_fan_loc="UP_HR", eta_fan_loc="DOWN_HR", n_rot_max=20.0,
          eta_hr_N=0.0, eta_xr_N=0.0):
    """Rotary wheel with the energy-optimal speed control (Optimal Control): the
    speed that meets the supply setpoint at minimum heating/cooling energy.
    eta_hr_N/eta_xr_N (!=0) override the nominal sensible/latent design
    effectiveness (WRG_calc_Vers=5). Returns (T_out, x_out, eta_hr, eta_xr, n_rot)."""
    c = dict(_ROTOR_CONSTANTS[heat_rec_type])
    if eta_hr_N != 0:
        c["eta_hr_nom"] = eta_hr_N
    if eta_xr_N != 0:
        c["eta_xr_nom"] = eta_xr_N
    f_ODA_min = _FODA[(sup_fan_loc, eta_fan_loc)]
    v_hr_N_para = c["v_hr_nom"]
    v_hr_eff = V_sup / V_nom

    f = _eff_factors(c, V_sup, V_eta, v_hr_eff, n_rot_max, n_rot_max, f_ODA_min,
                     v_hr_N_para, T_aul, T_abl, T_eta, x_eta, T_oda, x_oda,
                     V_eta, V_sup, sp.p_atm, heat_rec_type)
    eta_hr, eta_xr, n_rot, f_n, f_n_x = energy_control(
        c, n_rot_max, f["f_q"], f["f_v"], f["f_dx_x"], f["f_q_x"], f["f_v_x"],
        T_oda, x_oda, T_eta, x_eta, sp.T_min, sp.T_max,
        sp.x_min_Tmin, sp.x_max_Tmin, sp.x_min_Tmax, sp.x_max_Tmax,
        sp.h_min_Tmin, sp.h_min_Tmax, Bef_n_WRG, Bef_ZUL, h_H2O,
        KR_n_WRG, KR_Entf, dT_VentuKanal_ZUL, sp.p_atm)
    T_out = T_oda + eta_hr * (T_eta - T_oda)
    x_out = x_oda + eta_xr * (x_eta - x_oda)
    return T_out, x_out, eta_hr, eta_xr, n_rot


def preheater(T_in, x_in, m_dot, sp: Setpoints, dT_corr, eta_bef, h_H2O,
              Bef_after=True, Bef_ZUL=1, NHR_after=True, KR_after=True):
    """VHR pre-heater. Faithful port of the VHR inline block.

    With a spray humidifier downstream (Bef_after, Bef_ZUL=1) and air too dry,
    the VHR only pre-heats to the humidifier-limit temperature T_vBef so the
    adiabatic spray can reach the setpoint without cooling below T_min.
    Returns (T_out, x_out, Q_dot_kW).
    """
    p = sp.p_atm
    h_in = ma.h(T_in, x_in)

    # too dry, spray humidifier downstream (Bef_ZUL=1)
    if x_in < sp.x_min_Tmin and Bef_after and Bef_ZUL == 1:
        dx_Bef = sp.x_min_Tmin - x_in
        x_s = dx_Bef / eta_bef + x_in
        if NHR_after:
            T_s = ma.Ts(x_s, p)
            h_ZUL_Bef = ma.h(T_s, x_s)
            T_vBef = ma.T_from_hx(h_ZUL_Bef - h_H2O * dx_Bef, x_in)
        else:
            h_ZUL_Bef = ma.h(sp.T_min + dT_corr, sp.x_min_Tmin)
            T_vBef = ma.T_from_hx(h_ZUL_Bef - h_H2O * dx_Bef, x_in)
        T_out = max(T_in, T_vBef)
        x_out = x_in
        Q = m_dot * (ma.h(T_out, x_out) - h_in)
        return T_out, x_out, Q

    # too dry and below T_min, steam humidifier downstream (Bef_ZUL=2)
    if T_in < (sp.T_min + dT_corr) and x_in < sp.x_min_Tmin:
        if Bef_after and Bef_ZUL == 2:
            dx_Bef = sp.x_min_Tmin - x_in
            T_out = max(ma.T_from_hx(sp.h_min_Tmin - h_H2O * dx_Bef, x_in) + dT_corr, T_in)
            x_out = x_in
        else:
            x_out = x_in
            T_out = max(T_in, sp.T_min + dT_corr)
        Q = m_dot * (ma.h(T_out, x_out) - h_in)
        return T_out, x_out, Q

    # humidity in band, temp below max -> heat to phi_max line or T_min edge
    if T_in < (sp.T_max + dT_corr) and (sp.x_min_Tmin <= x_in <= sp.x_max_Tmax):
        x_out = x_in
        if x_in >= sp.x_max_Tmin:
            T_phi = _T_for_x_at_phi(x_in, sp.phi_max, p)
            T_out = max(T_phi + dT_corr, T_in)
        else:
            T_out = max(T_in, sp.T_min + dT_corr)
        Q = m_dot * (ma.h(T_out, x_out) - h_in)
        return T_out, x_out, Q

    # too humid (above band), below T_min, no cooler downstream -> heat to T_min
    # (humidity cannot be corrected without a cooler; MATLAB VHR lines 1977-1980).
    # MATLAB quirk: this branch raises T but does NOT assign Q_dot_VHR, so the
    # reported heat load stays 0 -- replicated here for faithfulness.
    if T_in < (sp.T_min + dT_corr) and x_in > sp.x_max_Tmax and not KR_after:
        T_out = max(T_in, sp.T_min + dT_corr)
        return T_out, x_in, 0.0

    # else inactive
    return T_in, x_in, 0.0


def cooler(T_in, x_in, m_dot, sp: Setpoints, dT_corr, KR_Entf=1,
           SprBef_n_KR=0, DBef_n_KR=0, h_H2O=0.0,
           dx_Entf_tol=0.03e-3, T_Entf_bnd=6.0):
    """KR cooling register. Faithful 1:1 port of the KR inline block
    (VKA main script lines ~2332-2447). Branch order matches MATLAB exactly
    (elseif cascade). SprBef_n_KR / DBef_n_KR flag whether a spray / steam
    humidifier follows the cooler. Returns (T_out, x_out, Q_dot_kW).

    Only the dehumidification branch carries the recondensation term in Q
    (-m*dx_Entf*(-1)*4.18*T_out); all other branches use plain m*(h_in-h_out).
    """
    p = sp.p_atm
    h_in = ma.h(T_in, x_in)
    Tlim_max = sp.T_max + dT_corr
    Tlim_min = sp.T_min + dT_corr

    # branch 2: abs humidity above upper band (+tol) -> dehumidify
    if x_in > (sp.x_max_Tmax + dx_Entf_tol):
        dx_Entf = 0.0
        if KR_Entf == 1:
            if ma.Ts(sp.x_max_Tmax, p) > T_Entf_bnd:
                dx_Entf = sp.x_max_Tmax - x_in
                T_out = ma.Ts(sp.x_max_Tmax, p); x_out = sp.x_max_Tmax
            else:
                if T_in > Tlim_max:
                    T_out = min(T_in, Tlim_max); x_out = x_in
                else:
                    T_out, x_out = T_in, x_in
                dx_Entf = x_out - x_in
            Q = (m_dot * (h_in - ma.h(T_out, x_out))
                 - m_dot * dx_Entf * (-1) * 4.18 * T_out)
        elif KR_Entf == 0 and T_in > Tlim_max:
            T_out = min(T_in, Tlim_max); x_out = x_in
            Q = m_dot * (h_in - ma.h(T_out, x_out))
        else:
            T_out, x_out = T_in, x_in
            Q = m_dot * (h_in - ma.h(T_out, x_out))
        return T_out, x_out, Q

    # branch 3: spray humidifier downstream and temp above max -> exploit
    # the spray's evaporative cooling so the coil overshoots less
    if SprBef_n_KR == 1 and T_in > Tlim_max:
        dx_tmp = sp.x_max_Tmax - x_in
        if h_in + h_H2O * dx_tmp < sp.h_min_Tmin + 0.01:
            T_out, x_out = T_in, x_in
        elif h_in + h_H2O * dx_tmp > sp.h_max_Tmax:
            T_out = (max(sp.T_max, T_in - (ma.T_from_hx(h_in + h_H2O * dx_tmp,
                     sp.x_max_Tmax) - sp.T_max)) + dT_corr)
            x_out = x_in
        else:
            T_out, x_out = T_in, x_in
        return T_out, x_out, m_dot * (h_in - ma.h(T_out, x_out))

    # branch 4: humidity in band, temp above max -> sensible cool to T_max
    if T_in > Tlim_max and (sp.x_min_Tmax <= x_in <= (sp.x_max_Tmax + dx_Entf_tol)):
        T_out = min(T_in, Tlim_max); x_out = x_in
        return T_out, x_out, m_dot * (h_in - ma.h(T_out, x_out))

    # branch 5: left band region, temp above min -> cool to phi_min line
    if T_in > Tlim_min and (sp.x_min_Tmax < x_in and x_in >= sp.x_min_Tmin):
        T_phi = _T_for_x_at_phi(x_in, sp.phi_min, p)
        T_out = min(T_in, T_phi + dT_corr); x_out = x_in
        return T_out, x_out, m_dot * (h_in - ma.h(T_out, x_out))

    # branch 6: humidity below band, no humidifier after -> cool to T_max
    if (SprBef_n_KR == 0 and DBef_n_KR == 0
            and x_in < sp.x_min_Tmin and T_in > Tlim_max):
        T_out = min(T_in, Tlim_max); x_out = x_in
        return T_out, x_out, m_dot * (h_in - ma.h(T_out, x_out))

    # branch 7: humidity below band, steam humidifier downstream
    if DBef_n_KR == 1 and x_in < sp.x_min_Tmin:
        dx_Bef = sp.x_min_Tmax - x_in
        T_ahu_out = ma.T_from_hx(h_in + h_H2O * dx_Bef, sp.x_min_Tmax)
        if T_ahu_out > Tlim_max:
            T_out = min(T_in, ma.T_from_hx(sp.h_min_Tmax - h_H2O * dx_Bef, x_in)
                        + dT_corr)
            x_out = x_in
        else:
            T_out, x_out = T_in, x_in
        return T_out, x_out, m_dot * (h_in - ma.h(T_out, x_out))

    # else: inactive
    return T_in, x_in, 0.0


def spray_humidifier(T_in, x_in, m_dot, sp: Setpoints, dT_corr, eta_bef,
                     h_H2O):
    """Sprühbefeuchter (Bef_ZUL=1): adiabatic humidification.
    Faithful 1:1 port of the spray-humidifier inline block (main script lines
    ~2051-2157), all four active branches. Returns
    (T_out, x_out, m_dot_water_kg_s, Q_dot_kW)."""
    p = sp.p_atm
    h_in = ma.h(T_in, x_in)
    Tlim_max = sp.T_max + dT_corr
    Tlim_min = sp.T_min + dT_corr

    def _finish(dx, x_out, T_out):
        m_water = dx * m_dot
        return T_out, x_out, m_water, m_water * h_H2O

    # branch 2: too dry (left band) and enthalpy below the left-band ceiling
    if x_in < sp.x_min_Tmin and h_in <= sp.h_min_Tmax + 0.01:
        if h_in > sp.h_min_Tmin and h_in <= sp.h_min_Tmax:
            dx = ma.x(ma.T_h_phi(h_in, sp.phi_min, p), sp.phi_min, p) - x_in
        else:
            dx = sp.x_min_Tmin - x_in
        x_s = dx / eta_bef + x_in
        if ma.xs(ma.T_h_phi(h_in + h_H2O * dx, 1.0, p), p) >= x_s:
            T_out = ma.T_from_hx(h_in + h_H2O * dx, sp.x_min_Tmin)
            x_out = x_in + dx
        else:
            x_s_bad = ma.xs(ma.T_h_phi(h_in + h_H2O * dx, 1.0, p), p)
            x_out = x_in + (x_s_bad - x_in) * eta_bef
            dx = x_out - x_in
            T_out = ma.T_from_hx(h_in + h_H2O * dx, x_out)
        return _finish(dx, x_out, T_out)

    # branch 3: in the left x-band but phi below phi_min, temp within band
    # -> humidify up to the phi_min line (exploit evaporative cooling)
    if (Tlim_min < T_in < Tlim_max and sp.x_min_Tmin <= x_in <= sp.x_min_Tmax
            and ma.phi(T_in, x_in, p) < sp.phi_min):
        h_ZUL_Bef = h_in
        # MATLAB uses dx_Bef (still 0 here) inside T_h_phi -> +h_H2O*0
        T_nBef = ma.T_h_phi(h_ZUL_Bef, sp.phi_min, p)
        dx = ma.x(T_nBef, sp.phi_min, p) - x_in
        x_s = dx / eta_bef + x_in
        if ma.xs(ma.T_h_phi(h_ZUL_Bef + h_H2O * dx, 1.0, p), p) >= x_s:
            x_out = ma.x(T_nBef, sp.phi_min, p)
            T_out = ma.T_from_hx(h_ZUL_Bef + h_H2O * dx, x_out)
        else:
            x_s_bad = ma.xs(ma.T_h_phi(h_ZUL_Bef + h_H2O * dx, 1.0, p), p)
            x_out = x_in + (x_s_bad - x_in) * eta_bef
            dx = x_out - x_in
            T_out = ma.T_from_hx(h_ZUL_Bef + h_H2O * dx, x_out)
        return _finish(dx, x_out, T_out)

    # branch 4: too warm, enthalpy within right band -> spray-cool toward T_max
    if (T_in > Tlim_max and sp.h_min_Tmax < h_in <= sp.h_max_Tmax):
        h_ZUL_Bef = h_in
        T_nBef = Tlim_max
        # MATLAB objective: (h_ZUL_Bef + 2*h_H2O*(x-x_in) - h(T_nBef,x))^2
        def _obj(xv):
            return (h_ZUL_Bef + h_H2O * (xv - x_in) - ma.h(T_nBef, xv)
                    + h_H2O * (xv - x_in)) ** 2
        x_nBef_test = float(minimize_scalar(_obj, bounds=(0.0, 100.0),
                                            method="bounded").x)
        dx = x_nBef_test - x_in
        x_s = dx / eta_bef + x_in
        if ma.xs(ma.T_h_phi(h_ZUL_Bef + h_H2O * dx, 1.0, p), p) >= x_s:
            x_out = x_nBef_test
            T_out = ma.T_from_hx(h_ZUL_Bef + h_H2O * dx, x_out)
        else:
            x_s_bad = ma.xs(ma.T_h_phi(h_ZUL_Bef + h_H2O * dx, 1.0, p), p)
            x_out = x_in + (x_s_bad - x_in) * eta_bef
            dx = x_out - x_in
            T_out = ma.T_from_hx(h_ZUL_Bef + h_H2O * dx, x_out)
        return _finish(dx, x_out, T_out)

    # else: inactive
    return T_in, x_in, 0.0, 0.0


def plate_recovery(T_oda, x_oda, T_eta, V_sup, V_exh,
                   RWZ_ZUL_N, V_ZUL_WT_N, V_ABL_WT_N):
    """Plate heat exchanger (PWÜ, WRG=1) — faithful port of the WRG==1 supply
    block (main script lines 1703-1762). Dry, sensible-only (x unchanged), fixed
    effectiveness (no modulation). Crossflow single-pass when both design RWZ
    <= 0.632, else counterflow; part-load via UA ~ V^0.4. Returns (T_out, x, RWZ).
    """
    Cstar_N = V_ZUL_WT_N / V_ABL_WT_N
    RWZ_ABL_N = RWZ_ZUL_N * (V_ZUL_WT_N / V_ABL_WT_N)
    crossflow = (RWZ_ZUL_N <= 0.632 and RWZ_ABL_N <= 0.632)
    if crossflow:
        # MATLAB: -log(1 - log(1-RWZ*C)/-1*C)  (left-to-right precedence)
        NTU_N = -np.log(1 - np.log(1 - RWZ_ZUL_N * Cstar_N) / -1 * Cstar_N)
    elif Cstar_N == 1:
        NTU_N = RWZ_ZUL_N / (1 - RWZ_ZUL_N)
    else:
        NTU_N = np.log((RWZ_ZUL_N - 1) / (RWZ_ZUL_N * Cstar_N - 1)) / (Cstar_N - 1)
    UA_N = NTU_N * V_ZUL_WT_N * 1.2 * 1.0 / 3600.0
    if V_exh == 0 or V_sup == 0:
        return T_oda, x_oda, 0.0
    Cstar = V_sup / V_exh
    UA = UA_N * (V_sup / V_ZUL_WT_N) ** 0.4 * (V_exh / V_ABL_WT_N) ** 0.4
    NTU = UA / (V_sup * 1.2 * 1.0 / 3600.0)
    if crossflow:
        RWZ = (1 - np.exp(-Cstar * (1 - np.exp(-NTU)))) / Cstar
    elif Cstar == 1:
        RWZ = NTU / (NTU + 1)
    else:
        RWZ = ((1 - np.exp(-NTU * (1 - Cstar)))
               / (1 - Cstar * np.exp(-NTU * (1 - Cstar))))
    return T_oda + RWZ * (T_eta - T_oda), x_oda, float(RWZ)


def frost_protection(T_in, x_in, m_dot, T_FS_Grenz):
    """Frostschutz FS==1 (electric pre-heater). Heats to the frost limit when
    the (active) inlet is below it. Faithful port of the FS block (lines
    683-708). Q uses the outdoor mass flow. Returns (T_out, x_out, Q_dot_kW)."""
    h_in = ma.h(T_in, x_in)
    if m_dot > 0 and T_in < T_FS_Grenz:
        T_out, x_out = T_FS_Grenz, x_in
    else:
        T_out, x_out = T_in, x_in
    return T_out, x_out, m_dot * (ma.h(T_out, x_out) - h_in)


def steam_humidifier(T_in, x_in, m_dot, sp: Setpoints, dT_corr, h_H2O):
    """Dampfbefeuchter (Bef_ZUL=2). Faithful 1:1 port of the steam-humidifier
    inline block (lines ~2170-2256). Steam adds enthalpy (h_H2O=2500.9+1.86*T).
    Returns (T_out, x_out, m_dot_water_kg_s, Q_dot_kW)."""
    p = sp.p_atm
    h_in = ma.h(T_in, x_in)
    Tlim_max = sp.T_max + dT_corr
    Tlim_min = sp.T_min + dT_corr

    def _phi_min_search(T_start, x_start, x_top):
        # 101-point march from x_start to x_top along the steam line; first
        # point reaching phi_min (MATLAB index capped at 101 -> 0-based 100).
        rng = x_top - x_start
        dx_nn = np.linspace(0.0, rng, 101)
        x_nn = x_start + dx_nn
        T_nn = ma.T_from_hx(ma.h(T_start, x_start) + h_H2O * dx_nn, x_nn)
        f = matlab_find.find_first(ma.phi(T_nn, x_nn, p) >= sp.phi_min)
        idx = f if f is not None else 100
        return float(T_nn[idx]), float(x_nn[idx])

    # branch 2: too dry (x_in < x_min_Tmin)
    if x_in < sp.x_min_Tmin:
        dx_Bef = sp.x_min_Tmin - x_in
        T_nBef = ma.T_from_hx(h_in + h_H2O * dx_Bef, sp.x_min_Tmin)
        dx_test = sp.x_min_Tmax - x_in
        T_nBef_test = ma.T_from_hx(h_in + h_H2O * dx_test, sp.x_min_Tmax)
        if ma.phi(T_nBef, sp.x_min_Tmin, p) < 1.0:
            if T_nBef_test > Tlim_max:
                T_out, x_out = T_nBef, sp.x_min_Tmin
            elif T_nBef <= Tlim_min:
                T_out, x_out = T_nBef, sp.x_min_Tmin
            else:
                T_out, x_out = _phi_min_search(T_nBef, sp.x_min_Tmin, sp.x_min_Tmax)
                dx_Bef = x_out - x_in
        else:
            # cannot reach setpoint -> humidify to saturation at T_in.
            # MATLAB does NOT update dx_Bef here (kept faithful for Q).
            dx = ma.xs(T_in, p) - x_in
            x_out = ma.xs(T_in, p)
            T_out = ma.T_from_hx(h_in + h_H2O * dx, x_out)
        m_water = dx_Bef * m_dot
        return T_out, x_out, m_water, m_water * h_H2O

    # branch 3: already in left x-band, temp above T_min -> top up to phi_min
    if T_in > Tlim_min and sp.x_min_Tmin <= x_in <= sp.x_min_Tmax:
        dx_test = sp.x_min_Tmax - x_in
        T_nBef_test = ma.T_from_hx(h_in + h_H2O * dx_test, sp.x_min_Tmax)
        if T_nBef_test > Tlim_max:
            return T_in, x_in, 0.0, 0.0
        T_out, x_out = _phi_min_search(T_in, x_in, sp.x_min_Tmax)
        dx_Bef = x_out - x_in
        m_water = dx_Bef * m_dot
        return T_out, x_out, m_water, m_water * h_H2O

    # else inactive
    return T_in, x_in, 0.0, 0.0


def reheater(T_in, x_in, m_dot, sp: Setpoints, dT_corr):
    """NHR re-heater: heats supply to the appropriate band edge.
    Faithful port of the NHR inline block. Returns (T_out, x_out, Q_dot_kW)."""
    p = sp.p_atm
    Tlim_max = sp.T_max + dT_corr
    Tlim_min = sp.T_min + dT_corr

    # within humidity band, temp below max
    if T_in < Tlim_max and (sp.x_min_Tmin <= x_in <= sp.x_max_Tmax):
        x_out = x_in
        if x_in >= sp.x_max_Tmin:
            T_phi = _T_for_x_at_phi(x_in, sp.phi_max, p)
            T_out = max(T_phi + dT_corr, T_in)
        else:
            T_out = max(T_in, Tlim_min)
        Q = m_dot * (ma.h(T_out, x_out) - ma.h(T_in, x_in))
        return T_out, x_out, Q

    # below temp min
    if T_in < Tlim_min:
        x_out = x_in
        T_out = max(T_in, Tlim_min)
        Q = m_dot * (ma.h(T_out, x_out) - ma.h(T_in, x_in))
        return T_out, x_out, Q

    # else inactive
    return T_in, x_in, 0.0


def _x_for_h_at_T(h_val, T):
    """Solve h(T,x)=h_val for x on [0,100] (MATLAB fminbnd objective)."""
    def obj(xv):
        return (h_val - ma.h(T, xv)) ** 2
    return float(minimize_scalar(obj, bounds=(0.0, 100.0), method="bounded").x)


def adiabatic_cooler(T_abl, x_abl, T_zul_at_vent, x_zul_at_vent, sp: Setpoints,
                     eta_bef, KR_nAK=1, dT_corr=0.0):
    """Exhaust-side adiabatic cooler (indirect evaporative cooling before wheel).

    Faithful 1:1 port of the Bef_ABL==1 active block (main script lines
    ~847-888). Activation and target depend on the *supply* state at the fan
    (Strangkopplung). Branches (in MATLAB elseif order):
      A) supply too humid + cooling coil present -> maximum adiabatic cooling
      B) supply too humid, no dehumid coil        -> cool to upper setpoint
      C) supply too warm, humidity in band        -> cool to reach T_max via wheel
      D) supply warm, left x-band, phi<phi_min     -> cool to reach phi_min line
      else inactive.
    Returns (T_out, x_out, m_dot_water_factor) — exhaust state entering wheel.
    """
    p = sp.p_atm
    h_abl = ma.h(T_abl, x_abl)
    Tlim_max = sp.T_max + dT_corr
    Tlim_min = sp.T_min + dT_corr

    def _sat_point():
        T_s = ma.T_h_phi(h_abl, 1.0, p)
        x_s = ma.x(T_s, 1.0, p)
        return T_s, x_s

    # A) supply too humid and cooling coil present -> maximum cooling
    if x_zul_at_vent > sp.x_max_Tmax and KR_nAK == 1:
        _, x_s = _sat_point()
        x_out = x_abl + eta_bef * (x_s - x_abl)
        T_out = ma.T_from_hx(h_abl, x_out)
        return T_out, x_out, (x_out - x_abl)

    # B) supply too humid, no dehumid coil -> cool toward upper temp setpoint
    if x_zul_at_vent > sp.x_max_Tmax and KR_nAK == 0:
        _, x_s = _sat_point()
        x_nAK = x_abl + eta_bef * (x_s - x_abl)
        T_nAK = ma.T_from_hx(h_abl, x_nAK)
        T_out = max((sp.T_max - T_zul_at_vent) / 0.6 + T_zul_at_vent + dT_corr,
                    T_nAK)
        x_out = _x_for_h_at_T(h_abl, T_out)
        return T_out, x_out, (x_out - x_abl)

    # C) supply too warm, humidity within band -> controlled cool to reach T_max
    if T_zul_at_vent > sp.T_max and (sp.x_min_Tmax <= x_zul_at_vent
                                     <= sp.x_max_Tmax):
        _, x_s = _sat_point()
        x_nAK = x_abl + eta_bef * (x_s - x_abl)
        T_nAK = ma.T_from_hx(h_abl, x_nAK)
        T_out = max((sp.T_max - T_zul_at_vent) / 0.6 + T_zul_at_vent + dT_corr,
                    T_nAK)
        x_out = _x_for_h_at_T(h_abl, T_out)
        return T_out, x_out, (x_out - x_abl)

    # D) supply warm, left x-band, phi below phi_min -> cool to reach phi_min
    if (T_zul_at_vent > Tlim_min and sp.x_min_Tmin <= x_zul_at_vent
            <= sp.x_min_Tmax
            and ma.phi(T_zul_at_vent, x_zul_at_vent, p) < sp.phi_min):
        _, x_s = _sat_point()
        # MATLAB quirk: x_nAK base reads the (still-zero) output column, not the
        # inlet -> base is 0, not x_abl (faithful to lines 878).
        x_nAK = 0.0 + eta_bef * (x_s - x_abl)
        T_nAK = ma.T_from_hx(h_abl, x_nAK)
        T_phi = _T_for_x_at_phi(x_zul_at_vent, sp.phi_min, p)
        T_out = max((T_phi - T_zul_at_vent) / 0.6 + T_zul_at_vent + dT_corr,
                    T_nAK)
        x_out = _x_for_h_at_T(h_abl, T_out)
        return T_out, x_out, (x_out - x_abl)

    # otherwise inactive (exhaust passes unchanged to the wheel)
    return T_abl, x_abl, 0.0
