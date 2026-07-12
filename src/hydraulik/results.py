"""Ergebnisobjekte und Berichte (Text, dict, CSV)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .network import CompiledNetwork
from .solver.hydraulic import HydraulicState
from .solver.settings import SolverSettings
from .solver.thermal import ThermalState


@dataclass
class NodeResult:
    label: str
    p_kPa: float
    t_C: float
    stagnant: bool


@dataclass
class ComponentResult:
    name: str
    type_name: str
    q_m3h: float                  # Volumenstrom in Kanten-Definitionsrichtung (in→out)
    m_dot_kg_s: float
    dp_kPa: float                 # p_in − p_out (Druckverlust positiv, Pumpe negativ)
    t_in_C: float                 # strömungsrichtungsbewusst (Upwind-Seite)
    t_out_C: float
    q_dot_kW: float               # Wärme ins Wasser
    extras: dict = field(default_factory=dict)


@dataclass
class SensorReading:
    """Messwert(e) eines Sensors samt hinterlegter BEMS-Datenpunkt-Zuordnung —
    die maschinenlesbare Brücke zwischen Simulation und Betriebsdaten."""
    name: str
    type_name: str
    readings: dict                # z.B. {"t_C": 55.2} oder WMZ: q_m3h/t_.../q_dot_kW
    bems: dict                    # bems_id(_...), bems_key, description (nur belegte)


@dataclass
class TSSegment:
    """Ein zusammenhängender Abschnitt einer Teilstrecken-Gruppe (ts-Label).

    Eine klassische Teilstrecke (Vor- + Rücklauf unter einer Nummer) zerfällt
    im Zustandsraum typischerweise in zwei Abschnitte; jeder Abschnitt trägt
    konstanten Volumenstrom und eigene Zustandspunkte an den Enden.
    """
    ts: str
    part: int
    components: list[str]
    q_m3h: float
    dp_kPa: float                 # Summe der Druckänderung in Strömungsrichtung
    p_in_kPa: float
    p_out_kPa: float
    t_in_C: float
    t_out_C: float


@dataclass
class SolutionResult:
    converged: bool
    iterations_hydraulic: int
    iterations_thermal: int
    mass_residual: float
    momentum_residual: float
    energy_imbalance_W: float
    fluid_name: str
    nodes: list[NodeResult]
    components: list[ComponentResult]
    notices: list[str]
    teilstrecken: list[TSSegment] = field(default_factory=list)
    sensors: list[SensorReading] = field(default_factory=list)

    def __getitem__(self, name: str) -> ComponentResult:
        for c in self.components:
            if c.name == name:
                return c
        raise KeyError(f"Keine Komponente/Kante '{name}' im Ergebnis. "
                       f"Vorhanden: {', '.join(c.name for c in self.components)}")

    def to_dict(self) -> dict:
        return {
            "converged": self.converged,
            "fluid": self.fluid_name,
            "iterations": {"hydraulic": self.iterations_hydraulic,
                           "thermal": self.iterations_thermal},
            "residuals": {"mass": self.mass_residual, "momentum": self.momentum_residual,
                          "energy_imbalance_W": self.energy_imbalance_W},
            "notices": list(self.notices),
            "components": [{**vars(c)} for c in self.components],
            "nodes": [{**vars(n)} for n in self.nodes],
            "teilstrecken": [{**vars(s)} for s in self.teilstrecken],
            "sensors": [{**vars(s)} for s in self.sensors],
        }

    def to_csv(self, path: str) -> None:
        import csv
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["name", "type", "q_m3h", "m_dot_kg_s", "dp_kPa",
                        "t_in_C", "t_out_C", "q_dot_kW"])
            for c in self.components:
                w.writerow([c.name, c.type_name, f"{c.q_m3h:.6f}", f"{c.m_dot_kg_s:.6f}",
                            f"{c.dp_kPa:.4f}", f"{c.t_in_C:.3f}", f"{c.t_out_C:.3f}",
                            f"{c.q_dot_kW:.4f}"])

    def report(self) -> str:
        lines = []
        lines.append("=" * 100)
        lines.append("HYDRAULIK – stationäres Ergebnis")
        lines.append("=" * 100)
        lines.append(f"Fluid: {self.fluid_name}   konvergiert: {'ja' if self.converged else 'NEIN'}   "
                     f"Iterationen: {self.iterations_hydraulic} (hydraulisch) / "
                     f"{self.iterations_thermal} (thermisch)")
        lines.append(f"Residuen: Masse {self.mass_residual:.2e}, Impuls {self.momentum_residual:.2e}, "
                     f"Energiebilanz {self.energy_imbalance_W:+.2f} W   "
                     f"(alle Drücke als Überdruck/gauge)")
        for note in self.notices:
            lines.append(f"Hinweis: {note}")
        lines.append("")
        header = (f"{'Komponente':<24}{'Typ':<20}{'V̇ [m³/h]':>10}{'ṁ [kg/s]':>10}"
                  f"{'Δp [kPa]':>10}{'T_ein [°C]':>11}{'T_aus [°C]':>11}{'Q̇ [kW]':>9}")
        lines.append(header)
        lines.append("-" * len(header))
        for c in self.components:
            lines.append(f"{c.name:<24}{c.type_name:<20}{c.q_m3h:>10.3f}{c.m_dot_kg_s:>10.4f}"
                         f"{c.dp_kPa:>10.3f}{c.t_in_C:>11.2f}{c.t_out_C:>11.2f}{c.q_dot_kW:>9.3f}")
        if self.sensors:
            lines.append("")
            h2 = f"{'Sensor':<20}{'Typ':<24}{'Messwerte':<38}{'BEMS (Key / ID)'}"
            lines.append(h2)
            lines.append("-" * 100)
            unit = {"t_C": "°C", "p_kPa": "kPa (ü)", "dp_kPa": "kPa", "q_m3h": "m³/h",
                    "m_dot_kg_s": "kg/s", "t_leitung_C": "°C", "t_ref_C": "°C",
                    "q_dot_kW": "kW"}
            for s in self.sensors:
                vals = "  ".join(f"{k.rsplit('_', 1)[0] if k in unit else k}="
                                 f"{v:.3f} {unit.get(k, '')}".strip()
                                 for k, v in s.readings.items())
                ids = " / ".join(x for x in (s.bems.get("bems_key"),
                                             s.bems.get("bems_id")) if x)
                lines.append((f"{s.name:<20}{s.type_name:<24}" + vals.ljust(38)
                              + ("  " + ids if ids else "")).rstrip())
        if self.teilstrecken:
            lines.append("")
            h3 = (f"{'TS':<5}{'Abschnitt':<10}{'Komponenten':<34}{'V̇ [m³/h]':>10}"
                  f"{'ΣΔp [kPa]':>11}{'p ein→aus [kPa]':>19}{'T ein→aus [°C]':>17}")
            lines.append(h3)
            lines.append("-" * len(h3))
            for s in self.teilstrecken:
                comps = ", ".join(s.components)
                if len(comps) > 32:
                    comps = comps[:29] + "…"
                lines.append(f"{s.ts:<5}{s.part:<10}{comps:<34}{s.q_m3h:>10.3f}"
                             f"{s.dp_kPa:>11.3f}"
                             f"{s.p_in_kPa:>9.1f}→{s.p_out_kPa:<9.1f}"
                             f"{s.t_in_C:>8.2f}→{s.t_out_C:<8.2f}")
        lines.append("")
        header2 = f"{'Knoten':<64}{'p [kPa]':>10}{'T [°C]':>9}"
        lines.append(header2)
        lines.append("-" * len(header2))
        for nd in self.nodes:
            flag = "  (stagnierend)" if nd.stagnant else ""
            lines.append(f"{nd.label:<64}{nd.p_kPa:>10.2f}{nd.t_C:>9.2f}{flag}")
        lines.append("=" * 100)
        return "\n".join(lines)


def _ts_segments(net: CompiledNetwork, hyd: HydraulicState, th: ThermalState,
                 notices: list[str]) -> list[TSSegment]:
    """Gruppiert Kanten nach dem ts-Label ihrer Komponente und zerlegt jede
    Gruppe in zusammenhängende Ketten in Strömungsrichtung.

    Konsistenzprüfungen (klassische TS-Definition: konstanter Volumenstrom):
    uneinheitliche Volumenströme oder Verzweigungen innerhalb einer Gruppe
    deuten auf falsch geschnittene Teilstrecken hin → Hinweis im Bericht.
    """
    groups: dict[str, list] = {}
    for e in net.edges:
        if e.component.ts is not None:
            groups.setdefault(e.component.ts, []).append(e)

    def sort_key(label: str):
        try:
            return (0, float(label), label)
        except ValueError:
            return (1, 0.0, label)

    segments: list[TSSegment] = []
    for ts in sorted(groups, key=sort_key):
        edges = groups[ts]
        flows = [abs(float(hyd.q[e.index])) for e in edges]
        q_scale = max(max(flows), 1e-12)
        if (max(flows) - min(flows)) > 1e-4 * q_scale + 1e-12:
            notices.append(
                f"Teilstrecke {ts}: uneinheitliche Volumenströme "
                f"({min(flows)*3600:.3f}…{max(flows)*3600:.3f} m³/h) – die Gruppe "
                f"überspannt eine Verzweigung; Teilstrecken dort neu schneiden.")
        # Kanten in Strömungsrichtung orientieren und Ketten verfolgen
        up = {e.index: (e.node_from if hyd.q[e.index] >= 0 else e.node_to) for e in edges}
        down = {e.index: (e.node_to if hyd.q[e.index] >= 0 else e.node_from) for e in edges}
        out_edges: dict[int, list] = {}
        indeg: dict[int, int] = {}
        for e in edges:
            out_edges.setdefault(up[e.index], []).append(e)
            indeg[down[e.index]] = indeg.get(down[e.index], 0) + 1
        if any(len(v) > 1 for v in out_edges.values()):
            notices.append(
                f"Teilstrecke {ts}: verzweigt innerhalb der Gruppe – "
                f"keine Kettenauswertung möglich; Teilstrecken neu schneiden.")
            continue
        starts = [n for n in out_edges if indeg.get(n, 0) == 0]
        if not starts and edges:                     # komplette Masche markiert
            starts = [up[edges[0].index]]
        visited: set[int] = set()
        part = 0
        for start in starts:
            node = start
            chain = []
            while node in out_edges:
                e = out_edges[node][0]
                if e.index in visited:
                    break
                visited.add(e.index)
                chain.append(e)
                node = down[e.index]
            if not chain:
                continue
            part += 1
            first, last = chain[0], chain[-1]
            dp_flow = sum(float(hyd.p[up[e.index]] - hyd.p[down[e.index]]) for e in chain)
            segments.append(TSSegment(
                ts=ts, part=part,
                components=[e.name for e in chain],
                q_m3h=abs(float(hyd.q[first.index])) * 3600.0,
                dp_kPa=dp_flow / 1e3,
                p_in_kPa=float(hyd.p[up[first.index]]) / 1e3,
                p_out_kPa=float(hyd.p[down[last.index]]) / 1e3,
                t_in_C=float(th.t_node[up[first.index]]),
                t_out_C=float(th.t_edge_out[last.index]),
            ))
    return segments


def build_result(net: CompiledNetwork, hyd: HydraulicState, th: ThermalState,
                 settings: SolverSettings) -> SolutionResult:
    comps: list[ComponentResult] = []
    fluid = net.fluid
    for e in net.edges:
        i = e.index
        q = float(hyd.q[i])
        upwind = e.node_from if q >= 0 else e.node_to
        dp = float(hyd.p[e.node_from] - hyd.p[e.node_to])
        extras = dict(th.edge_extras[i])
        d_inner = getattr(e.component, "d_inner", None)
        # Geschwindigkeit nur, wenn der Durchmesser physikalisch wirkt (Rohrmodell);
        # bei C-Wert-/Ideal-Modus (length = None) wäre der Default-Durchmesser irreführend
        if d_inner and getattr(e.component, "length", True) is not None:
            extras["v_m_s"] = abs(q) / (math.pi * d_inner ** 2 / 4.0)
        comps.append(ComponentResult(
            name=e.name, type_name=e.component.type_name,
            q_m3h=q * 3600.0, m_dot_kg_s=q * fluid.rho, dp_kPa=dp / 1e3,
            t_in_C=float(th.t_node[upwind]), t_out_C=float(th.t_edge_out[i]),
            q_dot_kW=float(th.q_dot_edge[i]) / 1e3,
            extras=extras,
        ))
    nodes = [NodeResult(label=nd.label, p_kPa=float(hyd.p[nd.index]) / 1e3,
                        t_C=float(th.t_node[nd.index]),
                        stagnant=nd.index in th.stagnant_nodes)
             for nd in net.nodes]
    notices = list(net.notices)
    segments = _ts_segments(net, hyd, th, notices)

    # Sensoren: Komponenten mit measure()-Hook lesen den gelösten Zustand ab
    node_of_ref = {el: nd.index for nd in net.nodes for el in nd.elements}
    sensors: list[SensorReading] = []
    for comp in net.components.values():
        fn = getattr(comp, "measure", None)
        if fn is None:
            continue
        readings = fn(net, hyd, th, node_of_ref.__getitem__)
        bems = {p.name: getattr(comp, p.name) for p in comp.PARAMS
                if (p.name.startswith("bems") or p.name == "description")
                and getattr(comp, p.name, None)}
        sensors.append(SensorReading(name=comp.name, type_name=comp.type_name,
                                     readings=readings, bems=bems))

    return SolutionResult(
        converged=hyd.converged and th.converged,
        iterations_hydraulic=hyd.iterations, iterations_thermal=th.iterations,
        mass_residual=hyd.mass_residual, momentum_residual=hyd.momentum_residual,
        energy_imbalance_W=th.energy_imbalance,
        fluid_name=fluid.name, nodes=nodes, components=comps, notices=notices,
        teilstrecken=segments, sensors=sensors)
