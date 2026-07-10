"""YAML/JSON-Eingabe: deklaratives Schaltungsschema → Network.

Schema (alle Einheiten über Suffixe, z.B. dp_kPa, q_m3h, t_C):

    fluid: {preset: water, t_C: 50}          # oder {rho: ..., mu: ..., cp: ...}
    settings: {alpha_p: 0.6, max_iter: 400}  # optional
    components:
      name: {type: <typ>, <parameter...>}
    connections:
      - [komp1.out, komp2.in]                # 2+ Ports; ≥3 = Verzweigung

Fehler werden GESAMMELT gemeldet (nummerierte Liste), damit die Datei in
einem Durchgang korrigiert werden kann – auch von einem LLM.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from .exceptions import ComponentParamError, NetworkValidationError
from .fluids import Fluid, WATER_DEFAULT, water_at
from .network import Network, component_from_dict
from .solver.settings import SolverSettings


def load(source: str | Path | dict) -> Network:
    """Lädt eine Schaltung aus YAML-/JSON-Datei, YAML-String oder dict."""
    if isinstance(source, dict):
        doc = source
    else:
        text = Path(source).read_text(encoding="utf-8") if _is_path(source) else str(source)
        doc = yaml.safe_load(text)
    if not isinstance(doc, dict):
        raise NetworkValidationError(
            ["Eingabe muss ein Mapping mit den Schlüsseln 'components' und 'connections' sein."])

    errors: list[str] = []
    # 'layout' wird vom Schaltbild-Editor geschrieben (Zeichnungskoordinaten)
    # und hier bewusst ignoriert.
    known_keys = {"fluid", "settings", "components", "connections", "layout"}
    for key in doc:
        if key not in known_keys:
            errors.append(f"Unbekannter Schlüssel '{key}' auf oberster Ebene. "
                          f"Erlaubt: {', '.join(sorted(known_keys))}")

    fluid = _parse_fluid(doc.get("fluid"), errors)
    net = Network(fluid=fluid)

    comps = doc.get("components")
    if not isinstance(comps, dict) or not comps:
        errors.append("'components' fehlt oder ist leer (erwartet: Mapping name → {type, parameter}).")
        comps = {}
    for name, spec in comps.items():
        if not isinstance(spec, dict):
            errors.append(f"Komponente '{name}': erwartet ein Mapping mit 'type', erhalten: {spec!r}")
            continue
        try:
            net.add(component_from_dict(str(name), spec))
        except (ComponentParamError, NetworkValidationError) as exc:
            if isinstance(exc, ComponentParamError):
                errors += [f"Komponente '{name}': {m}" for m in exc.messages]
            else:
                errors += exc.messages

    conns = doc.get("connections")
    if not isinstance(conns, list) or not conns:
        errors.append("'connections' fehlt oder ist leer (erwartet: Liste von Port-Listen).")
        conns = []
    for k, conn in enumerate(conns):
        if not isinstance(conn, (list, tuple)) or len(conn) < 2:
            errors.append(f"Verbindung Nr. {k+1} muss eine Liste mit mindestens 2 Ports sein, "
                          f"erhalten: {conn!r}")
            continue
        net.connections.append(tuple(str(p) for p in conn))

    if errors:
        raise NetworkValidationError(errors)
    return net


def load_settings(source: str | Path | dict) -> SolverSettings:
    """Liest den optionalen settings-Block derselben Datei."""
    if isinstance(source, dict):
        doc = source
    else:
        text = Path(source).read_text(encoding="utf-8") if _is_path(source) else str(source)
        doc = yaml.safe_load(text) or {}
    raw = doc.get("settings") or {}
    valid = {f.name for f in SolverSettings.__dataclass_fields__.values()}
    unknown = set(raw) - valid
    if unknown:
        raise NetworkValidationError(
            [f"Unbekannte Solver-Einstellung '{k}'. Gültig: {', '.join(sorted(valid))}"
             for k in sorted(unknown)])
    return SolverSettings(**raw)


def _is_path(source) -> bool:
    if isinstance(source, Path):
        return True
    s = str(source)
    return "\n" not in s and (s.endswith((".yaml", ".yml", ".json")) or Path(s).exists())


def _parse_fluid(spec, errors: list[str]) -> Fluid:
    if spec is None:
        return WATER_DEFAULT
    if not isinstance(spec, dict):
        errors.append(f"'fluid' muss ein Mapping sein, erhalten: {spec!r}")
        return WATER_DEFAULT
    if "preset" in spec:
        if spec["preset"] != "water":
            errors.append(f"Unbekanntes Fluid-Preset '{spec['preset']}'. Verfügbar: water")
            return WATER_DEFAULT
        return water_at(float(spec.get("t_C", 50.0)))
    try:
        return Fluid(name=str(spec.get("name", "custom")), rho=float(spec["rho"]),
                     mu=float(spec["mu"]), cp=float(spec["cp"]))
    except KeyError as exc:
        errors.append(f"'fluid': Schlüssel {exc} fehlt (erwartet rho, mu, cp oder preset: water).")
        return WATER_DEFAULT
