"""Sensoren: rückwirkungsfreie Messstellen mit BEMS-Datenpunkt-Zuordnung.

Zweck des Pakets ist ein maschinenlesbares Strangschema — nicht nur zur
hydraulischen Berechnung, sondern auch als semantische Karte für die
Betriebsdatenanalyse eines Building Energy Management Systems (BEMS,
z.B. Aedifion). Sensoren tragen dafür die Datenpunkt-Bezeichner
(`bems_id`, `bems_key`, `description`); ihre Position im Schema liefert
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

from ..fluids import Fluid
from ..params import Param, bems_id_params
from .base import Component, EdgeCoefficients, NetworkBuilder, TwoPortComponent
from .registry import register

_Q_NOM_PARAM = Param(
    "q_nom", "flow", default=10.0 / 3600.0, minv=1e-7,
    help="Nennvolumenstrom; Referenz-Druckverlust dort 1 Pa (quasi-ideal)")


class _TapSensor(Component):
    """Basis: Fühler ohne Kante — jeder Port verschmilzt mit der Messstelle."""

    def build(self, b: NetworkBuilder) -> None:
        for p in self.port_names():
            b.port(p)


@register("temperature_sensor")
class TemperatureSensor(_TapSensor):
    """Temperaturfühler: liest die Knotentemperatur der Messstelle."""

    PARAMS = bems_id_params("des Temperaturmesswerts [°C]")

    def port_names(self) -> tuple[str, ...]:
        return ("port",)

    def measure(self, net, hyd, th, node_of) -> dict:
        return {"t_C": float(th.t_node[node_of(f"{self.name}.port")])}


@register("pressure_sensor")
class PressureSensor(_TapSensor):
    """Drucksensor: liest den Knotendruck (Überdruck/gauge) der Messstelle."""

    PARAMS = bems_id_params("des Druckmesswerts [kPa ü]")

    def port_names(self) -> tuple[str, ...]:
        return ("port",)

    def measure(self, net, hyd, th, node_of) -> dict:
        return {"p_kPa": float(hyd.p[node_of(f"{self.name}.port")]) / 1e3}


@register("pressure_diff_sensor")
class PressureDiffSensor(_TapSensor):
    """Differenzdrucksensor: Δp = p(plus) − p(minus) zwischen zwei Messstellen
    (z.B. über einer Pumpe, einem Ventil oder als Schmutzfänger-Überwachung)."""

    PARAMS = bems_id_params("des Differenzdruck-Messwerts [kPa]")

    def port_names(self) -> tuple[str, ...]:
        return ("plus", "minus")

    def measure(self, net, hyd, th, node_of) -> dict:
        dp = hyd.p[node_of(f"{self.name}.plus")] - hyd.p[node_of(f"{self.name}.minus")]
        return {"dp_kPa": float(dp) / 1e3}


@register("flow_sensor")
class FlowSensor(TwoPortComponent):
    """Volumenstromsensor: sitzt in der Leitung, hydraulisch quasi-ideal
    (1 Pa Referenzverlust bei q_nom, wie link). Positive Richtung in → out."""

    q_nom: float

    PARAMS = (_Q_NOM_PARAM,) + bems_id_params("des Volumenstrom-Messwerts [m³/h]")

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
    Die fünf BEMS-Datenpunkte eines realen WMZ (V̇, Q̇, kumulierte Energie,
    ϑ_VL, ϑ_RL) haben eigene ID-Felder.
    """

    q_nom: float

    PARAMS = (
        _Q_NOM_PARAM,
        Param("bems_id_v_dot", "str", help="BEMS-ID Momentan-Volumenstrom [m³/h]"),
        Param("bems_id_q_dot", "str", help="BEMS-ID Momentanleistung [kW]"),
        Param("bems_id_q_cum", "str", help="BEMS-ID kumulierte Wärmemenge [kWh]"),
        Param("bems_id_t_vl", "str", help="BEMS-ID Vorlauftemperatur des Zählers [°C]"),
        Param("bems_id_t_rl", "str", help="BEMS-ID Rücklauftemperatur des Zählers [°C]"),
        Param("bems_key", "str", help="sprechender Datenpunkt-Alias/Präfix (z.B. EXP_HP_SEK)"),
    )

    def port_names(self) -> tuple[str, ...]:
        return ("in", "out", "t_ref")

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
