"""Heiz-/Kühlregister: ε-NTU-Modell Wasser ↔ Luftstrom mit Teillastverhalten.

Drei Betriebsarten (je Register über die Parameter gewählt):
1. Feste Leistung: q_prescribed (kein UA nötig).
2. ε-NTU mit Teillastkorrektur der Übertragungsfähigkeit nach dem
   NTU-/LMTD-Verfahren (FH Burgenland, Wärmetechnik Teil 2, Gl. 4.2):
       UA = UA_ref · [(V̇_Luft/V̇_Luft,ref) · (V̇_Wasser/V̇_Wasser,ref)]^n
   Default n = 0.4; ohne Referenzangaben bleibt UA konstant (n wirkungslos).
3. Nur Kühlregister: Greybox-Modell MIT Kondensation (Skill
   cooling-coil-greybox): trockenes (temperaturbasiertes) und nasses
   (enthalpiebasiertes ε-NTU*) Teilmodell, Q̇ = max(Q̇_trocken, Q̇_nass);
   beide Übertragungsfähigkeiten skalieren mit demselben Exponenten n.
   Psychrometrie: Magnus/Idealgemisch (p = 101325 Pa), Nassaustritt
   gesättigt (phi_out, Default 1.0). Kondensatrate in extras.
"""
from __future__ import annotations

import math

from ..friction import kv_to_b
from ..fluids import Fluid
from ..params import Param
from .base import EdgeCoefficients, ThermalResult, TwoPortComponent
from .registry import register

CP_AIR = 1006.0     # J/(kg·K), trockene Luft
P_ATM = 101325.0    # Pa
C_S = 2326.0        # J/(kg·K) Sättigungs-spez. Wärme (Greybox-Nassmodell)
H_LV0 = 2501e3      # J/kg Verdampfungsenthalpie bei 0 °C
CP_VAP = 1860.0     # J/(kg·K) Wasserdampf


def effectiveness(ntu: float, c_r: float, arrangement: str) -> float:
    if ntu <= 0.0:
        return 0.0
    if arrangement == "counterflow":
        if abs(1.0 - c_r) < 1e-9:
            return ntu / (1.0 + ntu)
        e = math.exp(-ntu * (1.0 - c_r))
        return (1.0 - e) / (1.0 - c_r * e)
    # crossflow_unmixed (Näherung nach Incropera)
    return 1.0 - math.exp(ntu ** 0.22 / c_r * (math.exp(-c_r * ntu ** 0.78) - 1.0))


# ---- Psychrometrie (Magnus / ideales Gemisch, wie Skill-Backend "simple") ----

def p_ws(t: float) -> float:
    """Sättigungsdampfdruck [Pa] über Wasser (Magnus)."""
    return 611.2 * math.exp(17.62 * t / (243.12 + t))


def x_from_rh(t: float, rh: float) -> float:
    """Wasserbeladung [kg/kg trockene Luft] aus Temperatur und rel. Feuchte."""
    pv = rh * p_ws(t)
    return 0.622 * pv / (P_ATM - pv)


def h_moist(t: float, x: float) -> float:
    """Enthalpie feuchter Luft [J/kg trockene Luft]."""
    return CP_AIR * t + x * (H_LV0 + CP_VAP * t)


def t_air_from_h_phi(h: float, phi: float) -> float:
    """Lufttemperatur zu Enthalpie h bei rel. Feuchte phi (Bisektion, [-30, 60] °C)."""
    lo, hi = -30.0, 60.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if h_moist(mid, x_from_rh(mid, phi)) < h:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


class _WaterAirCoil(TwoPortComponent):
    ua_ref: float | None
    n: float
    q_w_ref: float | None
    m_dot_air_ref: float | None
    m_dot_air: float
    t_air_in: float
    arrangement: str
    kv: float | None
    c: float | None
    q_prescribed: float | None

    PARAMS = (
        Param("ua_ref", "ua", minv=1.0,
              help="Übertragungsfähigkeit UA am Referenzpunkt; Pflicht außer bei q_prescribed"),
        Param("n", "none", default=0.4, minv=0.0, maxv=2.0,
              help="Teillastexponent der UA-Korrektur UA = UA_ref·[(V̇g/V̇g,ref)·(V̇w/V̇w,ref)]^n"),
        Param("q_w_ref", "flow", minv=1e-7,
              help="wasserseitiger Referenz-Volumenstrom zu UA_ref; ohne Angabe keine wasserseitige Teillastkorrektur"),
        Param("m_dot_air_ref", "massflow", minv=1e-4,
              help="luftseitiger Referenz-Massenstrom zu UA_ref (Default = m_dot_air)"),
        Param("m_dot_air", "massflow", required=True, minv=1e-4, help="Luftmassenstrom (trockene Luft)"),
        Param("t_air_in", "temperature", required=True, help="Lufteintrittstemperatur"),
        Param("arrangement", "str", default="counterflow",
              choices=("counterflow", "crossflow_unmixed"), help="Stromführung"),
        Param("kv", "kv", minv=1e-4,
              help="wasserseitiger Kv-Wert (ODER c; Default kv = 2 m³/h)"),
        Param("c", "quad_resistance",
              help="alternativ zu kv: wasserseitiger Widerstand C (dp = C·V̇·|V̇|)"),
        Param("q_prescribed", "power", help="feste Leistung ins Wasser (+) bzw. aus dem Wasser (−); überschreibt das Modell"),
    )

    def check_params(self):
        errs = []
        if self.kv is not None and self.c is not None:
            errs.append("Entweder 'kv_m3h' ODER 'c_Pa_m3h2' angeben – nicht beides.")
        if self.c is not None and self.c <= 0.0:
            errs.append("Der C-Wert muss positiv sein.")
        if self.ua_ref is None and self.q_prescribed is None:
            errs.append("'ua_ref_W_K' (Übertragungsfähigkeit am Referenzpunkt) ist "
                        "Pflicht, außer die Leistung wird mit 'q_prescribed_kW' fest "
                        "vorgegeben.")
        return errs or None

    def hydraulic_coefficients(self, q: float, fluid: Fluid) -> EdgeCoefficients:
        if self.c is not None:
            return EdgeCoefficients(b=self.c)
        kv = self.kv if self.kv is not None else 2.0
        return EdgeCoefficients(b=kv_to_b(kv, fluid.rho))

    def _partload_factor(self, m_dot_w: float, fluid: Fluid) -> float:
        """Gl. (4.2): [(V̇g/V̇g,ref)·(V̇w/V̇w,ref)]^n — fehlende Referenz ⇒ Faktor 1."""
        ratio = 1.0
        if self.m_dot_air_ref is not None:
            ratio *= self.m_dot_air / self.m_dot_air_ref
        if self.q_w_ref is not None:
            ratio *= m_dot_w / (fluid.rho * self.q_w_ref)
        return ratio ** self.n if ratio > 0.0 else 0.0

    def _ua_actual(self, m_dot_w: float, fluid: Fluid) -> float:
        return self.ua_ref * self._partload_factor(m_dot_w, fluid)

    def thermal_outlet(self, t_in: float, m_dot: float, fluid: Fluid) -> ThermalResult:
        c_w = m_dot * fluid.cp
        if self.q_prescribed is not None:
            return ThermalResult(t_in + self.q_prescribed / c_w, self.q_prescribed)
        c_a = self.m_dot_air * CP_AIR
        c_min, c_max = min(c_w, c_a), max(c_w, c_a)
        c_r = c_min / c_max
        ua = self._ua_actual(m_dot, fluid)
        ntu = ua / c_min
        eps = effectiveness(ntu, c_r, self.arrangement)
        q_dot = eps * c_min * (self.t_air_in - t_in)  # >0: Luft wärmer → Wärme ins Wasser
        t_air_out = self.t_air_in - q_dot / c_a
        return ThermalResult(t_in + q_dot / c_w, q_dot,
                             extras={"t_luft_aus_C": t_air_out, "epsilon": eps,
                                     "ntu": ntu, "ua_eff_W_K": ua})


@register("heating_coil")
class HeatingCoil(_WaterAirCoil):
    """Heizregister (warmes Wasser erwärmt Luft; Q̇ ins Wasser negativ)."""


@register("cooling_coil")
class CoolingCoil(_WaterAirCoil):
    """Kühlregister (warme Luft erwärmt Kaltwasser; Q̇ ins Wasser positiv).

    Betriebsarten: (1) q_prescribed fest, (2) sensibles ε-NTU mit
    Teillast-UA (wie Heizregister, ohne Kondensation), (3) mit Angabe von
    'ua_star_wet' + 'rh_air_in' das Greybox-Modell inkl. Kondensation:
    Q̇ = max(Q̇_trocken, Q̇_nass); nass = enthalpiebasiertes ε-NTU* mit
    m* = ṁ_Luft·c_s/(ṁ_w·cp_w) und Treiber h_Luft,ein − h_sat(ϑ_w,ein).
    extras: Betriebsmodus, Kondensat [kg/h], Luftaustritt, ggf. x-Werte.
    """

    ua_star_wet: float | None
    rh_air_in: float | None
    phi_out: float

    PARAMS = _WaterAirCoil.PARAMS + (
        Param("ua_star_wet", "massflow", minv=1e-4,
              help="Greybox: nasse Übertragungsfähigkeit UA*_wet am Referenzpunkt [kg/s] "
                   "(aktiviert das Kondensationsmodell; skaliert mit demselben n)"),
        Param("rh_air_in", "none", minv=0.0, maxv=1.0,
              help="relative Feuchte am Lufteintritt 0…1 (Pflicht für das Greybox-Modell)"),
        Param("phi_out", "none", default=1.0, minv=0.5, maxv=1.0,
              help="rel. Feuchte des Nass-Luftaustritts (Datenblatt-üblich gesättigt = 1.0)"),
    )

    def check_params(self):
        errs = list(super().check_params() or [])
        if self.ua_star_wet is not None and self.rh_air_in is None:
            errs.append("Das Greybox-Kondensationsmodell ('ua_star_wet_kg_s') braucht "
                        "die Eintrittsfeuchte 'rh_air_in' (0…1).")
        return errs or None

    def thermal_outlet(self, t_in: float, m_dot: float, fluid: Fluid) -> ThermalResult:
        if self.q_prescribed is not None or self.ua_star_wet is None:
            return super().thermal_outlet(t_in, m_dot, fluid)

        # Greybox: trockenes und nasses Teilmodell, das größere gewinnt
        m_da = self.m_dot_air
        c_w = m_dot * fluid.cp
        f = self._partload_factor(m_dot, fluid)

        dry = super().thermal_outlet(t_in, m_dot, fluid)   # nutzt UA·f intern
        q_dry = dry.q_dot

        m_star = m_da * C_S / c_w
        ntu_star = (self.ua_star_wet * f) / m_da
        if abs(1.0 - m_star) < 1e-9:
            eps_star = ntu_star / (1.0 + ntu_star)
        else:
            e = math.exp(-ntu_star * (1.0 - m_star))
            eps_star = (1.0 - e) / (1.0 - m_star * e)
        x_in = x_from_rh(self.t_air_in, self.rh_air_in)
        h_in = h_moist(self.t_air_in, x_in)
        q_wet = eps_star * m_da * (h_in - h_moist(t_in, x_from_rh(t_in, 1.0)))

        if q_wet <= q_dry:                                  # trockener Betrieb
            extras = dict(dry.extras)
            extras.update({"betrieb": "trocken", "kondensat_kg_h": 0.0})
            return ThermalResult(dry.t_out, q_dry, extras=extras)

        # nasser Betrieb: Austritt gesättigt (phi_out), Kondensat aus Bilanz
        h_out = h_in - q_wet / m_da
        t_a_out = t_air_from_h_phi(h_out, self.phi_out)
        x_out = min(x_from_rh(t_a_out, self.phi_out), x_in)
        if x_out >= x_in:                                   # keine Entfeuchtung
            t_a_out = (h_out - x_in * H_LV0) / (CP_AIR + x_in * CP_VAP)
        kondensat = m_da * (x_in - x_out) * 3600.0          # kg/h
        return ThermalResult(t_in + q_wet / c_w, q_wet, extras={
            "betrieb": "nass", "t_luft_aus_C": t_a_out,
            "kondensat_kg_h": kondensat, "x_ein_g_kg": x_in * 1e3,
            "x_aus_g_kg": x_out * 1e3, "epsilon_stern": eps_star})
