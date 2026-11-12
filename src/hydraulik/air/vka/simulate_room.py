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
(the single search variable). For each `r` the exhaust is ASSUMED at `r` and the
supply humidity is pinned to `r` − dx; ONE `simulate()` call gives the
energy-optimal rotor/register loads for that assumption. A candidate only counts
if it is SELF-CONSISTENT: the room humidity it produces (achieved supply humidity
+ dx) must equal the assumed exhaust humidity `r` within `consistency_tol` — a
plant that cannot reach the pinned supply humidity (no humidifier, no
dehumidification) would otherwise be credited with moisture recovery from an
exhaust state that does not exist. Per hour the lowest-energy feasible candidate
is kept.

The supply TEMPERATURE needs no fine search grid: its energy optimum is at a band
edge (20 °C when de-humidifying / heating, 23 °C when sensibly cooling) or at an
interior "no-conditioning" point (the rotor's natural outlet). So per `r` we
evaluate `n_grid_T` pinned supply temperatures plus the natural point: a
free-floating T-band run finds that temperature, and it is then re-evaluated with
supply T AND x pinned (the free-floating run itself pins φ, not x, and is therefore
only kept where it happens to be self-consistent).

If no self-consistent candidate holds the room-humidity band (band not reachable,
or a plant without humidity control whose natural state lies between the grid
points), the steady state is solved directly from the moisture balance
        x_room = x_supply(x_room) + dx
by bisection on the room humidity (controller target = nearest band value). The
result is always a balance-consistent operating point; `room_band_ok` /
`T_band_ok` tell whether it honours the requested bands. Single-threaded /
portable — no multiprocessing.
"""
import warnings
import numpy as np
from .simulate import simulate, _series_length
from . import moist_air as ma


def simulate_room(plant, T_AUL, hum_AUL,
                  T_room=23.0, room_rh_min=40.0, room_rh_max=55.0,
                  T_sup_min=20.0, T_sup_max=23.0,
                  moisture_g_h=0.0,
                  V_sup_m3h=None, V_exh_m3h=None,
                  rho_dry=1.2, humidity="rh", p_atm=1e5, dt_h=1.0,
                  n_grid=16, n_grid_T=4, objective=("heat", "cool", "humid"),
                  T_tol=0.05, rh_tol=0.1, consistency_tol=0.25, n_bisect=14):
    """Year/time-series simulation with a coupled room (exhaust) humidity band.

    Inputs (scalars or 1-D arrays, broadcast to length N; every series must have
    length 1 or N):
      T_AUL, hum_AUL : outdoor air  (hum_AUL unit per `humidity`: "rh" % or "x" g/kg)
      T_room         : room/exhaust temperature [°C]  (the WRG source temperature)
      room_rh_min/max: room/exhaust RELATIVE-humidity BAND [%]  (scalars; the
                       requirement — always in %, independent of `humidity`)
      T_sup_min/max  : supply temperature band [°C]  (scalars)
      moisture_g_h   : internal moisture load (e.g. persons) [g/h]  (0 = none)
      V_sup_m3h      : supply volume flow [m³/h] (0 = plant off that hour)
      n_grid         : resolution of the room/exhaust-humidity sweep (the single
                       search dimension; this is the accuracy driver).
      n_grid_T       : number of pinned supply-T support points (default 4, evenly
                       spaced over [T_sup_min, T_sup_max]). With each support point
                       supply T AND supply x are PINNED (T_min==T_max), where the
                       base tool is EXACT. On top, the natural (free-floating)
                       supply temperature is found per `r` and re-evaluated pinned.
      objective      : which loads enter the minimisation (subset of
                       "heat","cool","humid")
      T_tol, rh_tol  : tolerances [K, %] by which a candidate may exceed the
                       supply-T / room-rh band and still count as feasible.
      consistency_tol: largest allowed difference [% rH] between the exhaust
                       humidity assumed for a candidate and the room humidity it
                       produces (covers the kernel's own tolerances, e.g. the
                       0.03 g/kg dehumidification tolerance).
      n_bisect       : bisection steps of the moisture-balance fallback.

    Runtime: `simulate()` is NOT numpy-vectorised internally (it loops over the
    operating hours); this function calls it about `n_grid*(n_grid_T + 2)` times
    over the whole period (single-threaded), plus `n_bisect + 2` passes over the
    hours that need the fallback.

    Returns the same dict as `simulate()` (per-hour arrays + totals) PLUS:
      room_rh_pct   : the resulting room/exhaust rel. humidity per hour [%]
                      (= achieved supply humidity + dx — always the balance value)
      dx_load_gkg   : the people moisture rise supply->room per hour [g/kg]
      room_band_ok  : bool — room humidity within [room_rh_min, room_rh_max]
      T_band_ok     : bool — supply temperature within [T_sup_min, T_sup_max]
      rh_residual_pct : |assumed exhaust humidity − resulting room humidity| of
                      the chosen point [% rH]; above `consistency_tol` only where
                      the plant's control jumps and no steady state exists.
    Hours with zero flow are OFF (loads 0, states NaN) and hours with a NaN input
    are data gaps (everything NaN) — as in `simulate()`.
    """
    if room_rh_min > room_rh_max:
        raise ValueError("room_rh_min must be <= room_rh_max")
    if T_sup_min > T_sup_max:
        raise ValueError("supply band: T_sup_min must be <= T_sup_max")
    if not (0.0 <= room_rh_min and room_rh_max <= 100.0):
        raise ValueError("room_rh_min/room_rh_max must be in 0..100 %")
    if humidity not in ("rh", "x"):
        raise ValueError(f"humidity must be 'rh' or 'x', got {humidity!r}")
    if V_sup_m3h is None:
        raise ValueError("V_sup_m3h is required (supply volume flow in m3/h)")

    # broadcast helpers ----------------------------------------------------
    N = _series_length(dict(
        T_AUL=T_AUL, hum_AUL=hum_AUL, T_room=T_room, moisture_g_h=moisture_g_h,
        V_sup_m3h=V_sup_m3h,
        V_exh_m3h=V_sup_m3h if V_exh_m3h is None else V_exh_m3h))

    def arr(v):
        a = np.atleast_1d(np.asarray(v, float))
        return np.full(N, a[0]) if a.size == 1 else a

    Ta, ha, Troom = arr(T_AUL), arr(hum_AUL), arr(T_room)
    Vs = arr(V_sup_m3h)
    Ve = Vs if V_exh_m3h is None else arr(V_exh_m3h)
    mload = arr(moisture_g_h)
    valid = (np.isfinite(Ta) & np.isfinite(ha) & np.isfinite(Troom)
             & np.isfinite(Vs) & np.isfinite(Ve) & np.isfinite(mload))
    op = valid & (Vs > 0) & (Ve > 0)

    # people moisture balance: dx = load[g/h] / (V[m3/h] * rho)  -> g/kg
    dx_load = np.zeros(N)
    dx_load[op] = mload[op] / (Vs[op] * rho_dry)

    def x_of_rh(rh):
        return ma.rh_to_x(Troom, rh, p_atm)

    def rh_of_x(x_gkg):
        return ma.x_to_rh(Troom, x_gkg, p_atm)

    # search grids: room rel. humidity [%] (the search dim) x supply-T support pts
    rh_grid = (np.array([room_rh_min]) if n_grid < 2 or room_rh_min == room_rh_max
               else np.linspace(room_rh_min, room_rh_max, n_grid))
    T_grid = (np.array([T_sup_min]) if n_grid_T < 2 or T_sup_min == T_sup_max
              else np.linspace(T_sup_min, T_sup_max, n_grid_T))

    keys = {"heat": "Q_heat_total_kW", "cool": "Q_cool_KR_kW",
            "humid": "Q_humid_Bef_kW"}
    obj_keys = [keys[o] for o in objective]
    load_arrays = ["Q_heat_VHR_kW", "Q_heat_NHR_kW", "Q_heat_FS_kW",
                   "Q_heat_total_kW", "Q_cool_KR_kW", "Q_recovery_WRG_kW",
                   "Q_humid_Bef_kW", "water_kg_h"]
    state_arrays = ["T_sup_C", "x_sup_gkg", "phi_sup_pct", "eta_hr", "eta_xr",
                    "n_rot", "RWZ_KVS", "KR_dew_limited"]
    # Zusatzausgaben von simulate() (Stationszustände für Schema-Anzeigen),
    # soweit vorhanden: sie gehören zum SELBEN Betriebspunkt wie die Lasten
    extra_arrays = ["T_eta_wheel_C", "x_eta_wheel_gkg", "m_dot_kg_s"]

    res = {k: np.full(N, np.nan) for k in load_arrays + state_arrays}
    res["room_rh_pct"] = np.full(N, np.nan)
    res["rh_residual_pct"] = np.full(N, np.nan)
    extras: dict = {}
    chain: list = []

    # running best per hour. rank 0 = zulässig (min. Energie); rank 1 =
    # konsistent und Raumfeuchte im Band, aber Zuluft-T-Band verletzt
    # (kleinste Verletzung, dann Energie); rank 2 = Feuchtebilanz-Rückfall;
    # 9 = noch nichts.
    rank = np.full(N, 9)
    best_E = np.full(N, np.inf)
    best_dT = np.full(N, np.inf)

    def run(rh_exh, rh_target, t_lo, t_hi):
        """One simulate() pass: exhaust assumed at rh_exh [%], supply humidity
        pinned to x(rh_target) − dx. Hours with NaN in rh_exh are skipped."""
        rh_exh = np.broadcast_to(np.asarray(rh_exh, float), (N,))
        x_sup_t = np.maximum(x_of_rh(rh_target) - dx_load, 0.1)   # supply x [g/kg]
        # Abluftfeuchte in der Einheit, die `humidity` für AUL UND ABL festlegt
        # (früher wurde bei humidity="x" die Raumfeuchte in % als g/kg gelesen)
        hum_abl = x_of_rh(rh_exh) if humidity == "x" else rh_exh
        with warnings.catch_warnings():
            # Band-Hinweise von simulate() (φ-Untergrenze, Sättigung) betreffen
            # hier nur interne Suchkandidaten, nicht die Vorgabe des Aufrufers
            warnings.simplefilter("ignore", UserWarning)
            return simulate(plant, Ta, ha, Troom, hum_abl,
                            t_lo, t_hi, hum_sup_min=x_sup_t, hum_sup_max=x_sup_t,
                            supply_band="x", V_sup_m3h=Vs, V_exh_m3h=Ve,
                            humidity=humidity, p_atm=p_atm, dt_h=dt_h)

    def take(out, upd, room_act, resid):
        for k in load_arrays + state_arrays:
            if k in out:
                res[k][upd] = np.asarray(out[k], float)[upd]
        for k in extra_arrays:
            if k in out:
                extras.setdefault(k, np.full(N, np.nan))[upd] = \
                    np.asarray(out[k], float)[upd]
        if "chain" in out:
            if not chain:
                chain.extend({"T": None, "x_gkg": None} for _ in range(N))
            for i in np.flatnonzero(upd):
                chain[i] = out["chain"][i]
        res["room_rh_pct"][upd] = room_act[upd]
        res["rh_residual_pct"][upd] = resid[upd]

    def assess(out, rh_exh):
        """Resulting room humidity, consistency residual, T-band violation and
        objective energy of one pass."""
        Ts = np.asarray(out["T_sup_C"], float)
        xs = np.asarray(out["x_sup_gkg"], float)
        done = op & np.isfinite(Ts) & np.isfinite(xs)
        room_act = rh_of_x(np.nan_to_num(xs) + dx_load)
        resid = np.abs(room_act - np.broadcast_to(np.asarray(rh_exh, float), (N,)))
        dT = np.maximum(np.maximum(np.nan_to_num(Ts) - T_sup_max,
                                   T_sup_min - np.nan_to_num(Ts)), 0.0)
        e = sum(np.nan_to_num(out[k]) for k in obj_keys)
        return done, room_act, resid, dT, e

    def consider(out, rh_exh):
        """Compete this candidate in the per-hour running best. It must be
        self-consistent (assumed exhaust humidity == resulting room humidity)
        and keep the room humidity in its band; candidates that additionally
        hold the supply-T band (rank 0) beat those that only miss it (rank 1)."""
        done, room_act, resid, dT, e = assess(out, rh_exh)
        ok = (done & (resid <= consistency_tol)
              & (room_act <= room_rh_max + rh_tol)
              & (room_act >= room_rh_min - rh_tol))
        in_T = dT <= T_tol
        upd0 = ok & in_T & ((rank > 0) | (e < best_E))
        upd1 = (ok & ~in_T & (rank >= 1)
                & ((rank > 1) | (dT < best_dT - 1e-9)
                   | ((dT <= best_dT + 1e-9) & (e < best_E))))
        for upd, rk in ((upd0, 0), (upd1, 1)):
            if upd.any():
                rank[upd] = rk
                best_E[upd] = e[upd]
                best_dT[upd] = dT[upd]
                take(out, upd, room_act, resid)

    # 1-D sweep over the room/exhaust humidity. Per r: the pinned supply-T
    # support points (exact) plus the natural supply temperature of a
    # free-floating T-band run, re-evaluated pinned.
    for rh_room in rh_grid:
        r = float(rh_room)
        for t_sup in T_grid:
            consider(run(r, r, float(t_sup), float(t_sup)), r)
        if T_grid.size > 1:
            free = run(r, r, T_sup_min, T_sup_max)
            consider(free, r)
            t_nat = np.clip(np.asarray(free["T_sup_C"], float), T_sup_min, T_sup_max)
            t_nat[np.isin(np.round(t_nat, 6), np.round(T_grid, 6))] = np.nan
            if np.isfinite(t_nat).any():
                consider(run(np.where(np.isfinite(t_nat), r, np.nan), r,
                             t_nat, t_nat), r)

    # Feuchtebilanz-Rückfall: für Stunden ohne selbstkonsistenten Kandidaten im
    # Raumfeuchteband den stationären Zustand x_room = x_sup(x_room) + dx
    # direkt lösen. G(r) = Raumfeuchte(Abluft r) − r wechselt auf [0, 100] das
    # Vorzeichen (G(0) ≥ 0, G(100) ≤ 0); die Regelung zielt dabei auf den
    # nächstgelegenen Bandwert.
    todo = op & (rank > 1)
    if todo.any():
        g_best = np.full(N, np.inf)

        def G(r_arr):
            out = run(r_arr, np.clip(r_arr, room_rh_min, room_rh_max),
                      T_sup_min, T_sup_max)
            done, room_act, resid, dT, e = assess(out, r_arr)
            g = np.where(done, room_act - r_arr, np.nan)
            upd = done & todo & (np.abs(g) < g_best)
            if upd.any():
                g_best[upd] = np.abs(g[upd])
                rank[upd] = 2
                best_E[upd] = e[upd]
                best_dT[upd] = dT[upd]
                take(out, upd, room_act, resid)
            return g

        nan = np.full(N, np.nan)
        g_lo = G(np.where(todo, room_rh_min, nan))
        g_hi = G(np.where(todo, room_rh_max, nan))
        dry = todo & (g_lo < 0)                  # Raum trockener als das Band
        humid = todo & ~dry & (g_hi > 0)         # Raum feuchter als das Band
        a = np.where(dry, 0.0, np.where(humid, room_rh_max, room_rh_min))
        b = np.where(dry, room_rh_min, np.where(humid, 100.0, room_rh_max))
        active = todo & (b > a) & (g_best > 1e-4)
        for _ in range(int(n_bisect)):
            if not active.any():
                break
            mid = 0.5 * (a + b)
            g = G(np.where(active, mid, nan))
            pos = active & (g > 0)
            neg = active & ~pos & np.isfinite(g)
            a = np.where(pos, mid, a)
            b = np.where(neg, mid, b)
            active &= np.isfinite(g) & (g_best > 1e-4)

    # Anlage aus (V = 0): keine Luft, Lasten 0 — wie simulate()
    off = valid & ~op
    for k in load_arrays:
        res[k][off] = 0.0
    res["n_rot"][off] = 0.0

    room = res["room_rh_pct"]
    Tsup = res["T_sup_C"]
    with np.errstate(invalid="ignore"):
        res["room_band_ok"] = (op & (room <= room_rh_max + rh_tol)
                               & (room >= room_rh_min - rh_tol))
        res["T_band_ok"] = (op & (Tsup <= T_sup_max + T_tol)
                            & (Tsup >= T_sup_min - T_tol))
    res["dx_load_gkg"] = dx_load
    res.update(extras)
    if chain:
        res["chain"] = chain

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
        hours_room_band_missed=float((op & ~res["room_band_ok"]).sum()) * dt_h,
    )
    return res
