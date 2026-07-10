"""Heiz-/Kühlregister: sensibles ε-NTU-Modell Wasser ↔ Luftstrom.

Kühlregister v1 nur trocken (ohne Entfeuchtung) – dokumentierte Einschränkung.
"""
from __future__ import annotations

import math

from ..friction import kv_to_b
from ..fluids import Fluid
from ..params import Param
from .base import EdgeCoefficients, ThermalResult, TwoPortComponent
from .registry import register

CP_AIR = 1006.0  # J/(kg·K)


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


class _WaterAirCoil(TwoPortComponent):
    ua: float
    m_dot_air: float
    t_air_in: float
    arrangement: str
    kv: float | None
    c: float | None
    q_prescribed: float | None

    PARAMS = (
        Param("ua", "ua", required=True, minv=1.0, help="UA-Wert des Registers"),
        Param("m_dot_air", "massflow", required=True, minv=1e-4, help="Luftmassenstrom"),
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
        if self.kv is not None and self.c is not None:
            return ["Entweder 'kv_m3h' ODER 'c_Pa_m3h2' angeben – nicht beides."]
        if self.c is not None and self.c <= 0.0:
            return ["Der C-Wert muss positiv sein."]
        return None

    def hydraulic_coefficients(self, q: float, fluid: Fluid) -> EdgeCoefficients:
        if self.c is not None:
            return EdgeCoefficients(b=self.c)
        kv = self.kv if self.kv is not None else 2.0
        return EdgeCoefficients(b=kv_to_b(kv, fluid.rho))

    def thermal_outlet(self, t_in: float, m_dot: float, fluid: Fluid) -> ThermalResult:
        c_w = m_dot * fluid.cp
        if self.q_prescribed is not None:
            return ThermalResult(t_in + self.q_prescribed / c_w, self.q_prescribed)
        c_a = self.m_dot_air * CP_AIR
        c_min, c_max = min(c_w, c_a), max(c_w, c_a)
        c_r = c_min / c_max
        ntu = self.ua / c_min
        eps = effectiveness(ntu, c_r, self.arrangement)
        q_dot = eps * c_min * (self.t_air_in - t_in)  # >0: Luft wärmer → Wärme ins Wasser
        t_air_out = self.t_air_in - q_dot / c_a
        return ThermalResult(t_in + q_dot / c_w, q_dot,
                             extras={"t_luft_aus_C": t_air_out, "epsilon": eps, "ntu": ntu})


@register("heating_coil")
class HeatingCoil(_WaterAirCoil):
    """Heizregister (warmes Wasser erwärmt Luft; Q̇ ins Wasser negativ)."""


@register("cooling_coil")
class CoolingCoil(_WaterAirCoil):
    """Kühlregister, trockener Betrieb (warme Luft erwärmt Kaltwasser; Q̇ ins Wasser positiv)."""
