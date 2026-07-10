"""Erzeuger: Wärmepumpe und Kältemaschine (wasserseitig)."""
from __future__ import annotations

from ..fluids import Fluid
from ..params import Param
from .base import EdgeCoefficients, ThermalResult, TwoPortComponent
from .registry import register


class _PlantBase(TwoPortComponent):
    """Gemeinsame Basis: interner Druckverlust + Leistungsvorgabe oder
    Solltemperatur am Austritt (mit optionaler Leistungsbegrenzung)."""

    mode: str
    q_dot: float | None
    t_out_set: float | None
    q_max: float | None
    dp_nom: float
    q_nom: float

    PARAMS = (
        Param("mode", "str", default="prescribed_q", choices=("prescribed_q", "target_t_out"),
              help="Leistungsvorgabe oder Austritts-Solltemperatur"),
        Param("q_dot", "power", minv=0.0, help="Nutzleistung (Heizen bzw. Kühlen, positiv)"),
        Param("t_out_set", "temperature", help="Austritts-Solltemperatur (bei target_t_out)"),
        Param("q_max", "power", minv=0.0, help="Leistungsgrenze (bei target_t_out)"),
        Param("dp_nom", "pressure", default=15e3, minv=0.0, help="interner Druckverlust bei q_nom"),
        Param("q_nom", "flow", default=1.0 / 3600.0, minv=1e-6, help="Nennvolumenstrom"),
    )

    #: +1 = heizt das Wasser (Wärmepumpe), −1 = kühlt (Kältemaschine)
    _sign: float = +1.0

    def check_params(self):
        errs = []
        if self.mode == "prescribed_q" and self.q_dot is None:
            errs.append("Bei mode=prescribed_q ist 'q_dot_kW' erforderlich.")
        if self.mode == "target_t_out" and self.t_out_set is None:
            errs.append("Bei mode=target_t_out ist 't_out_set_C' erforderlich.")
        return errs or None

    def q_seed(self) -> float | None:
        return self.q_nom

    def hydraulic_coefficients(self, q: float, fluid: Fluid) -> EdgeCoefficients:
        return EdgeCoefficients(b=self.dp_nom / self.q_nom ** 2)

    def thermal_outlet(self, t_in: float, m_dot: float, fluid: Fluid) -> ThermalResult:
        c = m_dot * fluid.cp
        if self.mode == "prescribed_q":
            assert self.q_dot is not None
            q = self._sign * self.q_dot
        else:
            assert self.t_out_set is not None
            q = c * (self.t_out_set - t_in)
            # Erzeuger arbeitet nur in seine Richtung und höchstens bis q_max
            if self._sign > 0:
                q = max(0.0, q)
            else:
                q = min(0.0, q)
            if self.q_max is not None and abs(q) > self.q_max:
                q = self._sign * self.q_max
        return ThermalResult(t_in + q / c, q, extras={"q_nutz_W": abs(q)})


@register("heat_pump")
class HeatPump(_PlantBase):
    _sign = +1.0


@register("chiller")
class Chiller(_PlantBase):
    _sign = -1.0
