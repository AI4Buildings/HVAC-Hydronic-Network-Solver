"""Sensoren: rückwirkungsfreie Messstellen mit BEMS-Datenpunkt-Zuordnung.

Zweck des Pakets ist ein maschinenlesbares Strangschema — nicht nur zur
hydraulischen Berechnung, sondern auch als semantische Karte für die
Betriebsdatenanalyse eines Building Energy Management Systems (BEMS,
z.B. Aedifion). Jede Komponente trägt dafür die generische Messpunktliste
`bems: [{id, key, description}, …]` (frei viele Datenpunkte) sowie eine
`description`; ihre Position im Schema liefert
einem LLM die Semantik: welche Leitung, welcher Kreis, vor/nach welcher
Komponente gemessen wird.

Modellierung:
- Fühler (Temperatur, Druck, Differenzdruck) sind reine Knotenanzapfungen:
  keine Kante, keinerlei Einfluss auf Hydraulik oder Thermik. Verbinden =
  Knoten verschmelzen — der Fühler liest den Zustand der Messstelle.
- Volumenstromsensor und Wärmemengenzähler sitzen IN der Leitung (Zweitor);
  hydraulisch quasi-ideal wie link/Kugelhahn (1 Pa Referenzverlust bei q_nom).
- Messwerte erscheinen nach dem Lösen im Ergebnis (SolutionResult.sensors,
  to_dict()["sensors"]) und im Editor als Mouseover-Tooltip.
"""
from __future__ import annotations

import math

from ..fluids import Fluid
from ..params import Param
from .base import Component, EdgeCoefficients, NetworkBuilder, TwoPortComponent
from .registry import register

_Q_NOM_PARAM = Param(
    "q_nom", "flow", default=10.0 / 3600.0, minv=1e-7,
    help="Nennvolumenstrom; Referenz-Druckverlust dort 1 Pa (quasi-ideal)")


class _TapSensor(Component):
    """Basis: Fühler ohne Kante — jeder Port verschmilzt mit der Messstelle."""

    measurement_tap = True

    def build(self, b: NetworkBuilder) -> None:
        self._partners = {}
        for p in self.port_names():
            b.port(p)
            self._partners[p] = [(c, pn) for c, pn in b.partners(p) if not c.measurement_tap]


def _port_flow(net, hyd, comp, port: str, node_of) -> float:
    """Betrag des Volumenstroms durch den Anschluss comp.port [m³/s]."""
    n = node_of(f"{comp.name}.{port}")
    q = 0.0
    for e in net.edges:
        if e.component is comp and e.node_from != e.node_to:
            if e.node_to == n:
                q += float(hyd.q[e.index])
            elif e.node_from == n:
                q -= float(hyd.q[e.index])
    return abs(q)


def static_pressure(net, hyd, node_of, ref: str, partners, d_inner) -> tuple[float, float | None]:
    """Statischer Überdruck an der Messstelle [Pa] und dynamischer Anteil
    ρw²/2 [Pa] (None, wenn kein Querschnitt bekannt — dann statisch = Knoten-
    druck). Knotendrücke sind Totaldrücke; die Geschwindigkeit stammt aus dem
    Anschluss, an dem die Messleitung hängt (erster Partner mit bekanntem
    Querschnitt), oder aus d_inner mit dem Volumenstrom dieses Anschlusses."""
    p_node = float(hyd.p[node_of(ref)])
    loc = None
    if d_inner is not None:
        if partners:
            loc = (partners[0][0], partners[0][1], math.pi * d_inner ** 2 / 4.0)
    else:
        for comp, port in partners:
            area = comp.port_flow_area(port)
            if area:
                loc = (comp, port, area)
                break
    if loc is None:
        return p_node, None
    comp, port, area = loc
    w = _port_flow(net, hyd, comp, port, node_of) / area
    p_dyn = net.fluid.rho * w * w / 2.0
    return p_node - p_dyn, p_dyn


_D_TAP = Param("d_inner", "diameter", minv=1e-3,
               help="Innendurchmesser an der Messstelle für den dynamischen Anteil ρw²/2 "
                    "(leer: aus der angeschlossenen Leitung)")


@register("temperature_sensor")
class TemperatureSensor(_TapSensor):
    """Temperaturfühler: liest die Knotentemperatur der Messstelle."""

    PARAMS = ()

    def port_names(self) -> tuple[str, ...]:
        return ("port",)

    def measure(self, net, hyd, th, node_of) -> dict:
        return {"t_C": float(th.t_node[node_of(f"{self.name}.port")])}


@register("pressure_sensor")
class PressureSensor(_TapSensor):
    """Drucksensor: statischer Überdruck (gauge) an der Messstelle, wie ein
    realer Transmitter an der Wandanbohrung: p_Knoten − ρw²/2 (Knotendrücke
    sind Totaldrücke). w aus dem Querschnitt der Leitung, an der die
    Messleitung hängt, bzw. aus d_inner; ohne bekannten Querschnitt
    (Behälter, Sammler, ideale Verbindung) gilt der Knotendruck."""

    d_inner: float | None

    PARAMS = (_D_TAP,)

    def port_names(self) -> tuple[str, ...]:
        return ("port",)

    def measure(self, net, hyd, th, node_of) -> dict:
        p, p_dyn = static_pressure(net, hyd, node_of, f"{self.name}.port",
                                   self._partners.get("port", []), self.d_inner)
        out = {"p_kPa": p / 1e3}
        if p_dyn is not None:
            out["p_dyn_kPa"] = p_dyn / 1e3
        return out


@register("pressure_diff_sensor")
class PressureDiffSensor(_TapSensor):
    """Differenzdrucksensor: Δp = p(plus) − p(minus) der STATISCHEN Drücke an
    zwei Messstellen (z.B. über einer Pumpe, einem Ventil oder als
    Schmutzfänger-Überwachung); dynamischer Anteil je Seite wie beim
    Drucksensor (bei gleicher Nennweite beidseits identisch mit der
    Totaldruckdifferenz)."""

    d_inner_plus: float | None
    d_inner_minus: float | None

    PARAMS = (
        Param("d_inner_plus", "diameter", minv=1e-3,
              help="Innendurchmesser an der Messstelle plus (leer: aus der Leitung)"),
        Param("d_inner_minus", "diameter", minv=1e-3,
              help="Innendurchmesser an der Messstelle minus (leer: aus der Leitung)"),
    )

    def port_names(self) -> tuple[str, ...]:
        return ("plus", "minus")

    def measure(self, net, hyd, th, node_of) -> dict:
        p_plus, _ = static_pressure(net, hyd, node_of, f"{self.name}.plus",
                                    self._partners.get("plus", []), self.d_inner_plus)
        p_minus, _ = static_pressure(net, hyd, node_of, f"{self.name}.minus",
                                     self._partners.get("minus", []), self.d_inner_minus)
        return {"dp_kPa": (p_plus - p_minus) / 1e3}


@register("flow_sensor")
class FlowSensor(TwoPortComponent):
    """Volumenstromsensor: sitzt in der Leitung, hydraulisch quasi-ideal
    (1 Pa Referenzverlust bei q_nom, wie link). Positive Richtung in → out."""

    q_nom: float

    PARAMS = (_Q_NOM_PARAM,)

    def ideal_connection(self) -> tuple[str, str] | None:
        return ("in", "out")

    def hydraulic_coefficients(self, q: float, fluid: Fluid) -> EdgeCoefficients:
        return EdgeCoefficients(b=1.0 / self.q_nom ** 2)

    def measure(self, net, hyd, th, node_of) -> dict:
        e = next(e for e in net.edges if e.component is self)
        q = float(hyd.q[e.index])
        return {"q_m3h": q * 3600.0, "m_dot_kg_s": q * net.fluid.rho}


@register("energy_meter")
class EnergyMeter(TwoPortComponent):
    """Wärmemengenzähler (WMZ): Durchflussteil in der Leitung (in → out,
    quasi-ideal) plus zweiter Temperaturfühler `t_ref` in der Gegenleitung.

    Messwerte: V̇, beide Temperaturen und die Kreisleistung
    Q̇ = ṁ·cp·(ϑ_ref − ϑ_Leitung). Konvention wie in der Praxis: Einbau des
    Durchflussteils im RÜCKLAUF, Fühler t_ref im Vorlauf → Q̇ > 0 ist die vom
    Kreis abgegebene Wärme (Heizfall). Einbau im Vorlauf kehrt das Vorzeichen.
    Der WMZ misst nur — er trägt selbst nichts zur Energiebilanz bei.
    Die Datenpunkte eines realen WMZ (V̇, Q̇, kumulierte Energie, ϑ_VL, ϑ_RL)
    werden als Einträge der bems-Messpunktliste hinterlegt.
    """

    q_nom: float

    PARAMS = (_Q_NOM_PARAM,)

    def port_names(self) -> tuple[str, ...]:
        return ("in", "out", "t_ref")

    def ideal_connection(self) -> tuple[str, str] | None:
        return ("in", "out")              # Durchflussteil quasi-ideal; t_ref ist Anzapfung

    def build(self, b: NetworkBuilder) -> None:
        super().build(b)          # Durchflussteil in → out
        b.port("t_ref")           # Fühler-Anzapfung der Gegenleitung (keine Kante)

    def hydraulic_coefficients(self, q: float, fluid: Fluid) -> EdgeCoefficients:
        return EdgeCoefficients(b=1.0 / self.q_nom ** 2)

    def measure(self, net, hyd, th, node_of) -> dict:
        e = next(ed for ed in net.edges if ed.component is self)
        q = float(hyd.q[e.index])
        upwind = e.node_from if q >= 0 else e.node_to
        t_own = float(th.t_node[upwind])
        t_ref = float(th.t_node[node_of(f"{self.name}.t_ref")])
        q_dot = abs(q) * net.fluid.rho * net.fluid.cp * (t_ref - t_own)
        return {"q_m3h": q * 3600.0, "t_leitung_C": t_own, "t_ref_C": t_ref,
                "q_dot_kW": q_dot / 1e3}
