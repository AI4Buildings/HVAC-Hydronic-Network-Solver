"""Ventile: Regelventil, Strangregulierventil, 3-Wege-Mischventil (Kv-basiert)."""
from __future__ import annotations

from ..friction import kv_to_b
from ..fluids import Fluid
from ..params import Param
from .base import Component, EdgeCoefficients, NetworkBuilder, TwoPortComponent
from .registry import register


def valve_kv(kvs: float, opening: float, characteristic: str, rangeability: float) -> float:
    """Effektiver Kv im Regelbereich, mit Floor Kvs/Rangeability.

    Der Floor ist die reale Regelbereichsgrenze (Stellverhältnis): unterhalb
    Kv0 = Kvs/R verlässt ein Ventil seine Kennlinie. Vollständiges Absperren
    wird NICHT über den Kv abgebildet, sondern über opening == 0.0 → die
    Kante wird zur Randbedingung Q = 0 (siehe _KvValveBase.build).
    """
    if characteristic == "equal_percentage":
        kv = kvs * rangeability ** (opening - 1.0)
    else:  # linear
        kv = kvs * opening
    return max(kv, kvs / rangeability)


class _KvValveBase(TwoPortComponent):
    kvs: float
    opening: float
    characteristic: str
    rangeability: float

    def kv_effective(self) -> float:
        return valve_kv(self.kvs, self.opening, self.characteristic, self.rangeability)

    def build(self, b) -> None:
        if self.opening == 0.0:
            # Ventil zu: Volumenstrom exakt 0 als Randbedingung. Die Kante
            # entkoppelt die Drücke (Druckinsel-Analyse setzt bei Bedarf
            # Referenzdrücke); Δp über dem Sitz ist Ergebnis.
            b.edge(b.port("in"), b.port("out"), self.hydraulic_coefficients,
                   self.thermal_outlet, fixed_q=0.0)
        else:
            super().build(b)

    def hydraulic_coefficients(self, q: float, fluid: Fluid) -> EdgeCoefficients:
        return EdgeCoefficients(b=kv_to_b(self.kv_effective(), fluid.rho))


@register("control_valve")
class ControlValve(_KvValveBase):
    PARAMS = (
        Param("kvs", "kv", required=True, minv=1e-4, help="Kvs-Wert (voll offen) [m³/h]"),
        Param("opening", "none", default=1.0, minv=0.0, maxv=1.0, help="Ventilstellung 0…1"),
        Param("characteristic", "str", default="equal_percentage",
              choices=("equal_percentage", "linear"), help="Ventilkennlinie"),
        Param("rangeability", "none", default=100.0, minv=2.0, help="Stellverhältnis Kvs/Kv0"),
    )


@register("balancing_valve")
class BalancingValve(_KvValveBase):
    """Strangregulierventil: lineare Kennlinie, feste Voreinstellung."""
    PARAMS = (
        Param("kvs", "kv", required=True, minv=1e-4, help="Kvs-Wert [m³/h]"),
        Param("opening", "none", default=1.0, minv=0.0, maxv=1.0, help="Voreinstellung 0…1"),
        Param("characteristic", "str", default="linear",
              choices=("equal_percentage", "linear")),
        Param("rangeability", "none", default=50.0, minv=2.0),
    )


@register("check_valve")
class CheckValve(TwoPortComponent):
    """Rückschlagklappe: Durchfluss nur in Pfeilrichtung (in → out).

    Vorwärts wirkt der Kv-Wert (offen); rückwärts sperrt die Klappe mit
    Restleckage Kv_eff = kvs/1000 (Faktor 10⁶ im Widerstand). Die kleine
    Leckage hält die Kantengleichung regulär — der Solver findet die
    Durchflussrichtung selbst; exaktes Sperren wäre zustandsabhängig und
    ist mit opening=0-Semantik hier nicht möglich, da die Richtung erst
    Ergebnis der Rechnung ist.
    """

    kvs: float
    block_factor: float

    PARAMS = (
        Param("kvs", "kv", required=True, minv=1e-4,
              help="Kv-Wert in Durchlassrichtung (offen)"),
        Param("block_factor", "none", default=1e6, minv=1e2,
              help="Widerstandsfaktor in Sperrrichtung (Kv_eff = kvs/√Faktor)"),
    )

    def hydraulic_coefficients(self, q: float, fluid: Fluid) -> EdgeCoefficients:
        b = kv_to_b(self.kvs, fluid.rho)
        if q < 0.0:
            b *= self.block_factor
        return EdgeCoefficients(b=b)


@register("mixing_valve_3way")
class MixingValve3Way(Component):
    """3-Wege-Mischventil. Ports: a (Vorlauf/heiß), b (Beimischung), ab (Ausgang).

    Zerfällt in zwei Kv-Kanten a→ab und b→ab mit komplementärer Öffnung;
    die Mischtemperatur entsteht automatisch am ab-Knoten (ideale Mischung).
    """

    kvs: float
    opening: float
    characteristic: str
    rangeability: float

    PARAMS = (
        Param("kvs", "kv", required=True, minv=1e-4, help="Kvs-Wert je Pfad [m³/h]"),
        Param("opening", "none", default=1.0, minv=0.0, maxv=1.0,
              help="Stellung des A-Pfads (1 = A voll offen, B zu)"),
        Param("characteristic", "str", default="equal_percentage",
              choices=("equal_percentage", "linear")),
        Param("rangeability", "none", default=100.0, minv=2.0),
    )

    def port_names(self) -> tuple[str, ...]:
        return ("a", "b", "ab")

    def _coeff(self, opening: float):
        def fn(q: float, fluid: Fluid) -> EdgeCoefficients:
            kv = valve_kv(self.kvs, opening, self.characteristic, self.rangeability)
            return EdgeCoefficients(b=kv_to_b(kv, fluid.rho))
        return fn

    def _path(self, b: NetworkBuilder, port: str, opening: float) -> None:
        if opening == 0.0:
            # Endlage: dieser Pfad sperrt exakt (Q = 0 als Randbedingung)
            b.edge(b.port(port), b.port("ab"), self._coeff(0.0), fixed_q=0.0, label=port)
        else:
            b.edge(b.port(port), b.port("ab"), self._coeff(opening), label=port)

    def build(self, b: NetworkBuilder) -> None:
        self._path(b, "a", self.opening)
        self._path(b, "b", 1.0 - self.opening)
