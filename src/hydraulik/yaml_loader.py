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

import difflib
from pathlib import Path

import yaml

from .exceptions import ComponentParamError, NetworkValidationError
from .fluids import Fluid, WATER_DEFAULT, water_at
from .network import Network, component_from_dict
from .solver.settings import SolverSettings


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader, der doppelte Mapping-Schlüssel meldet statt sie
    stillschweigend zu überschreiben (YAML-Standardverhalten wäre
    'last wins' – ein mehrfach vergebener Komponentenname würde sonst
    unbemerkt eine Komponente verschlucken)."""


def _construct_mapping_unique(loader, node, deep=False):
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise NetworkValidationError(
                [f"Doppelter Schlüssel '{key}' in der YAML-Datei "
                 f"(Zeile {key_node.start_mark.line + 1}) – z.B. ein mehrfach "
                 f"vergebener Komponentenname. Bitte eindeutig benennen."])
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping_unique)


def load(source: str | Path | dict) -> Network:
    """Lädt eine Schaltung aus YAML-/JSON-Datei, YAML-String oder dict."""
    if isinstance(source, dict):
        doc = source
    else:
        text = Path(source).read_text(encoding="utf-8") if _is_path(source) else str(source)
        doc = yaml.load(text, Loader=_UniqueKeyLoader)
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


#: Solver-Einstellungen, die strikt positiv sein müssen bzw. im Intervall (0, 1] liegen
_SETTINGS_POSITIVE = frozenset({"max_iter", "max_iter_thermal", "tol_mass_rel", "tol_mom_rel",
                                "q_init", "q_eps_frac", "tol_t", "m_dot_eps"})
_SETTINGS_UNIT_INTERVAL = frozenset({"alpha_p", "alpha_q"})


def load_settings(source: str | Path | dict) -> SolverSettings:
    """Liest den optionalen settings-Block derselben Datei — typ- und
    bereichsgeprüft, Fehler gesammelt (wie bei Komponentenparametern)."""
    if isinstance(source, dict):
        doc = source
    else:
        text = Path(source).read_text(encoding="utf-8") if _is_path(source) else str(source)
        doc = yaml.load(text, Loader=_UniqueKeyLoader) or {}
    raw = doc.get("settings")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise NetworkValidationError(
            [f"'settings' muss ein Mapping sein (z.B. settings: {{alpha_p: 0.6, max_iter: 400}}), "
             f"erhalten: {raw!r}"])
    fields = SolverSettings.__dataclass_fields__
    errors: list[str] = []
    values: dict[str, float] = {}
    for key in sorted(raw):
        val = raw[key]
        if key not in fields:
            hint = difflib.get_close_matches(key, list(fields), n=1)
            sug = f" Meinten Sie '{hint[0]}'?" if hint else ""
            errors.append(f"Unbekannte Solver-Einstellung '{key}'.{sug} "
                          f"Gültig: {', '.join(sorted(fields))}")
            continue
        is_int = str(fields[key].type) in ("int", "<class 'int'>")
        if isinstance(val, bool) or not isinstance(val, (int, float)) or (is_int and not isinstance(val, int)):
            errors.append(f"Solver-Einstellung '{key}' = {val!r} muss eine "
                          f"{'Ganzzahl' if is_int else 'Zahl'} sein.")
            continue
        if key in _SETTINGS_POSITIVE and val <= 0:
            errors.append(f"Solver-Einstellung '{key}' = {val!r} muss größer als 0 sein.")
            continue
        if key in _SETTINGS_UNIT_INTERVAL and not 0.0 < val <= 1.0:
            errors.append(f"Solver-Einstellung '{key}' = {val!r} muss im Bereich 0 < α ≤ 1 liegen.")
            continue
        values[key] = val
    if errors:
        raise NetworkValidationError(errors)
    return SolverSettings(**values)


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
