"""Speicher und ideale Temperaturquellen (stationär).

- BufferStorage: ideal durchmischter Puffer = Mischknoten mit optionalem
  UA-Bereitschaftsverlust (Schichtung ist ein transientes Phänomen).
- IdealStorage (Zweitor): prägt dem austretenden Wasser eine feste
  Temperatur auf (z.B. geladener, geschichteter Speicher: Vorlauf konstant,
  Rücklauf ergibt sich aus dem konvektiven Transport des Netzes).
"""
from __future__ import annotations

from ..fluids import Fluid
from ..params import Param
from .base import Component, EdgeCoefficients, NetworkBuilder, ThermalResult, TwoPortComponent
from .registry import register


@register("buffer_storage")
class BufferStorage(Component):
    n_ports: int
    ua: float
    t_amb: float

    PARAMS = (
        Param("n_ports", "int", default=4, minv=2, maxv=12, help="Anzahl Anschlüsse (p1…pN)"),
        Param("ua", "ua", default=0.0, minv=0.0, help="Bereitschaftsverlust UA"),
        Param("t_amb", "temperature", default=20.0, help="Aufstellraumtemperatur"),
    )

    def port_names(self) -> tuple[str, ...]:
        return tuple(f"p{i+1}" for i in range(self.n_ports))

    def build(self, b: NetworkBuilder) -> None:
        core = b.port("p1")
        for pn in self.port_names()[1:]:
            b.alias(core, b.port(pn))
        if self.ua > 0.0:
            b.node_heat_loss(core, self.ua, self.t_amb)


@register("ideal_storage")
class IdealStorage(TwoPortComponent):
    """Geladener (ideal geschichteter) Speicher / Zweitor-Temperaturquelle:
    prägt dem austretenden Wasser t_set auf (Vorlauf konstant); die
    Eintrittstemperatur (Rücklauf) und die Leistung Q̇ = ṁ·cp·(t_set − t_ein)
    sind Ergebnis.

    Optional zusätzlich: q (eingeprägter Volumenstrom durch das Element,
    Δp ist Ergebnis) und/oder p_out (Überdruck am Austritt — verankert das
    Druckniveau wie ein Ausdehnungsgefäß; der automatische Referenzdruck-
    Hinweis entfällt). Alle Drücke gauge.
    """

    t_set: float
    q: float | None
    p_out: float | None
    dp_nom: float
    q_nom: float

    PARAMS = (
        Param("t_set", "temperature", required=True, help="Austrittstemperatur (Vorlauf)"),
        Param("q", "flow", help="optional: fester Volumenstrom durch das Element (Δp ist Ergebnis)"),
        Param("p_out", "pressure",
              help="optional: Überdruck (gauge) am Austritt — verankert das Druckniveau"),
        Param("dp_nom", "pressure", default=0.0, minv=0.0,
              help="interner Druckverlust bei q_nom (Default quasi 0)"),
        Param("q_nom", "flow", default=10.0 / 3600.0, minv=1e-7, help="Nennvolumenstrom"),
    )

    def q_seed(self) -> float | None:
        return self.q

    def build(self, b: NetworkBuilder) -> None:
        if self.q is not None:
            b.edge(b.port("in"), b.port("out"), self.hydraulic_coefficients,
                   self.thermal_outlet, fixed_q=self.q, q_seed=self.q)
        else:
            super().build(b)
        if self.p_out is not None:
            b.pressure_bc(b.port("out"), self.p_out, self.t_set)

    def hydraulic_coefficients(self, q: float, fluid: Fluid) -> EdgeCoefficients:
        return EdgeCoefficients(b=max(self.dp_nom, 1.0) / self.q_nom ** 2)

    def thermal_outlet(self, t_in: float, m_dot: float, fluid: Fluid) -> ThermalResult:
        q_dot = m_dot * fluid.cp * (self.t_set - t_in)
        return ThermalResult(self.t_set, q_dot, extras={"q_quelle_W": q_dot})
