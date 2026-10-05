"""Basisklassen und Solver-Verträge aller Komponenten.

Hydraulischer Vertrag (je Kante):   Δp = a·Q + b·Q·|Q| − Δp_source
Thermischer Vertrag (je Kante):     T_out, Q̇ = f(T_in, |ṁ|)  mit Q̇ > 0 = Wärme INS Wasser.
Positive Flussrichtung einer Kante ist von port "in" nach port "out";
Rückströmung (Q < 0) ist zulässig und wird thermisch per Upwind behandelt.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, ClassVar, Protocol

from ..exceptions import ComponentParamError
from ..fluids import Fluid
from ..params import Param, parse_params


@dataclass
class EdgeCoefficients:
    a: float = 0.0           # linearer Widerstand [Pa/(m³/s)]
    b: float = 0.0           # quadratischer Widerstand [Pa/(m³/s)²]
    dp_source: float = 0.0   # Druckerhöhung (Pumpe) [Pa], positiv in in→out-Richtung


@dataclass
class ThermalResult:
    t_out: float                       # [°C]
    q_dot: float                       # [W], positiv = Wärme ins Wasser
    extras: dict = field(default_factory=dict)


CoeffFn = Callable[[float, Fluid], EdgeCoefficients]
ThermalFn = Callable[[float, float, Fluid], ThermalResult]


class NetworkBuilder(Protocol):
    """Schnittstelle, über die Komponenten beim Kompilieren Knoten/Kanten registrieren."""

    def port(self, port_name: str) -> str: ...
    def internal(self, label: str) -> str: ...
    def alias(self, el_a: str, el_b: str) -> None: ...
    def edge(self, el_from: str, el_to: str, coeff_fn: CoeffFn,
             thermal_fn: ThermalFn | None = None, *, fixed_q: float | None = None,
             q_seed: float | None = None, label: str = "") -> None: ...
    def node_heat_loss(self, el: str, ua: float, t_amb: float) -> None: ...
    def pressure_bc(self, el: str, p: float, t_supply: float) -> None: ...
    def flow_bc(self, el: str, q: float, t_supply: float) -> None: ...


def parse_label(field: str, raw, errors: list[str]) -> str | None:
    """Label-Felder (ts, BEMS id/key/description) sind Zeichenketten.

    Ganzzahlen werden übernommen (ts: 4 → "4"; YAML 1.2 liest ts: 08 als 8),
    Fließkommazahlen, Wahrheitswerte und Strukturen sind ein Fehler — sie
    würden still verfälscht (1.10 → "1.1", true → "True")."""
    if raw is None or isinstance(raw, str):
        return raw
    if isinstance(raw, int) and not isinstance(raw, bool):
        return str(raw)
    errors.append(f"'{field}' = {raw!r} muss eine Zeichenkette sein – Labels bitte in "
                  f"Anführungszeichen setzen (z.B. {field}: \"1.10\"); ungequotet liest "
                  f"YAML 1.2 Zahlen und true/false.")
    return None


def _parse_bems(name: str, raw) -> list[dict]:
    """BEMS-Messpunktliste (reserviert für JEDE Komponente): frei viele
    Datenpunkte je Komponente, jeder mit id (abfragbare BEMS-/Aedifion-ID),
    key (sprechender Alias) und description (Semantik). Rein deklarativ."""
    if raw is None:
        return []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise ComponentParamError(name, [
            "'bems' muss eine Liste von Messpunkten sein, z.B. "
            "bems: [{id: \"...\", key: EXP_..., description: \"...\"}]"])
    out, errors = [], []
    for k, entry in enumerate(raw):
        if not isinstance(entry, dict):
            errors.append(f"bems[{k}]: erwartet ein Mapping mit id/key/description, "
                          f"erhalten: {entry!r}")
            continue
        unknown = set(entry) - {"id", "key", "description"}
        if unknown:
            errors.append(f"bems[{k}]: unbekannte Schlüssel {sorted(unknown)} "
                          f"(erlaubt: id, key, description)")
            continue
        rec = {}
        for f in ("id", "key", "description"):
            val = parse_label(f"bems[{k}].{f}", entry.get(f), errors)
            if val not in (None, ""):
                rec[f] = val
        out.append(rec)
    if errors:
        raise ComponentParamError(name, errors)
    return out


class Component(ABC):
    """Basisklasse. Parameter werden deklarativ über PARAMS definiert und im
    Konstruktor (Python-API wie YAML) mit Einheiten-Suffixen angenommen."""

    type_name: ClassVar[str] = ""
    PARAMS: ClassVar[tuple[Param, ...]] = ()
    #: reservierte Listen-Kwargs mit Abschnitts-Parametern (z.B. conduit.pipes);
    #: rein deklarativ für JSON Schema/Katalog — das Parsen übernimmt die Klasse
    LIST_PARAMS: ClassVar[dict[str, tuple[Param, ...]]] = {}

    #: optionales Teilstrecken-Label (klassische TS-Nummer als Gruppierung
    #: für den Bericht; jede Komponente kann z.B. ts="4" tragen)
    ts: str | None

    def __init__(self, name: str, **kwargs):
        self.name = str(name)
        errors: list[str] = []
        self.ts = parse_label("ts", kwargs.pop("ts", None), errors)
        try:
            self.bems = _parse_bems(self.name, kwargs.pop("bems", None))
        except ComponentParamError as exc:          # mit den übrigen Fehlern sammeln
            errors += exc.messages
            self.bems = []
        values, param_errors = parse_params(self.type_name, self.PARAMS, kwargs)
        errors += param_errors
        if errors:
            raise ComponentParamError(self.name, errors)
        for key, val in values.items():
            setattr(self, key, val)
        more = self.check_params()
        if more:
            raise ComponentParamError(self.name, list(more))

    def port_names(self) -> tuple[str, ...]:
        return ("in", "out")

    def check_params(self) -> list[str] | None:
        """Typspezifische Konsistenzprüfungen; Liste von Fehlermeldungen oder None."""
        return None

    def q_seed(self) -> float | None:
        """Nennvolumenstrom als Startwert für den Solver (falls bekannt)."""
        return None

    @abstractmethod
    def build(self, b: NetworkBuilder) -> None:
        """Interne Knoten, Kanten und Randbedingungen registrieren."""

    def __repr__(self) -> str:
        return f"<{type(self).__name__} '{self.name}'>"


class TwoPortComponent(Component):
    """Standardfall: eine hydraulische Kante von 'in' nach 'out'."""

    def build(self, b: NetworkBuilder) -> None:
        b.edge(b.port("in"), b.port("out"),
               self.hydraulic_coefficients, self.thermal_outlet,
               q_seed=self.q_seed())

    @abstractmethod
    def hydraulic_coefficients(self, q: float, fluid: Fluid) -> EdgeCoefficients: ...

    def thermal_outlet(self, t_in: float, m_dot: float, fluid: Fluid) -> ThermalResult:
        """Default: adiabater Durchfluss."""
        return ThermalResult(t_out=t_in, q_dot=0.0)
