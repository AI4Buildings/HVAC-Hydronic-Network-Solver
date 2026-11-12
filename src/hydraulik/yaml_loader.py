"""YAML/JSON-Eingabe: deklaratives Schaltungsschema → Network.

Schema (alle Einheiten über Suffixe, z.B. dp_kPa, q_m3h, t_C):

    fluid: {preset: water, t_C: 50}          # oder {rho: ..., mu: ..., cp: ...}
    settings: {alpha_p: 0.6, max_iter: 400}  # optional
    components:
      name: {type: <typ>, <parameter...>}
    connections:
      - [komp1.out, komp2.in]                # 2+ Ports; ≥3 = Verzweigung

Fehler werden GESAMMELT gemeldet (nummerierte Liste), damit die Datei in
einem Durchgang korrigiert werden kann – auch von einem LLM. load() prüft
Fluid, Komponenten und Verbindungsform, load_settings() den settings-Block;
load_checked() fasst beides MIT der Einbindungsprüfung (Network.compile:
Port-Referenzen, unverbundene Ports, Topologie) zu EINER Fehlerliste zusammen
(CLI run/export).
"""
from __future__ import annotations

import difflib
from pathlib import Path

from .components.base import Component
from .exceptions import ComponentParamError, NetworkValidationError, SingularNetworkError
from .fluids import FLUID_CUSTOM_PARAMS, FLUID_PRESET_PARAMS, Fluid, WATER_DEFAULT, water_at
from .network import Network, component_from_dict
from .params import as_int, finite_float, is_number, parse_params
from .solver.settings import SolverSettings
from .yamlio import load_document


#: Meldung, wenn das gelesene Dokument kein Mapping ist (auch von der CLI benutzt)
NOT_A_MAPPING = "Eingabe muss ein Mapping mit den Schlüsseln 'components' und 'connections' sein."


def _document(source) -> dict:
    """Eingabedokument lesen; es muss ein Mapping sein. Ein einzeiliger Text,
    der nur ein Wort/Pfad ist (keine Datei dieses Namens, kein YAML-Mapping),
    war fast sicher als Dateiname gemeint → 'nicht gefunden' statt 'kein Mapping'."""
    doc = load_document(source)
    if isinstance(doc, dict):
        return doc
    if isinstance(source, str) and isinstance(doc, str) and "\n" not in source and "\r" not in source:
        raise NetworkValidationError(
            [f"Datei '{source.strip()}' nicht gefunden – und als Text gelesen ist die Eingabe "
             f"kein Schaltungsdokument (Mapping mit 'components' und 'connections')."])
    raise NetworkValidationError([NOT_A_MAPPING])


def _build(doc: dict) -> tuple[Network, list[str], list[str]]:
    """Netz aus dem Dokument aufbauen, soweit möglich. Rückgabe (Netz, Fehler,
    Namen der Komponenten, die sich nicht erzeugen ließen)."""
    errors: list[str] = []
    failed: list[str] = []
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
        if str(name) == "":
            errors.append("Komponentenname darf nicht leer sein (leerer Schlüssel unter "
                          "'components') – bitte einen Namen vergeben.")
        if not isinstance(spec, dict):
            errors.append(f"Komponente '{name}': erwartet ein Mapping mit 'type', erhalten: {spec!r}")
            failed.append(str(name))
            continue
        try:
            net.add(component_from_dict(str(name), spec))
        except (ComponentParamError, NetworkValidationError) as exc:
            failed.append(str(name))
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
    return net, errors, failed


def load(source: str | Path | dict) -> Network:
    """Lädt eine Schaltung aus YAML-/JSON-Datei, YAML-String oder dict
    (YAML 1.2 Core Schema, zentral in yamlio)."""
    net, errors, _ = _build(_document(source))
    if errors:
        raise NetworkValidationError(errors)
    return net


class _Platzhalter(Component):
    """Steht bei der Einbindungsprüfung für eine Komponente, die sich wegen
    Parameterfehlern nicht erzeugen ließ: kennt genau die in den Verbindungen
    genannten Ports (ob es sie gibt, ist ohne die Komponente nicht prüfbar)
    und baut nichts. So prüft Network.compile alle übrigen Verbindungen, ohne
    Folgefehler zur fehlerhaften Komponente zu melden."""

    type_name = "(fehlerhaft)"

    def __init__(self, name: str, ports):
        super().__init__(name)
        self._ports = tuple(ports)

    def port_names(self) -> tuple[str, ...]:
        return self._ports

    def build(self, b) -> None:
        pass


def _compile_errors(doc: dict, net: Network, failed: list[str], settings) -> list[str]:
    """Fehler der Einbindung (Network.compile). Ließen sich Komponenten nicht
    erzeugen, treten Platzhalter an ihre Stelle — geprüft werden dann die
    Port-Referenzen und unverbundenen Ports der übrigen Komponenten; was das
    vollständige Netz braucht (feste Volumenströme je Teilnetz), entfällt."""
    check = net
    if failed:
        ports: dict[str, list[str]] = {name: [] for name in failed}
        for conn in net.connections:
            for ref in conn:
                cname, sep, pname = ref.rpartition(".")     # Port folgt auf den letzten Punkt
                if sep and cname in ports and pname not in ports[cname]:
                    ports[cname].append(pname)
        check = Network(fluid=net.fluid)
        for name in doc["components"]:                      # Dateireihenfolge
            name = str(name)
            if name in net.components:
                check.add(net.components[name])
            elif name not in check.components:
                check.add(_Platzhalter(name, ports.get(name, ())))
        check.connections = list(net.connections)
    try:
        check.compile(settings)
    except NetworkValidationError as exc:
        return exc.messages
    except SingularNetworkError as exc:
        if not failed:
            raise
    return []


def load_checked(source: str | Path | dict) -> tuple[Network, SolverSettings]:
    """Schaltung UND Solver-Einstellungen laden und die Einbindung prüfen —
    alle Fehler in EINER nummerierten Liste (ein Durchgang statt dreier:
    Parameter, settings, Verbindungen). Für CLI und Werkzeuge, die vor dem
    Rechnen bzw. Exportieren die ganze Datei beurteilen.

    Rückgabe (Network, SolverSettings). SingularNetworkError (feste
    Volumenströme bilanzieren nicht) bleibt ein eigener Fehler, wenn sonst
    nichts zu melden ist."""
    doc = _document(source)
    net, errors, failed = _build(doc)
    settings, settings_errors = _parse_settings(doc)
    errors += settings_errors
    singular = None
    if (net.components or failed) and net.connections:
        try:
            errors += [m for m in _compile_errors(doc, net, failed, settings) if m not in errors]
        except SingularNetworkError as exc:
            singular = exc
    if errors:
        raise NetworkValidationError(errors + ([str(singular)] if singular else []))
    if singular:
        raise singular
    assert settings is not None
    return net, settings


#: Solver-Einstellungen, die strikt positiv sein müssen bzw. im Intervall (0, 1] liegen
_SETTINGS_POSITIVE = frozenset({"max_iter", "max_iter_thermal", "tol_mass_rel", "tol_mom_rel",
                                "q_init", "q_eps_frac", "tol_t", "m_dot_eps"})
_SETTINGS_UNIT_INTERVAL = frozenset({"alpha_p", "alpha_q"})
#: Solver-Einstellungen ≥ 0 (0 = Funktion aus)
_SETTINGS_NONNEG = frozenset({"uniqueness_starts"})


def load_settings(source: str | Path | dict) -> SolverSettings:
    """Liest den optionalen settings-Block derselben Datei — typ- und
    bereichsgeprüft, Fehler gesammelt (wie bei Komponentenparametern)."""
    settings, errors = _parse_settings(_document(source))
    if errors:
        raise NetworkValidationError(errors)
    assert settings is not None
    return settings


def _parse_settings(doc: dict) -> tuple[SolverSettings | None, list[str]]:
    """settings-Block → (SolverSettings, []) bzw. (None, Fehler)."""
    raw = doc.get("settings")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        return None, [f"'settings' muss ein Mapping sein (z.B. settings: {{alpha_p: 0.6, "
                      f"max_iter: 400}}), erhalten: {raw!r}"]
    fields = SolverSettings.__dataclass_fields__
    errors: list[str] = []
    values: dict[str, float] = {}
    for key in sorted(raw, key=str):
        val = raw[key]
        if key not in fields:
            hint = difflib.get_close_matches(str(key), list(fields), n=1)
            sug = f" Meinten Sie '{hint[0]}'?" if hint else ""
            errors.append(f"Unbekannte Solver-Einstellung '{key}'.{sug} "
                          f"Gültig: {', '.join(sorted(fields))}")
            continue
        is_int = str(fields[key].type) in ("int", "<class 'int'>")
        # 1e3, 400.0 wie JSON Schema 'integer'; Zahlentypen der Python-API (numpy) → int/float
        num = as_int(val) if is_int else (val if is_number(val) else None)
        if num is None:
            errors.append(f"Solver-Einstellung '{key}' = {val!r} muss eine "
                          f"{'Ganzzahl' if is_int else 'Zahl'} sein.")
            continue
        if finite_float(num) is None:
            errors.append(f"Solver-Einstellung '{key}' = {val!r} ist keine endliche Zahl "
                          f"(inf/nan sind unzulässig).")
            continue
        if not isinstance(num, (int, float)):
            num = float(num)
        if key in _SETTINGS_POSITIVE and num <= 0:
            errors.append(f"Solver-Einstellung '{key}' = {val!r} muss größer als 0 sein.")
            continue
        if key in _SETTINGS_NONNEG and num < 0:
            errors.append(f"Solver-Einstellung '{key}' = {val!r} darf nicht negativ sein "
                          f"(0 = aus).")
            continue
        if key in _SETTINGS_UNIT_INTERVAL and not 0.0 < num <= 1.0:
            errors.append(f"Solver-Einstellung '{key}' = {val!r} muss im Bereich 0 < α ≤ 1 liegen.")
            continue
        values[key] = num
    if errors:
        return None, errors
    return SolverSettings(**values), []


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
