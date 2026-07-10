"""Hydraulische Weiche, Verteiler, Abzweiger (T-Stück)."""
from __future__ import annotations

from ..fluids import Fluid
from ..params import Param
from .base import Component, EdgeCoefficients, NetworkBuilder
from .registry import register


@register("hydraulic_separator")
class HydraulicSeparator(Component):
    """Hydraulische Weiche als Zwei-Knoten-Modell.

    Ports: prim_in, sec_out (oben) – sec_in, prim_out (unten), verbunden durch
    eine vertikale Niederwiderstandskante. Damit entsteht das reale Verhalten
    automatisch aus der idealen Knotenmischung:
    Sekundärstrom > Primärstrom → Rücklaufwasser strömt nach oben und senkt
    die Sekundär-Vorlauftemperatur; umgekehrt kurzschließt Überschusswasser
    nach unten.
    """

    q_nom: float
    dp_nom: float
    ua: float
    t_amb: float

    PARAMS = (
        Param("q_nom", "flow", default=2.0 / 3600.0, minv=1e-6,
              help="Nennvolumenstrom zur Dimensionierung der vertikalen Kante"),
        Param("dp_nom", "pressure", default=100.0, minv=1.0,
              help="Druckverlust der vertikalen Strecke bei q_nom (Default 100 Pa)"),
        Param("ua", "ua", default=0.0, minv=0.0, help="Wärmeverlust an Aufstellraum"),
        Param("t_amb", "temperature", default=20.0),
    )

    def port_names(self) -> tuple[str, ...]:
        return ("prim_in", "prim_out", "sec_in", "sec_out")

    def _vertical_coeff(self, q: float, fluid: Fluid) -> EdgeCoefficients:
        return EdgeCoefficients(b=self.dp_nom / self.q_nom ** 2)

    def build(self, b: NetworkBuilder) -> None:
        top = b.port("prim_in")
        b.alias(top, b.port("sec_out"))
        bottom = b.port("sec_in")
        b.alias(bottom, b.port("prim_out"))
        b.edge(top, bottom, self._vertical_coeff, label="vertikal")
        if self.ua > 0.0:
            b.node_heat_loss(top, self.ua / 2.0, self.t_amb)
            b.node_heat_loss(bottom, self.ua / 2.0, self.t_amb)


@register("manifold")
class Manifold(Component):
    """Verteiler/Sammler: Hauptanschluss + n Strang-Anschlüsse, ein Mischknoten."""

    n_ports: int

    PARAMS = (
        Param("n_ports", "int", required=True, minv=1, maxv=24,
              help="Anzahl Strang-Anschlüsse s1…sN (zusätzlich zu 'main')"),
    )

    def port_names(self) -> tuple[str, ...]:
        return ("main",) + tuple(f"s{i+1}" for i in range(self.n_ports))

    def build(self, b: NetworkBuilder) -> None:
        main = b.port("main")
        for pn in self.port_names()[1:]:
            b.alias(main, b.port(pn))


@register("tee")
class Tee(Component):
    """Abzweiger: drei Anschlüsse, ein idealer Mischknoten (v1 ohne ζ je Ast)."""

    PARAMS = ()

    def port_names(self) -> tuple[str, ...]:
        return ("a", "b", "c")

    def build(self, b: NetworkBuilder) -> None:
        a = b.port("a")
        b.alias(a, b.port("b"))
        b.alias(a, b.port("c"))
