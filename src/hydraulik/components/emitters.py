"""Wärmeabgabesysteme: Heizkörper (EN-442-Exponentenmodell), Fußbodenheizung."""
from __future__ import annotations

import math

from scipy.optimize import brentq

from .. import friction
from ..fluids import Fluid
from ..params import Param
from .base import EdgeCoefficients, ThermalResult, TwoPortComponent
from .registry import register


def lmtd(t_in: float, t_out: float, t_room: float) -> float:
    """Logarithmische Übertemperatur; stetiger Grenzwert für t_out → t_in."""
    d1, d2 = t_in - t_room, t_out - t_room
    if d1 <= 0.0 or d2 <= 0.0:
        return 0.0
    if abs(d1 - d2) < 1e-9:
        return d1
    return (d1 - d2) / math.log(d1 / d2)


@register("radiator")
class Radiator(TwoPortComponent):
    """Heizkörper: Q̇ = Q̇_nom·(ΔT_lm/ΔT_lm,nom)^n, gekoppelt mit der Enthalpiebilanz.

    Hydraulisch als Kv-Widerstand (Default aus Nennbedingungen dimensioniert).
    """

    q_nom: float | None
    t_sup_nom: float
    t_ret_nom: float
    t_room: float
    n: float
    kv: float | None
    c: float | None
    q_prescribed: float | None

    PARAMS = (
        Param("q_nom", "power", minv=1.0,
              help="Nennwärmeleistung (Pflicht, außer q_prescribed ist gesetzt)"),
        Param("t_sup_nom", "temperature", default=75.0, help="Nennvorlauftemperatur"),
        Param("t_ret_nom", "temperature", default=65.0, help="Nennrücklauftemperatur"),
        Param("t_room", "temperature", default=20.0, help="Raumtemperatur (Randbedingung)"),
        Param("n", "none", default=1.3, minv=1.0, maxv=1.6, help="Heizkörperexponent"),
        Param("kv", "kv", minv=1e-4,
              help="hydraulischer Kv-Wert (ODER c); ohne beides: 10 kPa bei Nennmassenstrom"),
        Param("c", "quad_resistance",
              help="alternativ zu kv: Widerstand C (dp = C·V̇·|V̇|, dichteunabhängig)"),
        Param("q_prescribed", "power", help="feste Wärmeabgabe an den Raum (überschreibt das Modell)"),
    )

    def check_params(self):
        errs = []
        if self.q_nom is None and self.q_prescribed is None:
            errs.append("Entweder 'q_nom_kW' (Exponentenmodell) oder "
                        "'q_prescribed_kW' (feste Leistung) angeben.")
        if self.kv is not None and self.c is not None:
            errs.append("Entweder 'kv_m3h' ODER 'c_Pa_m3h2' angeben – nicht beides.")
        if self.c is not None and self.c <= 0.0:
            errs.append("Der C-Wert muss positiv sein.")
        if self.t_ret_nom >= self.t_sup_nom:
            errs.append("t_ret_nom_C muss kleiner als t_sup_nom_C sein.")
        if self.t_room >= self.t_ret_nom:
            errs.append("t_room_C muss kleiner als t_ret_nom_C sein.")
        return errs or None

    def _dtlm_nom(self) -> float:
        return lmtd(self.t_sup_nom, self.t_ret_nom, self.t_room)

    def _m_dot_nom(self, cp: float) -> float:
        q_ref = self.q_nom if self.q_nom is not None else self.q_prescribed
        assert q_ref is not None  # check_params erzwingt eines von beiden
        return q_ref / (cp * (self.t_sup_nom - self.t_ret_nom))

    def q_seed(self) -> float | None:
        return self._m_dot_nom(4180.0) / 980.0

    def hydraulic_coefficients(self, q: float, fluid: Fluid) -> EdgeCoefficients:
        if self.c is not None:
            return EdgeCoefficients(b=self.c)
        if self.kv is not None:
            return EdgeCoefficients(b=friction.kv_to_b(self.kv, fluid.rho))
        # Default-Dimensionierung: 10 kPa Druckverlust beim Nennvolumenstrom
        q_nom_vol = self._m_dot_nom(fluid.cp) / fluid.rho
        return EdgeCoefficients(b=10e3 / q_nom_vol ** 2)

    def thermal_outlet(self, t_in: float, m_dot: float, fluid: Fluid) -> ThermalResult:
        c = m_dot * fluid.cp
        if self.q_prescribed is not None:
            q_emit = self.q_prescribed
            return ThermalResult(t_in - q_emit / c, -q_emit, extras={"q_emitted_W": q_emit})
        if t_in <= self.t_room + 0.01 or c <= 0.0:
            return ThermalResult(t_in, 0.0, extras={"q_emitted_W": 0.0})

        dtlm_nom = self._dtlm_nom()

        def balance(t_out: float) -> float:
            dtlm = lmtd(t_in, t_out, self.t_room)
            return c * (t_in - t_out) - self.q_nom * (dtlm / dtlm_nom) ** self.n

        # balance(t_room+) > 0, balance(t_in) < 0 → Vorzeichenwechsel garantiert
        t_out = float(brentq(balance, self.t_room + 1e-6, t_in, xtol=1e-8))
        q_emit = c * (t_in - t_out)
        return ThermalResult(t_out, -q_emit,
                             extras={"q_emitted_W": q_emit, "dt_lm_K": lmtd(t_in, t_out, self.t_room)})


@register("floor_heating")
class FloorHeatingLoop(TwoPortComponent):
    """Fußbodenheizkreis: hydraulisch ein Rohr, thermisch exponentieller
    Übertrager an die Raumtemperatur (T_out = T_room + (T_in−T_room)·e^(−kA/ṁcp))."""

    area: float
    k: float
    t_room: float
    length: float | None
    d_inner: float
    roughness: float
    zeta: float
    c: float | None
    q_prescribed: float | None

    PARAMS = (
        Param("area", "area", required=True, minv=0.1, help="beheizte Fläche"),
        Param("k", "u_area", default=5.5, minv=0.1,
              help="Wärmedurchgangskoeffizient Wasser→Raum je m² Fläche"),
        Param("t_room", "temperature", default=20.0, help="Raumtemperatur"),
        Param("length", "length", minv=1.0,
              help="Rohrlänge des Kreises (Rohrmodell; ODER c angeben)"),
        Param("d_inner", "diameter", default=0.012, minv=0.004, help="Rohrinnendurchmesser (Default 12 mm)"),
        Param("roughness", "diameter", default=0.007e-3, minv=0.0),
        Param("zeta", "none", default=0.0, minv=0.0),
        Param("c", "quad_resistance",
              help="alternativ zum Rohrmodell: Widerstand C (dp = C·V̇·|V̇|)"),
        Param("q_prescribed", "power", help="feste Wärmeabgabe an den Raum (überschreibt das Modell)"),
    )

    def check_params(self):
        if (self.length is None) == (self.c is None):
            return ["Hydraulik angeben: ENTWEDER 'length_m' (Rohrmodell, ggf. mit "
                    "d_inner/roughness/zeta) ODER 'c_Pa_m3h2' (konzentrierter Widerstand)."]
        if self.c is not None and self.c <= 0.0:
            return ["Der C-Wert muss positiv sein."]
        return None

    def hydraulic_coefficients(self, q: float, fluid: Fluid) -> EdgeCoefficients:
        if self.c is not None:
            return EdgeCoefficients(b=self.c)
        a, b = friction.pipe_coefficients(q, self.length, self.d_inner,
                                          self.roughness, self.zeta, fluid.rho, fluid.mu)
        return EdgeCoefficients(a=a, b=b)

    def thermal_outlet(self, t_in: float, m_dot: float, fluid: Fluid) -> ThermalResult:
        c = m_dot * fluid.cp
        if self.q_prescribed is not None:
            q_emit = self.q_prescribed
            return ThermalResult(t_in - q_emit / c, -q_emit, extras={"q_emitted_W": q_emit})
        ntu = self.k * self.area / c
        t_out = self.t_room + (t_in - self.t_room) * math.exp(-ntu)
        q_emit = c * (t_in - t_out)
        return ThermalResult(t_out, -q_emit,
                             extras={"q_emitted_W": q_emit, "q_flaeche_W_m2": q_emit / self.area})
