"""Pumpe mit den Betriebsarten konstante Druckdifferenz / konstanter Volumenstrom."""
from __future__ import annotations

from ..fluids import Fluid
from ..params import Param
from .base import EdgeCoefficients, NetworkBuilder, TwoPortComponent
from .registry import register


@register("pump")
class Pump(TwoPortComponent):
    mode: str
    dp: float | None
    q: float | None
    q_nom: float | None
    dp_internal_frac: float

    PARAMS = (
        Param("mode", "str", required=True, choices=("constant_dp", "constant_flow"),
              help="Regelung: konstante Druckdifferenz oder konstanter Volumenstrom"),
        Param("dp", "pressure", minv=0.0, help="Druckerhöhung (bei constant_dp)"),
        Param("q", "flow", help="Fördervolumenstrom (bei constant_flow)"),
        Param("q_nom", "flow", minv=0.0,
              help="Nennvolumenstrom; skaliert den internen Widerstand (Default 1 m³/h)"),
        Param("dp_internal_frac", "none", default=0.05, minv=1e-4, maxv=0.5,
              help="interner Druckverlust bei q_nom als Anteil von dp (Default 5 %; "
                   "klein wählen für ideale Δp-Quelle, z.B. 1e-4)"),
    )

    def check_params(self):
        errs = []
        if self.mode == "constant_dp" and self.dp is None:
            errs.append("Bei mode=constant_dp ist 'dp_kPa' (bzw. dp_Pa/dp_bar) erforderlich.")
        if self.mode == "constant_flow" and self.q is None:
            errs.append("Bei mode=constant_flow ist 'q_m3h' (bzw. q_l_s/q_m3s) erforderlich.")
        return errs or None

    def q_seed(self) -> float | None:
        if self.mode == "constant_flow":
            return self.q
        return self.q_nom

    def _b_internal(self) -> float:
        # Eine ideale Δp-Quelle mit R=0 macht die Kantenimpulsgleichung entartet
        # (Q unbestimmt). Kleiner interner quadratischer Widerstand, Default 5 %
        # von Δp beim Nennvolumenstrom (über dp_internal_frac einstellbar).
        q_nom = self.q_nom or (1.0 / 3600.0)
        return self.dp_internal_frac * self.dp / q_nom ** 2 if self.dp else 1e6

    def build(self, b: NetworkBuilder) -> None:
        if self.mode == "constant_flow":
            b.edge(b.port("in"), b.port("out"),
                   self.hydraulic_coefficients, self.thermal_outlet,
                   fixed_q=self.q, q_seed=self.q)
        else:
            super().build(b)

    def hydraulic_coefficients(self, q: float, fluid: Fluid) -> EdgeCoefficients:
        if self.mode == "constant_dp":
            assert self.dp is not None
            return EdgeCoefficients(b=self._b_internal(), dp_source=self.dp)
        return EdgeCoefficients()  # feste Durchfluss-Kante: Koeffizienten irrelevant
