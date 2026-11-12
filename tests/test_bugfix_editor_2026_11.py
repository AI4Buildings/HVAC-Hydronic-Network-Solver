"""Regressionen der Bugfix-Runde 2026-11 für beide Schema-Editoren.

Die Editoren sind Single-File-Anwendungen ohne Modulgrenzen. Damit ihre
Zustandslogik (Import, Export, Kopieren, Vorlagen, Prüfliste, Leitungen) in
pytest prüfbar ist, läuft hier das VOLLSTÄNDIGE Skript des gerenderten Editors
unter node mit einem minimalen DOM-Ersatz (Elemente als Platzhalterobjekte,
localStorage als Map, fetch ohne Server → lokaler YAML-Parser). Was echte
Maus-/Tastatur-/Fokusereignisse braucht, prüft tools/e2e/bugfix_2026_11_e2e.py
(Playwright).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

import hydraulik as h
from hydraulik import editor as editor_mod
from hydraulik.air import load_air, solve_air
from hydraulik.editor import catalog_script_literal, render_air_editor, render_editor
from hydraulik.yamlio import parse_yaml

NODE = shutil.which("node") or "node"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node nicht installiert")

_STUB = r"""
"use strict";
const __els = new Map(), __alerts = [], __store = new Map();
function __el(id) {
  return {id, innerHTML: "", textContent: "", value: "", style: {}, dataset: {}, files: [],
    classList: {add(){}, remove(){}, toggle(){}, contains(){ return false; }},
    addEventListener(){}, setAttribute(){}, getAttribute(){ return null; }, click(){}, focus(){},
    blur(){}, showModal(){}, close(){}, contains(){ return false; }, scrollTo(){},
    querySelector(){ return __el("?"); }, querySelectorAll(){ return []; }, closest(){ return null; },
    getBoundingClientRect(){ return {left: 0, top: 0, width: 1000, height: 800}; },
    clientWidth: 1000, clientHeight: 800, scrollLeft: 0, scrollTop: 0, isConnected: true};
}
const document = {
  body: __el("body"),
  getElementById(id) { if (!__els.has(id)) __els.set(id, __el(id)); return __els.get(id); },
  querySelectorAll() { return []; }, querySelector() { return null; },
  createElement() { return __el("new"); },
};
document.activeElement = document.body;
class HTMLInputElement {} class HTMLSelectElement {} class HTMLTextAreaElement {}
const window = globalThis; window.addEventListener = () => {}; window.innerWidth = 1500;
const localStorage = {getItem: k => __store.has(k) ? __store.get(k) : null,
                      setItem: (k, v) => { __store.set(k, String(v)); }, removeItem: k => { __store.delete(k); }};
const alert = m => { __alerts.push(String(m)); };
const confirm = () => true, prompt = () => "Vorlage";
const fetch = () => Promise.reject(new Error("kein Server"));
const navigator = {};
const html = id => document.getElementById(id).innerHTML;
const select = name => { selection = {kind: "comp", ref: state.comps.find(c => c.name === name)}; render(); };
"""

_HTML = {}


def run(air: bool, js: str, storage: dict | None = None):
    """Editor-Skript + Testcode unter node; `js` ist der Rumpf einer async-Funktion."""
    if air not in _HTML:
        _HTML[air] = render_air_editor() if air else render_editor()
    script = "\n".join(re.findall(r"<script>(.*?)</script>", _HTML[air], re.S))
    pre = "".join(f"__store.set({json.dumps(k)}, {json.dumps(v)});\n" for k, v in (storage or {}).items())
    full = (f"{_STUB}\n{pre}\n{script}\n;(async () => {{ const out = await (async () => {{ {js} }})();\n"
            f"console.log(JSON.stringify(out === undefined ? null : out)); }})()"
            f".catch(e => {{ console.log(JSON.stringify({{__error: String(e && e.stack || e)}})); }});")
    proc = subprocess.run([NODE], input=full, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-3000:]
    out = json.loads(proc.stdout.rstrip("\n").split("\n")[-1])   # nicht splitlines(): U+2028/U+0085 im Text
    assert not (isinstance(out, dict) and "__error" in out), out
    return out


def roundtrip(air: bool, text: str) -> dict:
    """Import (lokaler Parser) → Export; liefert {yaml, doc, alerts}."""
    out = run(air, f"await importYAML({json.dumps(text)}); return {{yaml: exportYAML(), alerts: __alerts}};")
    out["doc"] = parse_yaml(out["yaml"])
    return out


def knoten(doc: dict) -> set[frozenset]:
    """Knoten des Dokuments: Mengen verschmolzener Port-Referenzen (Union-Find)."""
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for conn in doc.get("connections") or []:
        for ref in conn:
            parent[find(str(ref))] = find(str(conn[0]))
    groups: dict[str, set] = {}
    for ref in parent:
        groups.setdefault(find(ref), set()).add(ref)
    return {frozenset(g) for g in groups.values()}


EDITORS = pytest.mark.parametrize("air", [False, True], ids=["hydraulik", "lueftung"])


# --- reine Hilfsfunktionen ------------------------------------------------------------

@EDITORS
def test_esc_escapt_auch_attributwerte(air):
    out = run(air, """return [esc('<a href="x" on=\\'y\\'>&'), esc(null), esc(undefined), esc(0), esc(1.5)];""")
    assert out == ["&lt;a href=&quot;x&quot; on=&#39;y&#39;&gt;&amp;", "", "", "0", "1.5"]


@EDITORS
@pytest.mark.parametrize("text, wert", [
    ("1,5", 1.5), ("1.5", 1.5), (" 2 ", 2), ("-3", -3), ("+.5", 0.5), ("5.", 5), (",5", 0.5),
    ("1e3", 1000), ("1,5e-3", 0.0015), ("0,125", 0.125), ("1.234", 1.234), ("−2", -2),
    ("0", 0), ("1234,5", 1234.5), ("1,2345", 1.2345), ("1,23", 1.23),
    ("", "leer"), ("   ", "leer"),
    ("abc", "ungueltig"), ("1,234", "ungueltig"), ("12,345", "ungueltig"), ("-1,000", "ungueltig"),
    ("1 000", "ungueltig"), ("1.234,5", "ungueltig"), ("1,234.5", "ungueltig"),
    ("1,2,3", "ungueltig"), ("1e", "ungueltig"), ("0x10", "ungueltig"), ("--1", "ungueltig"),
    ("1e400", "inf"),
])
def test_parse_num_dezimalkomma_unabhaengig_von_der_browsersprache(air, text, wert):
    """'1,5' ist 1.5 (type=number verschluckte das Komma je nach Browser-Sprache → 15);
    mehrdeutige Tausender-Schreibweisen werden abgelehnt statt geraten."""
    out = run(air, f"""const v = parseNum({json.dumps(text)});
        return v === undefined ? "leer" : v === null ? "ungueltig" : v === Infinity ? "inf" : v;""")
    assert out == wert


# --- Befund 5: Escaping überall -------------------------------------------------------

EVIL_HYD = """\
fluid: {preset: water, t_C: '50"><img src=x onerror="window.__pwn=1">'}
settings: {max_iter: '"><img src=x onerror=1>'}
components:
  'p" onfocus="window.__pwn=1" x="': {type: pump, mode: constant_dp, dp_kPa: '"><img src=x>', ts: '<b>"x"', description: 'Pumpe "Nord" <b>', bems: [{id: 'i"<', key: "k'>", description: '"d"'}]}
  b: {type: cap}
connections:
  - ['p" onfocus="window.__pwn=1" x=".out', b.port]
layout:
  components:
    'p" onfocus="window.__pwn=1" x="': {x: '0)"><img src=x onerror=1>', y: 300, rot: '90"><img>'}
  wires:
    - {a: 'p" onfocus="window.__pwn=1" x=".out', b: b.port, color: 'n" onmouseover="window.__pwn=1', pts: [['1"><img>', 2], [40, 50]]}
"""
EVIL_AIR = EVIL_HYD.split("components:")[0].split("fluid:")[0] + """\
components:
  'p" onfocus="window.__pwn=1" x="': {type: vorheizer, ts: '<b>"x"', description: 'VHR "Nord" <b>', bems: [{id: 'i"<', key: "k'>", description: '"d"'}]}
  b: {type: kuehler}
connections:
  - ['p" onfocus="window.__pwn=1" x=".out', b.in]
layout:
  components:
    'p" onfocus="window.__pwn=1" x="': {x: '0)"><img src=x onerror=1>', y: 300, rot: '90"><img>'}
  wires:
    - {a: 'p" onfocus="window.__pwn=1" x=".out', b: b.in, color: 'n" onmouseover="window.__pwn=1', pts: [['1"><img>', 2], [40, 50]]}
"""


@EDITORS
def test_importierte_texte_landen_nie_als_markup_in_der_seite(air):
    """Namen, Beschreibungen, Parameterwerte, Fluid, Layout und Leitungsfarbe aus
    einer Datei werden überall escapt (vorher: Attribut-Injektion → Skript lief
    beim Import bzw. beim Klick ins Namensfeld, dauerhaft über das Autosave)."""
    out = run(air, f"""await importYAML({json.dumps(EVIL_AIR if air else EVIL_HYD)});
        const c = state.comps[0];
        const leer = [html("cv"), html("inspector")];
        select(c.name);
        const fluid = [html("cv"), html("inspector")];
        inspectorTab = "bems"; renderInspector();
        const bems = html("inspector");
        selection = {{kind: "wire", ref: state.wires[0]}}; inspectorTab = "fluid"; render();
        return {{seiten: [...leer, ...fluid, bems, html("cv"), html("inspector")],
                 geo: [c.x, c.y, c.rot, state.wires[0].pts], color: state.wires[0].color}};""")
    alles = "\n".join(out["seiten"])
    for roh in ("<img", "<b>", 'onfocus="', 'onmouseover="', "onerror=1>"):
        assert roh not in alles, roh
    # die Texte sind da — escapt, also als Text bzw. Attributwert
    assert 'value="p&quot; onfocus=&quot;window.__pwn=1&quot; x=&quot;"' in alles
    assert "&lt;b&gt;" in alles and "&quot;Nord&quot;" in alles and "k&#39;&gt;" in alles
    assert 'class="wire n&quot; onmouseover=&quot;window.__pwn=1' in alles
    # Geometrie: nur Zahlen (ungültige Angaben → Rasterplatz bzw. verworfen)
    x, y, rot, pts = out["geo"]
    assert isinstance(x, int) and y == 300 and rot == 0 and pts == [{"x": 40, "y": 50}]


def test_fluid_und_settings_werden_escapt_angezeigt():
    out = run(False, f"""await importYAML({json.dumps(EVIL_HYD)}); return html("inspector");""")
    assert "<img" not in out and "onerror=&quot;window.__pwn=1&quot;&gt;" in out
    assert 'id="settings-hint"' in out


@EDITORS
def test_beschreibung_mit_anfuehrungszeichen_bleibt_vollstaendig(air):
    """value="Pumpe "Nord"" schnitt den Text im Feld ab; Bearbeiten kürzte ihn dauerhaft."""
    out = roundtrip(air, EVIL_AIR if air else EVIL_HYD)
    spec = out["doc"]["components"]['p" onfocus="window.__pwn=1" x="']
    assert spec["description"].endswith('"Nord" <b>') and spec["ts"] == '<b>"x"'
    assert spec["bems"] == [{"id": 'i"<', "key": "k'>", "description": '"d"'}]


@EDITORS
def test_vorlagenname_mit_anfuehrungszeichen_in_der_palette(air):
    out = run(air, """addComp(Object.keys(TYPES)[0], 200, 200);
        userTemplates['Heizkreis "Nord" <1>'] = templateFromSelection();
        paletteTab = "tpl"; buildPalette(); return html("palette");""")
    assert 'data-tpl="Heizkreis &quot;Nord&quot; &lt;1&gt;"' in out
    assert 'data-del="Heizkreis &quot;Nord&quot; &lt;1&gt;"' in out


# --- Befund 2: Kopieren/Einfügen ---------------------------------------------------------

def test_einfuegen_kopiert_rohrabschnitte_und_bems_der_leitung():
    """Die Kopie einer Rohrmodell-Leitung verlor pipes/bems und war still ideal (Δp = 0)."""
    src = """\
fluid: {preset: water, t_C: 50}
components:
  src: {type: inflow, t_set_C: 60, q_m3h: 1.0}
  hk: {type: radiator, q_prescribed_kW: 5, kv_m3h: 2}
  lt1: {type: conduit, ts: "4", u_linear_W_mK: 0.2, t_amb_C: 15, pipes: [{length_m: 10, d_inner_mm: 26}, {length_m: 5, d_inner_mm: 20, zeta: 2}], bems: [{id: dp-1, key: K1}]}
  snk: {type: outflow, p_kPa: 0}
connections:
  - [src.port, lt1.in]
  - [lt1.out, hk.in]
  - [hk.out, snk.port]
"""
    out = run(False, f"""await importYAML({json.dumps(src)});
        selection = {{kind: "multi", ids: new Set(state.comps.filter(c => c.name === "src" || c.name === "hk").map(c => c.id))}};
        copySelection(); pasteClipboard();
        const [orig, kopie] = state.wires.filter(w => w.type === "conduit");
        kopie.pipes[0].length_m = 99;                       // tiefe Kopie, kein gemeinsames Objekt
        return {{yaml: exportYAML(), namen: state.wires.map(w => w.name || null), origLen: orig.pipes[0].length_m}};""")
    doc = parse_yaml(out["yaml"])
    orig, kopie = doc["components"]["lt1"], doc["components"]["lt2"]
    assert out["origLen"] == 10 and kopie["pipes"][0]["length_m"] == 99
    kopie["pipes"][0]["length_m"] = 10
    assert kopie == orig and kopie["bems"] == [{"id": "dp-1", "key": "K1"}] and kopie["ts"] == "4"
    assert len(kopie["pipes"]) == 2


# --- Befund 3: Import von Verbindungsgruppen mit Leitungen -------------------------------

GRUPPEN = """\
fluid: {preset: water, t_C: 50}
components:
  src: {type: inflow, t_set_C: 60, q_m3h: 2.0}
  lt1: {type: conduit, c_Pa_m3h2: 50}
  hk1: {type: radiator, q_prescribed_kW: 5, kv_m3h: 2}
  hk2: {type: radiator, q_prescribed_kW: 5, kv_m3h: 2}
  tf: {type: temperature_sensor}
  snk: {type: outflow, p_kPa: 0}
connections:
  - [src.port, lt1.in, hk2.in, tf.port]     # Abzweig + Fühler direkt am Leitungsanfang
  - [lt1.out, hk1.in]
  - [hk1.out, hk2.out, snk.port]
"""


def _ergebnis(doc):
    r = h.load(doc).solve()
    return {c["name"]: (round(c["q_m3h"], 6), round(c["t_out_C"], 4)) for c in r.to_dict()["components"]}


def test_gruppe_mit_leitungsende_behaelt_alle_mitglieder():
    """[src.port, lt1.in, hk2.in, tf.port]: früher blieb nur src.port–lt1.in,
    der Abzweig und der Fühler gingen ohne Meldung verloren."""
    out = roundtrip(False, GRUPPEN)
    assert out["alerts"] == []
    assert knoten(out["doc"]) == knoten(parse_yaml(GRUPPEN))
    assert set(out["doc"]["components"]) == set(parse_yaml(GRUPPEN)["components"])
    assert _ergebnis(out["doc"]) == _ergebnis(parse_yaml(GRUPPEN))
    # zweiter Durchlauf ändert nichts mehr
    assert roundtrip(False, out["yaml"])["doc"] == out["doc"]


def test_leitungsende_ueber_zwei_gruppen_verbunden():
    src = GRUPPEN.replace("  - [src.port, lt1.in, hk2.in, tf.port]     # Abzweig + Fühler direkt am Leitungsanfang\n",
                          "  - [src.port, lt1.in]\n  - [lt1.in, hk2.in]\n  - [tf.port, lt1.in]\n")
    assert knoten(parse_yaml(src)) == knoten(parse_yaml(GRUPPEN))
    out = roundtrip(False, src)
    assert out["alerts"] == [] and knoten(out["doc"]) == knoten(parse_yaml(src))
    assert _ergebnis(out["doc"]) == _ergebnis(parse_yaml(src))


def test_leitung_an_leitung_wird_gemeldet_und_der_rest_bleibt():
    """Zwei Leitungen in Reihe kann der Editor nicht zeichnen (jedes Ende hängt an
    einem Komponenten-Port): EINE Meldung mit Grund; der Abzweig der ersten Gruppe
    bleibt erhalten (früher ging er still verloren)."""
    src = """\
components:
  src: {type: inflow, t_set_C: 60, q_m3h: 2.0}
  lt1: {type: conduit, c_Pa_m3h2: 50}
  lt2: {type: conduit, c_Pa_m3h2: 70}
  hk1: {type: radiator, q_prescribed_kW: 5, kv_m3h: 2}
  hk2: {type: radiator, q_prescribed_kW: 5, kv_m3h: 2}
  snk: {type: outflow, p_kPa: 0}
connections:
  - [src.port, lt1.in, hk2.in]
  - [lt1.out, lt2.in]
  - [lt2.out, hk1.in]
  - [hk1.out, hk2.out, snk.port]
"""
    out = roundtrip(False, src)
    assert len(out["alerts"]) == 1
    msg = out["alerts"][0]
    assert "'lt1'" in msg and "'lt2'" in msg and "lt1.out hängt nur an lt2.in" in msg and "T-Stück" in msg
    assert frozenset({"src.port", "hk2.in"}) in knoten(out["doc"])
    assert frozenset({"hk1.out", "hk2.out", "snk.port"}) in knoten(out["doc"])
    assert "lt1" not in out["doc"]["components"] and "hk1" in out["doc"]["components"]


def test_editor_eigene_exporte_bleiben_unveraendert():
    """Paarweise Verbindungen (Format des Editors) laufen durch denselben Import."""
    from pathlib import Path
    root = Path(__file__).parent.parent
    for path in (root / "schaltung.yaml", root / "examples" / "09_energetikum_lueftungsregister.yaml",
                 root / "examples" / "05_umlenk_einspritz.yaml", root / "tests" / "data" / "alt_editor_export.yaml"):
        text = path.read_text(encoding="utf-8")
        out = roundtrip(False, text)
        ref = parse_yaml(text)
        assert out["alerts"] == [], path.name
        assert knoten(out["doc"]) == knoten(ref), path.name
        assert set(out["doc"]["components"]) == set(ref["components"]), path.name


# --- Befund 4: settings und Fluidname ---------------------------------------------------

def test_settings_und_fluidname_ueberleben_import_export():
    src = """\
fluid: {name: glykol 30 %, rho: 1040, mu: 3.5e-3, cp: 3600}
settings: {max_iter: 77, alpha_p: 0.3, p_ref: 300000, unbekannt: [1, "zwei"]}
components:
  pu: {type: pump, mode: constant_flow, q_m3h: 1}
  r: {type: flow_resistance, c_Pa_m3h2: 100}
connections:
  - [pu.out, r.in]
  - [r.out, pu.in]
"""
    out = roundtrip(False, src)
    ref = parse_yaml(src)
    assert out["doc"]["settings"] == ref["settings"]            # auch der unbekannte Schlüssel
    assert out["doc"]["fluid"] == ref["fluid"]
    # ohne den unbekannten Schlüssel rechenbar — und p_ref wirkt
    doc = parse_yaml(roundtrip(False, src.replace(', unbekannt: [1, "zwei"]', ""))["yaml"])
    assert h.load_settings(doc).max_iter == 77 and h.load_settings(doc).p_ref == 300000
    assert h.load(doc).fluid.name == "glykol 30 %"


def test_settings_bleiben_bei_undo_und_ohne_settings_fehlt_der_block():
    out = run(False, """await importYAML("settings: {max_iter: 77}\\ncomponents:\\n  a: {type: cap}\\nconnections: []\\n");
        addComp("cap", 300, 300); undo();
        const mit = exportYAML();
        await importYAML("components:\\n  a: {type: cap}\\nconnections: []\\n");
        return [mit, exportYAML()];""")
    assert parse_yaml(out[0])["settings"] == {"max_iter": 77}
    assert "settings" not in parse_yaml(out[1])


def test_settings_falscher_typ_wird_unveraendert_durchgereicht():
    out = roundtrip(False, "settings: [1, 2]\ncomponents:\n  a: {type: cap}\nconnections: []\n")
    assert out["doc"]["settings"] == [1, 2]                      # der Loader meldet es beim Rechnen
    with pytest.raises(h.NetworkValidationError):
        h.load_settings(out["doc"])


# --- Befund 6: BEMS als Mapping, ts: null, id: 0 ---------------------------------------

@EDITORS
def test_bems_mapping_ts_null_und_id_null_wie_im_loader(air):
    typ = "vorheizer" if air else "flow_resistance"
    extra = "" if air else ", c_Pa_m3h2: 100"
    src = f"""\
components:
  a: {{type: {typ}{extra}, ts: ~, bems: {{id: bacnet-4711, key: EXP_T_VL, description: Vorlauf}}}}
  b: {{type: {typ}{extra}, ts: 08, bems: [{{id: 0, key: K}}, {{}}, "kein Mapping"]}}
  c: {{type: {typ}{extra}, ts: 1.10, bems: [{{id: 1.5}}]}}
connections:
  - [a.out, b.in]
  - [b.out, c.in]
"""
    out = roundtrip(air, src)
    comps = out["doc"]["components"]
    assert "ts" not in comps["a"]                                # null = kein Label (nicht "null")
    assert comps["a"]["bems"] == [{"id": "bacnet-4711", "key": "EXP_T_VL", "description": "Vorlauf"}]
    assert comps["b"]["ts"] == "8"                               # Ganzzahl → Text, wie parse_label
    assert comps["b"]["bems"] == [{"id": "0", "key": "K"}, {}]   # id 0 bleibt, Nicht-Mapping entfällt
    # Kommazahlen sind als Label ein Fehler des Loaders — unverändert durchreichen
    assert comps["c"]["ts"] == 1.1 and comps["c"]["bems"] == [{"id": 1.5}]


def test_pipes_als_einzelnes_mapping_bleibt_rohrmodell():
    """pipes: {…} (ein Abschnitt als Mapping) akzeptiert der Loader; der Editor
    ließ es fallen — die Leitung wurde still ideal."""
    src = """\
components:
  src: {type: inflow, t_set_C: 60, q_m3h: 1.0}
  lt1: {type: conduit, pipes: {length_m: 10, d_inner_mm: 20}}
  snk: {type: outflow, p_kPa: 0}
connections:
  - [src.port, lt1.in]
  - [lt1.out, snk.port]
"""
    out = roundtrip(False, src)
    assert out["doc"]["components"]["lt1"]["pipes"] == [{"length_m": 10, "d_inner_mm": 20}]
    assert _ergebnis(out["doc"]) == _ergebnis(parse_yaml(src))


# --- Befund 9: Import von Nicht-Schaltungen ------------------------------------------------

@EDITORS
@pytest.mark.parametrize("text", ["", "   \n", "nur ein Satz", "- a\n- b\n", "a: 1\n", "components: [a, b]\n"])
def test_import_ohne_schaltung_laesst_die_zeichnung_stehen(air, text):
    """Statischer Editor: OK im leeren Import-Dialog leerte Zeichnung UND Autosave."""
    out = run(air, f"""addComp(Object.keys(TYPES)[0], 200, 200); addComp(Object.keys(TYPES)[1], 400, 200);
        const vorher = exportYAML(); let fehler = null;
        try {{ await importYAML({json.dumps(text)}); }} catch (e) {{ fehler = e.message; }}
        return {{fehler, gleich: exportYAML() === vorher, n: state.comps.length,
                 save: localStorage.getItem({json.dumps("hydraulik_luft_editor" if air else "hydraulik_editor")}) === vorher}};""")
    assert out["fehler"] and "nichts importiert" in out["fehler"]
    assert out["gleich"] and out["n"] == 2 and out["save"]


@EDITORS
def test_leere_schaltung_mit_components_abschnitt_ist_importierbar(air):
    out = run(air, """addComp(Object.keys(TYPES)[0], 200, 200);
        await importYAML("components: {}\\nconnections: []\\n"); return state.comps.length;""")
    assert out == 0


# --- Befund 10: Namen mit Punkt ---------------------------------------------------------------

def test_namen_mit_punkt_hydraulik():
    src = """\
fluid: {preset: water, t_C: 50}
components:
  "in 1.OG": {type: inflow, t_set_C: 60, q_m3h: 1.0}
  "HK 1.OG": {type: flow_resistance, c_Pa_m3h2: 100}
  "LT 1.2": {type: conduit, c_Pa_m3h2: 50}
  "v.2.out": {type: outflow, p_kPa: 0}
connections:
  - ["in 1.OG.port", "LT 1.2.in"]
  - ["LT 1.2.out", "HK 1.OG.in"]
  - ["HK 1.OG.out", "v.2.out.port"]
"""
    out = roundtrip(False, src)
    ref = parse_yaml(src)
    assert out["alerts"] == [] and out["doc"]["connections"] == ref["connections"]
    assert set(out["doc"]["components"]) == set(ref["components"])
    assert _ergebnis(out["doc"]) == _ergebnis(ref)               # Loader trennt am letzten Punkt
    refs = run(False, """return [parseRef("a.b.c"), parseRef("x"), parseRef(".in")];""")
    assert refs == [None, None, None]                            # unbekannte Komponenten


def test_namen_mit_punkt_lueftung():
    src = """\
components:
  "AUL 1.OG": {type: aussenluft, t_C: -5, rh: 80}
  "VHR 1.2": {type: vorheizer}
  "T.1": {type: temperatursensor_luft}
connections:
  - ["AUL 1.OG.out", "VHR 1.2.in"]
  - ["T.1.port", "AUL 1.OG.out"]
"""
    out = roundtrip(True, src)
    assert out["doc"]["connections"] == parse_yaml(src)["connections"]
    load_air(out["doc"])                 # der Luft-Loader trennt ebenfalls am letzten Punkt


# --- Befund 11: angefangene Leitung nach dem Löschen ---------------------------------------------

@EDITORS
def test_angefangene_leitung_wird_beim_loeschen_verworfen(air):
    """Port anklicken, Komponente löschen, zweiten Port anklicken: früher entstand eine
    Leitung ohne Komponente, jedes Neuzeichnen warf, der Export enthielt '?.out'."""
    out = run(air, """const a = addComp(Object.keys(TYPES)[0], 200, 200), b = addComp(Object.keys(TYPES)[1], 400, 200);
        const pa = portNames(a)[0], pb = portNames(b)[0];
        pendingPort = {comp: a.id, port: pa}; selection = {kind: "comp", ref: a};
        deleteSelection();
        const nachLoeschen = pendingPort;
        addWire({comp: a.id, port: pa}, {comp: b.id, port: pb});      // gelöschte Komponente
        render();
        const p = absPort(undefined, "out");
        pendingPort = {comp: b.id, port: pb};
        document.getElementById("btn-new").onclick();                 // „Neu" verwirft sie ebenfalls
        return {nachLoeschen, wires: state.wires.length, p, nachNeu: pendingPort, y: exportYAML()};""")
    assert out["nachLoeschen"] is None and out["wires"] == 0 and out["nachNeu"] is None
    assert out["p"] == {"x": 0, "y": 0} and "?" not in out["y"]


# --- Befund 13: Messleitung belegt den Kanal-Anschluss nicht (Lüftung) ----------------------------

def test_kanal_nach_messleitung_und_kettenregel():
    out = run(True, """const a = addComp("aussenluft", 200, 200), v = addComp("vorheizer", 400, 200),
              k = addComp("kuehler", 600, 200), s1 = addComp("temperatursensor_luft", 300, 100),
              s2 = addComp("feuchtesensor_luft", 340, 100);
        const P = (c, port) => ({comp: c.id, port});
        const n = [];
        addWire(P(s1, "port"), P(a, "out")); n.push(state.wires.length);      // Messleitung zuerst
        addWire(P(a, "out"), P(v, "in")); n.push(state.wires.length);         // … dann der Kanal
        addWire(P(a, "out"), P(k, "in")); n.push(state.wires.length);         // zweiter Kanal: Kette!
        addWire(P(s2, "port"), P(a, "out")); n.push(state.wires.length);      // zweiter Fühler am Port
        addWire(P(s1, "port"), P(v, "out")); n.push(state.wires.length);      // Sensoranschluss belegt
        const offen = validate().map(i => i.msg).filter(m => m.includes("unverbunden"));
        return {n, offen, y: exportYAML()};""")
    assert out["n"] == [1, 2, 2, 3, 3]
    assert "aul1.out ist unverbunden" not in out["offen"] and "kr1.in ist unverbunden" in out["offen"]
    doc = parse_yaml(out["y"])
    assert ["aul1.out", "vhr1.in"] in doc["connections"] and ["ts1.port", "aul1.out"] in doc["connections"]


def test_kanalanschluss_nur_mit_messleitung_gilt_als_unverbunden():
    out = run(True, """const a = addComp("aussenluft", 200, 200), s = addComp("temperatursensor_luft", 300, 100);
        addWire({comp: s.id, port: "port"}, {comp: a.id, port: "out"});
        return validate().map(i => i.msg);""")
    assert "aul1.out ist unverbunden" in out and "ts1.port ist unverbunden" not in out


# --- Befund 15: Export über YamlCore ---------------------------------------------------------------

@EDITORS
@pytest.mark.parametrize("text", ["A\u2028 B", "A\u0085B", "A\u007fB", "A\ufeffB", "A\u00a0", "x\u0000y",
                                  "08", "yes", "a: b #c", "zwei\nZeilen", "😀"])
def test_ts_und_bems_mit_sonderzeichen_bleiben_im_export_lesbar(air, text):
    """ts/BEMS liefen über JSON.stringify: U+007F lehnte der Server im eigenen
    Export des Editors ab, U+2028/U+0085 las er als Zeilenumbruch."""
    out = run(air, f"""const c = addComp(Object.keys(TYPES)[0], 200, 200);
        c.ts = {json.dumps(text)}; c.bems = [{{id: {json.dumps(text)}, description: {json.dumps(text)}}}];
        c.params[{json.dumps("schlüssel: mit # und , ")}] = 1;
        const y = exportYAML(); return {{y, js: YamlCore.parse(y).components[c.name]}};""")
    spec = next(iter(parse_yaml(out["y"])["components"].values()))
    for got in (spec, out["js"]):
        assert got["ts"] == text and got["bems"] == [{"id": text, "description": text}]
        assert got["schlüssel: mit # und , "] == 1


# --- Vorlagen ------------------------------------------------------------------------------------------

@EDITORS
def test_vorlage_mit_unbekanntem_typ_wird_abgelehnt_statt_die_zeichnung_zu_brechen(air):
    """Beide Editoren speicherten ihre Vorlagen als hydraulik_vorlagen.json; eine
    Vorlage des anderen Editors ließ render() bei jedem Aufruf werfen."""
    fremd = "pump" if air else "vorheizer"
    out = run(air, f"""addComp(Object.keys(TYPES)[0], 200, 200);
        const vorher = exportYAML(), hist = history.length;
        const tpl = {{comps: [{{type: {json.dumps(fremd)}, name: "x1", dx: 0, dy: 0, params: {{}}}}], wires: []}};
        const ok = insertTemplate(tpl, 600, 300);
        render();
        return {{ok, gleich: exportYAML() === vorher, hist: history.length === hist, alerts: __alerts,
                 probleme: [templateProblem(tpl), templateProblem({{comps: []}}), templateProblem(null),
                            templateProblem({{comps: [{{type: Object.keys(TYPES)[0]}}], wires: [{{a: [5, "x"], b: [0, "y"]}}]}}),
                            templateProblem({{comps: [{{type: "constructor"}}]}}),
                            templateProblem({{comps: [{{type: Object.keys(TYPES)[0]}}]}})]}};""")
    assert out["ok"] is False and out["gleich"] and out["hist"]
    assert len(out["alerts"]) == 1 and fremd in out["alerts"][0]
    p = out["probleme"]
    assert fremd in p[0] and p[1] and p[2] and "Endpunkt" in p[3] and "constructor" in p[4] and p[5] is None


@EDITORS
def test_mitgelieferte_vorlagen_sind_gueltig_und_einfuegbar(air):
    out = run(air, """const res = {};
        for (const n of Object.keys(BUILTIN_TEMPLATES)) {
          state.comps = []; state.wires = []; idSeq = 1;
          res[n] = [templateProblem(BUILTIN_TEMPLATES[n]), insertTemplate(deepClone(BUILTIN_TEMPLATES[n]), 800, 500),
                    state.comps.length, validate().filter(i => i.err && i.msg.includes("Port")).length];
        }
        return {res, alerts: __alerts};""")
    assert out["alerts"] == [] and len(out["res"]) >= 3
    for name, (problem, ok, n, portfehler) in out["res"].items():
        assert problem is None and ok is True and n > 0 and portfehler == 0, name


@EDITORS
@pytest.mark.parametrize("stored", ["null", "[1, 2]", "42", '"text"', "{kaputt",
                                    '{"constructor": {"comps": []}, "__proto__": {"comps": []}}'])
def test_kaputter_vorlagenspeicher_reisst_den_editor_nicht(air, stored):
    key = "hydraulik_luft_templates" if air else "hydraulik_templates"
    out = run(air, """paletteTab = "tpl"; buildPalette();
        return {namen: Object.keys(userTemplates), builtin: getTemplate(Object.keys(BUILTIN_TEMPLATES)[0]) !== null,
                fremd: getTemplate("toString"), n: html("palette").length};""", storage={key: stored})
    assert out["builtin"] and out["fremd"] is None and out["n"] > 0
    assert set(out["namen"]) <= {"constructor", "__proto__"}


def test_typ_constructor_ist_kein_komponententyp():
    out = roundtrip(False, "components:\n  a: {type: constructor}\n  b: {type: toString}\n  c: {type: cap}\nconnections: []\n")
    assert list(out["doc"]["components"]) == ["c"]
    assert len(out["alerts"]) == 2 and "Unbekannter Typ 'constructor'" in out["alerts"][0]


# --- Portanzahl verkleinert --------------------------------------------------------------------------------

@pytest.mark.parametrize("typ, port", [("manifold", "s3"), ("buffer_storage", "p3")])
def test_leitung_an_entferntem_port_steht_in_der_pruefliste(typ, port):
    out = run(False, f"""const m = addComp({json.dumps(typ)}, 300, 300); m.params.n_ports = 3;
        const c = addComp("cap", 500, 300);
        addWire({{comp: m.id, port: {json.dumps(port)}}}, {{comp: c.id, port: "port"}});
        const vorher = validate().filter(i => i.err).map(i => i.msg);
        m.params.n_ports = 2;
        const nachher = validate().filter(i => i.err);
        m.params.n_ports = 1e9; const viele = portNames(m).length;      // Zeichnung bleibt bedienbar
        return {{vorher, nachher, viele}};""")
    assert not any("Port" in m for m in out["vorher"])
    treffer = [i for i in out["nachher"] if f".{port}:" in i["msg"]]
    assert len(treffer) == 1 and "gibt es nicht" in treffer[0]["msg"] and "lt1" in treffer[0]["msg"]
    assert treffer[0]["comp"] == 1 and out["viele"] <= 100


# --- Luft: Hinweise des Rechenkerns sichtbar -------------------------------------------------------------------

def test_luft_ergebniskopf_zeigt_hinweise_escapt():
    out = run(True, """lastResult = {regelung: "band", zuluft: {t_C: 20}, leistungen: {},
                                     hinweise: ["<b>Hinweis</b> & mehr"]};
        return fluidForm();""")
    assert "Hinweis: &lt;b&gt;Hinweis&lt;/b&gt; &amp; mehr" in out and "<b>Hinweis" not in out


def test_luft_vorlage_rechnet_nach_editor_export():
    out = run(True, """insertTemplate(deepClone(BUILTIN_TEMPLATES[Object.keys(BUILTIN_TEMPLATES)[0]]), 800, 500);
        return exportYAML();""")
    assert solve_air(parse_yaml(out))["ok"] is True


# --- editor.py: Katalog im <script>-Block ---------------------------------------------------------------------------

def test_katalogtexte_koennen_den_script_block_nicht_beenden():
    cat = {"types": [{"help": "a</script><script>alert(1)</script> <!-- x --> /*__YAML_CORE_JS__*/ __CATALOG_JSON__"}]}
    lit = catalog_script_literal(cat)
    assert "</" not in lit and "<!--" not in lit
    proc = subprocess.run([NODE, "-e", f"process.stdout.write(JSON.stringify({lit}))"],
                          capture_output=True, text=True)
    assert proc.returncode == 0 and json.loads(proc.stdout) == cat      # gleicher Wert


def test_platzhalter_im_katalog_werden_nicht_erneut_ersetzt(monkeypatch, tmp_path):
    echt = editor_mod.component_catalog()
    echt["types"][0]["doc"] = "Text mit /*__YAML_CORE_JS__*/ und __CATALOG_JSON__ und </script>"
    monkeypatch.setattr(editor_mod, "component_catalog", lambda: echt)
    html = editor_mod.render_editor()
    assert html.count("const YamlCore = (() => {") == 1                 # Kern genau einmal eingesetzt
    assert html.count("</script>") == 1
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    f = tmp_path / "editor.js"
    f.write_text("\n".join(scripts), encoding="utf-8")
    assert subprocess.run([NODE, "--check", str(f)], capture_output=True).returncode == 0
    assert "Text mit /*__YAML_CORE_JS__*/ und __CATALOG_JSON__ und <\\/script>" in html
