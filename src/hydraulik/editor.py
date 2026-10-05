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


def port_spec(type_name: str, cls) -> dict:
    """Ports eines Typs: feste Namen bzw. Vorlage + Anzahlparameter (manifold, Puffer)."""
    if type_name in _DYNAMIC_PORTS:
        return dict(_DYNAMIC_PORTS[type_name])
    obj = cls.__new__(cls)          # statische port_names() brauchen keine Parameter
    return {"base": list(obj.port_names()), "template": None, "count_param": None}


def _catalog_from(registry: dict) -> dict:
    """Maschinenlesbarer Katalog eines Komponentenregisters."""
    types = []
    for type_name in sorted(registry):
        cls = registry[type_name]
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
            "ports": port_spec(type_name, cls),
            "params": params,
        })
    units = {g: dict(sfx) for g, sfx in UNIT_GROUPS.items()}
    return {"types": types, "units": units}


def component_catalog() -> dict:
    """Katalog der Hydraulik-Komponenten."""
    return _catalog_from(COMPONENT_REGISTRY)


def air_catalog() -> dict:
    """Katalog der Luft-Komponenten (Lüftungsanlage)."""
    from .air.components import AIR_REGISTRY
    return _catalog_from(AIR_REGISTRY)


#: Platzhalter für den gemeinsamen YAML-1.2-Code beider Editoren (yaml_core.js)
_YAML_CORE_PLACEHOLDER = "/*__YAML_CORE_JS__*/"


def yaml_core_js() -> str:
    """Gemeinsamer YAML-1.2-Parser/-Serializer der Editoren (gleiche Semantik
    wie yamlio; die Paritätstests laden dieselbe Datei mit node)."""
    return resources.files("hydraulik").joinpath("yaml_core.js").read_text(encoding="utf-8")


def _render(template_name: str, catalog: dict) -> str:
    template = resources.files("hydraulik").joinpath(template_name).read_text(encoding="utf-8")
    html = template.replace("__CATALOG_JSON__", json.dumps(catalog, ensure_ascii=False))
    html = html.replace(_YAML_CORE_PLACEHOLDER, yaml_core_js())
    if "__CATALOG_JSON__" in html or _YAML_CORE_PLACEHOLDER in html:
        raise RuntimeError(f"Platzhalter in {template_name} nicht ersetzt.")
    return html


def render_editor() -> str:
    """Editor-HTML mit injiziertem Komponentenkatalog und YAML-Kern."""
    return _render("editor_template.html", component_catalog())


def build_editor(path: str | Path = "hydraulik_editor.html") -> Path:
    """Schreibt den Editor als eigenständige HTML-Datei (ohne Rechen-Endpunkt;
    für Rechnen im GUI: `hydraulik serve`)."""
    out = Path(path)
    out.write_text(render_editor(), encoding="utf-8")
    return out


def render_air_editor() -> str:
    """Lüftungsschema-Editor-HTML mit injiziertem Luft-Katalog und YAML-Kern."""
    return _render("air_editor_template.html", air_catalog())


def build_air_editor(path: str | Path = "lueftung_editor.html") -> Path:
    out = Path(path)
    out.write_text(render_air_editor(), encoding="utf-8")
    return out
