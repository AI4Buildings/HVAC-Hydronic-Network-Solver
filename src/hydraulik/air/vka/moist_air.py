#!/usr/bin/env python3
"""Moist-air properties — faithful 1:1 port of the MATLAB h,x model.

These reproduce the Mollier (h,x) simplified model used by the Energetikum VKA
MATLAB toolbox, NOT CoolProp's real-gas model. Keeping the exact same model is
what makes the Python results validate against the MATLAB reference points.

Model constants (from the .m files):
    cp_air      = 1.0   kJ/(kg·K)
    h_evap_0    = 2500  kJ/kg      (water evaporation enthalpy at 0 °C)
    cp_vapour   = 1.85  kJ/(kg·K)
    R_ratio     = 0.622 (M_water / M_air)
Temperatures are in °C, pressures in Pa, humidity ratio x in kg/kg,
relative humidity phi in [0,1] (NOT %), enthalpy h in kJ/kg (dry-air basis,
reference 0 °C). p_atm is passed explicitly (the MATLAB script uses 1e5 Pa).

ps(T) is the exact split Magnus correlation from the toolbox's ps.m. All seven
functions are now MATLAB-faithful.
"""

from __future__ import annotations
import numpy as np
from scipy.optimize import minimize_scalar

# --- model constants (verbatim from the MATLAB functions) --------------------
CP_AIR = 1.0       # kJ/(kg·K)
H_EVAP_0 = 2500.0  # kJ/kg
CP_VAP = 1.85      # kJ/(kg·K)
R_RATIO = 0.622    # M_water / M_air


# =============================================================================
# PLACEHOLDER — replace with the exact ps.m correlation once provided.
# =============================================================================
def ps(T):
    """Saturation vapour pressure [Pa] as a function of T [°C].

    Exact port of ps.m — a split Magnus-type correlation, different coefficients
    below and at/above 0 °C:
        T <  0:  p_s = 611.0 * exp(22.44*T / (272.44 + T))
        T >= 0:  p_s = 611.0 * exp(17.08*T / (234.18 + T))
    """
    T = np.asarray(T, dtype=float)
    A1, A2 = 22.44, 17.08
    B1, B2 = 272.44, 234.18
    below = 611.0 * np.exp(A1 * T / (B1 + T))
    atabove = 611.0 * np.exp(A2 * T / (B2 + T))
    return np.where(T < 0.0, below, atabove)


# =============================================================================
# Faithful ports of the MATLAB .m files
# =============================================================================
def h(T, x):
    """Specific enthalpy h_{1+x} [kJ/kg] — from h.m:  h = 1.0*T + x*(2500+1.85*T)."""
    return CP_AIR * np.asarray(T, float) + np.asarray(x, float) * (H_EVAP_0 + CP_VAP * np.asarray(T, float))


def x(T, phi, p_atm):
    """Humidity ratio [kg/kg] — from x.m:  x = 0.622*phi*ps(T)/(p_atm - phi*ps(T))."""
    pst = ps(T)
    return R_RATIO * phi * pst / (p_atm - phi * pst)


def xs(T, p_atm):
    """Saturation humidity ratio [kg/kg] — from xs.m: x = 0.622*ps(T)/(p_atm-ps(T))."""
    pst = ps(T)
    return R_RATIO * pst / (p_atm - pst)


def phi(T, x_val, p_atm):
    """Relative humidity [0..1] — from phi.m: min(x*p/(ps(T)*(0.622+x)), 1)."""
    val = np.asarray(x_val, float) * p_atm / (ps(T) * (R_RATIO + np.asarray(x_val, float)))
    return np.minimum(val, 1.0)


# --- agent-friendly convenience wrappers (PERCENT in, g/kg out) --------------
# Use these for input preparation (e.g. a moisture-balance supply band) so you
# never need CoolProp. Same h,x model as the simulation. Scalars or arrays.
def rh_to_x(T, rh_percent, p_atm=1e5):
    """Absolute humidity [g/kg] from temperature [°C] and rel. humidity [%]."""
    return x(T, np.asarray(rh_percent, float) / 100.0, p_atm) * 1000.0


def x_to_rh(T, x_gkg, p_atm=1e5):
    """Rel. humidity [%] from temperature [°C] and absolute humidity [g/kg]."""
    return phi(T, np.asarray(x_gkg, float) / 1000.0, p_atm) * 100.0


def T_from_hx(h_val, x_val):
    """Temperature [°C] from h and x — from T.m: T = (h - 2500*x)/(1 + 1.85*x)."""
    return (np.asarray(h_val, float) - H_EVAP_0 * np.asarray(x_val, float)) / (
        1.0 + CP_VAP * np.asarray(x_val, float)
    )


def T_h_phi(h_val, phi_val, p_atm):
    """Temperature [°C] from h and phi — from T_h_phi.m.

    Solves h = 1.0*T + x(T,phi)*(2500+1.85*T) for T on [0,100] °C, matching the
    MATLAB fminbnd objective.
    """
    def obj(T):
        xT = R_RATIO * phi_val * ps(T) / (p_atm - phi_val * ps(T))
        return (h_val - (CP_AIR * T + xT * (H_EVAP_0 + CP_VAP * T))) ** 2
    res = minimize_scalar(obj, bounds=(0.0, 100.0), method="bounded")
    return float(res.x)


def Ts(x_val, p_atm):
    """Dew-point / saturation temperature [°C] from x — from Ts.m.

    Solves x = xs(T) for T on [0,100] °C (MATLAB fminbnd objective).
    """
    def obj(T):
        return (x_val - xs(T, p_atm)) ** 2
    res = minimize_scalar(obj, bounds=(0.0, 100.0), method="bounded")
    return float(res.x)


if __name__ == "__main__":
    P = 1e5
    print("Smoke test (exact MATLAB ps):")
    print(f"  h(25, 0.010)      = {h(25.0, 0.010):.4f} kJ/kg")
    print(f"  x(25, 0.5, 1e5)   = {x(25.0, 0.5, P):.6f} kg/kg")
    print(f"  xs(25, 1e5)       = {xs(25.0, P):.6f} kg/kg")
    print(f"  phi(25, 0.010,1e5)= {phi(25.0, 0.010, P):.4f}")
    print(f"  T_from_hx(50,0.01)= {T_from_hx(50.0, 0.010):.4f} °C")
    print(f"  T_h_phi(50,0.5,1e5)={T_h_phi(50.0, 0.5, P):.4f} °C")
    print(f"  Ts(0.010, 1e5)    = {Ts(0.010, P):.4f} °C")
