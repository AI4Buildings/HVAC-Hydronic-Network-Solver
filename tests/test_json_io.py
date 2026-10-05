"""JSON als gleichwertige Ein- und Ausgabe (AP5).

- .json-Dateien werden strikt als JSON gelesen (doppelte Schlüssel und
  NaN/Infinity sind Fehler, Syntaxfehler mit Position).
- `hydraulik export --json` schreibt das Eingabemodell als kanonisches JSON
  (gleiche Schlüssel, stabile Reihenfolge); YAML → JSON → identische Lösung.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import hydraulik as h
from hydraulik.cli import main
from hydraulik.exceptions import NetworkValidationError
from hydraulik.yamlio import canonical_json, load_document

ROOT = Path(__file__).parent.parent
EXAMPLES = sorted(ROOT.glob("examples/*.yaml")) + [ROOT / "schaltung.yaml",
                                                    ROOT / "tests/data/blockstil_kreis.yaml"]


def _cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "hydraulik.cli", *args],
                          capture_output=True, text=True)


# --- JSON-Eingabe -------------------------------------------------------------------

@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_yaml_json_roundtrip_identische_loesung(path, tmp_path):
    """YAML → export --json → JSON-Datei laden → bitgleiche Lösung."""
    out = tmp_path / (path.stem + ".json")
    proc = _cli("export", "--json", str(path), "--out", str(out))
    assert proc.returncode == 0, proc.stderr
    assert load_document(out) == load_document(path)            # gleiche Daten
    r_yaml = h.load(path).solve(h.load_settings(path)).to_dict()
    r_json = h.load(out).solve(h.load_settings(out)).to_dict()
    assert json.dumps(r_yaml, sort_keys=True) == json.dumps(r_json, sort_keys=True)


def test_cli_run_mit_json_datei(tmp_path):
    f = tmp_path / "kreis.json"
    f.write_text(canonical_json(load_document(ROOT / "examples/01_single_loop.yaml")), encoding="utf-8")
    proc = _cli("run", str(f), "--json")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["converged"] is True


def _json_file(tmp_path, text: str) -> Path:
    f = tmp_path / "e.json"
    f.write_text(text, encoding="utf-8")
    return f


def test_json_doppelte_schluessel_mit_zeilen(tmp_path):
    f = _json_file(tmp_path, '{\n "components": {\n  "a": {"type": "cap"},\n  "a": {"type": "cap"}\n },\n'
                             ' "connections": [["a.port", "a.port"]]\n}\n')
    with pytest.raises(NetworkValidationError) as ei:
        h.load(f)
    msg = " ".join(ei.value.messages)
    assert "Doppelter Schlüssel 'a'" in msg and "JSON" in msg and "3" in msg and "4" in msg


@pytest.mark.parametrize("text, fragment", [
    ('{"components": {"a": {"type": "cap", "x": NaN}}}', "NaN"),
    ('{"components": {"a": {"type": "cap", "x": -Infinity}}}', "Infinity"),
    ('{"components": {"a": {"type": "cap"},}}', "JSON-Syntaxfehler (Zeile 1"),
    ('{"components": {\n  "a": {"type": \'cap\'}}}', "JSON-Syntaxfehler (Zeile 2"),
    ('# Kommentar\n{"components": {}}', "JSON-Syntaxfehler (Zeile 1"),
    ('components:\n  a: {type: cap}\n', ".yaml"),                  # YAML in .json-Datei
    ('', "JSON-Syntaxfehler"),
])
def test_json_strikt_mit_meldung(tmp_path, text, fragment):
    with pytest.raises(NetworkValidationError) as ei:
        load_document(_json_file(tmp_path, text))
    assert any(fragment in m for m in ei.value.messages), ei.value.messages


def test_json_gleiche_semantik_wie_yaml(tmp_path):
    """Dasselbe Modell als JSON-Datei, JSON-Text (YAML-1.2-Obermenge) und YAML."""
    doc = {"fluid": {"preset": "water", "t_C": 45.0},
           "components": {"no": {"type": "inflow", "t_set_C": 60, "p_kPa": 10, "ts": "08"},
                          "true": {"type": "outflow", "p_kPa": 0}},
           "connections": [["no.port", "true.port"]]}
    text = json.dumps(doc)
    assert load_document(_json_file(tmp_path, text)) == doc
    assert load_document(text) == doc                         # JSON-Text über den YAML-Parser
    assert h.load(doc).components["no"].ts == "08"


# --- kanonischer Export ---------------------------------------------------------------

def test_canonical_json_reihenfolge_und_stabilitaet():
    doc = {"layout": {"components": {}}, "connections": [["b.out", "a.in"]],
           "components": {"zz": {"kvs_m3h": 1, "type": "control_valve", "opening": 0.5},
                          "aa": {"p_kPa": 0, "type": "outflow"}},
           "settings": {"max_iter": 400}, "fluid": {"t_C": 50, "preset": "water"}}
    text = canonical_json(doc)
    assert list(json.loads(text)) == ["fluid", "settings", "components", "connections", "layout"]
    comps = json.loads(text)["components"]
    assert list(comps) == ["zz", "aa"]                                  # Dateireihenfolge
    assert list(comps["zz"]) == ["type", "kvs_m3h", "opening"]          # type zuerst
    assert canonical_json(json.loads(text)) == text                     # idempotent
    assert text.endswith("\n") and "  " in text                         # eingerückt


def test_export_stdout_und_fehler(tmp_path, capsys):
    assert main(["export", "--json", str(ROOT / "examples/01_single_loop.yaml")]) == 0
    out = capsys.readouterr().out
    assert json.loads(out)["components"]["wp1"]["type"] == "heat_pump"
    bad = tmp_path / "kaputt.yaml"
    bad.write_text("components:\n  p: {type: pump, q_m3h: yes}\nconnections: [[p.in, p.out]]\n")
    assert main(["export", "--json", str(bad)]) == 1
    assert "FEHLER" in capsys.readouterr().err


def test_export_luft(tmp_path, capsys):
    f = tmp_path / "luft.yaml"
    f.write_text("components:\n  aul: {type: aussenluft, t_C: 5, rh: 80}\n  fol: {type: fortluft}\n"
                 "connections:\n  - [aul.out, fol.in]\n")
    assert main(["export", "--json", "--luft", str(f)]) == 0
    assert json.loads(capsys.readouterr().out)["components"]["aul"]["t_C"] == 5


def test_export_ist_schema_konform(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    from hydraulik.schema import json_schema
    v = jsonschema.Draft202012Validator(json_schema())
    for path in EXAMPLES:
        assert not list(v.iter_errors(json.loads(canonical_json(load_document(path)))))
