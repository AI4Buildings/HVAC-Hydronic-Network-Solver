"""Rohrenden: offen (Druck-/Volumenstrom-RB) oder dicht (Endstück)."""
from __future__ import annotations

from ..params import Param
from .base import Component, NetworkBuilder
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


@register("inflow")
class Inflow(Component):
    """Inflow (Zulauf/Quelle): Randelement mit EINEM Anschlusspunkt für
    eintretendes Wasser. Gibt die Eintrittstemperatur vor sowie ENTWEDER
    den Volumenstrom q ODER den Überdruck p (gauge) am Anschluss."""

    t_set: float
    q: float | None
    p: float | None

    PARAMS = (
        Param("t_set", "temperature", required=True, help="Temperatur eintretenden Wassers"),
        Param("q", "flow", help="Zulauf-Volumenstrom ins Netz (ENTWEDER q ODER p)"),
        Param("p", "pressure", help="Überdruck (gauge) am Anschluss (ENTWEDER q ODER p)"),
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
            b.pressure_bc(el, self.p, self.t_set)


@register("outflow")
class Outflow(Component):
    """Outflow (Ablauf/Austritt): Randelement mit EINEM Anschlusspunkt für
    austretendes Wasser. ENTWEDER Überdruck p am Austritt (gauge; Auslauf
    ins Freie: p_kPa: 0) ODER Entnahme-Volumenstrom q (> 0 = aus dem Netz).
    Die Austrittstemperatur ist Ergebnis (konvektiver Transport). Bei
    Strömungsumkehr (Eintritt über den Outflow) wird t_reverse angesetzt."""

    q: float | None
    p: float | None
    t_reverse: float

    PARAMS = (
        Param("p", "pressure", help="Überdruck (gauge) am Austritt (ENTWEDER p ODER q)"),
        Param("q", "flow", minv=0.0, help="Entnahme-Volumenstrom aus dem Netz (ENTWEDER p ODER q)"),
        Param("t_reverse", "temperature", default=20.0,
              help="Temperatur, falls Wasser rückwärts eintritt (sonst ohne Bedeutung)"),
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
            b.pressure_bc(el, self.p, self.t_reverse)
        else:
            assert self.q is not None
            b.flow_bc(el, -self.q, self.t_reverse)   # Entnahme = negative Fluss-RB


@register("open_end")
class OpenEnd(Component):
    """Systemgrenze. bc=pressure: Knotendruck vorgegeben (Flüsse ergeben sich);
    bc=flow: Volumenstrom vorgegeben (q > 0 = ins Netz hinein).
    t_supply ist die Temperatur eintretenden Wassers."""

    bc: str
    p: float | None
    q: float | None
    t_supply: float

    PARAMS = (
        Param("bc", "str", required=True, choices=("pressure", "flow"), help="Art der Randbedingung"),
        Param("p", "pressure",
              help="Überdruck (gauge) am Rohrende (bei bc=pressure); Auslauf ins Freie: 0"),
        Param("q", "flow", help="Volumenstrom, q > 0 = ins Netz (bei bc=flow)"),
        Param("t_supply", "temperature", default=20.0, help="Temperatur eintretenden Wassers"),
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
            b.pressure_bc(el, self.p, self.t_supply)
        else:
            assert self.q is not None
            b.flow_bc(el, self.q, self.t_supply)
