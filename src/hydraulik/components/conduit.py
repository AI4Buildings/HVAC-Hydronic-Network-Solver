"""Verbindungsleitung: universelles Leitungs-Zweitor (im Editor als Linie).

Genau EINE Hydraulik-Angabe (oder keine):
  - keine Angabe            → ideal (Referenzwiderstand 1 Pa bei q_nom)
  - c                       → konzentrierter Widerstand, dp = C·V̇·|V̇|
  - dp + q                  → Auslegungspunkt, intern C = dp/q²
  - length (+ d_inner, …)   → Rohrmodell (Churchill), EIN Abschnitt
  - pipes: [{length, d_inner, roughness, zeta}, …] → Rohrmodell mit beliebig
    vielen Abschnitten in Reihe (unterschiedliche Dimensionen/ζ je Abschnitt);
    Widerstände (a, b) werden je Abschnitt gebildet und summiert, der
    Wärmeverlust (u_linear, t_amb — global) wirkt über die Gesamtlänge.
"""
from __future__ import annotations

import math

from .. import friction
from ..exceptions import ComponentParamError
from ..fluids import Fluid
from ..params import Param, parse_params
from .base import EdgeCoefficients, ThermalResult, TwoPortComponent
from .registry import register

#: Parameter EINES Rohrabschnitts der pipes-Liste (Einheiten-Suffixe wie üblich)
PIPE_SEGMENT_PARAMS = (
    Param("length", "length", required=True, minv=1e-3, help="Rohrlänge des Abschnitts"),
    Param("d_inner", "diameter", default=0.026, minv=1e-3, help="Rohrinnendurchmesser"),
    Param("roughness", "diameter", default=0.007e-3, minv=0.0, help="Rohrrauheit"),
    Param("zeta", "none", default=0.0, minv=0.0, help="Summe Einzelwiderstände des Abschnitts"),
)


def _parse_pipes(name: str, raw) -> list[dict]:
    if raw is None:
        return []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise ComponentParamError(name, [
            "'pipes' muss eine Liste von Rohrabschnitten sein, z.B. "
            "pipes: [{length_m: 12, d_inner_mm: 26, zeta: 2.5}, …]"])
    out, errors = [], []
    for k, entry in enumerate(raw):
        if not isinstance(entry, dict):
            errors.append(f"pipes[{k}]: erwartet ein Mapping (length_m, d_inner_mm, "
                          f"roughness_mm, zeta), erhalten: {entry!r}")
            continue
        vals, errs = parse_params(f"pipes[{k}]", PIPE_SEGMENT_PARAMS, dict(entry))
        errors += [f"pipes[{k}]: {e}" for e in errs]
        if not errs:
            out.append(vals)
    if errors:
        raise ComponentParamError(name, errors)
    return out


@register("conduit")
class Conduit(TwoPortComponent):
    c: float | None
    dp: float | None
    q: float | None
    length: float | None
    d_inner: float
    roughness: float
    zeta: float
    u_linear: float
    t_amb: float
    q_nom: float

    pipes: list[dict]

    PARAMS = (
        Param("c", "quad_resistance",
              help="Widerstand C (dp = C·V̇·|V̇|); ohne Angabe: ideale Verbindung"),
        Param("dp", "pressure", help="Auslegungspunkt-Druckverlust (nur zusammen mit q)"),
        Param("q", "flow", help="Auslegungspunkt-Volumenstrom (nur zusammen mit dp)"),
        Param("length", "length", minv=1e-3, help="Rohrlänge → Rohrmodell statt C/ideal"),
        Param("d_inner", "diameter", default=0.026, minv=1e-3, help="Rohrinnendurchmesser (Default 26 mm)"),
        Param("roughness", "diameter", default=0.007e-3, minv=0.0),
        Param("zeta", "none", default=0.0, minv=0.0, help="Summe Einzelwiderstände (Rohrmodell)"),
        Param("u_linear", "u_linear", default=0.0, minv=0.0,
              help="Wärmeverlust U' [W/(m·K)] (nur Rohrmodell)"),
        Param("t_amb", "temperature", default=20.0, help="Umgebungstemperatur für Wärmeverlust"),
        Param("q_nom", "flow", default=10.0 / 3600.0, minv=1e-7,
              help="Nennvolumenstrom der idealen Verbindung (1 Pa Referenz)"),
    )

    def __init__(self, name: str, **kwargs):
        self.pipes = _parse_pipes(str(name), kwargs.pop("pipes", None))
        super().__init__(name, **kwargs)

    def check_params(self):
        errs = []
        has_c, has_len = self.c is not None, self.length is not None
        has_dp, has_q = self.dp is not None, self.q is not None
        if has_dp != has_q:
            errs.append("Auslegungspunkt unvollständig: 'dp_kPa' und 'q_m3h' gemeinsam angeben.")
        modes = sum([has_c, has_dp and has_q, has_len, bool(self.pipes)])
        if modes > 1:
            errs.append("Höchstens EINE Hydraulik-Angabe: 'c_Pa_m3h2' ODER "
                        "Auslegungspunkt 'dp_kPa'+'q_m3h' ODER Rohrmodell "
                        "('length_m' für einen Abschnitt ODER 'pipes'-Liste).")
        elif self.c is not None and self.c <= 0.0:
            errs.append("Der C-Wert muss positiv sein.")
        elif self.dp is not None and self.q is not None:
            if self.dp <= 0.0 or self.q <= 0.0:
                errs.append("Auslegungspunkt: dp und q müssen positiv sein.")
            else:
                self.c = self.dp / self.q ** 2
        return errs or None

    def q_seed(self) -> float | None:
        return self.q

    def hydraulic_coefficients(self, q: float, fluid: Fluid) -> EdgeCoefficients:
        if self.pipes:
            a_sum = b_sum = 0.0
            for seg in self.pipes:                      # Abschnitte in Reihe
                a, b = friction.pipe_coefficients(
                    q, seg["length"], seg["d_inner"], seg["roughness"],
                    seg["zeta"], fluid.rho, fluid.mu)
                a_sum += a
                b_sum += b
            return EdgeCoefficients(a=a_sum, b=b_sum)
        if self.length is not None:
            a, b = friction.pipe_coefficients(q, self.length, self.d_inner,
                                              self.roughness, self.zeta, fluid.rho, fluid.mu)
            return EdgeCoefficients(a=a, b=b)
        if self.c is not None:
            return EdgeCoefficients(b=self.c)
        return EdgeCoefficients(b=1.0 / self.q_nom ** 2)     # ideal: 1 Pa bei q_nom

    def thermal_outlet(self, t_in: float, m_dot: float, fluid: Fluid) -> ThermalResult:
        total_len = sum(seg["length"] for seg in self.pipes) if self.pipes else self.length
        if total_len is None or self.u_linear <= 0.0 or m_dot <= 0.0:
            return ThermalResult(t_in, 0.0)
        ntu = self.u_linear * total_len / (m_dot * fluid.cp)
        t_out = self.t_amb + (t_in - self.t_amb) * math.exp(-ntu)
        q_dot = m_dot * fluid.cp * (t_out - t_in)
        return ThermalResult(t_out, q_dot, extras={"q_verlust_W": -q_dot})
