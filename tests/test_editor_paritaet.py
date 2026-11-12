"""Gleiche YAML-Semantik in Editor (yaml_core.js) und Solver (yamlio).

Der Editor-Parser läuft per node; ohne node werden diese Tests übersprungen
(auf GitHub-Runnern ist node vorinstalliert). Geprüft wird:
- Parität: Korpus (alle Beispiele, Grenzfall-Datei, Block-Stil) und ein
  Zufallskorpus von Skalaren lesen in JS und Python identisch — oder beide
  melden einen Fehler. Was der Editor-Parser nicht kann, lehnt er ab.
- Round-Trip: Editor-Export → Python-Loader liefert exakt die Editorwerte,
  inkl. sehr kleiner/großer Zahlen und missverständlicher Strings.
- Alte Editor-Exporte (vor YAML 1.2) bleiben lesbar.
"""
from __future__ import annotations

import json
import math
import random
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import hydraulik as h
from hydraulik.editor import render_air_editor, render_editor, yaml_core_js
from hydraulik.exceptions import NetworkValidationError
from hydraulik.yamlio import parse_yaml

NODE = shutil.which("node") or "node"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node nicht installiert")

ROOT = Path(__file__).parent.parent
DATA = Path(__file__).parent / "data"
CORPUS = sorted(ROOT.glob("examples/*.yaml")) + [ROOT / "schaltung.yaml"] + sorted(DATA.glob("*.yaml"))

# JS-Werte → JSON ohne Informationsverlust (inf/nan als Marker)
_JS_ENC = """
const enc = v => JSON.stringify(v, (k, x) =>
  typeof x === "number" && !Number.isFinite(x) ? {"$num": String(x)} : x);
"""


def _node(script: str):
    # Skript über stdin (kein Längenlimit wie bei 'node -e')
    proc = subprocess.run([NODE], input=script, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _js_parse_many(texts: list[str]) -> list[dict]:
    """Jeden Text mit YamlCore.parse lesen → [{ok: wert} | {err: meldung}]."""
    script = yaml_core_js() + _JS_ENC + f"""
const texts = {json.dumps(texts)};
console.log(JSON.stringify(texts.map(t => {{
  try {{ return {{ok: JSON.parse(enc(YamlCore.parse(t)))}}; }}
  catch (e) {{ return {{err: e.message}}; }}
}})));"""
    return _node(script)


def _py_parse(text: str) -> dict:
    try:
        return {"ok": _enc_py(parse_yaml(text))}
    except NetworkValidationError as exc:
        return {"err": "; ".join(exc.messages)}


def _enc_py(v):
    if isinstance(v, float) and not math.isfinite(v):
        return {"$num": "NaN" if math.isnan(v) else ("Infinity" if v > 0 else "-Infinity")}
    if isinstance(v, dict):
        return {k: _enc_py(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_enc_py(x) for x in v]
    return v


def _same(a, b) -> bool:
    """Tiefer Vergleich: bool ≠ Zahl, int/float numerisch gleich, Schlüsselreihenfolge egal."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return type(a) is type(b) and a == b


def _parity(texts: list[str]) -> list[str]:
    """Abweichungen zwischen JS und Python (leer = Parität)."""
    bad = []
    for text, js in zip(texts, _js_parse_many(texts)):
        py = _py_parse(text)
        if "ok" in py and "ok" in js and _same(py["ok"], js["ok"]):
            continue
        if "err" in py and "err" in js:
            continue
        bad.append(f"{text!r}: python={py} js={js}")
    return bad


# --- Parität: Korpusdateien ------------------------------------------------------

@pytest.mark.parametrize("path", CORPUS, ids=lambda p: p.name)
def test_korpus_paritaet(path):
    text = path.read_text(encoding="utf-8")
    js = _js_parse_many([text])[0]
    assert "ok" in js, f"Editor-Parser lehnt {path.name} ab: {js}"
    assert _same(_enc_py(parse_yaml(text)), js["ok"])


def test_befund_tabelle_identisch_und_wie_yaml12():
    """Jede Zeile der Review-Tabelle: Editor und Solver gleich, Wert nach YAML 1.2."""
    expected = {"1.4e0": 1.4, "14e-1": 1.4, "1e3": 1000.0, "5e-7": 5e-7, "no": "no",
                "off": "off", "yes": "yes", "08": 8, "07": 7}
    texts = [f"v: {k}\n" for k in expected]
    for (k, exp), js in zip(expected.items(), _js_parse_many(texts)):
        py = parse_yaml(f"v: {k}\n")["v"]
        assert _same(py, exp) and _same(js["ok"]["v"], exp), (k, py, js)
    # Komponentennamen no/off: Schlüssel sind Strings — im Editor wie im Solver
    text = "components:\n  no: {type: cap}\n  off: {type: cap}\n"
    assert list(_js_parse_many([text])[0]["ok"]["components"]) == ["no", "off"]
    assert list(parse_yaml(text)["components"]) == ["no", "off"]


def test_zufallsskalare_paritaet():
    """Zufallskorpus aus zahl-/wortähnlichen Zeichenfolgen (fester Seed): die
    Typauflösung beider Parser ist deckungsgleich."""
    rng = random.Random(20261005)
    alphabet = "0123456789" * 3 + ".eE+-_xobXO" + "aflnrstuyNTFY~" + ": #,'\"[]{}"
    words = ["true", "True", "TRUE", "false", "null", "Null", "NULL", "yes", "no", "on",
             "off", ".inf", "-.Inf", ".NaN", "0o", "0x", "1e", "e1", "~"]
    samples = set()
    while len(samples) < 2500:
        if rng.random() < 0.15:
            s = rng.choice(words) + "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 3)))
        else:
            s = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 9)))
        samples.add(s)
    texts = []
    for s in sorted(samples):
        texts += [f"v: {s}\n", f"v: [{s}]\n", f"v: {{k: {s}}}\n"]
    assert _parity(texts) == []


_SCAL = ["1", "1.4e0", "5e-7", "08", "no", "yes", "true", "~", "null", "x", "a b", "qu1.port",
         "12:30", "'q'", '"d#x"', "'it''s'", "-1", ".inf", "Kreis #2", "a#b", "x:y", "1_000"]
_KEYS = ["a", "no", "08", "true", "'k k'", '"q"', "x-y", "-", "type", "p_kPa"]


def _random_doc(rng) -> str:
    """Zufallsdokument: verschachtelte Mappings/Listen in Block- und Flow-Stil,
    kompakte Formen ('- - x', '- a: 1'), Listen auf Schlüssel-Einrückung,
    Kommentare und Leerzeilen."""
    def val(d):
        r = rng.random()
        if d >= 3 or r < 0.5:
            return ("s", rng.choice(_SCAL))
        if r < 0.75:
            return ("m", [(k, val(d + 1)) for k in rng.sample(_KEYS, rng.randint(0, 3))])
        return ("l", [val(d + 1) for _ in range(rng.randint(0, 3))])

    def flow(v):
        t, x = v
        if t == "s":
            return f'"{x}"' if x.startswith(("Kreis", "a b")) else x
        if t == "m":
            return "{" + ", ".join(f"{k}: {flow(c)}" for k, c in x) + "}"
        return "[" + ", ".join(flow(c) for c in x) + "]"

    def block(v, ind, lines, prefix):
        t, x = v
        if t == "s" or not x or rng.random() < 0.3:
            lines.append(prefix + flow(v) + (" # c" if rng.random() < 0.2 else ""))
        elif t == "m":
            for n, (k, c) in enumerate(x):
                pre = prefix if n == 0 else " " * ind
                if c[0] == "s" or not c[1] or rng.random() < 0.3:
                    lines.append(f"{pre}{k}: {flow(c)}")
                    continue
                lines.append(f"{pre}{k}:")
                step = rng.choice([2, 4])
                if c[0] == "l" and rng.random() < 0.3:
                    block(c, ind, lines, " " * ind)
                else:
                    block(c, ind + step, lines, " " * (ind + step))
        else:
            for c in x:
                block(c, ind + 2, lines, " " * ind + "- ")

    lines: list[str] = []
    block(("m", [(k, val(1)) for k in rng.sample(_KEYS, rng.randint(1, 4))]), 0, lines, "")
    if rng.random() < 0.3:
        lines.insert(rng.randint(0, len(lines)), "# Kommentar")
    if rng.random() < 0.2:
        lines.insert(rng.randint(0, len(lines)), "")
    return "\n".join(lines) + "\n"


def test_zufallsdokumente_paritaet():
    """Zufallsdokumente (fester Seed) samt Ein-Zeichen-Mutationen: JS und Python
    lesen identisch, melden beide einen Fehler — oder der Editor-Parser lehnt
    ausdrücklich ab ('nicht unterstützt', z.B. mehrzeilige Werte)."""
    rng = random.Random(4711)
    texts = []
    for _ in range(1500):
        d = _random_doc(rng)
        texts.append(d)
        i = rng.randrange(len(d))
        ch = rng.choice(":-#[]{},'\" \nx1")
        texts.append(d[:i] + d[i + 1:] if rng.random() < 0.4 else d[:i] + ch + d[i:])
    bad = [b for b in _parity(texts)
           if not ("python={'ok'" in b and "js={'err'" in b and "nicht unterstützt" in b)]
    assert bad == []


@pytest.mark.parametrize("text", [
    "a: &x 1\n", "a: *x\n", "a: !!str 1\n", "a: |\n  text\n", "a: >\n  text\n",
    "a: \"zwei\n  zeilen\"\n", "a: b\n  c\n", "? a\n: b\n", "%YAML 1.2\n---\na: 1\n",
])
def test_nicht_unterstuetztes_klar_abgelehnt(text):
    """Der Editor-Parser liest nie still etwas anderes als Python: entweder
    gleiches Ergebnis oder eine Meldung mit Verweis auf den Server-Parser."""
    js = _js_parse_many([text])[0]
    assert "err" in js and "hydraulik serve" in js["err"], js


@pytest.mark.parametrize("text", [
    "a: 1\na: 2\n", "a: {x: 1, x: 2}\n", "components:\n  p: {q_m3h: 1,\n     q_m3h: 2}\n",
    "a: [1, , 2]\n", "a: {b\n", "a: 1\n---\nb: 2\n", "a:\tb\n", "\ta: 1\n", "a: b: c\n",
    "base: &b {x: 1}\nc:\n  <<: *b\n", "v: <<\n",
])
def test_fehler_in_beiden_parsern(text):
    py, js = _py_parse(text), _js_parse_many([text])[0]
    if text == "v: <<\n":                 # '<<' als WERT ist ein normaler String
        assert py == js == {"ok": {"v": "<<"}}
        return
    assert "err" in py and "err" in js, (py, js)


# --- Round-Trip: Editor-Export → Python ---------------------------------------------

#: Testzeichnung mit missverständlichen Namen/Werten; rechenbar (Zulauf → Ventil → Ablauf)
STATE_HYD = {
    "fluid": {"mode": "preset", "t_C": 45.5, "rho": 998, "mu": 0.001, "cp": 4180},
    "comps": [
        {"id": 1, "type": "inflow", "name": "no", "x": 120.5, "y": 240, "rot": 0, "ts": "08",
         "params": {"t_set_C": 60, "p_kPa": 1e1},
         "bems": [{"id": "FHP'A,B #1", "key": "yes", "description": "Vorlauf \"VL\" – Δϑ\nZeile 2"}]},
        {"id": 2, "type": "control_valve", "name": "off", "x": 280, "y": 2.4e2, "rot": 90,
         "ts": "1.10", "params": {"kvs_m3h": 10, "opening": 0.30000000000000004,
                                  "description": "Kreis #2: it's \"x\" – a: b"}},
        {"id": 3, "type": "outflow", "name": "true", "x": 1e-7, "y": 5e-324, "rot": 180,
         "params": {"p_kPa": 0}},
        {"id": 4, "type": "temperature_sensor", "name": "08", "x": 1e21, "y": -0.0, "rot": 0,
         "params": {"description": "yes"}},
        {"id": 5, "type": "flow_resistance", "name": "x y", "x": 1, "y": 1.7976931348623157e308,
         "rot": 0, "params": {"c_Pa_m3h2": 2.5e-3, "description": "Null"}},
        {"id": 6, "type": "flow_resistance", "name": "Null", "x": 3, "y": 4, "rot": 0,
         "params": {"c_Pa_m3h2": 123456789.123456789, "description": "1e3"}},
    ],
    "wires": [
        {"id": 10, "type": "conduit", "name": "lt1", "a": {"comp": 1, "port": "port"},
         "b": {"comp": 2, "port": "in"}, "color": "vl",
         "params": {"length_m": 12.5, "d_inner_mm": 26, "roughness_mm": 1.5e-6},
         "pts": [{"x": 1e-7, "y": 0.1}, {"x": -3.5, "y": 1e22}]},
        {"id": 11, "type": "conduit", "name": "on", "a": {"comp": 2, "port": "out"},
         "b": {"comp": 3, "port": "port"}, "color": "n", "params": {},
         "pipes": [{"length_m": 5, "d_inner_mm": 20, "zeta": 0.3},
                   {"length_m": 2.5e-1, "d_inner_mm": 1.5e1, "roughness_mm": 5e-7}]},
        {"id": 12, "type": "mess", "a": {"comp": 4, "port": "port"},
         "b": {"comp": 2, "port": "out"}, "color": "y"},
        {"id": 13, "type": "conduit", "name": "lt3", "a": {"comp": 5, "port": "out"},
         "b": {"comp": 6, "port": "in"}, "color": "off", "params": {}},
        {"id": 14, "type": "conduit", "name": "lt4", "a": {"comp": 6, "port": "out"},
         "b": {"comp": 5, "port": "in"}, "color": "rl", "params": {}},
    ],
}

STATE_AIR = {
    "fluid": {"mode": "preset", "t_C": 50},
    "comps": [
        {"id": 1, "type": "aussenluft", "name": "no", "x": 1e-7, "y": 10, "rot": 0, "ts": "07",
         "params": {"t_C": -1.2e1, "rh": 80}, "bems": [{"id": "on", "key": "08"}]},
        {"id": 2, "type": "wrg", "name": "off", "x": 0.1, "y": 0.2, "rot": 270,
         "params": {"adiab_exhaust": False, "description": "#1: yes"}},
    ],
    "wires": [
        {"id": 3, "a": {"comp": 1, "port": "out"}, "b": {"comp": 2, "port": "sup_in"},
         "color": "n", "pts": [{"x": 5e-7, "y": 1e21}]},
    ],
}


def _export_section(html: str) -> str:
    start = html.index("/* ---------- YAML Export")
    end = html.index("/* ---------- YAML Import")
    return html[start:end]


def _helpers_section(html: str) -> str:
    """Reine Hilfsfunktionen des Editors (esc, parseNum, labelIn/-Out, …) — ohne DOM."""
    start = html.index("/* ---------- Reine Hilfsfunktionen")
    end = html.index("/* ---------- Einheiten-Anzeige")
    return html[start:end]


def _run_export(html: str, state: dict, with_core: bool = True) -> str:
    script = (yaml_core_js() if with_core else "") + f"""
let state = {json.dumps(state)};
{_helpers_section(html)}
function compById(id){{ return state.comps.find(c => c.id === id); }}
function portRef(end){{ const c = compById(end.comp); return `${{c ? c.name : "?"}}.${{end.port}}`; }}
{_export_section(html)}
console.log(JSON.stringify(exportYAML()));"""
    return _node(script)


def _expected_doc(state: dict, air: bool) -> dict:
    """Was der Export inhaltlich enthalten MUSS (aus dem Editorzustand)."""
    names = {c["id"]: c["name"] for c in state["comps"]}
    ref = lambda end: f"{names[end['comp']]}.{end['port']}"   # noqa: E731
    comps, conns, wires = {}, [], []
    for c in state["comps"] + [w for w in state["wires"] if w.get("type") == "conduit"]:
        spec = {"type": c.get("type")} | dict(c.get("params", {}))
        if c.get("ts"):
            spec["ts"] = c["ts"]
        if c.get("bems"):
            spec["bems"] = c["bems"]
        if c.get("pipes"):
            spec["pipes"] = c["pipes"]
        comps[c["name"]] = spec
    for w in state["wires"]:
        if w.get("type") == "conduit":
            conns += [[ref(w["a"]), w["name"] + ".in"], [w["name"] + ".out", ref(w["b"])]]
        else:
            conns.append([ref(w["a"]), ref(w["b"])])
        wire = ({"name": w["name"]} if w.get("type") == "conduit" else {}) | {
            "a": ref(w["a"]), "b": ref(w["b"]), "color": w["color"]}
        if w.get("pts"):
            wire["pts"] = [[p["x"], p["y"]] for p in w["pts"]]
        wires.append(wire)
    lay = {c["name"]: {"x": c["x"], "y": c["y"]} | ({"rot": c["rot"]} if c["rot"] else {})
           for c in state["comps"]}
    doc = {"components": comps, "connections": conns,
           "layout": {"components": lay, "wires": wires}}
    if not air:
        doc = {"fluid": {"preset": "water", "t_C": state["fluid"]["t_C"]}} | doc
    return doc


@pytest.mark.parametrize("air", [False, True], ids=["hydraulik", "lueftung"])
def test_export_roundtrip_exakt(air):
    html = render_air_editor() if air else render_editor()
    state = STATE_AIR if air else STATE_HYD
    text = _run_export(html, state)
    py = parse_yaml(text)
    assert _same(py, _expected_doc(state, air)), text
    js = _js_parse_many([text])[0]
    assert "ok" in js and _same(_enc_py(py), js["ok"])
    # Zahlen bitgenau (repr-Gleichheit), auch 5e-324, 1.797e308, 0.30000000000000004
    if not air:
        assert py["layout"]["components"]["true"]["y"] == 5e-324
        assert py["layout"]["components"]["x y"]["y"] == 1.7976931348623157e308
        assert py["components"]["x y"]["c_Pa_m3h2"] == 2.5e-3
        assert py["components"]["off"]["opening"] == 0.30000000000000004
        assert py["components"]["on"]["pipes"][1]["roughness_mm"] == 5e-7
    # Exponentenschreibweise auch für YAML-1.1-Leser eindeutig (Punkt + Vorzeichen)
    for m in re.finditer(r"(?<![\w.\"'])(-?\d+(?:\.\d+)?)[eE]([-+]?)\d+", text):
        assert "." in m.group(1) and m.group(2), m.group(0)


@pytest.mark.parametrize("air", [False, True], ids=["hydraulik", "lueftung"])
def test_export_erfuellt_json_schema(air):
    """Was der Editor exportiert, ist schema-konform (JSON Schema aus der Registry)."""
    jsonschema = pytest.importorskip("jsonschema")
    from hydraulik.schema import json_schema
    text = _run_export(render_air_editor() if air else render_editor(), STATE_AIR if air else STATE_HYD)
    v = jsonschema.Draft202012Validator(json_schema("air" if air else "hydraulik"))
    assert [e.message for e in v.iter_errors(parse_yaml(text))] == []


def test_export_ist_rechenbar_und_werte_gleich():
    text = _run_export(render_editor(), STATE_HYD)
    doc = parse_yaml(text)
    # Sensor-Messleitung + Kurzschlussring lt3/lt4 sind rechenbar; Kreis no → off → true
    net = h.load(doc)
    assert set(net.components) >= {"no", "off", "true", "08", "x y", "Null", "lt1", "on"}
    assert net.components["no"].ts == "08" and net.components["off"].ts == "1.10"
    assert net.components["off"].description == "Kreis #2: it's \"x\" – a: b"
    assert net.components["no"].bems[0] == STATE_HYD["comps"][0]["bems"][0]
    assert net.components["lt1"].roughness == pytest.approx(1.5e-9, rel=1e-15)


@pytest.mark.parametrize("value", [
    0, -0.0, 1, -1, 0.1, 0.30000000000000004, 1 / 3, 5e-7, 1e-7, 1.5e-6, 1e-300, 5e-324,
    # 1.2345678901234568e20: früher als Python-int 123456789012345680000 notiert — der Editor
    # hält den double und schreibt ihn jetzt als Gleitkommazahl (Ganzzahlen > 2^53 lehnt der
    # Editor-Parser ab; die Ziffernfolge wäre eine ANDERE Ganzzahl als der double)
    2.2250738585072014e-308, 1e21, 1e22, 1.7976931348623157e308, -2.5e-10, 1.2345678901234568e20,
    2 ** 53, 1e16, 12345.678e-20, float("inf"), float("-inf"),
    True, False, None, "", " ", "yes", "no", "on", "off", "y", "n", "Y", "NO", "true", "True",
    "FALSE", "null", "Null", "~", "08", "07", "010", "1e3", "1.4e0", "0x10", "0o7", "1_000", "0b101",
    ".inf", "-.inf", ".nan", "inf", "nan", "2026-10-05", "12:30", "Kreis #2", "a: b", "- x", "[x]",
    "{x}", "it's", 'say "hi"', "Δp ϑ", "tab\there", "zeile\numbruch", "\x7f", "\x85", "\u2028",
    "\u2029", "\x00", "\x1b", "\x9f", "a\ufeffb", "\ufffe", "\uffff", "20\u00a0", "\u00a0x", "😀",
    2.0 ** 53 + 2, -(2.0 ** 60), 1.2345678901234567e19,
    " führend", "nachlaufend ", "#hash", "%pct", "@at", "`bt", "&amp", "*stern", "!bang", "|pipe",
    ">gt", "?frage", ",komma", "-", "--", "---", "...", "<<", "qu1.port", "lt_1-a", "_x", ".x",
    [1, "no", [5e-7, None]], {"a": "yes", "08": 1e21},
])
def test_scalar_export_roundtrip(value):
    """YamlCore.scalar(v) → Python und JS lesen exakt v zurück."""
    script = yaml_core_js() + _JS_ENC + f"""
const v = JSON.parse({json.dumps(json.dumps(_enc_py(value)))}, (k, x) =>
  x && typeof x === "object" && "$num" in x ? Number(x["$num"]) : x);
const text = "v: " + YamlCore.scalar(v) + "\\n";
console.log(JSON.stringify({{text, back: JSON.parse(enc(YamlCore.parse(text)))}}));"""
    out = _node(script)
    py = parse_yaml(out["text"])["v"]
    assert _same(_enc_py(py), _enc_py(value)), out["text"]
    back = out["back"]["v"]
    if isinstance(value, float) and isinstance(back, int) and not isinstance(back, bool):
        # JSON-Transport des Tests: JS schreibt einen ganzzahligen double ohne Exponent
        # (kürzeste Ziffernfolge desselben double) — als double vergleichen
        back = float(back)
    assert _same(back, _enc_py(value)), out["text"]
    if isinstance(value, float) and math.isfinite(value):
        # bitgenau (ganzzahlige Werte schreibt JS ohne Exponent → Python-int, gleicher Wert)
        assert float(py).hex() == value.hex() and math.copysign(1, py) == math.copysign(1, value)


def test_nan_export_roundtrip():
    out = _node(yaml_core_js() + 'console.log(JSON.stringify(YamlCore.scalar(NaN)));')
    assert out == ".nan" and math.isnan(parse_yaml(f"v: {out}")["v"])


# --- Zeichenvertrag beider Parser (Bugfix-Runde 2026-11) ----------------------------
# Leerraum ist nur das Leerzeichen; rohe Steuerzeichen sind überall ein Fehler;
# was der Editor-Parser nicht exakt wie yamlio liest, lehnt er ab.

@pytest.mark.parametrize("text, expected", [
    ("pu: {type: pump, dp_kPa: 20\u00a0}\n", {"pu": {"type": "pump", "dp_kPa": "20\u00a0"}}),
    ("pu: {\u00a0type: pump, dp_kPa: 20}\n", {"pu": {"\u00a0type": "pump", "dp_kPa": 20}}),
    ("c:\n  - [a.out,\u00a0b.in]\n", {"c": [["a.out", "\u00a0b.in"]]}),
    ("dp_kPa: 20\u00a0\n", {"dp_kPa": "20\u00a0"}),
    ("a:\n  \u00a0b: 1\n", {"a": {"\u00a0b": 1}}),
    ("a: [1,\u00a02]\n", {"a": [1, "\u00a02"]}),
    ("\u00a0\n", "\u00a0"),
    ("v: true\ufeff\n", {"v": "true\ufeff"}),
    ("a: \ufeff\n", {"a": "\ufeff"}),
    ("\ufeffa: 1\n", {"a": 1}),                       # BOM nur ganz am Anfang entfällt
    ("a: 1\u00a0# kein Kommentar\n", {"a": "1\u00a0# kein Kommentar"}),
], ids=lambda v: ascii(v)[:40] if isinstance(v, str) else "")
def test_nur_leerzeichen_sind_leerraum(text, expected):
    """U+00A0 und ein U+FEFF mitten im Text sind gewöhnliche Zeichen (früher
    schnitt .trim() im Editor sie ab: '20<NBSP>' wurde zur Zahl 20)."""
    py, js = _py_parse(text), _js_parse_many([text])[0]
    assert py == {"ok": expected}, py
    assert js == {"ok": expected}, js


_ROH = ["\x00", "\x01", "\x08", "\x0b", "\x0c", "\x1b", "\x7f", "\x80", "\x85", "\x9f",
        "\u2028", "\u2029", "\ufffe", "\uffff"]


@pytest.mark.parametrize("ch", _ROH, ids=lambda c: f"U+{ord(c):04X}")
def test_rohe_steuerzeichen_ueberall_abgelehnt(ch):
    texts = [t.replace("@", ch) for t in (
        "v: a@b\n", 'v: "a@b"\n', "v: 'a@b'\n", "v: 1 # k@k\n", "a@b: 1\n", "v: [a@, b]\n",
        "@\n", "# nur Kommentar @\nv: 1\n", "v: 1\n@")]
    for text, js in zip(texts, _js_parse_many(texts)):
        py = _py_parse(text)
        assert "err" in py and "err" in js, (text, py, js)
        assert f"U+{ord(ch):04X}" in js["err"] and "Escape" in js["err"], js


def test_escapes_in_doppelten_anfuehrungszeichen_bleiben_erlaubt():
    text = 'v: "\\x85 \\u2028 \\x7f \\t \\u00a0 \\ud83d\\ude00 \\U0001F600"\n'
    expected = {"ok": {"v": "\x85 \u2028 \x7f \t \u00a0 😀 😀"}}
    assert _py_parse(text) == expected and _js_parse_many([text])[0] == expected


@pytest.mark.parametrize("text", ['v: "\\ud800"\n', 'v: "a\\ude00\\ud83d"\n', 'v: "\\ud83d x \\ude00"\n'])
def test_einzelnes_surrogat_als_escape_abgelehnt(text):
    py, js = _py_parse(text), _js_parse_many([text])[0]
    assert "err" in py and "err" in js and "Surrogat" in js["err"], (py, js)


@pytest.mark.parametrize("text", [
    "v: 9007199254740992\n", "v: -9007199254740993\n", "v: 123456789012345678901234567890\n",
    "v: 0x20000000000000\n", "v: 0o777777777777777777777\n", "v: [1, 9007199254740993]\n",
    "v: {k: +9007199254740992}\n", "bems: [{id: 12345678901234567890}]\n",
])
def test_ganzzahlen_jenseits_2_hoch_53_lehnt_der_editor_ab(text):
    """JavaScript rechnet mit double: 9007199254740993 würde als …992 gelesen.
    Der Editor-Parser lehnt ab, statt einen anderen Wert zu lesen als yamlio."""
    py, js = _py_parse(text), _js_parse_many([text])[0]
    assert "ok" in py
    assert "err" in js and "2^53" in js["err"] and "Zeichenkette" in js["err"], js


@pytest.mark.parametrize("text, expected", [
    ("v: 9007199254740991\n", {"v": 9007199254740991}),
    ("v: -9007199254740991\n", {"v": -9007199254740991}),
    ("v: 9007199254740992.0\n", {"v": 9007199254740992.0}),
    ("v: 1.0e+20\n", {"v": 1e20}),
    ('v: "12345678901234567890"\n', {"v": "12345678901234567890"}),
    ("12345678901234567890: 1\n", {"12345678901234567890": 1}),       # Schlüssel bleibt Text
])
def test_grosse_zahlen_die_exakt_bleiben(text, expected):
    py, js = _py_parse(text), _js_parse_many([text])[0]
    assert "ok" in py and "ok" in js and _same(py["ok"], expected) and _same(js["ok"], expected)


def test_ganzzahliger_double_jenseits_2_hoch_53_wird_als_gleitkommazahl_geschrieben():
    out = _node(yaml_core_js() + "console.log(JSON.stringify([2**53, 2**53 + 2, -(2**60), 1e21, "
                                 "2**53 - 1].map(YamlCore.number)));")
    assert out == ["9.007199254740992e+15", "9.007199254740994e+15", "-1.152921504606847e+18",
                   "1.0e+21", "9007199254740991"]
    for text in out:                      # beide Parser lesen den Export des Editors
        assert parse_yaml(f"v: {text}")["v"] == float(text)
        assert "ok" in _js_parse_many([f"v: {text}\n"])[0]


@pytest.mark.parametrize("text", [
    "... x: 1\n",                                 # Dokumentende am Zeilenanfang
    "v: [a,\n... b]\n", "v: [a,\n--- b]\n",     # Dokumentmarke in offener Klammer
    "- [a #x, b]\n- c, d]\n",                    # ']' steht im Kommentar
    "v: [a #x\n  b]\n", "v: [a\n  #x\n  b]\n", "v: [a b #x\n  c, d]\n",
    "v: [a: 1 #x\n  b]\n", "v: {a: 1 # c }\nb: 2}\n",
    "connections:\n  - [pu.out, hk.in  # Vorlauf]\n  - [hk.out, pu.in]\n",
])
def test_kommentar_beendet_skalar_fehler_in_beiden_parsern(text):
    """Früher fügte der Editor-Parser über den Kommentar hinweg zusammen
    ('- [a #x, b]\\n- c, d]' → [['a - c', 'd']]); yamlio meldet einen Fehler."""
    py, js = _py_parse(text), _js_parse_many([text])[0]
    assert "err" in py and "err" in js, (py, js)


@pytest.mark.parametrize("text, wert", [
    ("v: [a\n\n  b]\n", {"v": ["a\nb"]}),       # Leerzeile im Skalar: yamlio faltet zu \n
    ("v: [a\n  - b]\n", {"v": ["a - b"]}),       # Fortsetzung beginnt mit '- '
    ("- [a\n  - b]\n", [["a - b"]]),
])
def test_editor_lehnt_ab_was_er_nicht_wie_yamlio_liest(text, wert):
    py, js = _py_parse(text), _js_parse_many([text])[0]
    assert py == {"ok": wert}
    assert "err" in js, js


@pytest.mark.parametrize("text, wert", [
    ("v: [a, # x\n  b]\n", {"v": ["a", "b"]}),
    ('v: ["a" #x\n  , b]\n', {"v": ["a", "b"]}),
    ("v: [a #x\n  , b]\n", {"v": ["a", "b"]}),
    ("v: [a #x\n]\n", {"v": ["a"]}),
    ("v: {a: 1, # x\n\n  b: 2}\n", {"v": {"a": 1, "b": 2}}),
    ("v: [a\n  b]\n", {"v": ["a b"]}),            # mehrzeiliger Skalar ohne Kommentar: gefaltet
    (" ... x: 1\n", {"... x": 1}),                # eingerückt: gewöhnlicher Text
    ("...x: 1\n", {"...x": 1}),
])
def test_kommentare_und_leerzeilen_zwischen_flow_eintraegen_bleiben_lesbar(text, wert):
    assert _py_parse(text) == {"ok": wert} and _js_parse_many([text])[0] == {"ok": wert}


def test_zufall_mit_sonderzeichen_nie_still_anders():
    """Zufallskorpus mit U+00A0, BOM, NEL, U+2028, DEL, VT …: der Editor-Parser
    liest wie yamlio oder lehnt ab — nie ein anderer Wert, nie 'Editor liest,
    yamlio lehnt ab'."""
    rng = random.Random(20261112)
    alphabet = list("0123456789.eE+-_x: #,'\"[]{}aTn~") + [
        "\u00a0", "\ufeff", "\x85", "\u2028", "\u2029", "\x7f", "\x0b", "\x0c", "\x1b", "ä", "😀",
        ": ", " #", "- ", "true", "null", "1e3", ".inf", "...", "---"]
    ctx = ["v: @\n", "v: [@]\n", "v: {k: @}\n", "@: 1\n", "- @\n", "@\n", "a:\n  @\n",
           "v: [a,\n  @]\n", "v: @ # c\n", "a: 1\n@\nb: 2\n", 'v: "@"\n', "v: '@'\n"]
    texts = sorted({rng.choice(ctx).replace("@", "".join(rng.choice(alphabet)
                                                         for _ in range(rng.randint(1, 6))))
                    for _ in range(2500)})
    bad = []
    for text, js in zip(texts, _js_parse_many(texts)):
        py = _py_parse(text)
        if "ok" in js and not ("ok" in py and _same(py["ok"], js["ok"])):
            bad.append((text, py, js))
    assert bad == []


# --- Alte Editor-Exporte (Format vor YAML 1.2) bleiben lesbar ------------------------

def test_alter_editor_export_bleibt_lesbar():
    """Exportformat bis v0.6.0 (unquotierte Namen, color: n, JSON-Zahlen):
    neuer Editor-Parser (localStorage-Autosave!) und Python-Loader lesen gleich."""
    old = (DATA / "alt_editor_export.yaml").read_text(encoding="utf-8")
    js = _js_parse_many([old])[0]
    assert "ok" in js
    assert _same(_enc_py(parse_yaml(old)), js["ok"])
    net = h.load(old)
    assert net.solve().converged


# --- gerenderte Editoren: Skript syntaktisch gültig ----------------------------------

@pytest.mark.parametrize("render", [render_editor, render_air_editor], ids=["hydraulik", "lueftung"])
def test_gerendertes_skript_syntaktisch_gueltig(render, tmp_path):
    scripts = re.findall(r"<script>(.*?)</script>", render(), re.S)
    f = tmp_path / "editor.js"
    f.write_text("\n".join(scripts), encoding="utf-8")
    proc = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "const YamlCore" in scripts[0]
