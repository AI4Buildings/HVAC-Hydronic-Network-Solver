"""Zentrales Einlesen von Eingabedokumenten (YAML 1.2 Core Schema, JSON).

EINZIGE Stelle im Paket, die YAML/JSON parst — Hydraulik-Loader, Luft-Loader,
Server (/solve, /normalize) und CLI rufen alle `load_document()` bzw.
`parse_yaml()`. Dateien mit Endung .json werden strikt als JSON gelesen
(`parse_json`); `canonical_json()` schreibt das Eingabemodell als JSON
(hydraulik export --json). Für YAML gilt überall dieselbe Typauflösung:

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
- **Zeichenvertrag** (identisch in yaml_core.js): Leerraum sind nur Leerzeichen
  und Tabulator, Zeilenumbrüche LF, CRLF und CR. Rohe Steuerzeichen (C0 außer
  TAB/LF/CR, DEL, C1 inkl. NEL U+0085) sowie U+2028/U+2029 sind überall im
  Text ein Fehler — als Escape in doppelten Anführungszeichen (``"\\u0085"``)
  bleiben sie schreibbar. NBSP und ein U+FEFF mitten im Text sind gewöhnliche
  Zeichen. Eine Direktive ``%YAML`` mit anderer Version als 1.2 wird abgelehnt.
- **Größe**: ein Dokument mit mehr als ``MAX_NODES`` Werten (nach Auflösen der
  Aliase) oder mit einem rekursiven Alias ist ein Fehler — Aliase können ein
  Dokument sonst exponentiell aufblähen.
- Escape-Surrogatpaare (``"\\ud83d\\ude00"``, wie sie JSON schreibt) ergeben
  wie in JSON EIN Zeichen; ein einzelnes Surrogat ist ein Fehler.
"""
from __future__ import annotations

import json
import math
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

#: Knotenbudget: so viele Werte (Skalare, Listen, Mappings — Aliase aufgelöst
#: gezählt) darf ein Eingabedokument höchstens haben. Reale Schaltungen liegen
#: um Größenordnungen darunter; die Grenze fängt Alias-Kaskaden ab, die aus
#: 1 kB Text Gigabytes machen (YAML-„Billion Laughs“).
MAX_NODES = 200_000

#: Zeichen, die roh nirgends im YAML-Text stehen dürfen (Zeichenvertrag mit
#: yaml_core.js): C0 außer TAB/LF/CR, DEL, C1 (inkl. NEL) und U+2028/U+2029.
_RE_FORBIDDEN = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u2028\u2029]")
_RE_BREAK = re.compile(r"\r\n|\r|\n")
_RE_SURROGATE = re.compile("[\ud800-\udfff]")
_CHAR_NAMES = {0x00: "NUL", 0x0B: "Vertikal-Tabulator", 0x0C: "Seitenvorschub", 0x1B: "ESC",
               0x7F: "DEL", 0x85: "NEL, „Next Line“", 0x2028: "Zeilentrenner",
               0x2029: "Absatztrenner"}
_TOO_MANY_DIGITS = ("Ganzzahl mit zu vielen Stellen (Grenze der Zahlumwandlung, rund 4300 "
                    "Ziffern) – so große Zahlen sind unzulässig.")
#: höchstens so viele gleichartige Einzelmeldungen (Steuerzeichen, Surrogate, inf/nan)
_MAX_ISSUES = 20


def _line_col(text: str, pos: int) -> tuple[int, int]:
    """Zeile/Spalte (ab 1) einer Textposition; Zeilenumbrüche LF, CRLF, CR."""
    line, start = 1, 0
    for m in _RE_BREAK.finditer(text, 0, pos):
        line, start = line + 1, m.end()
    return line, pos - start + 1


def _forbidden_char_issues(text: str) -> list[str]:
    """Rohe Steuerzeichen im Text (siehe _RE_FORBIDDEN) — ruamel läse NEL,
    U+2028 und U+2029 nach YAML 1.1 als Zeilenumbruch, der Editor-Parser nicht."""
    issues: list[str] = []
    hits = list(_RE_FORBIDDEN.finditer(text))
    for m in hits[:_MAX_ISSUES]:
        cp = ord(m.group())
        line, col = _line_col(text, m.start())
        name = f" ({_CHAR_NAMES[cp]})" if cp in _CHAR_NAMES else ""
        issues.append(
            f"Steuerzeichen U+{cp:04X}{name} in Zeile {line}, Spalte {col} ist im YAML-Text "
            f"nicht erlaubt – bitte entfernen oder als Escape in einer Zeichenkette in "
            f"doppelten Anführungszeichen schreiben (\"\\u{cp:04x}\").")
    if len(hits) > _MAX_ISSUES:
        issues.append(f"… und {len(hits) - _MAX_ISSUES} weitere unzulässige Steuerzeichen.")
    return issues


def _directive_issues(text: str) -> list[str]:
    """%YAML-Direktive mit anderer Version als 1.2 (Direktiven stehen nur im
    Dokumentvorspann: vor der ersten Zeile, die weder leer noch Kommentar ist)."""
    for no, line in enumerate(_RE_BREAK.split(text.lstrip("\ufeff")), start=1):
        if not line.strip(" \t") or line.lstrip(" \t").startswith("#"):
            continue
        if not line.startswith("%"):
            break
        m = re.match(r"%YAML[ \t]+(\S+)", line)
        if m and m.group(1) != "1.2":
            return [f"Direktive '%YAML {m.group(1)}' (Zeile {no}): gelesen wird ausschließlich "
                    f"YAML 1.2 – nach YAML 1.1 hätten yes/no/on/off, 010 oder 1:30 eine andere "
                    f"Bedeutung. Bitte die Direktive entfernen (oder '%YAML 1.2' schreiben) und "
                    f"solche Werte prüfen bzw. in Anführungszeichen setzen."]
    return []


def _join_surrogates(value: str) -> tuple[str, bool]:
    """Escape-Surrogatpaare zu einem Zeichen verbinden (wie JSON). Rückgabe
    (Text, ok); ok = False, wenn ein einzelnes Surrogat übrig bleibt."""
    if not _RE_SURROGATE.search(value):
        return value, True
    try:
        return value.encode("utf-16-le", "surrogatepass").decode("utf-16-le"), True
    except UnicodeDecodeError:
        return value, False


def _path_text(path: tuple) -> str:
    out = ""
    for part in path:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out or "(oberste Ebene)"


def check_document(doc, *, finite: bool = False, what: str = "Dokument") -> None:
    """Prüft ein gelesenes bzw. per Python-API übergebenes Dokument, bevor es
    weiterverarbeitet oder als JSON geschrieben wird:

    - höchstens MAX_NODES Werte (Aliase aufgelöst gezählt) und kein rekursiver
      Bezug — sonst sofort NetworkValidationError (das Dokument wird dafür
      nur bis zum Budget durchlaufen, nie vollständig aufgefaltet);
    - keine einzelnen Surrogate in Zeichenketten (nicht als UTF-8 schreibbar);
    - mit finite=True zusätzlich nur endliche Zahlen (JSON kennt kein inf/nan).
    """
    issues: list[str] = []
    skipped = 0
    count = 0
    on_path: set[int] = set()
    stack: list = [(doc, (), False)]

    def note(msg: str) -> None:
        nonlocal skipped
        if len(issues) < _MAX_ISSUES:
            issues.append(msg)
        else:
            skipped += 1

    def scalar(value, path: tuple) -> None:
        if isinstance(value, str):
            if _RE_SURROGATE.search(value):
                note(f"Zeichenkette unter '{_path_text(path)}' enthält ein einzelnes Surrogat "
                     f"({value!r:.80}) – ein unvollständiges Zeichen; Zeichen außerhalb der "
                     f"BMP als Paar (\"\\ud83d\\ude00\") oder direkt schreiben.")
        elif finite and isinstance(value, float) and not math.isfinite(value):
            note(f"Nicht endliche Zahl ({value}) unter '{_path_text(path)}' – erlaubt sind nur "
                 f"endliche Zahlen (.inf/.nan bzw. ein Überlauf wie 1e999 sind unzulässig; "
                 f"JSON kennt sie nicht).")

    while stack:
        node, path, leaving = stack.pop()
        if leaving:
            on_path.discard(id(node))
            continue
        count += 1
        if isinstance(node, (dict, list, tuple)):
            if id(node) in on_path:
                raise NetworkValidationError(
                    [f"{what}: rekursiver Bezug unter '{_path_text(path)}' – ein Anker (&name) "
                     f"wird über seinen Alias (*name) in sich selbst eingesetzt. Bitte die "
                     f"Struktur ausschreiben."])
            on_path.add(id(node))
            stack.append((node, path, True))
            items = node.items() if isinstance(node, dict) else enumerate(node)
            for key, child in items:
                if isinstance(node, dict) and isinstance(key, str) and _RE_SURROGATE.search(key):
                    scalar(key, path)
                if isinstance(child, (dict, list, tuple)):
                    stack.append((child, path + (key,), False))
                else:
                    count += 1
                    scalar(child, path + (key,))
                if count > MAX_NODES:
                    break
        else:
            scalar(node, path)
        if count > MAX_NODES:
            raise NetworkValidationError(
                [f"{what} zu groß: mehr als {MAX_NODES} Werte (Aliase *name aufgelöst "
                 f"gezählt). Aliase auf Listen/Mappings, die selbst wieder Aliase enthalten, "
                 f"vervielfachen den Inhalt – bitte ausschreiben bzw. das Dokument verkleinern."])
    if skipped:
        issues.append(f"… und {skipped} weitere gleichartige Meldungen.")
    if issues:
        raise NetworkValidationError(issues)


class _CoreResolver(BaseResolver):
    """Tag-Auflösung ungequoteter Skalare nach YAML 1.2.2 Core Schema."""

    #: eigene Tabelle Anfangszeichen → [(Tag, Regex)] — siehe _add_resolver
    yaml_implicit_resolvers: dict = {}

    def __init__(self, version=None, loader=None):
        BaseResolver.__init__(self, loadumper=loader)

    @property
    def processing_version(self):
        return (1, 2)


def _add_resolver(tag: str, regexp, first: list[str]) -> None:
    """Regel NUR am eigenen Resolver eintragen. BaseResolver.add_implicit_resolver
    hängt jede Regel zusätzlich an ruamels modulweite Liste implicit_resolvers —
    damit läse jeder andere ruamel-Nutzer im selben Prozess (YAML() ohne eigenen
    Resolver) nach dem Import von hydraulik anders (.5e3 als Zahl, in
    YAML-1.1-Dokumenten scheiterte 08 mit ValueError)."""
    for ch in first:
        _CoreResolver.yaml_implicit_resolvers.setdefault(ch, []).append((tag, regexp))


# Reihenfolge je Anfangszeichen: bool/null vor int vor float ('12' ist int)
_add_resolver(_TAG + "bool", re.compile(rf"^(?:{CORE_BOOL_TRUE}|{CORE_BOOL_FALSE})$"), list("tTfF"))
_add_resolver(_TAG + "null", _RE_NULL, ["~", "n", "N", ""])
_add_resolver(_TAG + "int", _RE_INT, list("-+0123456789"))
_add_resolver(_TAG + "float", _RE_FLOAT, list("-+0123456789."))


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
    def _bad(self, node, what: str, show: str | None = None):
        shown = repr(node.value) if show is None else show
        self.issues.append(f"Ungültiger Wert {shown} für {node.tag.replace(_TAG, '!!')} "
                           f"({_where(node)}): {what}")
        return None

    def _text(self, node) -> str:
        """Zeichenkette bzw. Schlüssel: Escape-Surrogatpaare wie in JSON zu
        einem Zeichen verbinden; ein einzelnes Surrogat melden."""
        value, ok = _join_surrogates(node.value)
        if not ok:
            self.issues.append(
                f"Zeichenkette mit einzelnem Surrogat ({_where(node)}) – ein unvollständiges "
                f"Zeichen; Zeichen außerhalb der BMP als Paar (\"\\ud83d\\ude00\") oder "
                f"direkt schreiben.")
            value = _RE_SURROGATE.sub("\ufffd", value)
        return value

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
        try:
            if v.startswith("0o"):
                return int(v[2:], 8)
            if v.startswith("0x"):
                return int(v[2:], 16)
            return int(v, 10)
        except ValueError:                      # Stellenlimit der int-Umwandlung
            return self._bad(node, _TOO_MANY_DIGITS, show=f"{v[:12]}…({len(v)} Zeichen)")

    def construct_core_float(self, node):
        v = self.construct_scalar(node)
        if _RE_INT.match(v) and not v.startswith(("0o", "0x")):
            return float(v)                     # float() kennt kein Stellenlimit
        if not _RE_FLOAT.match(v):
            return self._bad(node, "keine Zahl nach YAML 1.2 (z.B. 1.5, 1.4e0, .inf).")
        low = v.lower()
        if low.endswith(".inf"):
            return float("-inf") if low.startswith("-") else float("inf")
        if low == ".nan":
            return float("nan")
        return float(v)

    def construct_core_str(self, node):
        self.construct_scalar(node)
        return self._text(node)

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
            key = self._text(key_node)                  # Originaltext, nie umgedeutet
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

    Fehler (Syntax mit Position, doppelte Schlüssel, nicht unterstützte Tags,
    rohe Steuerzeichen, %YAML ≠ 1.2, Knotenbudget MAX_NODES, rekursive Aliase)
    als NetworkValidationError mit gesammelten deutschen Meldungen. Das
    Ergebnis ist endlich groß und zyklenfrei; inf/nan (.inf/.nan) sind als
    Werte möglich und werden erst vom Loader bzw. canonical_json abgelehnt."""
    pre = _forbidden_char_issues(text) + _directive_issues(text)
    if pre:
        raise NetworkValidationError(pre)
    y = _new_yaml()
    try:
        doc = y.load(text)
    except RecursionError:
        raise NetworkValidationError(
            ["YAML zu tief verschachtelt (Listen/Mappings in zu vielen Ebenen) – "
             "bitte flacher strukturieren."]) from None
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
    check_document(doc, what="YAML-Dokument")
    return doc


class _NonStandardConstant(ValueError):
    pass


class _TooManyDigits(ValueError):
    pass


def _reject_constant(name: str):
    raise _NonStandardConstant(name)


def _json_int(text: str) -> int:
    try:
        return int(text)
    except ValueError:                          # Stellenlimit der int-Umwandlung
        raise _TooManyDigits(f"{text[:12]}…({len(text)} Zeichen)") from None


def parse_json(text: str):
    """Strikter JSON-Text → reine Python-Daten. Doppelte Schlüssel (mit
    Zeilen der Vorkommen) und NaN/Infinity sind Fehler, Syntaxfehler mit Position."""
    dups: list[str] = []

    def pairs_hook(pairs):
        out: dict = {}
        for key, value in pairs:
            if key in out:
                dups.append(key)
            out[key] = value
        return out

    text = text.removeprefix("\ufeff")          # UTF-8-BOM (RFC 8259: darf ignoriert werden)
    try:
        doc = json.loads(text, object_pairs_hook=pairs_hook, parse_constant=_reject_constant,
                         parse_int=_json_int)
    except _NonStandardConstant as exc:
        raise NetworkValidationError(
            [f"JSON: '{exc}' ist kein gültiger JSON-Wert (NaN/Infinity sind nicht "
             f"standardkonform; erlaubt sind nur endliche Zahlen)."]) from None
    except _TooManyDigits as exc:
        raise NetworkValidationError([f"JSON: Ungültiger Wert {exc}: {_TOO_MANY_DIGITS}"]) from None
    except RecursionError:
        raise NetworkValidationError(
            ["JSON zu tief verschachtelt (Listen/Objekte in zu vielen Ebenen) – "
             "bitte flacher strukturieren."]) from None
    except json.JSONDecodeError as exc:
        hint = ""
        if exc.pos == 0 and text.strip():
            hint = (" – die Datei ist kein JSON (JSON kennt keine Kommentare); "
                    "YAML-Inhalt bitte mit Endung .yaml/.yml speichern")
        raise NetworkValidationError(
            [f"JSON-Syntaxfehler (Zeile {exc.lineno}, Spalte {exc.colno}): {exc.msg}{hint}"]) from None
    messages = []
    for key in dict.fromkeys(dups):
        quoted = re.escape(json.dumps(key, ensure_ascii=False))
        lines = [text.count("\n", 0, m.start()) + 1 for m in re.finditer(quoted + r"\s*:", text)]
        where = f" (Vorkommen in Zeile {', '.join(map(str, lines))})" if lines else ""
        messages.append(f"Doppelter Schlüssel '{key}' in der JSON-Datei{where} – z.B. ein "
                        f"mehrfach vergebener Komponentenname. Bitte eindeutig benennen.")
    if messages:
        raise NetworkValidationError(messages)
    check_document(doc, what="JSON-Dokument")
    return doc


#: Reihenfolge der obersten Ebene im kanonischen JSON
_TOP_ORDER = ("fluid", "settings", "components", "connections", "layout")


def canonical_json(doc: dict) -> str:
    """Eingabemodell als kanonisches JSON: gleiche Schlüssel (keine Einheiten-
    umrechnung), stabile Reihenfolge — oberste Ebene fest, Komponenten in
    Dateireihenfolge mit 'type' zuerst, sonst Dateireihenfolge. Kommentare
    des YAML-Originals entfallen.

    Nicht als JSON Schreibbares (inf/nan — auch im sonst ungeprüften
    'layout'-Block —, rekursive Bezüge, mehr als MAX_NODES Werte, einzelne
    Surrogate) ist ein NetworkValidationError mit Fundstelle. DEL, C1-Zeichen
    und U+2028/U+2029 werden als \\uXXXX geschrieben, damit die Ausgabe auch
    als YAML-Text wieder lesbar ist (Zeichenvertrag von parse_yaml)."""
    check_document(doc, finite=True, what="Dokument")
    out = {k: doc[k] for k in _TOP_ORDER if k in doc}
    out.update({k: v for k, v in doc.items() if k not in out})
    comps = out.get("components")
    if isinstance(comps, dict):
        out["components"] = {
            name: ({"type": spec["type"], **{k: v for k, v in spec.items() if k != "type"}}
                   if isinstance(spec, dict) and "type" in spec else spec)
            for name, spec in comps.items()}
    text = json.dumps(out, indent=2, ensure_ascii=False, allow_nan=False)
    return _RE_FORBIDDEN.sub(lambda m: f"\\u{ord(m.group()):04x}", text) + "\n"


def is_path(source) -> bool:
    """Pfad oder Text? Text ist alles Mehrzeilige, Leere und alles, was mit
    '{' bzw. '[' beginnt (JSON/Flow-YAML in einer Zeile); sonst ein Pfad, wenn
    die Endung .yaml/.yml/.json lautet oder eine Datei dieses Namens existiert."""
    if isinstance(source, Path):
        return True
    s = str(source)
    if not s.strip() or "\n" in s or "\r" in s or s.lstrip()[:1] in ("{", "["):
        return False
    if s.lower().endswith((".yaml", ".yml", ".json")):
        return True
    try:
        return Path(s).exists()
    except (OSError, ValueError):               # z.B. „File name too long“ bei langem Text
        return False


def _read_text(path: Path) -> str:
    """Datei als UTF-8 lesen (ein BOM am Anfang wird übergangen); alle Lese-
    und Kodierungsfehler als NetworkValidationError."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise NetworkValidationError([f"Datei '{path}' nicht gefunden."]) from None
    except IsADirectoryError:
        raise NetworkValidationError([f"'{path}' ist ein Verzeichnis, keine Datei."]) from None
    except OSError as exc:
        raise NetworkValidationError(
            [f"Datei '{path}' kann nicht gelesen werden: {exc.strerror or exc}."]) from None
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            detail = "sie ist UTF-16-kodiert (Byte-Order-Mark am Dateianfang)"
        else:
            line = raw.count(b"\n", 0, exc.start) + 1
            detail = (f"ungültiges Byte 0x{raw[exc.start]:02x} in Zeile {line} – vermutlich "
                      f"Windows-1252/Latin-1 (Umlaute)")
        raise NetworkValidationError(
            [f"Datei '{path}' ist nicht UTF-8-kodiert: {detail}. Bitte als UTF-8 speichern."]
        ) from None


def load_document(source):
    """Eingabedokument aus dict, Datei (Path/Pfad-String) oder YAML-/JSON-Text.

    Ein dict wird unverändert durchgereicht (Python-API). Dateien werden als
    UTF-8 gelesen (BOM erlaubt) — .json strikt als JSON, sonst YAML 1.2;
    fehlende, unlesbare oder falsch kodierte Dateien → NetworkValidationError.
    Text wird als YAML 1.2 gelesen (JSON ist darin enthalten). Ein Path ist
    immer eine Datei; bei einem str entscheidet is_path()."""
    if isinstance(source, dict):
        return source
    if is_path(source):
        path = Path(source)
        text = _read_text(path)
        if path.suffix.lower() == ".json":
            return parse_json(text)
        return parse_yaml(text)
    return parse_yaml(str(source))
