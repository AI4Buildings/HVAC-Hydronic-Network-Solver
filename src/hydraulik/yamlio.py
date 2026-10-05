"""Zentrales Einlesen von Eingabedokumenten (YAML 1.2 Core Schema).

EINZIGE Stelle im Paket, die YAML parst — Hydraulik-Loader, Luft-Loader,
Server (/solve, /normalize) und CLI rufen alle `load_document()` bzw.
`parse_yaml()`. Damit gilt überall dieselbe Typauflösung:

- **YAML 1.2.2 Core Schema** (Kap. 10.3.2), nichts darüber hinaus:
  null = ``~ | null | Null | NULL | (leer)``;
  bool = ``true | True | TRUE | false | False | FALSE``;
  int  = ``[-+]?[0-9]+ | 0o[0-7]+ | 0x[0-9a-fA-F]+`` (``08`` = 8, ``010`` = 10);
  float = Dezimal-/Exponentenschreibweise (``1.4e0``, ``5e-7``), ``.inf``, ``.nan``;
  alles andere ist ein String — ``yes/no/on/off``, ``1_000``, ``0b101``,
  ``2026-10-05``, ``12:30`` (YAML-1.1-Sondertypen) werden NICHT umgedeutet.
- **Mapping-Schlüssel sind immer Strings im Originaltext** (``true:`` →
  ``"true"``, ``08:`` → ``"08"``) — wie in JSON und im Editor-Parser
  (yaml_core.js); Namen werden nie zu Zahlen oder Wahrheitswerten.
- Rückgabe nur reine Python-Typen (dict, list, str, int, float, bool, None).
- Doppelte Schlüssel, nicht unterstützte Tags (``!!binary``, ``!!set``,
  ``!!timestamp``, eigene Tags) und YAML-1.1-Merge-Schlüssel (``<<``) werden
  GESAMMELT als NetworkValidationError gemeldet, Syntaxfehler mit Position.
"""
from __future__ import annotations

import re
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.constructor import BaseConstructor
from ruamel.yaml.error import MarkedYAMLError, YAMLError
from ruamel.yaml.nodes import MappingNode, ScalarNode, SequenceNode
from ruamel.yaml.resolver import BaseResolver

from .exceptions import NetworkValidationError

_TAG = "tag:yaml.org,2002:"

#: Core Schema als reguläre Ausdrücke (vollständiger Match) — identisch in
#: yaml_core.js (Editor); der Paritätstest hält beide Seiten deckungsgleich.
CORE_NULL = r"~|null|Null|NULL|"
CORE_BOOL_TRUE = r"true|True|TRUE"
CORE_BOOL_FALSE = r"false|False|FALSE"
CORE_INT = r"[-+]?[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+"
CORE_FLOAT = (r"[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?"
              r"|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN)")

_RE_NULL = re.compile(rf"^(?:{CORE_NULL})$")
_RE_TRUE = re.compile(rf"^(?:{CORE_BOOL_TRUE})$")
_RE_FALSE = re.compile(rf"^(?:{CORE_BOOL_FALSE})$")
_RE_INT = re.compile(rf"^(?:{CORE_INT})$")
_RE_FLOAT = re.compile(rf"^(?:{CORE_FLOAT})$")


class _CoreResolver(BaseResolver):
    """Tag-Auflösung ungequoteter Skalare nach YAML 1.2.2 Core Schema."""

    def __init__(self, version=None, loader=None):
        BaseResolver.__init__(self, loadumper=loader)

    @property
    def processing_version(self):
        return (1, 2)


# Reihenfolge je Anfangszeichen: bool/null vor int vor float ('12' ist int)
_CoreResolver.add_implicit_resolver(
    _TAG + "bool", re.compile(rf"^(?:{CORE_BOOL_TRUE}|{CORE_BOOL_FALSE})$"), list("tTfF"))
_CoreResolver.add_implicit_resolver(
    _TAG + "null", _RE_NULL, ["~", "n", "N", ""])
_CoreResolver.add_implicit_resolver(_TAG + "int", _RE_INT, list("-+0123456789"))
_CoreResolver.add_implicit_resolver(_TAG + "float", _RE_FLOAT, list("-+0123456789."))


def _where(node) -> str:
    m = node.start_mark
    return f"Zeile {m.line + 1}, Spalte {m.column + 1}"


class _CoreConstructor(BaseConstructor):
    """Baut nur Core-Typen; sammelt Inhaltsfehler in self.issues statt beim
    ersten abzubrechen (doppelte Schlüssel, Tags, Merge-Schlüssel)."""

    yaml_constructors: dict = {}
    yaml_multi_constructors: dict = {}

    def __init__(self, preserve_quotes=None, loader=None):
        BaseConstructor.__init__(self, preserve_quotes=preserve_quotes, loader=loader)
        self.issues: list[str] = []

    # --- Skalare ------------------------------------------------------------
    def _bad(self, node, what: str):
        self.issues.append(f"Ungültiger Wert {node.value!r} für {node.tag.replace(_TAG, '!!')} "
                           f"({_where(node)}): {what}")
        return None

    def construct_core_null(self, node):
        self.construct_scalar(node)
        return None if _RE_NULL.match(node.value) else self._bad(node, "erwartet null oder ~.")

    def construct_core_bool(self, node):
        v = self.construct_scalar(node)
        if _RE_TRUE.match(v):
            return True
        if _RE_FALSE.match(v):
            return False
        return self._bad(node, "erwartet true oder false (YAML 1.2).")

    def construct_core_int(self, node):
        v = self.construct_scalar(node)
        if not _RE_INT.match(v):
            return self._bad(node, "keine Ganzzahl nach YAML 1.2 (z.B. 12, 0o17, 0x1F).")
        if v.startswith("0o"):
            return int(v[2:], 8)
        if v.startswith("0x"):
            return int(v[2:], 16)
        return int(v, 10)

    def construct_core_float(self, node):
        v = self.construct_scalar(node)
        if _RE_INT.match(v) and not v.startswith(("0o", "0x")):
            return float(int(v))
        if not _RE_FLOAT.match(v):
            return self._bad(node, "keine Zahl nach YAML 1.2 (z.B. 1.5, 1.4e0, .inf).")
        low = v.lower()
        if low.endswith(".inf"):
            return float("-inf") if low.startswith("-") else float("inf")
        if low == ".nan":
            return float("nan")
        return float(v)

    def construct_core_str(self, node):
        return self.construct_scalar(node)

    # --- Sammlungen ---------------------------------------------------------
    def construct_core_seq(self, node):
        data: list = []
        yield data
        data.extend(self.construct_sequence(node))

    def construct_core_map(self, node):
        data: dict = {}
        yield data
        data.update(self.construct_mapping(node))

    def construct_mapping(self, node, deep=False):
        if not isinstance(node, MappingNode):
            self.issues.append(f"Mapping erwartet ({_where(node)}).")
            return {}
        out: dict = {}
        for key_node, value_node in node.value:
            if not isinstance(key_node, ScalarNode):
                self.issues.append(
                    f"Schlüssel muss ein einfacher Name sein, keine Liste/kein Mapping "
                    f"({_where(key_node)}).")
                continue
            key = key_node.value                        # Originaltext, nie umgedeutet
            if key == "<<" and key_node.style is None:
                self.issues.append(
                    f"Merge-Schlüssel '<<' ({_where(key_node)}) gehört nicht zu YAML 1.2 – "
                    f"bitte die Werte ausschreiben (Anker/Aliase *name sind erlaubt).")
                continue
            if key in out:
                self.issues.append(
                    f"Doppelter Schlüssel '{key}' in der YAML-Datei "
                    f"(Zeile {key_node.start_mark.line + 1}) – z.B. ein mehrfach "
                    f"vergebener Komponentenname. Bitte eindeutig benennen.")
                continue
            out[key] = self.construct_object(value_node, deep=deep)
        return out

    def construct_unsupported(self, node):
        tag = node.tag.replace(_TAG, "!!")
        self.issues.append(
            f"Tag '{tag}' ({_where(node)}) wird nicht unterstützt – erlaubt sind nur "
            f"Zahlen, Zeichenketten, true/false, null, Listen und Mappings.")
        if isinstance(node, ScalarNode):
            return node.value
        if isinstance(node, SequenceNode):
            return []
        return {}


for _name, _fn in (("null", _CoreConstructor.construct_core_null),
                   ("bool", _CoreConstructor.construct_core_bool),
                   ("int", _CoreConstructor.construct_core_int),
                   ("float", _CoreConstructor.construct_core_float),
                   ("str", _CoreConstructor.construct_core_str),
                   ("seq", _CoreConstructor.construct_core_seq),
                   ("map", _CoreConstructor.construct_core_map)):
    _CoreConstructor.add_constructor(_TAG + _name, _fn)
_CoreConstructor.add_constructor(None, _CoreConstructor.construct_unsupported)


def _new_yaml() -> YAML:
    # pure=True: reiner Python-Parser, identisches Verhalten mit/ohne C-Erweiterung.
    # Je Aufruf eine neue Instanz (Server ist mehrfädig).
    y = YAML(typ="safe", pure=True)
    y.Resolver = _CoreResolver
    y.Constructor = _CoreConstructor
    return y


def parse_yaml(text: str):
    """YAML-Text → reine Python-Daten nach YAML 1.2 Core Schema.

    Fehler (Syntax mit Position, doppelte Schlüssel, nicht unterstützte Tags)
    als NetworkValidationError mit gesammelten deutschen Meldungen."""
    y = _new_yaml()
    try:
        doc = y.load(text)
    except MarkedYAMLError as exc:
        mark, ctx = exc.problem_mark or exc.context_mark, exc.context_mark
        where = f" (Zeile {mark.line + 1}, Spalte {mark.column + 1})" if mark else ""
        problem = exc.problem or exc.context or str(exc)
        if "expected a single document" in str(exc):
            problem = "mehrere Dokumente ('---') – bitte genau ein Dokument je Datei"
        elif exc.context and ctx is not None and ctx is not mark:
            # z.B. offene Klammer: der Parser merkt es erst später, der
            # Kontext zeigt, wo die Struktur begann
            problem += f" ({exc.context}, begonnen in Zeile {ctx.line + 1})"
        raise NetworkValidationError([f"YAML-Syntaxfehler{where}: {problem}"]) from None
    except YAMLError as exc:
        raise NetworkValidationError([f"YAML-Syntaxfehler: {exc}"]) from None
    issues = y.constructor.issues
    if issues:
        raise NetworkValidationError(list(issues))
    return doc


def is_path(source) -> bool:
    """Pfad oder Text? (Text enthält Zeilenumbrüche oder endet nicht auf eine
    Dateiendung einer existierenden Datei.)"""
    if isinstance(source, Path):
        return True
    s = str(source)
    return "\n" not in s and (s.endswith((".yaml", ".yml", ".json")) or Path(s).exists())


def load_document(source):
    """Eingabedokument aus dict, Datei (Path/Pfad-String) oder YAML-Text.

    Ein dict wird unverändert durchgereicht (Python-API). Dateien werden als
    UTF-8 gelesen; fehlende Dateien → NetworkValidationError."""
    if isinstance(source, dict):
        return source
    if is_path(source):
        path = Path(source)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise NetworkValidationError([f"Datei '{path}' nicht gefunden."]) from None
        except IsADirectoryError:
            raise NetworkValidationError([f"'{path}' ist ein Verzeichnis, keine Datei."]) from None
        return parse_yaml(text)
    return parse_yaml(str(source))
