"""YAML-Loader für Lüftungsanlagen-Schemata (Luftseite).

Gleiches Dateiformat wie die Hydraulik (components + connections, optionaler
layout-Block des Editors), aber mit der Luft-Registry und Ketten-Semantik:
Verbindungen sind PAARE von Ports, jeder Port wird genau einmal verbunden —
die Anlage besteht aus zwei linearen Strängen (Zuluft: aussenluft → … →
zuluft; Abluft: abluft_raum → … → fortluft), gekoppelt über die WRG.
Fehler werden gesammelt gemeldet (LLM-tauglich, mit Vorschlägen).
"""
from __future__ import annotations

import difflib
from pathlib import Path

import yaml

from ..exceptions import ComponentParamError, NetworkValidationError
from ..yaml_loader import _UniqueKeyLoader
from .components import AIR_REGISTRY


class AirPlant:
    """Geladenes Luftschema: Komponenten + Portverbindungen."""

    def __init__(self, components: dict, connections: list[tuple[str, str]]):
        self.components = components
        self.connections = connections


def load_air(source) -> AirPlant:
    if isinstance(source, dict):
        doc = source
    else:
        text = (Path(source).read_text(encoding="utf-8")
                if _is_path(source) else str(source))
        doc = yaml.load(text, Loader=_UniqueKeyLoader)
    if not isinstance(doc, dict):
        raise NetworkValidationError(
            ["Eingabe muss ein Mapping mit 'components' und 'connections' sein."])

    errors: list[str] = []
    known = {"components", "connections", "layout"}
    for key in doc:
        if key not in known:
            errors.append(f"Unbekannter Schlüssel '{key}' auf oberster Ebene. "
                          f"Erlaubt: {', '.join(sorted(known))}")

    comps: dict = {}
    raw = doc.get("components")
    if not isinstance(raw, dict) or not raw:
        errors.append("'components' fehlt oder ist leer.")
        raw = {}
    for name, spec in raw.items():
        if not isinstance(spec, dict) or "type" not in spec:
            errors.append(f"Komponente '{name}': Mapping mit 'type' erwartet.")
            continue
        spec = dict(spec)
        tname = str(spec.pop("type"))
        cls = AIR_REGISTRY.get(tname)
        if cls is None:
            hint = difflib.get_close_matches(tname, AIR_REGISTRY, n=1)
            sugg = f" Meinten Sie '{hint[0]}'?" if hint else ""
            errors.append(f"Komponente '{name}': unbekannter Lufttyp '{tname}'.{sugg} "
                          f"Gültig: {', '.join(sorted(AIR_REGISTRY))}")
            continue
        try:
            comps[str(name)] = cls(str(name), **spec)
        except ComponentParamError as exc:
            errors += [f"Komponente '{name}': {m}" for m in exc.messages]

    conns: list[tuple[str, str]] = []
    used: dict[str, int] = {}
    raw_c = doc.get("connections")
    if not isinstance(raw_c, list) or not raw_c:
        errors.append("'connections' fehlt oder ist leer.")
        raw_c = []
    for k, conn in enumerate(raw_c):
        if not isinstance(conn, (list, tuple)) or len(conn) != 2:
            errors.append(f"Verbindung Nr. {k + 1}: Luftstränge sind Ketten — "
                          f"genau ZWEI Ports je Verbindung, erhalten: {conn!r}")
            continue
        pair = tuple(str(p) for p in conn)
        for ref in pair:
            cname, _, port = ref.partition(".")
            if cname in comps and port not in comps[cname].port_names():
                errors.append(f"Verbindung Nr. {k + 1}: '{ref}' — Port unbekannt "
                              f"(gültig: {', '.join(comps[cname].port_names())}).")
            elif cname not in comps and cname not in raw:
                errors.append(f"Verbindung Nr. {k + 1}: unbekannte Komponente '{cname}'.")
            used[ref] = used.get(ref, 0) + 1
        conns.append(pair)
    for ref, n in used.items():
        if n > 1:
            errors.append(f"Port '{ref}' ist {n}-fach verbunden — in Luftsträngen "
                          f"ist jeder Port genau einmal verbunden (keine Verzweigung).")

    if errors:
        raise NetworkValidationError(errors)
    return AirPlant(comps, conns)


def _is_path(source) -> bool:
    if isinstance(source, Path):
        return True
    s = str(source)
    return "\n" not in s and (s.endswith((".yaml", ".yml", ".json")) or Path(s).exists())
