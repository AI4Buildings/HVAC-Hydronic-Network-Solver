"""Befunde der Fehlersuche November 2026 in der Ein-/Ausgabeschicht
(yamlio, yaml_loader, params, schema, cli, exceptions, conduit).

Jeder Test hält einen Befund fest, der vor der Korrektur ein roher
Python-Fehler, eine irreführende Meldung oder ein still anderes Ergebnis war.
Nummern = Befundnummern des Prüfberichts (IO-1 … IO-15).
"""
from __future__ import annotations

import copy
import json
import os
import pickle
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

import hydraulik as h
from hydraulik import yamlio
from hydraulik.exceptions import (ComponentModelError, ComponentParamError,
                                  NetworkValidationError, SingularNetworkError)
from hydraulik.yaml_loader import load_checked
from hydraulik.yamlio import canonical_json, load_document, parse_json, parse_yaml

ROOT = Path(__file__).parent.parent
BEISPIEL = ROOT / "examples/01_single_loop.yaml"

KREIS = """
components:
  pu: {type: pump, mode: constant_dp, dp_kPa: 30}
  r1: {type: flow_resistance, dp_kPa: 30, q_m3h: 1.0}
connections:
  - [pu.out, r1.in]
  - [r1.out, pu.in]
"""


def _cli(*args, **kw):
    return subprocess.run([sys.executable, "-m", "hydraulik.cli", *map(str, args)],
                          capture_output=True, text=True, timeout=120, **kw)


def _messages(fn, *args, **kw) -> list[str]:
    with pytest.raises(NetworkValidationError) as exc:
        fn(*args, **kw)
    return exc.value.messages


# --- IO-1: einzeiliger JSON-/YAML-Text ist Text, kein Dateiname -----------------------

def test_io1_einzeiliger_json_text_laenger_als_ein_dateiname():
    """json.dumps schreibt eine Zeile; > 255 Zeichen scheiterte Path.exists()
    mit OSError 'File name too long'."""
    doc = load_document(BEISPIEL)
    text = json.dumps(doc)
    assert "\n" not in text and len(text) > 255
    assert load_document(text) == doc
    assert h.load(text).solve(h.load_settings(text)).converged
    lang = "{components: {" + ", ".join(f"c{i}: {{type: cap}}" for i in range(60)) + "}}"
    assert len(parse_yaml(lang)["components"]) == 60 and len(load_document(lang)["components"]) == 60


def test_io1_leerer_text_ist_kein_verzeichnis():
    assert load_document("") is None and load_document("  ") is None
    msgs = _messages(h.load, "")
    assert "Mapping" in msgs[0] and "Verzeichnis" not in msgs[0]


# --- IO-2/IO-4 (Loader-/Schema-Teil): Punkte im Namen erlaubt, leerer Name nicht -------

def test_io2_schema_erlaubt_punkte_im_namen_aber_keinen_leeren_namen():
    jsonschema = pytest.importorskip("jsonschema")
    from hydraulik.schema import json_schema
    for kind, typ in (("hydraulik", "cap"), ("air", "fortluft")):
        v = jsonschema.Draft202012Validator(json_schema(kind))
        ok = {"components": {"TS1.1": {"type": typ}, "HK 1.OG": {"type": typ}},
              "connections": [["TS1.1.port", "HK 1.OG.port"]]}
        assert list(v.iter_errors(ok)) == [], kind
        leer = {"components": {"": {"type": typ}}, "connections": [["x.port", "y.port"]]}
        assert list(v.iter_errors(leer)), kind
        for ref in ("ohnepunkt", "name.", ".port"):          # Port fehlt / Name leer
            bad = {"components": {"a": {"type": typ}}, "connections": [[ref, "a.port"]]}
            assert list(v.iter_errors(bad)), (kind, ref)


def test_io2_loader_lehnt_leeren_komponentennamen_ab():
    doc = {"components": {"": {"type": "pump", "mode": "constant_dp", "dp_kPa": 30},
                          "r1": {"type": "flow_resistance", "dp_kPa": 30, "q_m3h": 1.0}},
           "connections": [[".out", "r1.in"], ["r1.out", ".in"]]}
    for fn in (h.load, load_checked):
        msgs = _messages(fn, doc)
        assert any("Komponentenname darf nicht leer sein" in m for m in msgs), msgs
    assert any("nicht leer" in m for m in _messages(h.load, "components:\n  '': {type: cap}\n"
                                                            "connections:\n  - [a.port, b.port]\n"))


def test_io2_namen_mit_punkt_rechnen_und_exportieren(tmp_path):
    doc = {"components": {"pu": {"type": "pump", "mode": "constant_dp", "dp_kPa": 30},
                          "TS1.1": {"type": "flow_resistance", "dp_kPa": 30, "q_m3h": 1.0}},
           "connections": [["pu.out", "TS1.1.in"], ["TS1.1.out", "pu.in"]]}
    net, settings = load_checked(doc)
    assert net.solve(settings)["TS1.1"].q_m3h == pytest.approx(1.0, rel=0.2)
    f = tmp_path / "punkt.yaml"
    f.write_text(json.dumps(doc), encoding="utf-8")
    out = tmp_path / "punkt.json"
    assert _cli("export", "--json", f, "--out", out).returncode == 0
    assert _cli("run", out).returncode == 0
    jsonschema = pytest.importorskip("jsonschema")              # Export besteht das eigene Schema
    from hydraulik.schema import json_schema
    exportiert = json.loads(out.read_text(encoding="utf-8"))
    assert list(jsonschema.Draft202012Validator(json_schema()).iter_errors(exportiert)) == []


# --- IO-3: Import verändert ruamel nicht prozessweit -------------------------------------

def test_io3_import_laesst_fremde_ruamel_nutzer_unberuehrt():
    """Die Core-Schema-Regeln dürfen nur am eigenen Resolver hängen, nicht an
    ruamels modulweiter Liste (galt sonst für jedes YAML() im Prozess)."""
    code = (
        "import warnings; warnings.simplefilter('ignore')\n"
        "import ruamel.yaml.resolver as r\n"
        "from ruamel.yaml import YAML\n"
        "n = len(r.implicit_resolvers)\n"
        "doc12 = 'a: .5e3\\nb: 08\\nc: yes\\n'\n"
        "doc11 = '%YAML 1.1\\n---\\na: 08\\nb: .5e3\\n'\n"
        "vorher = [repr(YAML(typ=t, pure=True).load(d)) for t in ('safe', 'rt') for d in (doc12, doc11)]\n"
        "import hydraulik, hydraulik.yamlio\n"
        "assert hydraulik.yamlio.parse_yaml('a: .5e3\\nb: 08\\n') == {'a': 500.0, 'b': 8}\n"
        "assert len(r.implicit_resolvers) == n, 'globale Resolverliste verändert'\n"
        "nachher = [repr(YAML(typ=t, pure=True).load(d)) for t in ('safe', 'rt') for d in (doc12, doc11)]\n"
        "assert vorher == nachher, (vorher, nachher)\n"
        "print('unveraendert')\n")
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0 and "unveraendert" in proc.stdout, proc.stderr


# --- IO-5: ein Durchgang für Parameter-, settings- und Verbindungsfehler ----------------

GEMISCHT = """
settings: {max_itr: 5}
components:
  pu: {type: pump, mode: constant_dp, dp_kPa: 30}
  r1: {type: flow_resistance, dp_kPa: 30, q_m3h: 1.0, foo: 1}
  r2: {type: flow_resistance, dp_kPa: 30, q_m3h: 1.0}
connections:
  - [pu.out, r1.inn]
  - [r1.out, r2.inn]
  - [r2.out, pumpe.in]
"""


def test_io5_load_checked_sammelt_alle_fehler():
    msgs = _messages(load_checked, GEMISCHT)
    text = "\n".join(msgs)
    assert "Unbekannter Parameter 'foo'" in text                 # Parameter (load)
    assert "'max_itr'" in text                                   # settings (load_settings)
    assert "hat keinen Port 'inn'" in text and "'r2'" in text    # Verbindung (compile)
    assert "Unbekannte Komponente 'pumpe'" in text
    assert "Port 'pu.in' ist mit nichts verbunden." in msgs
    # keine Folgefehler zur Komponente mit Parameterfehler (Ports nicht prüfbar)
    assert "Unbekannte Komponente 'r1'" not in text and "Port 'r1." not in text
    assert "'r1' (Typ" not in text
    assert len(msgs) == len(set(msgs))


def test_io5_load_checked_gibt_netz_und_einstellungen():
    net, settings = load_checked(BEISPIEL)
    assert isinstance(settings, h.SolverSettings) and net.solve(settings).converged
    net, settings = load_checked("settings: {max_iter: 77}\n" + KREIS)
    assert settings.max_iter == 77 and set(net.components) == {"pu", "r1"}


def test_io5_singulaeres_netz_bleibt_eigener_fehler():
    doc = {"components": {"p1": {"type": "pump", "mode": "constant_flow", "q_m3h": 1.0},
                          "p2": {"type": "pump", "mode": "constant_flow", "q_m3h": 2.0}},
           "connections": [["p1.out", "p2.in"], ["p2.out", "p1.in"]]}
    with pytest.raises(SingularNetworkError):
        load_checked(doc)
    msgs = _messages(load_checked, {**doc, "settings": {"gibtsnicht": 1}})   # mit anderem Fehler: eine Liste
    assert len(msgs) == 2 and "bilanzieren nicht" in msgs[1]


def test_io5_cli_run_und_export_ein_durchgang(tmp_path):
    f = tmp_path / "gemischt.yaml"
    f.write_text(GEMISCHT, encoding="utf-8")
    out = tmp_path / "gemischt.json"
    for proc in (_cli("run", f), _cli("export", "--json", f, "--out", out)):
        assert proc.returncode == 1 and "Traceback" not in proc.stderr
        for teil in ("'foo'", "'max_itr'", "Port 'inn'", "'pumpe'"):
            assert teil in proc.stderr, (teil, proc.stderr)
        assert proc.stderr.count("Netzwerk-Validierung fehlgeschlagen") == 1
    assert not out.exists()


def test_io5_export_ist_geprueft_auch_die_verbindungen(tmp_path):
    """'export --json' schrieb Dateien mit unbekannten Komponenten/Ports (rc 0)."""
    f = tmp_path / "ref.yaml"
    f.write_text(KREIS.replace("[pu.out, r1.in]", "[pu.out, gibtsnicht.in]"), encoding="utf-8")
    out = tmp_path / "ref.json"
    proc = _cli("export", "--json", f, "--out", out)
    assert proc.returncode == 1 and "Unbekannte Komponente 'gibtsnicht'" in proc.stderr
    assert not out.exists()
    f.write_text(KREIS, encoding="utf-8")
    assert _cli("export", "--json", f, "--out", out).returncode == 0 and out.exists()


# --- IO-6: CLI liest die Datei einmal; fehlende Datei heißt „nicht gefunden“ ------------

@pytest.mark.parametrize("name", ["schaltung_ohne_endung", "notiz.txt", "x.YAML"])
def test_io6_cli_fehlende_datei_heisst_nicht_gefunden(tmp_path, name):
    for cmd in (("run",), ("export", "--json")):
        proc = _cli(*cmd, tmp_path / name)
        assert proc.returncode == 1 and "nicht gefunden" in proc.stderr, proc.stderr
        assert "Mapping" not in proc.stderr


def test_io6_python_api_einzeiliger_pfad_ohne_datei():
    msgs = _messages(h.load, "gibt/es/nicht/schaltung")
    assert "Datei 'gibt/es/nicht/schaltung' nicht gefunden" in msgs[0]
    assert "nicht gefunden" in _messages(h.load_settings, "auchnicht")[0]
    assert "nicht gefunden" not in _messages(h.load, "- a\n- b\n")[0]      # Text, kein Pfad


@pytest.mark.skipif(not Path("/dev/stdin").exists(), reason="kein /dev/stdin")
def test_io6_cli_liest_die_eingabe_nur_einmal():
    """load() + load_settings() lasen die Datei zweimal — bei /dev/stdin und
    Prozess-Substitution war der Strom beim zweiten Mal leer."""
    text = BEISPIEL.read_text(encoding="utf-8")
    proc = _cli("run", "/dev/stdin", "--json", input=text)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["converged"] is True


# --- IO-7: Datei- und Kodierungsfehler ohne Traceback --------------------------------------

def test_io7_nicht_utf8_datei(tmp_path):
    latin = tmp_path / "latin1.yaml"
    latin.write_bytes(KREIS.replace("dp_kPa: 30}", 'dp_kPa: 30, description: "Wärme"}', 1)
                      .encode("latin-1"))
    msg = _messages(h.load, latin)[0]
    assert "nicht UTF-8-kodiert" in msg and "0xe4" in msg and "Zeile 3" in msg and "Latin-1" in msg
    utf16 = tmp_path / "utf16.yaml"
    utf16.write_text(KREIS, encoding="utf-16")
    assert "UTF-16" in _messages(load_document, utf16)[0]
    proc = _cli("run", latin)
    assert proc.returncode == 1 and "Traceback" not in proc.stderr and "UTF-8" in proc.stderr


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="Leserechte nicht entziehbar")
def test_io7_unlesbare_datei(tmp_path):
    f = tmp_path / "gesperrt.yaml"
    f.write_text(KREIS, encoding="utf-8")
    f.chmod(0)
    try:
        assert "kann nicht gelesen werden" in _messages(load_document, f)[0]
    finally:
        f.chmod(0o644)


def test_io7_cli_schreibfehler_ohne_traceback(tmp_path):
    f = tmp_path / "kreis.yaml"
    f.write_text(KREIS, encoding="utf-8")
    ziel = tmp_path / "gibt" / "es" / "nicht"
    for args in (("run", f, "--csv", ziel / "a.csv"), ("export", "--json", f, "--out", ziel / "a.json"),
                 ("schema", "--out", ziel / "s.json"), ("editor", "--out", ziel / "e.html")):
        proc = _cli(*args)
        assert proc.returncode == 1, args
        assert "FEHLER" in proc.stderr and "Traceback" not in proc.stderr, proc.stderr


# --- IO-8: Zahlentypen der Python-API (numpy) --------------------------------------------

@pytest.mark.parametrize("wert", [np.int64(30), np.int32(30), np.float32(30), np.float64(30),
                                  np.arange(30, 31)[0], Fraction(30)])
def test_io8_numpy_zahlen_als_parameter(wert):
    pu = h.Pump("pu", mode="constant_dp", dp_kPa=wert)
    assert pu.dp == 30000.0 and type(pu.dp) is float


def test_io8_numpy_ganzzahl_wahrheitswert_und_settings():
    assert type(h.BufferStorage("ps", n_ports=np.int64(3)).n_ports) is int
    assert h.BufferStorage("ps", n_ports=np.float32(3.0)).n_ports == 3
    assert h.BallValve("kh", closed=np.bool_(True)).closed is True
    assert h.ControlValve("v", kvs_m3h=2.5, opening=np.arange(0, 2)[1]).opening == 1.0
    s = h.load_settings({"settings": {"max_iter": np.int64(5), "alpha_p": np.float32(0.5)}})
    assert s.max_iter == 5 and type(s.max_iter) is int and type(s.alpha_p) is float
    # Wahrheitswerte bleiben keine Zahlen, Zeichenketten auch nicht
    for falsch in (True, np.bool_(True), "30", None):
        with pytest.raises(ComponentParamError):
            h.Pump("pu", mode="constant_dp", dp_kPa=falsch)
    with pytest.raises(ComponentParamError):
        h.BufferStorage("ps", n_ports=np.float64(2.5))
    with pytest.raises(NetworkValidationError):
        h.load_settings({"settings": {"max_iter": np.bool_(True)}})


# --- IO-9: Fehlerklassen überstehen pickle/copy (multiprocessing) ---------------------------

@pytest.mark.parametrize("fehler", [
    NetworkValidationError(["Port 'a.in' ist mit nichts verbunden.", "zweiter Fehler"]),
    ComponentParamError("pu", ["'dp_kPa' muss eine Zahl sein", "noch einer"]),
    ComponentModelError("hk1", "radiator", "thermisches", "t_in = 5 °C", ValueError("x")),
], ids=lambda e: type(e).__name__)
def test_io9_fehler_sind_pickle_und_kopierbar(fehler):
    for kopie in (pickle.loads(pickle.dumps(fehler)), copy.copy(fehler), copy.deepcopy(fehler)):
        assert type(kopie) is type(fehler) and str(kopie) == str(fehler)
        for attr, wert in vars(fehler).items():
            assert repr(getattr(kopie, attr)) == repr(wert), attr


# --- IO-10: Knotenbudget, rekursive Aliase, inf/nan beim Export ------------------------------

def _alias_kaskade(stufen: int) -> str:
    text = "layout:\n  a0: &a0 [x, x, x, x, x, x, x, x, x, x]\n"
    for i in range(1, stufen):
        text += f"  a{i}: &a{i} [" + ", ".join([f"*a{i - 1}"] * 10) + "]\n"
    return text


def test_io10_alias_kaskade_wird_abgelehnt_statt_aufgefaltet():
    """9 Stufen × 10 Aliase = 10^9 Werte aus 1,3 kB — vorher lief export
    in die Speichergrenze; jetzt Fehler nach höchstens MAX_NODES Schritten."""
    assert yamlio.MAX_NODES == 200_000
    bombe = KREIS + _alias_kaskade(9)
    assert len(bombe) < 2000
    for fn in (parse_yaml, load_document, h.load, load_checked):
        msg = _messages(fn, bombe)[0]
        assert "zu groß" in msg and "200000" in msg and "Alias" in msg
    # gewöhnliche Aliase bleiben erlaubt
    klein = KREIS + _alias_kaskade(3)
    assert h.load(klein).solve().converged


def test_io10_knotenbudget_zaehlt_aufgeloeste_werte(monkeypatch):
    monkeypatch.setattr(yamlio, "MAX_NODES", 10)
    assert parse_yaml("[1, 2, 3, 4, 5, 6, 7, 8, 9]") == list(range(1, 10))     # 10 Werte
    assert "zu groß" in _messages(parse_yaml, "[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]")[0]
    assert parse_yaml("a: &a [1, 2, 3]\nb: *a\nc: 1\n")["b"] == [1, 2, 3]       # 10 Werte
    assert "zu groß" in _messages(parse_yaml, "a: &a [1, 2, 3]\nb: *a\nc: *a\n")[0]
    assert "zu groß" in _messages(parse_json, json.dumps(list(range(10))))[0]


@pytest.mark.parametrize("text, ort", [("a: &a [*a]\n", "a[0]"), ("a: &a {b: *a}\n", "a.b"),
                                       ("layout:\n  wires: &w [1, {x: *w}]\n", "layout.wires[1].x")])
def test_io10_rekursiver_alias(text, ort):
    msg = _messages(parse_yaml, text)[0]
    assert "rekursiver Bezug" in msg and f"'{ort}'" in msg


def test_io10_export_meldet_inf_nan_im_layout(tmp_path):
    doc = parse_yaml(KREIS + "layout:\n  components: {pu: {x: .inf, y: [1, .nan]}}\n")
    assert h.load(doc).solve().converged                       # der Rechenkern übergeht layout
    msgs = _messages(canonical_json, doc)
    assert "'layout.components.pu.x'" in msgs[0] and "'layout.components.pu.y[1]'" in msgs[1]
    assert "inf" in msgs[0] and "nan" in msgs[1]
    for name, text in (("inf.yaml", KREIS + "layout: {components: {pu: {x: .inf}}}\n"),
                       ("ueberlauf.json", json.dumps({**parse_yaml(KREIS), "layout": {"x": 0}})
                        .replace('"x": 0', '"x": 1e999'))):
        f = tmp_path / name
        f.write_text(text, encoding="utf-8")
        proc = _cli("export", "--json", f)
        assert proc.returncode == 1 and "Traceback" not in proc.stderr, proc.stderr
        assert "Nicht endliche Zahl" in proc.stderr and proc.stdout == ""
    rekursiv = {"components": {}, "layout": []}
    rekursiv["layout"].append(rekursiv["layout"])               # dict aus der Python-API
    assert "rekursiver Bezug" in _messages(canonical_json, rekursiv)[0]


# --- IO-11: überlange Ganzzahlen und zu tiefe Schachtelung --------------------------------

def test_io11_ueberlange_ganzzahl():
    ziffern = "9" * 5000
    for fn, text in ((parse_yaml, f"a: {ziffern}\n"), (parse_yaml, f"a: !!int {ziffern}\n"),
                     (parse_json, '{"a": %s}' % ziffern)):
        msg = _messages(fn, text)[0]
        assert "zu vielen Stellen" in msg and "5000 Zeichen" in msg
    assert "zu vielen Stellen" in _messages(h.load, KREIS.replace("dp_kPa: 30}", f"dp_kPa: {ziffern}}}", 1))[0]
    assert parse_yaml(f"a: !!float {'9' * 400}\n")["a"] == float("9" * 400)
    assert parse_yaml("a: 1e999\n")["a"] == float("inf")       # Überlauf bleibt Sache des Loaders


def test_io11_zu_tiefe_schachtelung():
    assert "zu tief verschachtelt" in _messages(parse_yaml, "a: " + "[" * 3000 + "]" * 3000 + "\n")[0]
    assert "zu tief verschachtelt" in _messages(parse_json, "[" * 100000 + "]" * 100000)[0]


# --- IO-12: JSON-Datei mit UTF-8-BOM -------------------------------------------------------

def test_io12_json_mit_bom(tmp_path):
    doc = load_document(BEISPIEL)
    f = tmp_path / "bom.json"
    f.write_text(canonical_json(doc), encoding="utf-8-sig")
    assert f.read_bytes()[:3] == b"\xef\xbb\xbf"
    assert load_document(f) == doc and h.load(f).solve().converged
    assert parse_json('﻿{"a": 1}') == {"a": 1}
    y = tmp_path / "bom.yaml"
    y.write_text(BEISPIEL.read_text(encoding="utf-8"), encoding="utf-8-sig")
    assert load_document(y) == doc


# --- IO-13: pipes-Fehler werden mit den übrigen gesammelt -----------------------------------

def test_io13_conduit_sammelt_pipes_und_parameterfehler():
    with pytest.raises(ComponentParamError) as exc:
        h.Conduit("l", pipes=[{"length_m": -1}], foo=1, ts=1.5, d_inner_mm="x")
    text = "\n".join(exc.value.messages)
    assert "pipes[0]" in text and "'foo'" in text and "'ts'" in text and "'d_inner_mm'" in text
    assert len(exc.value.messages) == 4
    with pytest.raises(ComponentParamError) as exc:            # Modus-Konflikt trotz pipes-Fehler
        h.Conduit("l", pipes=[{"length_m": -1}], c_Pa_m3h2=5)
    assert len(exc.value.messages) == 2 and "Höchstens EINE" in exc.value.messages[1]
    assert len(h.Conduit("l", pipes=[{"length_m": 3}, {"length_m": 4, "d_inner_mm": 20}]).pipes) == 2
    assert h.Conduit("l", pipes=[]).ideal_connection() == ("in", "out")


# --- IO-14: Bereichsgrenzen trotz Einheitenrundung; layout im Schema -------------------------

def test_io14_wert_genau_an_der_grenze_wird_angenommen():
    """0.36 kg/h · (1/3600) = 9.999999999999999e-05 < 1e-4 kg/s — die Meldung
    lautete „0.36 liegt unter dem Minimum (0.36 kg_h)“."""
    coil = h.HeatingCoil("h", ua_ref_W_K=500, m_dot_air_kg_h=0.36, t_air_in_C=20)
    assert coil.m_dot_air == 1e-4
    for unter in (0.3599, 0.36 * (1 - 1e-6)):
        with pytest.raises(ComponentParamError):
            h.HeatingCoil("h", ua_ref_W_K=500, m_dot_air_kg_h=unter, t_air_in_C=20)
    # ohne Einheitenumrechnung bleibt die Grenze exakt
    with pytest.raises(ComponentParamError):
        h.ControlValve("v", kvs_m3h=2.5, opening=1.0000000001)
    with pytest.raises(ComponentParamError):
        h.Pipe("p", length_m=1, d_inner_mm=20, roughness_mm=-1e-300)


def test_io14_schema_minimum_wird_vom_loader_angenommen():
    """Für jeden Schlüssel mit Bereichsgrenze: der Grenzwert des Schemas (in
    der Einheit des Schlüssels) ist auch für den Loader gültig."""
    from hydraulik.air.components import AIR_REGISTRY
    from hydraulik.components.registry import COMPONENT_REGISTRY
    from hydraulik.params import parse_params
    from hydraulik.schema import json_schema
    probleme = []
    for kind, reg in (("hydraulik", COMPONENT_REGISTRY), ("air", AIR_REGISTRY)):
        defs = json_schema(kind)["$defs"]
        for tname, cls in reg.items():
            for p in cls.PARAMS:
                for key in p.accepted_keys():
                    for grenze in ("minimum", "maximum"):
                        wert = defs[f"type:{tname}"]["properties"][key].get(grenze)
                        if wert is None:
                            continue
                        _, errs = parse_params(tname, cls.PARAMS, {key: wert})
                        if any(f"'{key}'" in e for e in errs):
                            probleme.append(f"{tname}.{key} = {wert!r}")
    assert probleme == []


@pytest.mark.parametrize("layout", [5, None, [], "x", {"wires": {}}, {"components": []},
                                    {"components": {"pu": {"x": 1, "y": 2}}, "wires": []}])
def test_io14_schema_lehnt_kein_layout_ab_das_der_loader_liest(layout):
    jsonschema = pytest.importorskip("jsonschema")
    from hydraulik.schema import json_schema
    doc = {**parse_yaml(KREIS), "layout": layout}
    assert h.load(doc).solve().converged
    assert list(jsonschema.Draft202012Validator(json_schema()).iter_errors(doc)) == []


# --- IO-15: Zeichenvertrag yamlio ↔ yaml_core.js ---------------------------------------------

@pytest.mark.parametrize("text, code", [
    ("a: 1 # Kommentar b: 2\n", "U+2028"), ("a: x y\n", "U+2029"), ('a: "x\u0085y"\n', "U+0085"),
    ("# c\u0085d\na: 1\n", "U+0085"), ("a: 'x y'\n", "U+2028"), ("a: |\n  x \n", "U+2029"),
    ("a: x\x0cy\n", "U+000C"), ("a: x\x0by\n", "U+000B"), ("a: x\x7fy\n", "U+007F"),
    ("a: x\x9fy\n", "U+009F"), ("a: x\x00y\n", "U+0000"), ("a: \x1b[0m\n", "U+001B"),
])
def test_io15_rohe_steuerzeichen_sind_ueberall_ein_fehler(text, code):
    """ruamel las NEL/U+2028/U+2029 nach YAML 1.1 als Zeilenumbruch: ein
    Kommentar endete dort, in Zeichenketten wurde ein Leerzeichen daraus."""
    msg = _messages(parse_yaml, text)[0]
    assert f"Steuerzeichen {code}" in msg and "Zeile" in msg and "Escape" in msg


def test_io15_steuerzeichen_position_und_escapes():
    msgs = _messages(parse_yaml, "a: 1\r\nb: 2\rc: x \nd: \u0085\n")
    assert "Zeile 3, Spalte 5" in msgs[0] and "Zeile 4, Spalte 4" in msgs[1] and len(msgs) == 2
    # als Escape in doppelten Anführungszeichen bleiben sie schreibbar
    doc = parse_yaml('a: "x\\u0085y\\u2028z\\u2029\\x7f\\x0c"\n')
    assert doc == {"a": "x\u0085y z \x7f\x0c"}
    assert parse_yaml('a: "x\\Ny\\Lz\\P"\n') == {"a": "x\u0085y z "}


def test_io15_leerraum_ist_nur_leerzeichen_und_tab():
    """NBSP und ein U+FEFF mitten im Text sind gewöhnliche Zeichen."""
    assert parse_yaml("a: 1 \n") == {"a": "1 "}
    assert parse_yaml("{a: 1, b: 2}\n") == {"a": 1, " b": 2}
    assert parse_yaml("a: 1\n﻿b: 2\n") == {"a": 1, "﻿b": 2}
    assert parse_yaml("﻿a: 1\n") == {"a": 1}               # BOM am Anfang
    assert parse_yaml("a: 1\rb:\r\n  - x\n") == {"a": 1, "b": ["x"]}   # CR, CRLF, LF


@pytest.mark.parametrize("version", ["1.1", "1.0", "1.3", "2.0"])
def test_io15_yaml_direktive_ungleich_1_2_wird_abgelehnt(version):
    """%YAML 1.1 wurde still nach 1.2 gelesen (010 → 10 statt 8, yes → 'yes')."""
    msg = _messages(parse_yaml, f"# Kopf\n%YAML {version}\n---\na: yes\nb: 010\n")[0]
    assert f"'%YAML {version}'" in msg and "Zeile 2" in msg and "YAML 1.2" in msg


def test_io15_yaml_direktive_1_2_und_text_im_dokument():
    assert parse_yaml("%YAML 1.2\n---\na: yes\nb: 010\n") == {"a": "yes", "b": 10}
    # '%YAML 1.1' als Inhalt (nicht im Vorspann) ist keine Direktive
    assert parse_yaml('a: "%YAML 1.1"\nb: |\n  %YAML 1.1\n') == {"a": "%YAML 1.1", "b": "%YAML 1.1\n"}


def test_io15_json_text_als_yaml_liest_surrogatpaare_wie_json():
    text = json.dumps({"description": "Vorlauf 😀 𝑄", "😀": 1})    # ensure_ascii → 😀
    assert "\\ud83d\\ude00" in text
    doc = parse_yaml(text)
    assert doc == parse_json(text) == {"description": "Vorlauf 😀 𝑄", "😀": 1}
    doc["description"].encode("utf-8")                           # keine einzelnen Surrogate


@pytest.mark.parametrize("fn, text", [(parse_yaml, 'a: "x\\ud83dy"\n'), (parse_yaml, '"\\ude00": 1\n'),
                                      (parse_json, '{"a": "x\\ud83dy"}'), (parse_json, '{"\\ude00": 1}')])
def test_io15_einzelnes_surrogat_ist_ein_fehler(fn, text):
    assert "einzelne" in _messages(fn, text)[0] and "Surrogat" in _messages(fn, text)[0]


def test_io15_kanonisches_json_bleibt_als_yaml_text_lesbar():
    """canonical_json schrieb DEL/C1/U+2028 roh — als Text (YAML-Weg) wäre
    die eigene Ausgabe am Zeichenvertrag gescheitert."""
    doc = {**parse_yaml(KREIS), "layout": {"notiz": "a\u0085b c d\x7fe\x9ff äöü 😀"}}
    text = canonical_json(doc)
    assert "\\u0085" in text and "\\u2028" in text and "\\u2029" in text and "\\u007f" in text
    assert not yamlio._RE_FORBIDDEN.search(text) and "äöü 😀" in text
    assert parse_json(text) == doc == parse_yaml(text) == load_document(text)
