"""Zentraler Eingabe-Loader (yamlio): YAML 1.2 Core Schema, überall gleich.

Regressionstests zu den Review-Befunden (PyYAML = YAML 1.1 lieferte z.B.
'1.4e0' als String und 'no' als False) sowie zum Vertrag des Loaders:
nur reine Python-Typen, Schlüssel immer Strings, doppelte Schlüssel und
Syntaxfehler als gesammelte, deutsche NetworkValidationError-Meldungen.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

import hydraulik as h
from hydraulik.air import load_air
from hydraulik.exceptions import NetworkValidationError
from hydraulik.server import normalize_payload
from hydraulik.yamlio import load_document, parse_yaml

ROOT = Path(__file__).parent.parent


def _v(text: str):
    return parse_yaml(f"v: {text}\n")["v"]


# --- Befund-Tabelle des Reviews -------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("1.4e0", 1.4), ("14e-1", 1.4), ("1e3", 1000.0), ("5e-7", 5e-7),
    ("1.0e+3", 1000.0), ("-2.5E-3", -2.5e-3), (".5", 0.5), ("1.", 1.0), ("+.5", 0.5),
])
def test_exponentenschreibweise_ist_zahl(text, expected):
    v = _v(text)
    assert isinstance(v, float) and v == pytest.approx(expected, rel=1e-15)


@pytest.mark.parametrize("text", ["no", "off", "yes", "on", "No", "OFF", "Yes", "y", "n"])
def test_yaml11_wahrheitswoerter_bleiben_strings(text):
    assert _v(text) == text


@pytest.mark.parametrize("text, expected", [
    ("08", 8), ("07", 7), ("010", 10), ("0o17", 15), ("0x1F", 31), ("-12", -12), ("+3", 3),
])
def test_ganzzahlen_nach_core_schema(text, expected):
    v = _v(text)
    assert type(v) is int and v == expected


@pytest.mark.parametrize("text, expected", [
    ("true", True), ("True", True), ("TRUE", True),
    ("false", False), ("False", False), ("FALSE", False),
    ("null", None), ("Null", None), ("NULL", None), ("~", None), ("", None),
])
def test_bool_und_null_nach_core_schema(text, expected):
    assert _v(text) is expected


@pytest.mark.parametrize("text", [
    "1_000", "0b101", "2026-10-05", "2026-10-05T12:00:00", "12:30", "1:30:00",
    "0x", "-0x10", "Infinity", "NaN", "inf", "1e", "e3", ".", "+-1", "1.2.3",
])
def test_kein_yaml11_sondertyp(text):
    """YAML 1.2 Core kennt keine Unterstriche, Binär-, Datums- oder
    Sexagesimalzahlen – alles bleibt String (und fällt später mit klarer
    Typmeldung auf, statt still falsch interpretiert zu werden)."""
    assert _v(text) == text


def test_nicht_endliche_zahlen_werden_gelesen():
    import math
    assert _v(".inf") == math.inf and _v("-.Inf") == -math.inf
    assert math.isnan(_v(".nan")) and math.isnan(_v(".NaN"))
    assert _v("1e400") == math.inf


def test_gequotete_werte_bleiben_strings():
    doc = parse_yaml("a: '08'\nb: \"1e3\"\nc: 'it''s'\nd: \"Kreis #2\"\ne: 'no'\n")
    assert doc == {"a": "08", "b": "1e3", "c": "it's", "d": "Kreis #2", "e": "no"}


def test_explizite_core_tags():
    doc = parse_yaml("a: !!str 12\nb: !!float 1\nc: !!int '7'\nd: !!bool true\ne: !!null ''\n")
    assert doc == {"a": "12", "b": 1.0, "c": 7, "d": True, "e": None}
    assert type(doc["b"]) is float


# --- Vertrag: reine Python-Typen, Schlüssel = Originaltext ---------------------

def _assert_plain(obj):
    if type(obj) is dict:
        for k, v in obj.items():
            assert type(k) is str
            _assert_plain(v)
    elif type(obj) is list:
        for v in obj:
            _assert_plain(v)
    else:
        assert type(obj) in (str, int, float, bool, type(None)), type(obj)


@pytest.mark.parametrize("path", sorted(ROOT.glob("examples/*.yaml")) + [ROOT / "schaltung.yaml"],
                         ids=lambda p: p.name)
def test_rueckgabe_nur_reine_python_typen(path):
    _assert_plain(load_document(path))


def test_schluessel_sind_immer_strings_im_originaltext():
    doc = parse_yaml("true: 1\n08: 2\n1.50: 3\nnull: 4\n~: 5\n'x y': 6\nno: 7\n")
    assert list(doc) == ["true", "08", "1.50", "null", "~", "x y", "no"]
    _assert_plain(doc)


def test_anker_und_aliase_erlaubt():
    doc = parse_yaml("a: &p {type: cap}\nb: *p\n")
    assert doc == {"a": {"type": "cap"}, "b": {"type": "cap"}}


# --- Befund: Komponentennamen no/off, ts mit führender Null ---------------------

YAML_NO_OFF = """\
components:
  no:  {type: inflow, t_set_C: 50, q_m3h: 1.4e0}
  off: {type: outflow, p_kPa: 0, ts: 08}
  on:  {type: flow_resistance, c_Pa_m3h2: 1e3, ts: 07}
connections:
  - [no.port, on.in]
  - [on.out, off.port]
"""


def test_komponentennamen_no_off_on_und_exponenten():
    net = h.load(YAML_NO_OFF)
    assert set(net.components) == {"no", "off", "on"}
    r = net.solve()
    assert r.converged
    assert r["on"].q_m3h == pytest.approx(1.4, rel=1e-9)
    # dp = C·V̇² mit C = 1e3 Pa/(m³/h)²
    assert r["on"].dp_kPa * 1e3 == pytest.approx(1e3 * 1.4 ** 2, rel=1e-6)


def test_ts_labels_mit_fuehrender_null_als_zahl():
    comps = h.load(YAML_NO_OFF).components
    assert comps["off"].ts == "8" and comps["on"].ts == "7"


# --- Fehlerpfade -----------------------------------------------------------------

def test_doppelte_schluessel_mit_zeilennummer_gesammelt():
    text = "components:\n  a: {type: cap}\n  b: {type: cap}\n  a: {type: cap}\n" \
           "connections:\n  - [a.port, b.port]\n  - [b.port, a.port, b.port, b.port]\n" \
           "components: {}\n"
    with pytest.raises(NetworkValidationError) as ei:
        h.load(text)
    msgs = ei.value.messages
    assert any("Doppelter Schlüssel 'a'" in m and "Zeile 4" in m for m in msgs)
    assert any("Doppelter Schlüssel 'components'" in m and "Zeile 8" in m for m in msgs)


def test_doppelte_schluessel_auch_in_komponenten_und_textgleich():
    with pytest.raises(NetworkValidationError, match=r"Doppelter Schlüssel 'q_m3h'.*Zeile 3"):
        parse_yaml("components:\n  p: {type: pump, q_m3h: 1,\n      q_m3h: 2}\n")
    # 1 und 01 sind verschiedene Schlüssel (Originaltext), keine Kollision
    assert parse_yaml("1: a\n01: b\n") == {"1": "a", "01": "b"}


def test_syntaxfehler_mit_position_statt_traceback():
    # Fehler fällt in Zeile 3 auf; der Kontext nennt die offene Klammer in Zeile 2
    with pytest.raises(NetworkValidationError,
                       match=r"YAML-Syntaxfehler \(Zeile 3, Spalte \d+\): .*Zeile 2"):
        h.load("components:\n  a: {type: cap\nconnections: []\n")
    with pytest.raises(NetworkValidationError, match="YAML-Syntaxfehler"):
        h.load("components:\n\ta: {type: cap}\n")          # Tab-Einrückung


@pytest.mark.parametrize("text, fragment", [
    ("v: !!binary aGk=", "!!binary"),
    ("v: !!set {a, b}", "!!set"),
    ("v: !!timestamp 2026-01-01", "!!timestamp"),
    ("v: !!omap [a: 1]", "!!omap"),
    ("v: !foo bar", "!foo"),
    ("v: !!int 1_000", "!!int"),
    ("v: !!bool yes", "!!bool"),
    ("v: !!float abc", "!!float"),
])
def test_nicht_unterstuetzte_tags_mit_meldung(text, fragment):
    with pytest.raises(NetworkValidationError) as ei:
        parse_yaml(text + "\n")
    assert any(fragment in m and "Zeile 1" in m for m in ei.value.messages)


def test_nicht_skalare_schluessel_und_merge_schluessel():
    with pytest.raises(NetworkValidationError, match="Schlüssel"):
        parse_yaml("? [a, b]\n: 1\n")
    with pytest.raises(NetworkValidationError, match="Merge"):
        parse_yaml("base: &b {type: cap}\nx:\n  <<: *b\n")


def test_leeres_dokument_und_mehrere_dokumente():
    with pytest.raises(NetworkValidationError, match="Mapping"):
        h.load("# nur ein Kommentar\n")
    with pytest.raises(NetworkValidationError, match="YAML-Syntaxfehler|mehrere Dokumente"):
        h.load("components: {}\n---\ncomponents: {}\n")


def test_fehlende_datei_klare_meldung(tmp_path):
    with pytest.raises(NetworkValidationError, match="nicht gefunden"):
        h.load(tmp_path / "gibtsnicht.yaml")


def test_cli_syntaxfehler_ohne_traceback(tmp_path):
    f = tmp_path / "kaputt.yaml"
    f.write_text("components:\n  a: {type: cap\n", encoding="utf-8")
    proc = subprocess.run([sys.executable, "-m", "hydraulik.cli", "run", str(f)],
                          capture_output=True, text=True)
    assert proc.returncode == 1
    assert "YAML-Syntaxfehler" in proc.stderr and "Traceback" not in proc.stderr


# --- eine Ladefunktion für alle Stellen ---------------------------------------

def test_luft_und_server_nutzen_denselben_loader():
    text = ("components:\n  no: {type: aussenluft, t_C: 1e1, rh: 80}\n"
            "connections: []\n")
    doc = normalize_payload(text, "air")["doc"]
    assert doc["components"]["no"]["t_C"] == 10.0
    with pytest.raises(NetworkValidationError) as ei:
        load_air(text)
    assert not any("False" in m for m in ei.value.messages)
    with pytest.raises(NetworkValidationError, match="Doppelter Schlüssel"):
        load_air("components:\n  a: {type: aussenluft}\n  a: {type: aussenluft}\n")


def test_server_liest_keine_lokalen_dateien_ueber_den_body(tmp_path):
    """Der Request-Body ist immer YAML-Text, nie ein Dateipfad."""
    f = tmp_path / "geheim.yaml"
    f.write_text("components: {a: {type: cap}}\nconnections: [[a.port, a.port]]\n")
    with pytest.raises(NetworkValidationError, match="Mapping"):
        normalize_payload(str(f))


def test_keine_direkten_yaml_aufrufe_mehr():
    """Nur yamlio.py parst YAML; PyYAML wird nirgends mehr importiert."""
    offenders = []
    for path in list((ROOT / "src").rglob("*.py")) + list((ROOT / "examples").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if re.search(r"^\s*(import yaml|from yaml\b)", text, re.M):
            offenders.append(str(path.relative_to(ROOT)))
        if path.name != "yamlio.py" and re.search(r"ruamel", text):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
