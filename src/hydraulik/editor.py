"""Schaltbild-Editor: generiert eine Single-File-HTML-Anwendung zum Zeichnen
hydraulischer Schaltungen.

Die Komponentenpalette, Ports und Parameterformulare werden aus der
Komponenten-Registry und den Param-Deklarationen erzeugt (Single Source of
Truth) — der Editor kann dem Solver nicht "davonlaufen": neue Komponenten
oder Parameter erscheinen nach Neu-Generierung automatisch.

    hydraulik editor --out hydraulik_editor.html
    # dann im Browser öffnen; Export erzeugt direkt rechenbares YAML
    # (inkl. layout-Block mit den Zeichnungskoordinaten für den Re-Import).
"""
from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from .components.registry import COMPONENT_REGISTRY
from .params import UNIT_GROUPS

#: Portregeln für Typen mit parametrischer Portanzahl
_DYNAMIC_PORTS = {
    "manifold": {"base": ["main"], "template": "s{}", "count_param": "n_ports"},
    "buffer_storage": {"base": [], "template": "p{}", "count_param": "n_ports"},
}


def _port_spec(type_name: str, cls) -> dict:
    if type_name in _DYNAMIC_PORTS:
        return dict(_DYNAMIC_PORTS[type_name])
    obj = cls.__new__(cls)          # statische port_names() brauchen keine Parameter
    return {"base": list(obj.port_names()), "template": None, "count_param": None}


def component_catalog() -> dict:
    """Maschinenlesbarer Katalog aller registrierten Komponenten."""
    types = []
    for type_name in sorted(COMPONENT_REGISTRY):
        cls = COMPONENT_REGISTRY[type_name]
        params = []
        for p in cls.PARAMS:
            params.append({
                "name": p.name,
                "group": p.group,
                "keys": p.accepted_keys(),
                "required": p.required,
                "default": p.default,          # SI bzw. Rohwert
                "minv": p.minv,
                "maxv": p.maxv,
                "choices": list(p.choices) if p.choices else None,
                "help": p.help,
            })
        doc = (cls.__doc__ or type_name).strip().splitlines()[0]
        types.append({
            "type": type_name,
            "doc": doc,
            "ports": _port_spec(type_name, cls),
            "params": params,
        })
    units = {g: dict(sfx) for g, sfx in UNIT_GROUPS.items()}
    return {"types": types, "units": units}


def render_editor() -> str:
    """Editor-HTML mit injiziertem Komponentenkatalog."""
    template = resources.files("hydraulik").joinpath("editor_template.html").read_text(
        encoding="utf-8")
    catalog = json.dumps(component_catalog(), ensure_ascii=False)
    html = template.replace("__CATALOG_JSON__", catalog)
    if "__CATALOG_JSON__" in html:
        raise RuntimeError("Platzhalter im Editor-Template nicht ersetzt.")
    return html


def build_editor(path: str | Path = "hydraulik_editor.html") -> Path:
    """Schreibt den Editor als eigenständige HTML-Datei (ohne Rechen-Endpunkt;
    für Rechnen im GUI: `hydraulik serve`)."""
    out = Path(path)
    out.write_text(render_editor(), encoding="utf-8")
    return out
