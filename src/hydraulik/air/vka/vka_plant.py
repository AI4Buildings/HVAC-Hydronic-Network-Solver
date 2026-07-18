#!/usr/bin/env python3
"""VKA plant driver — runs a full rotor-based AHU over operating points.

Ties together the validated components (moist_air, heat_rec_wheel, vka_chain)
into a single call that computes, for each operating point, the air state after
every component and the component heat loads. Validated bit-faithfully against
both reference plants of the VKA tool (see validate_vka.py).

Two ready-made plant configurations match the validation examples:
    plant_anlage1()  sorption wheel + VHR + cooler + spray humidifier + NHR
    plant_anlage2()  condensation wheel + adiabatic exhaust cooling + cooler + NHR

Build your own with PlantConfig.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import numpy as np
from . import moist_air as ma
from .vka_chain import (Setpoints, fan, wheel, preheater, cooler,
                       spray_humidifier, reheater, adiabatic_cooler,
                       frost_protection, steam_humidifier, plate_recovery)
from .kvs import kvs_recovery


@dataclass
class PlantConfig:
    heat_rec_type: str          # 'ROT_SORP' | 'ROT_HYG' | 'ROT_NH' (rotor)
    SFP: float                  # W/(m3/s), supply & exhaust fan
    f_rec: float = 0.6          # fan motor heat recovery fraction
    has_VHR: bool = False       # pre-heater present
    has_KR: bool = True         # cooling register present
    has_spray: bool = False     # supply humidifier present (type via bef_type)
    has_NHR: bool = True         # re-heater present
    has_adiab_exhaust: bool = False   # exhaust-side adiabatic cooling
    eta_bef: float = 0.9
    T_H2O: float = 15.0         # humidifier water temperature [C]
    V_nom_m3h: float = 10000.0  # design flow for the wheel
    p_atm: float = 1e5
    # --- extensions: WRG type, frost protection, humidifier type, KVS ---------
    wrg: str = "ROTOR"          # 'ROTOR' | 'KVS' | 'PLATE'
    eta_hr_N: float = 0.0       # rotor design sensible effectiveness (0 = norm)
    eta_xr_N: float = 0.0       # rotor design latent effectiveness (0 = norm)
    has_FS: bool = False        # frost protection present
    fs_type: int = 1            # 1 = electric pre-heater, 2 = WRG bypass
    T_FS_Grenz: float = -3.0    # frost-protection limit temperature [C]
    bef_type: int = 1           # supply humidifier: 1 spray, 2 steam
    exhaust_fan_before_wrg: bool = False   # ABL fan upstream of WRG (KVS plant)
    has_uml: bool = False                  # Umluft (recirculation) bypass
    V_UML_m3h: float = 0.0                 # recirculation volume flow [m3/h]
    zul_order: Optional[list] = None       # explicit supply component order
    # KVS design parameters
    V_M_KVS_N: float = 2.5      # design medium flow [m3/h]
    rho_M_KVS: float = 1000.0
    cp_M_KVS: float = 4.19
    RWZ_KVS_ZUL_N: float = 0.76
    RWZ_KVS_ABL_N: float = 0.76
    V_ZUL_KVS_N: float = 10000.0
    V_ABL_KVS_N: float = 10000.0
    # plate (PWÜ, WRG='PLATE') design parameters
    RWZ_WT_N: float = 0.6       # design supply effectiveness (Rückwärmezahl)
    V_ZUL_WT_N: float = 10000.0
    V_ABL_WT_N: float = 10000.0


@dataclass
class OperatingPoint:
    T_AUL: float; phi_AUL: float       # outdoor (phi in %)
    T_ABL: float; phi_ABL: float       # room exhaust (phi in %)
    V_sup_m3h: float
    V_exh_m3h: Optional[float] = None   # defaults to V_sup
    # optional: pass absolute humidity directly [kg/kg]; if set, used instead of
    # computing x from phi (phi_AUL/phi_ABL are then ignored).
    x_AUL: Optional[float] = None
    x_ABL: Optional[float] = None


def _default_order(cfg: PlantConfig):
    order = ["Vent_ZUL"]
    if cfg.has_FS:
        order.append("FS")
    order.append("WRG")
    if cfg.has_VHR:
        order.append("VHR")
    if cfg.has_KR:
        order.append("KR")
    if cfg.has_spray:
        order.append("Bef")
    if cfg.has_NHR:
        order.append("NHR")
    return order


def run_plant(cfg: PlantConfig, sp: Setpoints, ops: list[OperatingPoint]):
    """Run the plant over all operating points. Returns a list of dicts, one per
    point, with the state after each supply component and the heat loads (kW).
    Components are processed in cfg.zul_order (rotor-WRG or KVS)."""
    h_H2O = (2500.9 + 1.86 * cfg.T_H2O) if cfg.bef_type == 2 else 4.18 * cfg.T_H2O
    T_set_mean = 0.5 * (sp.T_min + sp.T_max)
    order = cfg.zul_order or _default_order(cfg)
    Bef_ZUL = cfg.bef_type if cfg.has_spray else 0
    wi = order.index("WRG")
    after_wrg = order[wi + 1:]
    Bef_n_WRG = 1 if "Bef" in after_wrg else 0
    KR_n_WRG = 1 if ("KR" in after_wrg and cfg.has_KR) else 0
    results = []

    for op in ops:
        V = op.V_sup_m3h
        V_exh = op.V_exh_m3h if op.V_exh_m3h is not None else V
        x_aul = op.x_AUL if op.x_AUL is not None else ma.x(op.T_AUL, op.phi_AUL / 100.0, cfg.p_atm)
        x_abl = op.x_ABL if op.x_ABL is not None else ma.x(op.T_ABL, op.phi_ABL / 100.0, cfg.p_atm)
        dT_corr = 0.0  # channel losses neglected (WV off)

        # supply fan (always first)
        T1, x1, m_dot, Q_fan, dTv = fan(op.T_AUL, x_aul, V, cfg.SFP, cfg.f_rec,
                                        T_set_mean, cfg.p_atm)

        # exhaust state entering the WRG
        rho = cfg.p_atm / (287.0 * (273.0 + T_set_mean))
        m_dot_abl = V_exh / 3600.0 * rho
        if cfg.exhaust_fan_before_wrg:        # KVS plant: ABL fan upstream
            dT_eta_fan = (cfg.f_rec * (cfg.SFP / 1000.0) * (V_exh / 3600.0)
                          / m_dot_abl) if m_dot_abl > 0 else 0.0
            T_eta, x_eta = op.T_ABL + dT_eta_fan, x_abl
        elif cfg.has_adiab_exhaust and cfg.heat_rec_type in ("ROT_HYG", "ROT_NH"):
            T_eta, x_eta, _ = adiabatic_cooler(op.T_ABL, x_abl, T1, x1, sp,
                                               cfg.eta_bef,
                                               KR_nAK=1 if cfg.has_KR else 0)
        else:
            T_eta, x_eta = op.T_ABL, x_abl

        T, x = T1, x1
        chain_T = {"Vent_ZUL": T1}
        chain_x = {"Vent_ZUL": x1}
        Q = {}
        m_w = 0.0
        eta_hr = eta_xr = n_rot = 0.0
        RWZ_kvs = float("nan"); V_M_kvs = 0.0
        T2 = x2 = None

        FS_Byp = False
        # Umluft-Bypass: recirculate m_dot_UML of room air. Components upstream
        # of the mix see the reduced (fresh-air) flow m_dot - m_dot_UML; those
        # downstream see the full flow (faithful to the V_dot_*_calc rules).
        m_dot_UML = (cfg.V_UML_m3h / 3600.0 * rho) if cfg.has_uml else 0.0
        uml_seen = False
        for comp in order[1:]:
            flow = m_dot if (uml_seen or not cfg.has_uml) else max(0.0, m_dot - m_dot_UML)
            if comp == "UML_Byp":
                if m_dot > 0 and (m_dot - m_dot_UML) >= 0:
                    T = (T * (m_dot - m_dot_UML) + op.T_ABL * m_dot_UML) / m_dot
                    x = (x * (m_dot - m_dot_UML) + x_abl * m_dot_UML) / m_dot
                uml_seen = True
            elif comp == "FS":
                if cfg.fs_type == 2:          # bypass: VHR takes over frost duty
                    FS_Byp = (m_dot > 0 and T < cfg.T_FS_Grenz)
                    Q["FS"] = 0.0             # no heating by FS itself
                else:                          # FS==1 electric pre-heater
                    # MATLAB Q_dot_FS uses m_dot_AUL (full outdoor flow)
                    T, x, Q["FS"] = frost_protection(T, x, m_dot, cfg.T_FS_Grenz)
            elif comp == "WRG" and FS_Byp:
                # frost bypass: supply skips the WRG (no recovery), eta/n_rot 0
                T2, x2 = T, x
                Q["WRG"] = 0.0
            elif comp == "WRG":
                # effective WRG flows (reduced by recirc when WRG is upstream of
                # the bypass mix; the exhaust tap-off always reduces the ABL flow)
                V_sup_w = (V - cfg.V_UML_m3h) if (cfg.has_uml and not uml_seen) else V
                V_exh_w = (V_exh - cfg.V_UML_m3h) if cfg.has_uml else V_exh
                if cfg.wrg == "KVS":
                    T_in_wrg = T
                    T, x, T_eta_out, RWZ_kvs, V_M_kvs = kvs_recovery(
                        T, x, T_eta, x_eta, V_sup_w, V_exh_w, sp,
                        cfg.V_M_KVS_N, cfg.rho_M_KVS, cfg.cp_M_KVS,
                        cfg.RWZ_KVS_ZUL_N, cfg.RWZ_KVS_ABL_N,
                        cfg.V_ZUL_KVS_N, cfg.V_ABL_KVS_N,
                        Bef_ZUL, Bef_n_WRG, KR_n_WRG, 1, h_H2O, dT_corr)
                    Q["WRG"] = flow * (ma.h(T, x) - ma.h(T_in_wrg, x))
                elif cfg.wrg == "PLATE":
                    T_in_wrg = T
                    T, x, RWZ_kvs = plate_recovery(
                        T, x, T_eta, V_sup_w, V_exh_w, cfg.RWZ_WT_N,
                        cfg.V_ZUL_WT_N, cfg.V_ABL_WT_N)
                    Q["WRG"] = flow * (ma.h(T, x) - ma.h(T_in_wrg, x))
                else:
                    T_in_wrg, x_in_wrg = T, x
                    T, x, eta_hr, eta_xr, n_rot = wheel(
                        T, x, T_eta, x_eta, op.T_AUL, op.T_ABL, V_sup_w, V_exh_w,
                        cfg.V_nom_m3h, cfg.heat_rec_type, sp, 0.0,
                        Bef_ZUL=Bef_ZUL, Bef_n_WRG=Bef_n_WRG,
                        KR_n_WRG=KR_n_WRG, KR_Entf=1, h_H2O=h_H2O,
                        eta_hr_N=cfg.eta_hr_N, eta_xr_N=cfg.eta_xr_N)
                    Q["WRG"] = flow * (ma.h(T, x) - ma.h(T_in_wrg, x_in_wrg))
                T2, x2 = T, x
            elif comp == "VHR":
                vi = order.index("VHR")
                KR_after_vhr = "KR" in order[vi + 1:]
                T, x, Q["VHR"] = preheater(T, x, flow, sp, dT_corr, cfg.eta_bef,
                                           h_H2O, Bef_after=cfg.has_spray,
                                           Bef_ZUL=Bef_ZUL, NHR_after=cfg.has_NHR,
                                           KR_after=KR_after_vhr)
            elif comp == "KR":
                ci = order.index("KR")
                bef_after_kr = "Bef" in order[ci + 1:]
                SprBef_n_KR = 1 if (bef_after_kr and Bef_ZUL == 1) else 0
                DBef_n_KR = 1 if (bef_after_kr and Bef_ZUL == 2) else 0
                T, x, Q["KR"] = cooler(T, x, flow, sp, dT_corr, KR_Entf=1,
                                       SprBef_n_KR=SprBef_n_KR,
                                       DBef_n_KR=DBef_n_KR, h_H2O=h_H2O)
            elif comp == "Bef":
                if cfg.bef_type == 2:
                    T, x, m_w, Q["Bef"] = steam_humidifier(T, x, flow, sp,
                                                           dT_corr, h_H2O)
                else:
                    T, x, m_w, Q["Bef"] = spray_humidifier(T, x, flow, sp,
                                                           dT_corr, cfg.eta_bef,
                                                           h_H2O)
            elif comp == "NHR":
                T, x, Q["NHR"] = reheater(T, x, flow, sp, dT_corr)
            chain_T[comp], chain_x[comp] = T, x

        phi_sup = ma.phi(T, x, cfg.p_atm)
        results.append(dict(
            # NOTE on units: x_sup/x_wheel/chain_x are in kg/kg; phi_sup in [0..1].
            # The *_gkg / *_pct fields below are the same in g/kg and percent
            # (use these to avoid unit confusion).
            T_sup=T, x_sup=x, phi_sup=phi_sup,
            x_sup_gkg=x * 1000.0, phi_sup_pct=phi_sup * 100.0,
            T_wheel=T2, x_wheel=x2, eta_hr=eta_hr, eta_xr=eta_xr, n_rot=n_rot,
            RWZ_KVS_ges=RWZ_kvs, V_M_KVS=V_M_kvs,
            m_dot=m_dot, Q_fan=Q_fan, m_dot_Bef=m_w,
            m_water_kg_h=m_w * 3600.0,
            T_eta_wheel=T_eta, x_eta_wheel=x_eta,
            chain_T=chain_T, chain_x=chain_x,
            chain_x_gkg={k: v * 1000.0 for k, v in chain_x.items()},
            **{f"Q_{k}": v for k, v in Q.items()}))
    return results


def plant_anlage1() -> PlantConfig:
    """Validation plant 1: sorption wheel, VHR, KR, spray humidifier, NHR."""
    return PlantConfig(heat_rec_type="ROT_SORP", SFP=750.0, has_VHR=True,
                       has_KR=True, has_spray=True, has_NHR=True,
                       has_adiab_exhaust=False, V_nom_m3h=10000.0)


def plant_anlage2() -> PlantConfig:
    """Validation plant 2: condensation wheel, adiabatic exhaust cooling, KR, NHR."""
    return PlantConfig(heat_rec_type="ROT_NH", SFP=1250.0, has_VHR=False,
                       has_KR=True, has_spray=False, has_NHR=True,
                       has_adiab_exhaust=True, V_nom_m3h=10000.0)


def plant_kvs() -> PlantConfig:
    """KVS validation plant (catalogue 'Anlage 2'): supply Vent, electric frost
    protection (5 C), run-around coil (KVS), steam humidifier, cooler, re-heater;
    exhaust Vent then KVS. Water medium 2.5 m3/h, RWZ 0.76 per exchanger."""
    return PlantConfig(
        heat_rec_type="ROT_NH", SFP=1250.0, wrg="KVS", has_FS=True,
        T_FS_Grenz=5.0, has_VHR=False, has_KR=True, has_spray=True, bef_type=2,
        has_NHR=True, has_adiab_exhaust=False, exhaust_fan_before_wrg=True,
        T_H2O=100.0, V_M_KVS_N=2.5, rho_M_KVS=1000.0, cp_M_KVS=4.19,
        RWZ_KVS_ZUL_N=0.76, RWZ_KVS_ABL_N=0.76, V_ZUL_KVS_N=10000.0,
        V_ABL_KVS_N=10000.0,
        zul_order=["Vent_ZUL", "FS", "WRG", "Bef", "KR", "NHR"])


def plant_plate() -> PlantConfig:
    """Plate-exchanger plant: Vent, Plattentauscher (PWÜ), VHR, KR, spray
    humidifier, NHR. Dry sensible recovery, design effectiveness 0.6."""
    return PlantConfig(
        heat_rec_type="ROT_NH", SFP=1250.0, wrg="PLATE", has_VHR=True,
        has_KR=True, has_spray=True, bef_type=1, has_NHR=True,
        has_adiab_exhaust=False, RWZ_WT_N=0.6, V_ZUL_WT_N=10000.0,
        V_ABL_WT_N=10000.0,
        zul_order=["Vent_ZUL", "WRG", "VHR", "KR", "Bef", "NHR"])


if __name__ == "__main__":
    sp = Setpoints(22.0, 24.0, 0.40, 0.55, 1e5)
    ops = [OperatingPoint(-10, 60, 22, 45, 10000),
           OperatingPoint(33, 55, 24, 55, 10000)]
    print("Anlage 1:")
    for r in run_plant(plant_anlage1(), sp, ops):
        print(f"  T_sup={r['T_sup']:.2f}  x_sup={r['x_sup']*1000:.2f}  "
              f"phi={r['phi_sup']*100:.1f}%  Q_NHR={r.get('Q_NHR',0):.1f} kW")
