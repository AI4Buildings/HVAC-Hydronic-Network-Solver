"""Coupled room-humidity extension for the VKA simulation (EN 16798-5-1).

Builds ON TOP of the validated `simulate()` (which stays UNCHANGED — that is the
"fixed values / no internal moisture source" tool). This module adds the one thing
the base tool cannot express:

  * an internal ROOM MOISTURE LOAD (e.g. people) that couples supply and room:
        x_room = x_supply + dx ,   dx = moisture_g_h / (V_dry · rho)        [g/kg]
  * the ROOM / exhaust humidity given as a BAND  [room_rh_min, room_rh_max]
    instead of a single fixed value.

`simulate()` already does the expensive part — for a given outdoor air, exhaust and
a supply T-band + humidity-band it picks the energy-optimal rotor setting. The ONLY
unknown it cannot close is the room/exhaust humidity, because it is not free: it
equals the supply humidity plus the people load. So the problem is **1-dimensional**
in the room/exhaust humidity `r`.

This function therefore SWEEPS the room humidity `r` over [room_rh_min, room_rh_max]
(the single search variable). For each `r` the operating point is self-consistent —
exhaust = `r`, supply humidity = `r` − dx — so ONE `simulate()` call gives the
energy-optimal rotor/register loads for that `r`; per hour the lowest-energy feasible
`r` is kept. The supply TEMPERATURE needs no search grid: its energy optimum is
always at a band edge (20 °C when de-humidifying / heating, 23 °C when sensibly
cooling) or floating in the band when no humidity action is needed. So per `r` we
evaluate the two pinned edges {T_sup_min, T_sup_max} (where the base tool is exact)
plus one free-floating T-band run for the "no humidity action" hours; the feasibility
check keeps whichever honours the constraints. Single-threaded / portable — no
multiprocessing.
"""
import numpy as np
from .simulate import simulate
from . import moist_air as ma


def simulate_room(plant, T_AUL, hum_AUL,
                  T_room=23.0, room_rh_min=40.0, room_rh_max=55.0,
                  T_sup_min=20.0, T_sup_max=23.0,
                  moisture_g_h=0.0,
                  V_sup_m3h=None, V_exh_m3h=None,
                  rho_dry=1.2, humidity="rh", p_atm=1e5, dt_h=1.0,
                  n_grid=16, n_grid_T=4, objective=("heat", "cool", "humid"),
                  T_tol=0.05, rh_tol=0.1):
    """Year/time-series simulation with a coupled room (exhaust) humidity band.

    Inputs (scalars or 1-D arrays, broadcast to length N):
      T_AUL, hum_AUL : outdoor air  (hum_AUL unit per `humidity`: "rh" % or "x" g/kg)
      T_room         : room/exhaust temperature [°C]  (the WRG source temperature)
      room_rh_min/max: room/exhaust RELATIVE-humidity BAND [%]  (the requirement)
      T_sup_min/max  : supply temperature band [°C]
      moisture_g_h   : internal moisture load (e.g. persons) [g/h]  (0 = none)
      V_sup_m3h      : supply volume flow [m³/h] (0 = plant off that hour)
      n_grid         : resolution of the room/exhaust-humidity sweep (the single
                       search dimension; this is the accuracy driver).
      n_grid_T       : number of pinned supply-T support points (default 4, evenly
                       spaced over [T_sup_min, T_sup_max]). With each support point
                       supply T AND supply x are PINNED (T_min==T_max), where the
                       base tool is EXACT. A free-floating T-band run is added on top.
                       The supply-T optimum is at a band edge (de-humidify/heat -> 20,
                       sensible cool -> 23) OR at an interior "no-conditioning" point
                       (the rotor's natural outlet, often ~21 °C in winter). Edges +
                       free-T do NOT capture that interior optimum well, so a few
                       interior support points matter: 4 is ~2 %, 5 ~1 %, 7 reference
                       vs. a fine grid (raise it for reference-grade accuracy).
      objective      : which loads enter the minimisation (subset of
                       "heat","cool","humid")
      T_tol, rh_tol  : tolerances [K, %] by which a candidate may exceed the
                       supply-T / room-rh band and still count as feasible.

    Runtime: `simulate()` is NOT numpy-vectorised internally (it loops over the
    operating hours); this function calls it `n_grid*n_grid_T + n_grid` times over
    the whole period (single-threaded). With the defaults that is ~48 passes/year.
    The constraints (supply T in band, room rh in band) are honoured to within
    T_tol / rh_tol.

    Returns the same dict as `simulate()` (per-hour arrays + totals) PLUS:
      room_rh_pct : the chosen room/exhaust rel. humidity per hour [%]
      dx_load_gkg : the people moisture rise supply->room per hour [g/kg]
    """
    if room_rh_min > room_rh_max:
        raise ValueError("room_rh_min must be <= room_rh_max")

    # broadcast helpers ----------------------------------------------------
    cols = [T_AUL, hum_AUL, V_sup_m3h if V_sup_m3h is not None else 0.0]
    N = max(np.atleast_1d(np.asarray(c, float)).size for c in cols)

    def arr(v):
        a = np.atleast_1d(np.asarray(v, float))
        return np.full(N, a[0]) if a.size == 1 else a

    Vs = arr(V_sup_m3h)
    Ve = Vs if V_exh_m3h is None else arr(V_exh_m3h)
    op = Vs > 0
    mload = arr(moisture_g_h)

    # people moisture balance: dx = load[g/h] / (V[m3/h] * rho)  -> g/kg
    dx_load = np.zeros(N)
    dx_load[op] = mload[op] / (Vs[op] * rho_dry)

    # search grids: room rel. humidity [%] (the search dim) x supply-T support pts
    rh_grid = (np.array([room_rh_min]) if n_grid < 2 or room_rh_min == room_rh_max
               else np.linspace(room_rh_min, room_rh_max, n_grid))
    T_grid = (np.array([T_sup_min]) if n_grid_T < 2 or T_sup_min == T_sup_max
              else np.linspace(T_sup_min, T_sup_max, n_grid_T))

    keys = {"heat": "Q_heat_total_kW", "cool": "Q_cool_KR_kW",
            "humid": "Q_humid_Bef_kW"}
    obj_keys = [keys[o] for o in objective]
    scalar_arrays = ["Q_heat_VHR_kW", "Q_heat_NHR_kW", "Q_heat_FS_kW",
                     "Q_heat_total_kW", "Q_cool_KR_kW", "Q_recovery_WRG_kW",
                     "Q_humid_Bef_kW", "water_kg_h", "T_sup_C", "x_sup_gkg",
                     "phi_sup_pct", "eta_hr", "eta_xr", "n_rot"]

    # running minimum over the candidates — O(N) memory ---------------------
    best_E = np.full(N, np.inf)
    res = {k: np.full(N, np.nan) for k in scalar_arrays}
    res["room_rh_pct"] = np.full(N, np.nan)

    def eval_candidate(rh_room, t_lo, t_hi):
        """One self-consistent operating point: exhaust = rh_room, supply humidity
        pinned to (rh_room - dx). Returns the full simulate() output dict."""
        x_room = ma.rh_to_x(T_room, rh_room)
        x_sup_t = np.maximum(x_room - dx_load, 0.1)            # supply x target [g/kg]
        return simulate(plant, T_AUL, hum_AUL, T_room, rh_room,
                        t_lo, t_hi, hum_sup_min=x_sup_t, hum_sup_max=x_sup_t,
                        supply_band="x", V_sup_m3h=Vs, V_exh_m3h=Ve,
                        humidity=humidity, p_atm=p_atm, dt_h=dt_h)

    def consider(out):
        """Compete this candidate in the per-hour running minimum, but ONLY for
        hours where it actually honours the constraints (supply T in band AND
        resulting room rh in band). Infeasible hours are skipped."""
        Ts = np.asarray(out["T_sup_C"], float)
        room_act = ma.x_to_rh(T_room, np.nan_to_num(out["x_sup_gkg"]) + dx_load)
        feasible = (op
                    & (Ts <= T_sup_max + T_tol) & (Ts >= T_sup_min - T_tol)
                    & (room_act <= room_rh_max + rh_tol)
                    & (room_act >= room_rh_min - rh_tol))
        e = sum(np.nan_to_num(out[k]) for k in obj_keys)
        e = np.where(feasible, e, np.inf)
        upd = e < best_E
        if upd.any():
            best_E[upd] = e[upd]
            for k in scalar_arrays:
                if k in out:
                    res[k][upd] = np.asarray(out[k], float)[upd]
            res["room_rh_pct"][upd] = room_act[upd]

    # 1-D sweep over the room/exhaust humidity. Per r: the pinned supply-T edges
    # (exact) plus one free-floating T-band run (for no-humidity-action hours).
    for rh_room in rh_grid:
        for t_sup in T_grid:
            consider(eval_candidate(float(rh_room), float(t_sup), float(t_sup)))
        if T_grid.size > 1:
            consider(eval_candidate(float(rh_room), T_sup_min, T_sup_max))
    res["room_rh_pct"][~op] = np.nan
    res["dx_load_gkg"] = dx_load

    # totals ---------------------------------------------------------------
    def tsum(k):
        return float(np.nan_to_num(res[k]).sum() * dt_h) if k in res else 0.0
    res["totals"] = dict(
        heating_kWh=tsum("Q_heat_total_kW"),
        cooling_kWh=tsum("Q_cool_KR_kW"),
        recovery_kWh=tsum("Q_recovery_WRG_kW"),
        humid_kWh=tsum("Q_humid_Bef_kW"),
        water_kg=tsum("water_kg_h"),
        hours=float(N) * dt_h,
        operating_hours=float(op.sum()) * dt_h,
    )
    return res
