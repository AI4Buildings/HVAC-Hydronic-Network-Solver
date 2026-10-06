"""Rohrenden: offen (Druck-/Volumenstrom-RB) oder dicht (Endstück)."""
from __future__ import annotations

import math

from ..params import Param
from .base import Component, EdgeCoefficients, NetworkBuilder
from .registry import register


@register("cap")
class Cap(Component):
    """Dichtes Endstück (Blindstopfen): verschließt einen Anschluss, V̇ = 0.

    Nützlich, um Teilbereiche einer Anlage zu testen, während der Rest
    noch nicht angeschlossen ist. Es ist keine Randbedingung nötig – am
    Sackknoten erzwingt die Kontinuität den Volumenstrom 0; der Druck dort
    ergibt sich aus dem angeschlossenen Netz, der Strang wird thermisch als
    stagnierend markiert.
    """

    PARAMS = ()

    def port_names(self) -> tuple[str, ...]:
        return ("port",)

    def build(self, b: NetworkBuilder) -> None:
        b.port("port")   # Knoten existiert; V̇ = 0 folgt aus der Kontinuität


_D_CONN = Param(
    "d_inner", "diameter", minv=1e-3,
    help="Innendurchmesser am Anschluss für den dynamischen Anteil ρw²/2 (leer: aus der "
         "angeschlossenen Leitung; ohne bekannten Querschnitt gilt p als Knotendruck)")


def _dyn_coeff(area: float):
    """Übergang Anschluss → Randknoten: p_Knoten − p_statisch = ρw²/2 in BEIDE
    Richtungen (Knotendrücke sind Totaldrücke, am Rand ist der statische Druck
    vorgegeben). Tangentenform mit positiver Steigung (s. T-Stück)."""
    def fn(q: float, fluid) -> EdgeCoefficients:
        k = fluid.rho / (2.0 * area * area)
        S = k * q * q
        a = 2.0 * k * abs(q)
        return EdgeCoefficients(a=a, dp_source=a * q - S)
    return fn


def static_pressure_bc(comp, b: NetworkBuilder, p: float, t: float, d_inner) -> None:
    """Druckrandbedingung als STATISCHER Überdruck am Anschluss. Die Geschwindig-
    keit am Rand ist nur definiert, wenn der Rand am Ende GENAU EINER Leitung
    mit bekanntem Querschnitt sitzt (der Knoten enthält nur Rand + Leitung)
    oder d_inner angegeben ist: dann hängt der Randknoten über eine Übergangs-
    kante mit Δp = ρw²/2 am Netzknoten. Sonst (Knotenpunkt mehrerer Bauteile,
    Behälter, ideale Verbindung) wird der Knotendruck direkt vorgegeben — so
    bleiben auch widersprüchliche Randbedingungen am selben Knoten erkennbar."""
    el = b.port("port")
    area = None
    if d_inner is not None:
        area = math.pi * d_inner ** 2 / 4.0
    else:
        others = [(c, pn) for c, pn in b.same_node_ports("port") if not c.measurement_tap]
        if len(others) == 1:
            area = others[0][0].port_flow_area(others[0][1]) or None
        elif any(c.port_flow_area(pn) for c, pn in others):
            b.notice(
                f"Druckrandbedingung '{comp.name}' sitzt an einem Knotenpunkt mehrerer Bauteile "
                f"— Strömungsgeschwindigkeit am Rand nicht definiert, p gilt als Knotendruck "
                f"(statisch = total). Für den statischen Druck am Ende einer Leitung den Rand "
                f"direkt an die Leitung anschließen oder d_inner_mm angeben.")
    if area is None:
        b.pressure_bc(el, p, t)
        return
    rand = b.internal("rand")
    b.pressure_bc(rand, p, t)
    b.edge(el, rand, _dyn_coeff(area), None, label="dyn")


@register("inflow")
class Inflow(Component):
    """Inflow (Zulauf/Quelle): Randelement mit EINEM Anschlusspunkt für
    eintretendes Wasser. Gibt die Eintrittstemperatur vor sowie ENTWEDER
    den Volumenstrom q ODER den statischen Überdruck p (gauge) am Anschluss."""

    t_set: float
    q: float | None
    p: float | None
    d_inner: float | None

    PARAMS = (
        Param("t_set", "temperature", required=True, help="Temperatur eintretenden Wassers"),
        Param("q", "flow", help="Zulauf-Volumenstrom ins Netz (ENTWEDER q ODER p)"),
        Param("p", "pressure",
              help="statischer Überdruck (gauge) am Anschluss (ENTWEDER q ODER p)"),
        _D_CONN,
    )

    def check_params(self):
        if (self.q is None) == (self.p is None):
            return ["Genau EINE Randbedingung angeben: 'q_m3h' (Volumenstrom) "
                    "ODER 'p_kPa' (Überdruck)."]
        return None

    def port_names(self) -> tuple[str, ...]:
        return ("port",)

    def build(self, b: NetworkBuilder) -> None:
        el = b.port("port")
        if self.q is not None:
            b.flow_bc(el, self.q, self.t_set)
        else:
            assert self.p is not None
            static_pressure_bc(self, b, self.p, self.t_set, self.d_inner)


@register("outflow")
class Outflow(Component):
    """Outflow (Ablauf/Austritt): Randelement mit EINEM Anschlusspunkt für
    austretendes Wasser. ENTWEDER statischer Überdruck p am Austritt (gauge;
    Auslauf ins Freie: p_kPa: 0) ODER Entnahme-Volumenstrom q (> 0 = aus dem
    Netz).
    Die Austrittstemperatur ist Ergebnis (konvektiver Transport). Bei
    Strömungsumkehr (Eintritt über den Outflow) wird t_reverse angesetzt."""

    q: float | None
    p: float | None
    t_reverse: float
    d_inner: float | None

    PARAMS = (
        Param("p", "pressure",
              help="statischer Überdruck (gauge) am Austritt (ENTWEDER p ODER q)"),
        Param("q", "flow", minv=0.0, help="Entnahme-Volumenstrom aus dem Netz (ENTWEDER p ODER q)"),
        Param("t_reverse", "temperature", default=20.0,
              help="Temperatur, falls Wasser rückwärts eintritt (sonst ohne Bedeutung)"),
        _D_CONN,
    )

    def check_params(self):
        if (self.q is None) == (self.p is None):
            return ["Genau EINE Randbedingung angeben: 'p_kPa' (Überdruck) "
                    "ODER 'q_m3h' (Entnahme)."]
        return None

    def port_names(self) -> tuple[str, ...]:
        return ("port",)

    def build(self, b: NetworkBuilder) -> None:
        el = b.port("port")
        if self.p is not None:
            static_pressure_bc(self, b, self.p, self.t_reverse, self.d_inner)
        else:
            assert self.q is not None
            b.flow_bc(el, -self.q, self.t_reverse)   # Entnahme = negative Fluss-RB


@register("open_end")
class OpenEnd(Component):
    """Systemgrenze. bc=pressure: statischer Überdruck am Anschluss vorgegeben
    (Flüsse ergeben sich);
    bc=flow: Volumenstrom vorgegeben (q > 0 = ins Netz hinein).
    t_supply ist die Temperatur eintretenden Wassers."""

    bc: str
    p: float | None
    q: float | None
    t_supply: float
    d_inner: float | None

    PARAMS = (
        Param("bc", "str", required=True, choices=("pressure", "flow"), help="Art der Randbedingung"),
        Param("p", "pressure",
              help="statischer Überdruck (gauge) am Rohrende (bei bc=pressure); "
                   "Auslauf ins Freie: 0"),
        Param("q", "flow", help="Volumenstrom, q > 0 = ins Netz (bei bc=flow)"),
        Param("t_supply", "temperature", default=20.0, help="Temperatur eintretenden Wassers"),
        _D_CONN,
    )

    def check_params(self):
        errs = []
        if self.bc == "pressure" and self.p is None:
            errs.append("Bei bc=pressure ist 'p_kPa' (bzw. p_Pa/p_bar) erforderlich.")
        if self.bc == "flow" and self.q is None:
            errs.append("Bei bc=flow ist 'q_m3h' (bzw. q_l_s/q_m3s) erforderlich.")
        return errs or None

    def port_names(self) -> tuple[str, ...]:
        return ("port",)

    def build(self, b: NetworkBuilder) -> None:
        el = b.port("port")
        if self.bc == "pressure":
            assert self.p is not None
            static_pressure_bc(self, b, self.p, self.t_supply, self.d_inner)
        else:
            assert self.q is not None
            b.flow_bc(el, self.q, self.t_supply)
