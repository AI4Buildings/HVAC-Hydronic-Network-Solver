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

from .exceptions import ComponentParamError, NetworkValidationError
from .fluids import FLUID_CUSTOM_PARAMS, FLUID_PRESET_PARAMS, Fluid, WATER_DEFAULT, water_at
from .network import Network, component_from_dict
from .params import finite_float, parse_params
from .solver.settings import SolverSettings
from .yamlio import load_document


def load(source: str | Path | dict) -> Network:
    """Lädt eine Schaltung aus YAML-/JSON-Datei, YAML-String oder dict
    (YAML 1.2 Core Schema, zentral in yamlio)."""
    doc = load_document(source)
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
    doc = load_document(source)
    if not isinstance(doc, dict):
        raise NetworkValidationError(
            ["Eingabe muss ein Mapping mit den Schlüsseln 'components' und 'connections' sein."])
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
        if is_int and isinstance(val, float) and val.is_integer():
            val = int(val)                       # 1e3, 400.0 wie JSON Schema 'integer'
        if isinstance(val, bool) or not isinstance(val, (int, float)) or (is_int and not isinstance(val, int)):
            errors.append(f"Solver-Einstellung '{key}' = {val!r} muss eine "
                          f"{'Ganzzahl' if is_int else 'Zahl'} sein.")
            continue
        if finite_float(val) is None:
            errors.append(f"Solver-Einstellung '{key}' = {val!r} ist keine endliche Zahl "
                          f"(inf/nan sind unzulässig).")
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


def _parse_fluid(spec, errors: list[str]) -> Fluid:
    """fluid-Block: preset: water (+ t_C) ODER eigene Stoffwerte rho/mu/cp
    (+ name) — typ-, bereichs- und schlüsselgeprüft wie Komponentenparameter."""
    if spec is None:
        return WATER_DEFAULT
    if not isinstance(spec, dict):
        errors.append(f"'fluid' muss ein Mapping sein, erhalten: {spec!r}")
        return WATER_DEFAULT
    preset = "preset" in spec
    values, errs = parse_params("fluid (preset)" if preset else "fluid (Stoffwerte)",
                                FLUID_PRESET_PARAMS if preset else FLUID_CUSTOM_PARAMS, spec)
    if errs:
        errors += [f"'fluid': {m}" for m in errs]
        if preset and set(spec) & {"rho", "mu", "cp"}:
            errors.append("'fluid': entweder preset: water (+ t_C) ODER eigene Stoffwerte "
                          "rho/mu/cp (+ name) – nicht beides.")
        return WATER_DEFAULT
    if preset:
        return water_at(values["t"])
    return Fluid(name=values["name"], rho=values["rho"], mu=values["mu"], cp=values["cp"])
