"""Netzwerkaufbau und Kompilierung: Ports → Knoten (Union-Find), Kanten,
Randbedingungen, Druckinsel-Analyse und Validierung."""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field

from .components.base import CoeffFn, Component, ThermalFn
from .components.registry import COMPONENT_REGISTRY
from .exceptions import NetworkValidationError, SingularNetworkError
from .fluids import Fluid, WATER_DEFAULT


@dataclass
class Node:
    index: int
    label: str
    elements: list[str]
    ua: float = 0.0                       # Wärmeverlust UA [W/K] (Summe)
    t_amb_weighted: float = 0.0           # UA-gewichtete Umgebungstemperatur
    p_bc: float | None = None             # Druck-Randbedingung [Pa]
    is_auto_ref: bool = False             # automatisch gesetzter Referenzdruck
    flow_bc: float = 0.0                  # Summe Quellterme [m³/s], >0 = ins Netz
    bc_supplies: list = field(default_factory=list)  # [(q, t_supply)] je Fluss-RB
    t_supply: float | None = None         # Zulauftemperatur der Druck-RB

    @property
    def t_amb(self) -> float:
        return self.t_amb_weighted / self.ua if self.ua > 0 else 20.0

    @property
    def pinned(self) -> bool:
        return self.p_bc is not None


@dataclass
class HydraulicEdge:
    index: int
    component: Component
    label: str
    node_from: int
    node_to: int
    coeff_fn: CoeffFn
    thermal_fn: ThermalFn | None
    fixed_q: float | None = None          # fest vorgegebener Volumenstrom
    q_seed: float | None = None

    @property
    def name(self) -> str:
        return f"{self.component.name}:{self.label}" if self.label else self.component.name

    @property
    def is_fixed(self) -> bool:
        return self.fixed_q is not None


@dataclass
class CompiledNetwork:
    fluid: Fluid
    nodes: list[Node]
    edges: list[HydraulicEdge]
    components: dict[str, Component]
    notices: list[str] = field(default_factory=list)


class _UnionFind:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def add(self, x: str):
        self.parent.setdefault(x, x)

    def find(self, x: str) -> str:
        self.add(x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: str, b: str):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


@dataclass
class _RawEdge:
    component: Component
    el_from: str
    el_to: str
    coeff_fn: CoeffFn
    thermal_fn: ThermalFn | None
    fixed_q: float | None
    q_seed: float | None
    label: str


class _Builder:
    """NetworkBuilder-Implementierung für genau eine Komponente."""

    def __init__(self, comp: Component, uf: _UnionFind):
        self.comp = comp
        self.uf = uf
        self.raw_edges: list[_RawEdge] = []
        self.heat_losses: list[tuple[str, float, float]] = []
        self.pressure_bcs: list[tuple[str, float, float]] = []
        self.flow_bcs: list[tuple[str, float, float]] = []

    def port(self, port_name: str) -> str:
        return f"{self.comp.name}.{port_name}"

    def internal(self, label: str) -> str:
        el = f"{self.comp.name}::{label}"
        self.uf.add(el)
        return el

    def alias(self, el_a: str, el_b: str) -> None:
        self.uf.union(el_a, el_b)

    def edge(self, el_from: str, el_to: str, coeff_fn, thermal_fn=None, *,
             fixed_q=None, q_seed=None, label="") -> None:
        self.uf.add(el_from)
        self.uf.add(el_to)
        self.raw_edges.append(_RawEdge(self.comp, el_from, el_to, coeff_fn,
                                       thermal_fn, fixed_q, q_seed, label))

    def node_heat_loss(self, el: str, ua: float, t_amb: float) -> None:
        self.heat_losses.append((el, ua, t_amb))

    def pressure_bc(self, el: str, p: float, t_supply: float) -> None:
        self.pressure_bcs.append((el, p, t_supply))

    def flow_bc(self, el: str, q: float, t_supply: float) -> None:
        self.flow_bcs.append((el, q, t_supply))


class Network:
    """Nutzerseitiger Netzaufbau (Python-API; der YAML-Loader baut darauf auf)."""

    def __init__(self, fluid: Fluid | None = None):
        self.fluid = fluid or WATER_DEFAULT
        self.components: dict[str, Component] = {}
        self.connections: list[tuple[str, ...]] = []

    def add(self, component: Component) -> Component:
        if component.name in self.components:
            raise NetworkValidationError(
                [f"Komponentenname '{component.name}' ist doppelt vergeben."])
        self.components[component.name] = component
        return component

    def connect(self, *port_refs: str) -> None:
        """Verbindet zwei oder mehr Ports ('comp.port'). Drei und mehr Ports
        oder mehrfach genannte Ports bilden implizit eine Verzweigung."""
        if len(port_refs) < 2:
            raise NetworkValidationError(
                [f"Eine Verbindung braucht mindestens 2 Ports, erhalten: {list(port_refs)}"])
        self.connections.append(tuple(str(p) for p in port_refs))

    # ------------------------------------------------------------------
    def compile(self) -> CompiledNetwork:
        errors: list[str] = []
        notices: list[str] = []

        if not self.components:
            raise NetworkValidationError(["Das Netz enthält keine Komponenten."])

        all_ports = {f"{c.name}.{p}" for c in self.components.values() for p in c.port_names()}

        # 1. Verbindungen prüfen und Ports mergen
        uf = _UnionFind()
        for el in all_ports:
            uf.add(el)
        connected_ports: set[str] = set()
        for conn in self.connections:
            valid_refs = []
            for ref in conn:
                if ref in all_ports:
                    valid_refs.append(ref)
                    connected_ports.add(ref)
                else:
                    errors.append(self._port_error(ref, all_ports))
            for other in valid_refs[1:]:
                uf.union(valid_refs[0], other)

        # 2. Komponenten bauen (interne Knoten/Kanten, Randbedingungen)
        raw_edges: list[_RawEdge] = []
        heat_losses: list[tuple[str, float, float]] = []
        pressure_bcs: list[tuple[str, float, float]] = []
        flow_bcs: list[tuple[str, float, float]] = []
        for comp in self.components.values():
            b = _Builder(comp, uf)
            comp.build(b)
            raw_edges += b.raw_edges
            heat_losses += b.heat_losses
            pressure_bcs += b.pressure_bcs
            flow_bcs += b.flow_bcs

        # 3. Unverbundene Ports melden
        for el in sorted(all_ports - connected_ports):
            errors.append(f"Port '{el}' ist mit nichts verbunden.")

        if errors:
            raise NetworkValidationError(errors)

        # 4. Knoten aus Union-Find-Wurzeln bilden
        root_members: dict[str, list[str]] = {}
        for el in uf.parent:
            root_members.setdefault(uf.find(el), []).append(el)
        nodes: list[Node] = []
        el_to_node: dict[str, int] = {}
        for root, members in sorted(root_members.items()):
            idx = len(nodes)
            ports_only = [m for m in members if "::" not in m]
            label = " = ".join(sorted(ports_only)[:3]) or root
            if len(ports_only) > 3:
                label += f" (+{len(ports_only) - 3})"
            nodes.append(Node(index=idx, label=label, elements=sorted(members)))
            for m in members:
                el_to_node[m] = idx

        # 5. Randbedingungen den Knoten zuordnen
        for el, ua, t_amb in heat_losses:
            n = nodes[el_to_node[el]]
            n.ua += ua
            n.t_amb_weighted += ua * t_amb
        for el, p, t_sup in pressure_bcs:
            n = nodes[el_to_node[el]]
            if n.p_bc is not None and abs(n.p_bc - p) > 1e-9:
                errors.append(
                    f"Widersprüchliche Druck-Randbedingungen am Knoten '{n.label}': "
                    f"{n.p_bc/1e3:.3f} kPa vs. {p/1e3:.3f} kPa")
            n.p_bc = p
            n.t_supply = t_sup
        for el, q, t_sup in flow_bcs:
            n = nodes[el_to_node[el]]
            n.flow_bc += q
            n.bc_supplies.append((q, t_sup))   # Enthalpie je RB, nicht "last wins"

        edges = [HydraulicEdge(index=i, component=r.component, label=r.label,
                               node_from=el_to_node[r.el_from], node_to=el_to_node[r.el_to],
                               coeff_fn=r.coeff_fn, thermal_fn=r.thermal_fn,
                               fixed_q=r.fixed_q, q_seed=r.q_seed)
                 for i, r in enumerate(raw_edges)]

        if errors:
            raise NetworkValidationError(errors)

        # 6. Druckinsel-Analyse (Zusammenhang über druckempfindliche Kanten)
        self._analyse_islands(nodes, edges, notices)

        return CompiledNetwork(fluid=self.fluid, nodes=nodes, edges=edges,
                               components=dict(self.components), notices=notices)

    # ------------------------------------------------------------------
    def _port_error(self, ref: str, all_ports: set[str]) -> str:
        if "." not in ref:
            return (f"Ungültige Port-Referenz '{ref}' – erwartetes Format 'komponente.port'.")
        comp_name, port = ref.split(".", 1)
        if comp_name not in self.components:
            hint = difflib.get_close_matches(comp_name, list(self.components), n=1)
            sug = f" Meinten Sie '{hint[0]}'?" if hint else ""
            return f"Unbekannte Komponente '{comp_name}' in Verbindung '{ref}'.{sug}"
        valid = self.components[comp_name].port_names()
        hint = difflib.get_close_matches(port, valid, n=1)
        sug = f" Meinten Sie '{comp_name}.{hint[0]}'?" if hint else ""
        return (f"Komponente '{comp_name}' (Typ '{self.components[comp_name].type_name}') "
                f"hat keinen Port '{port}'. Gültige Ports: {', '.join(valid)}.{sug}")

    @staticmethod
    def _analyse_islands(nodes: list[Node], edges: list[HydraulicEdge], notices: list[str]):
        """Je Zusammenhangskomponente des druckempfindlichen Teilgraphen muss
        genau ein Druckanker existieren; feste Volumenströme müssen je Insel
        bilanzieren (sonst ist das Gleichungssystem widersprüchlich)."""
        uf = _UnionFind()
        for n in nodes:
            uf.add(str(n.index))
        for e in edges:
            if not e.is_fixed:
                uf.union(str(e.node_from), str(e.node_to))

        islands: dict[str, list[Node]] = {}
        for n in nodes:
            islands.setdefault(uf.find(str(n.index)), []).append(n)

        for members in islands.values():
            member_idx = {n.index for n in members}
            has_real_bc = any(n.p_bc is not None and not n.is_auto_ref for n in members)
            if not any(n.pinned for n in members):
                ref = members[0]
                ref.p_bc = 150e3
                ref.is_auto_ref = True
                notices.append(
                    f"Kein Druck vorgegeben im Teilnetz um '{ref.label}' – Referenzdruck "
                    f"150 kPa dort gesetzt (geschlossener Kreis, nur Druckdifferenzen relevant).")
            if has_real_bc:
                continue  # offene Grenze kann jede Bilanz aufnehmen
            balance = sum(n.flow_bc for n in members)
            involved: list[str] = []
            for e in edges:
                if e.is_fixed:
                    assert e.fixed_q is not None
                    if e.node_to in member_idx:
                        balance += e.fixed_q
                        involved.append(e.name)
                    if e.node_from in member_idx:
                        balance -= e.fixed_q
                        if e.name not in involved:
                            involved.append(e.name)
            scale = max((abs(e.fixed_q or 0.0) for e in edges if e.is_fixed), default=0.0)
            scale = max(scale, max((abs(n.flow_bc) for n in members), default=0.0), 1e-9)
            if abs(balance) > 1e-9 + 1e-6 * scale:
                raise SingularNetworkError(
                    f"Feste Volumenströme bilanzieren nicht (Differenz {balance*3600:.4f} m³/h) "
                    f"im Teilnetz um '{members[0].label}'. Beteiligt: {', '.join(involved) or '–'}. "
                    f"Typische Ursachen: zwei Konstant-Volumenstrom-Pumpen in Reihe mit "
                    f"unterschiedlichen Sollwerten oder unbalancierte Volumenstrom-Randbedingungen.")

    # ------------------------------------------------------------------
    def solve(self, settings=None, thermal: bool = True):
        """Komfortmethode: kompilieren, hydraulisch und (optional) thermisch lösen.

        thermal=False überspringt die Energiegleichung – nützlich für rein
        hydraulische Studien und für Fälle ohne stationäre Temperaturlösung
        (z.B. thermisch isolierter Umlauf mit fest vorgegebener Leistung).
        """
        from .solver.hydraulic import solve_hydraulics
        from .solver.settings import SolverSettings
        from .solver.thermal import skipped_thermal, solve_thermal
        from .results import build_result

        settings = settings or SolverSettings()
        compiled = self.compile()
        hyd = solve_hydraulics(compiled, settings)
        if thermal:
            th = solve_thermal(compiled, hyd, settings)
        else:
            th = skipped_thermal(compiled, settings)
            compiled.notices.append(
                "Thermik nicht berechnet (thermal=False) – Temperaturen zeigen den Startwert.")
        return build_result(compiled, hyd, th, settings)


def component_from_dict(name: str, spec: dict) -> Component:
    """Erzeugt eine Komponente aus einem YAML-/JSON-Dict ({'type': ..., params...})."""
    spec = dict(spec)
    type_name = spec.pop("type", None)
    if not type_name:
        raise NetworkValidationError(
            [f"Komponente '{name}': Schlüssel 'type' fehlt. "
             f"Verfügbare Typen: {', '.join(sorted(COMPONENT_REGISTRY))}"])
    cls = COMPONENT_REGISTRY.get(type_name)
    if cls is None:
        hint = difflib.get_close_matches(type_name, list(COMPONENT_REGISTRY), n=1)
        sug = f" Meinten Sie '{hint[0]}'?" if hint else ""
        raise NetworkValidationError(
            [f"Komponente '{name}': unbekannter Typ '{type_name}'.{sug} "
             f"Verfügbare Typen: {', '.join(sorted(COMPONENT_REGISTRY))}"])
    return cls(name, **spec)
