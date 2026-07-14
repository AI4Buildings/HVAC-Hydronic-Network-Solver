"""Rohrleitung: Darcy-Weisbach (Churchill) + Einzelwiderstände + Wärmeverlust."""
from __future__ import annotations

import math

from .. import friction
from ..fluids import Fluid
from ..params import Param
from .base import EdgeCoefficients, ThermalResult, TwoPortComponent
from .registry import register


@register("pipe")
class Pipe(TwoPortComponent):
    length: float
    d_inner: float
    roughness: float
    zeta: float
    u_linear: float
    t_amb: float

    PARAMS = (
        Param("length", "length", required=True, minv=1e-3, help="Rohrlänge"),
        Param("d_inner", "diameter", required=True, minv=1e-3, maxv=2.0, help="Innendurchmesser"),
        Param("roughness", "diameter", default=0.007e-3, minv=0.0, help="Rauheit k (Default 0.007 mm, Cu/PE)"),
        Param("zeta", "none", default=0.0, minv=0.0, help="Summe Einzelwiderstände ζ"),
        Param("u_linear", "u_linear", default=0.0, minv=0.0, help="längenbez. Wärmeverlustkoeffizient U' [W/(m·K)]"),
        Param("t_amb", "temperature", default=20.0, help="Umgebungstemperatur für Wärmeverlust"),
    )

    def hydraulic_coefficients(self, q: float, fluid: Fluid) -> EdgeCoefficients:
        a, b = friction.pipe_coefficients(q, self.length, self.d_inner,
                                          self.roughness, self.zeta, fluid.rho, fluid.mu)
        return EdgeCoefficients(a=a, b=b)

    def thermal_outlet(self, t_in: float, m_dot: float, fluid: Fluid) -> ThermalResult:
        if self.u_linear <= 0.0 or m_dot <= 0.0:
            return ThermalResult(t_in, 0.0)
        # stationäres Rohr mit Wärmeverlust an konstante Umgebung: exponentielles Abklingen
        ntu = self.u_linear * self.length / (m_dot * fluid.cp)
        t_out = self.t_amb + (t_in - self.t_amb) * math.exp(-ntu)
        q_dot = m_dot * fluid.cp * (t_out - t_in)
        return ThermalResult(t_out, q_dot, extras={"q_verlust_W": -q_dot})
