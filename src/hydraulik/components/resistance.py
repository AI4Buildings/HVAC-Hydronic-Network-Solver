"""Konzentrierter strömungstechnischer Widerstand (C-Wert-Eingabe).

Für Teilstrecken aus Handrechnungen (RegulaA/REGuA): dp = a·Q + C·Q·|Q|.
Der C-Wert kann direkt (Pa/(m³/h)² oder SI) oder über einen Auslegungspunkt
(dp, q) angegeben werden, aus dem intern C = dp/q² berechnet wird.
"""
from __future__ import annotations

from ..fluids import Fluid
from ..params import Param
from .base import EdgeCoefficients, TwoPortComponent
from .registry import register


@register("flow_resistance")
class FlowResistance(TwoPortComponent):
    c: float | None      # nach check_params immer gesetzt [Pa/(m³/s)²]
    a: float
    dp: float | None
    q: float | None

    PARAMS = (
        Param("c", "quad_resistance",
              help="quadratischer Widerstand C (dp = a·Q + C·Q·|Q|); "
                   "alternativ Auslegungspunkt dp + q angeben"),
        Param("a", "lin_resistance", default=0.0, minv=0.0,
              help="optionaler linearer (laminarer) Anteil, Default 0"),
        Param("dp", "pressure",
              help="Druckverlust im Auslegungspunkt (nur zusammen mit q, statt c)"),
        Param("q", "flow",
              help="Volumenstrom im Auslegungspunkt (nur zusammen mit dp, statt c)"),
    )

    def check_params(self):
        errs = []
        has_c = self.c is not None
        has_dp, has_q = self.dp is not None, self.q is not None
        if has_c and (has_dp or has_q):
            errs.append("Entweder den C-Wert ('c_Pa_m3h2' bzw. 'c_Pa_m3s2') ODER den "
                        "Auslegungspunkt ('dp_kPa' + 'q_m3h') angeben – nicht beides.")
        elif has_c:
            assert self.c is not None
            if self.c <= 0.0:
                errs.append("Der C-Wert muss positiv sein (dp = C·Q·|Q| mit C > 0).")
        elif has_dp != has_q:
            fehlt = "'q_m3h'" if has_dp else "'dp_kPa'"
            errs.append(f"Auslegungspunkt unvollständig: {fehlt} fehlt "
                        f"(dp und q müssen gemeinsam angegeben werden).")
        elif not has_dp:
            errs.append("Widerstand fehlt: entweder 'c_Pa_m3h2' (bzw. 'c_Pa_m3s2') oder "
                        "den Auslegungspunkt 'dp_kPa' + 'q_m3h' angeben.")
        else:
            assert self.dp is not None and self.q is not None
            if self.dp <= 0.0 or self.q <= 0.0:
                errs.append("Auslegungspunkt: dp und q müssen positiv sein.")
            else:
                self.c = self.dp / self.q ** 2   # C_SI [Pa/(m³/s)²]
        return errs or None

    def q_seed(self) -> float | None:
        return self.q

    def hydraulic_coefficients(self, q: float, fluid: Fluid) -> EdgeCoefficients:
        assert self.c is not None
        return EdgeCoefficients(a=self.a, b=self.c)
