"""Parameterdefinition mit Einheiten-Suffixen.

Eine `Param`-Deklaration je Komponententyp ist die Single Source of Truth:
sie treibt sowohl die Python-API (Konstruktor-Kwargs) als auch die
YAML-Validierung. Alle Werte werden intern in strikten SI-Einheiten
gehalten (Pa, m³/s, kg/s, W, m, °C).
"""
from __future__ import annotations

from dataclasses import dataclass

# Suffix -> Faktor zur SI-Einheit. Erster Eintrag = bevorzugte Anzeige.
UNIT_GROUPS: dict[str, dict[str, float]] = {
    "pressure":    {"Pa": 1.0, "kPa": 1e3, "bar": 1e5, "mbar": 1e2},
    "flow":        {"m3s": 1.0, "m3h": 1.0 / 3600.0, "l_s": 1e-3, "l_min": 1e-3 / 60.0},
    "massflow":    {"kg_s": 1.0, "kg_h": 1.0 / 3600.0},
    "temperature": {"C": 1.0},                       # intern °C
    "power":       {"W": 1.0, "kW": 1e3},
    "length":      {"m": 1.0, "mm": 1e-3, "cm": 1e-2},
    "area":        {"m2": 1.0},
    "kv":          {"m3h": 1.0},                     # Kv-Wert per Definition in m³/h
    "u_area":      {"W_m2K": 1.0},
    "u_linear":    {"W_mK": 1.0},
    "ua":          {"W_K": 1.0},
    # Strömungswiderstände (dp = a·Q + C·Q·|Q|): Handrechnungen (RegulaA/REGuA)
    # geben C üblicherweise in Pa/(m³/h)² an; SI ist Pa/(m³/s)².
    "quad_resistance": {"Pa_m3h2": 3600.0 ** 2, "Pa_m3s2": 1.0},
    "lin_resistance":  {"Pa_m3h": 3600.0, "Pa_m3s": 1.0},
}


@dataclass(frozen=True)
class Param:
    """Deklaration eines Komponentenparameters."""

    name: str                       # SI-Attributname, z.B. "dp"
    group: str = "none"             # Schlüssel in UNIT_GROUPS oder "none"/"str"/"int"/"bool"
    required: bool = False
    default: object = None          # SI-Wert (bzw. Rohwert bei none/str/int/bool)
    minv: float | None = None       # Bereichsgrenzen in SI
    maxv: float | None = None
    choices: tuple[str, ...] | None = None
    help: str = ""

    def accepted_keys(self) -> list[str]:
        if self.group in ("none", "str", "int", "bool"):
            return [self.name]
        return [f"{self.name}_{sfx}" for sfx in UNIT_GROUPS[self.group]]

    def display_key(self) -> str:
        return self.accepted_keys()[0]


def parse_params(type_name: str, specs: tuple[Param, ...], kwargs: dict) -> tuple[dict, list[str]]:
    """Wertet kwargs gegen die Param-Deklarationen aus.

    Returns (si_values, error_messages). Sammelt alle Fehler statt beim
    ersten abzubrechen.
    """
    errors: list[str] = []
    values: dict[str, object] = {}
    consumed: set[str] = set()

    for spec in specs:
        keys = spec.accepted_keys()
        given = [k for k in keys if k in kwargs]
        if len(given) > 1:
            errors.append(
                f"Parameter '{spec.name}' mehrfach angegeben ({', '.join(given)}) – bitte genau einen verwenden."
            )
            consumed.update(given)
            continue
        if not given:
            if spec.required:
                errors.append(
                    f"Pflichtparameter fehlt: '{spec.display_key()}'"
                    + (f" (alternativ: {', '.join(keys[1:])})" if len(keys) > 1 else "")
                    + (f" – {spec.help}" if spec.help else "")
                )
            else:
                values[spec.name] = spec.default
            continue

        key = given[0]
        consumed.add(key)
        raw = kwargs[key]

        if spec.group == "str":
            if not isinstance(raw, str):
                errors.append(f"'{key}' muss eine Zeichenkette sein, erhalten: {raw!r}")
                continue
            if spec.choices and raw not in spec.choices:
                errors.append(f"'{key}' = {raw!r} ungültig, erlaubt: {', '.join(spec.choices)}")
                continue
            values[spec.name] = raw
            continue
        if spec.group == "bool":
            if not isinstance(raw, bool):
                errors.append(f"'{key}' muss true/false sein, erhalten: {raw!r}")
                continue
            values[spec.name] = raw
            continue
        if spec.group == "int":
            if isinstance(raw, bool) or not isinstance(raw, int):
                errors.append(f"'{key}' muss eine Ganzzahl sein, erhalten: {raw!r}")
                continue
            val = raw
        else:
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                errors.append(f"'{key}' muss eine Zahl sein, erhalten: {raw!r}")
                continue
            factor = 1.0 if spec.group == "none" else UNIT_GROUPS[spec.group][key[len(spec.name) + 1:]]
            val = float(raw) * factor

        if spec.minv is not None and val < spec.minv:
            errors.append(f"'{key}' = {raw} liegt unter dem Minimum ({_fmt_si(spec.minv, spec)}).")
            continue
        if spec.maxv is not None and val > spec.maxv:
            errors.append(f"'{key}' = {raw} liegt über dem Maximum ({_fmt_si(spec.maxv, spec)}).")
            continue
        values[spec.name] = val

    unknown = set(kwargs) - consumed
    if unknown:
        valid = sorted(k for s in specs for k in s.accepted_keys())
        import difflib
        for key in sorted(unknown):
            hint = difflib.get_close_matches(key, valid, n=1)
            suggestion = f" Meinten Sie '{hint[0]}'?" if hint else ""
            errors.append(
                f"Unbekannter Parameter '{key}' für Typ '{type_name}'.{suggestion} "
                f"Gültige Parameter: {', '.join(valid)}"
            )
    return values, errors


def _fmt_si(v: float, spec: Param) -> str:
    unit = "" if spec.group in ("none", "int") else f" [{spec.display_key().split('_', 1)[-1] if '_' in spec.display_key() else ''}]"
    return f"{v:g}{unit}"


def params_doc(specs: tuple[Param, ...]) -> str:
    """Menschen-/LLM-lesbare Parameterliste eines Typs (für Fehlermeldungen und Doku)."""
    lines = []
    for s in specs:
        req = "PFLICHT" if s.required else f"optional, Default {s.default!r}"
        lines.append(f"  {s.display_key()}  ({req})" + (f" – {s.help}" if s.help else ""))
    return "\n".join(lines)

