"""JSON Schema (Draft 2020-12) des Eingabeformats — generiert, nie gepflegt.

    hydraulik schema [--luft] [--out datei.json]

Quellen (Single Source of Truth, dieselben wie für Loader und Editor):
Komponenten-Registry + Param-Deklarationen (Schlüssel je Einheiten-Suffix,
Pflicht, Bereich in der Einheit des Suffixes, choices, genau eine Alternative),
reservierte Felder (ts, bems, description, LIST_PARAMS wie conduit.pipes),
FLUID_*_PARAMS und SolverSettings samt den Bereichsregeln des Loaders.

Was ein Schema nicht ausdrücken kann, steht in den description-Texten und
prüft weiterhin der Loader: Existenz referenzierter Komponenten/Ports,
Portanzahl (manifold, buffer_storage), typspezifische Konsistenz
(check_params, z.B. Betriebsart ↔ Parameter), nicht endliche Zahlen
(.inf/.nan; JSON kennt sie nicht) und die Strang-Semantik der Luftseite.
"""
from __future__ import annotations

import dataclasses

from . import __version__
from .components.registry import COMPONENT_REGISTRY
from .editor import port_spec
from .fluids import FLUID_CUSTOM_PARAMS, FLUID_PRESET_PARAMS
from .params import UNIT_GROUPS, Param
from .solver.settings import SolverSettings
from .yaml_loader import _SETTINGS_NONNEG, _SETTINGS_POSITIVE, _SETTINGS_UNIT_INTERVAL

DRAFT = "https://json-schema.org/draft/2020-12/schema"
LOADER = "prüft der Loader"

#: Labels (ts, BEMS-Felder): Zeichenkette; Ganzzahlen übernimmt der Loader
_LABEL_NOTE = ("Zeichenkette (bitte quoten); eine Ganzzahl wird übernommen (YAML 1.2 liest "
               "ts: 08 als 8 → '8'), Fließkommazahlen/Wahrheitswerte sind ein Fehler. "
               "Hinweis: JSON Schema hält 1.0 für eine Ganzzahl, der Loader nicht.")


def _clean(x: float) -> float:
    """Grenzwert ohne Gleitkomma-Rauschen der Einheitenumrechnung (0.00036000000000000003)."""
    return float(f"{x:.15g}")


def _factor(p: Param, key: str) -> float:
    if p.group in ("none", "int", "str", "bool"):
        return 1.0
    return UNIT_GROUPS[p.group][key[len(p.name) + 1:]]


def _value_schema(p: Param, key: str) -> dict:
    """Schema EINES Schlüssels (z.B. dp_kPa) in der Einheit seines Suffixes."""
    s: dict = {}
    if p.group == "str":
        s["type"] = "string"
        if p.choices:
            s["enum"] = list(p.choices)
    elif p.group == "bool":
        s["type"] = "boolean"
    else:
        s["type"] = "integer" if p.group == "int" else "number"
        f = _factor(p, key)
        if p.minv is not None:
            s["minimum"] = _clean(p.minv / f)
        if p.maxv is not None:
            s["maximum"] = _clean(p.maxv / f)
    parts = [p.help] if p.help else []
    if p.group not in ("none", "int", "str", "bool"):
        parts.append(f"Einheit: {key[len(p.name) + 1:]}")
    if len(p.accepted_keys()) > 1:
        parts.append(f"Alternativen (genau eine): {', '.join(p.accepted_keys())}")
    if p.default is not None:
        default = p.default
        if isinstance(default, (int, float)) and not isinstance(default, bool):
            default = _clean(default / _factor(p, key))
            if p.group == "int":
                default = int(default)
        s["default"] = default
    if parts:
        s["description"] = " – ".join(parts)
    return s


def _params_object(specs: tuple[Param, ...], extra_props: dict | None = None,
                   required: list[str] | None = None) -> dict:
    """Objektschema aus Param-Deklarationen: alle Suffix-Schlüssel, Pflicht
    (bei Alternativen: genau eine), keine unbekannten Schlüssel."""
    props: dict = dict(extra_props or {})
    req = list(required or [])
    all_of: list = []
    exclusive: dict = {}
    for p in specs:
        keys = p.accepted_keys()
        for k in keys:
            props[k] = _value_schema(p, k)
        if p.required:
            if len(keys) == 1:
                req.append(keys[0])
            else:
                all_of.append({"description": f"Pflichtparameter '{p.name}': einer von {', '.join(keys)}",
                               "anyOf": [{"required": [k]} for k in keys]})
        if len(keys) > 1:
            for k in keys:
                exclusive[k] = {"description": f"'{p.name}' nur einmal angeben (genau eine Einheit)",
                                "not": {"anyOf": [{"required": [o]} for o in keys if o != k]}}
    out: dict = {"type": "object", "properties": props, "additionalProperties": False}
    if req:
        out["required"] = req
    if all_of:
        out["allOf"] = all_of
    if exclusive:
        out["dependentSchemas"] = exclusive
    return out


def _reserved_defs() -> dict:
    """Reservierte Felder jeder Komponente — einmal definiert, je Typ referenziert."""
    label = {"type": ["string", "integer", "null"]}
    entry = {"type": "object", "additionalProperties": False,
             "properties": {"id": {**label, "description": "abfragbare BEMS-/Aedifion-Datenpunkt-ID"},
                            "key": {**label, "description": "sprechender Alias"},
                            "description": {**label, "description": "Semantik des Messpunkts"}}}
    return {
        "ts": {"type": ["string", "integer", "null"],
               "description": "Teilstrecken-Label (Gruppierung im Bericht, keine Recheneinheit). "
                              + _LABEL_NOTE},
        "bems": {"description": "BEMS-Messpunkte [{id, key, description}, …] – rein deklarativ. "
                                "Felder: " + _LABEL_NOTE,
                 "oneOf": [{"type": "null"}, entry, {"type": "array", "items": entry}]},
    }


def _ports_text(type_name: str, cls) -> str:
    spec = port_spec(type_name, cls)
    ports = list(spec["base"])
    if spec["template"]:
        ports.append(spec["template"].format("1") + "…" + spec["template"].format(f"<{spec['count_param']}>"))
    return ", ".join(ports)


def _component_def(type_name: str, cls) -> dict:
    extra = {"type": {"const": type_name}, "ts": {"$ref": "#/$defs/ts"},
             "bems": {"$ref": "#/$defs/bems"}}
    for list_key, seg_specs in getattr(cls, "LIST_PARAMS", {}).items():
        seg = _params_object(seg_specs)
        extra[list_key] = {"description": f"Liste von Abschnitten (je Mapping); ein einzelnes Mapping "
                                          f"gilt als Liste mit einem Eintrag",
                           "oneOf": [{"type": "null"}, seg, {"type": "array", "items": seg}]}
    d = _params_object(cls.PARAMS, extra, required=["type"])
    doc = (cls.__doc__ or type_name).strip().splitlines()[0]
    d["description"] = (f"{doc.rstrip('.')}. Ports: {_ports_text(type_name, cls)}. Typspezifische Konsistenz "
                        f"(z.B. Betriebsart ↔ Parameter) {LOADER}.")
    return d


def _fluid_schema() -> dict:
    preset = _params_object(FLUID_PRESET_PARAMS)
    preset["description"] = "Wasser aus der Stoffwerttabelle: {preset: water, t_C: 50}"
    custom = _params_object(FLUID_CUSTOM_PARAMS)
    custom["description"] = "Konstante Stoffwerte: {rho, mu, cp[, name]} (SI)"
    return {"description": "Fluid: preset: water (+ t_C) ODER eigene Stoffwerte – nicht beides. "
                           "Fehlt der Block, gilt Wasser bei 50 °C.",
            "oneOf": [{"type": "null"}, preset, custom]}


def _settings_schema() -> dict:
    props = {}
    for f in dataclasses.fields(SolverSettings):
        is_int = str(f.type) in ("int", "<class 'int'>")
        s: dict = {"type": "integer" if is_int else "number", "default": f.default}
        if f.name in _SETTINGS_POSITIVE:
            s["exclusiveMinimum"] = 0
        if f.name in _SETTINGS_UNIT_INTERVAL:
            s["exclusiveMinimum"] = 0
            s["maximum"] = 1
        if f.name in _SETTINGS_NONNEG:
            s["minimum"] = 0
        s["description"] = f"Solver-Einstellung (Default {f.default!r})"
        props[f.name] = s
    return {"description": "Optionale Solver-Einstellungen (Defaults in SolverSettings).",
            "oneOf": [{"type": "null"},
                      {"type": "object", "properties": props, "additionalProperties": False}]}


def json_schema(kind: str = "hydraulik") -> dict:
    """JSON Schema des Eingabeformats — 'hydraulik' oder 'air' (Lüftung)."""
    air = kind == "air"
    if air:
        from .air.components import AIR_REGISTRY as registry
    else:
        registry = COMPONENT_REGISTRY
    types = sorted(registry)
    defs: dict = {f"type:{t}": _component_def(t, registry[t]) for t in types}
    defs.update(_reserved_defs())
    defs["component"] = {
        "type": "object", "required": ["type"],
        "description": "Komponente: {type: <typ>, <Parameter mit Einheiten-Suffix…>}",
        "properties": {"type": {"enum": types, "description": "Komponententyp (Registry)"}},
        "allOf": [{"if": {"properties": {"type": {"const": t}}, "required": ["type"]},
                   "then": {"$ref": f"#/$defs/type:{t}"}} for t in types],
    }
    port_ref = {"type": "string", "pattern": r"^[^.]+\.[^.]+$",
                "description": "Port-Referenz 'komponente.port'"}
    conn_items: dict = {"type": "array", "items": port_ref, "minItems": 2}
    if air:
        conn_items["maxItems"] = 2
    props: dict = {
        "components": {
            "type": "object", "minProperties": 1,
            "propertyNames": {"pattern": r"^[^.]+$",
                              "description": "Komponentenname ohne Punkt (Trenner in 'komponente.port')"},
            "additionalProperties": {"$ref": "#/$defs/component"},
            "description": "Komponenten: Name → {type, Parameter}. Namen sind immer Zeichenketten.",
        },
        "connections": {
            "type": "array", "minItems": 1, "items": conn_items,
            "description": ("Verbindungen als Listen von Port-Referenzen. "
                            + ("Luftstränge sind Ketten: genau ZWEI Ports je Verbindung, jeder "
                               "Kanalport genau einmal verbunden – das " if air else
                               "≥ 3 Ports bzw. mehrfach genutzte Ports = Verzweigung. ")
                            + f"Existenz von Komponente und Port {LOADER}."),
        },
        "layout": {
            "type": "object",
            "description": "Zeichnungskoordinaten des Schema-Editors – vom Rechenkern ignoriert.",
            "properties": {"components": {"type": "object"}, "wires": {"type": "array"}},
        },
    }
    if not air:
        props = {"fluid": _fluid_schema(), "settings": _settings_schema(), **props}
    title = "Lüftungsanlage" if air else "Hydraulikschaltung"
    return {
        "$schema": DRAFT,
        "title": f"hydraulik {__version__} – Eingabeformat {title}",
        "description": (
            "Generiert aus der Komponenten-Registry (hydraulik schema"
            + (" --luft" if air else "") + "). YAML 1.2 (Core Schema) oder JSON. Grenzwerte "
            "gelten in der Einheit des jeweiligen Schlüssels (Umrechnung auf Gleitkomma-"
            f"genauigkeit). Nicht endliche Zahlen (.inf/.nan) {LOADER}."),
        "type": "object",
        "properties": props,
        "required": ["components", "connections"],
        "additionalProperties": False,
        "$defs": defs,
    }
