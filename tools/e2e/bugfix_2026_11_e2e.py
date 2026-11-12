"""Browser-Regressionen der Bugfix-Runde 2026-11 (Chromium/Playwright), beide
Editoren: alles, was echte Maus-, Tastatur- und Fokusereignisse braucht und
deshalb nicht in pytest (tests/test_bugfix_editor_2026_11.py, node) prüfbar ist.

    python3 tools/e2e/bugfix_2026_11_e2e.py        # endet mit "ERGEBNIS: alles ok"
"""
import json, sys, tempfile, threading
from pathlib import Path
from playwright.sync_api import sync_playwright
from hydraulik.server import make_server, MAX_BODY_BYTES
from hydraulik.editor import build_editor, build_air_editor
from hydraulik.yamlio import parse_yaml

fails = []
def check(cond, msg):
    print(("  ok   " if cond else "  FEHLER ") + msg)
    if not cond: fails.append(msg)

S = Path(tempfile.mkdtemp(prefix="hydraulik_bugfix_e2e_"))
srv = make_server(0); PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
STATIC = {False: build_editor(S / "hyd.html").as_uri(), True: build_air_editor(S / "luft.html").as_uri()}
URL = {False: f"http://127.0.0.1:{PORT}/hydraulik", True: f"http://127.0.0.1:{PORT}/lueftung"}
SAVE = {False: "hydraulik_editor", True: "hydraulik_luft_editor"}
T2 = {False: ("pump", "radiator"), True: ("vorheizer", "kuehler")}      # zwei Zweitor-Typen je Editor

EVIL = {False: """\
fluid: {preset: water, t_C: '50"><img src=x onerror="window.__pwn=1">'}
components:
  'p" autofocus onfocus="window.__pwn=2" x="': {type: pump, mode: constant_dp, dp_kPa: '"><img src=x onerror="window.__pwn=3">', description: 'Pumpe "Nord" <b>'}
  b: {type: cap}
connections:
  - ['p" autofocus onfocus="window.__pwn=2" x=".out', b.port]
layout:
  components:
    'p" autofocus onfocus="window.__pwn=2" x="': {x: 200, y: 200}
    b: {x: 400, y: 200}
  wires:
    - {a: 'p" autofocus onfocus="window.__pwn=2" x=".out', b: b.port, color: 'n" onmouseover="window.__pwn=4" style="pointer-events:all'}
""", True: """\
components:
  'p" autofocus onfocus="window.__pwn=2" x="': {type: vorheizer, description: 'VHR "Nord" <b>', bems: [{id: '"><img src=x onerror="window.__pwn=3">'}]}
  b: {type: kuehler}
connections:
  - ['p" autofocus onfocus="window.__pwn=2" x=".out', b.in]
layout:
  components:
    'p" autofocus onfocus="window.__pwn=2" x="': {x: 200, y: 200}
    b: {x: 400, y: 200}
  wires:
    - {a: 'p" autofocus onfocus="window.__pwn=2" x=".out', b: b.in, color: 'n" onmouseover="window.__pwn=4" style="pointer-events:all'}
"""}

def xy(page, x, y):                     # Modell- → Bildschirmkoordinaten (Zoom 1)
    r = page.evaluate("(() => { const r = document.getElementById('cv').getBoundingClientRect(); return [r.left, r.top]; })()")
    return r[0] + x, r[1] + y

def fresh(page):
    page.evaluate("localStorage.clear()"); page.reload(); page.wait_for_load_state("load")

def labels(page):                       # Ergebnis-Beschriftungen und Strömungspfeile auf der Zeichenfläche
    return page.evaluate("""[document.querySelectorAll('#cv text[fill="#0b7a3e"]').length,
                             document.querySelectorAll('#cv .wire-arrow').length]""")

with sync_playwright() as p:
    browser = p.chromium.launch()
    for air in (False, True):
        name = "Lüftung" if air else "Hydraulik"
        print(f"== {name} (Server)")
        ctx = browser.new_context(viewport={"width": 1500, "height": 900}); page = ctx.new_page()
        errors, dialogs = [], []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
        page.goto(URL[air]); page.wait_for_load_state("load"); fresh(page)
        ta, tb = T2[air]

        # --- Befund 1: Auswahlwechsel bei fokussiertem Eingabefeld ------------------------
        page.evaluate(f"addComp('{ta}', 200, 200); addComp('{tb}', 400, 200); selection = null; render();")
        na, nb = page.evaluate("state.comps.map(c => c.name)")
        page.mouse.click(*xy(page, 200, 200))
        page.click("#c-name"); page.keyboard.press("End"); page.keyboard.type("X")       # Fokus bleibt im Feld
        x, y = xy(page, 400, 200)
        page.mouse.move(x, y); page.mouse.down(); page.mouse.up()                      # Klick ohne Mausbewegung
        check(page.evaluate("selection.ref.name") == nb and page.input_value("#c-name") == nb,
              "Klick auf andere Komponente bei fokussiertem Feld: Inspector zeigt die neue Auswahl")
        check(page.evaluate("state.comps.map(c => c.name)") == [na + "X", nb],
              "die angefangene Eingabe wurde noch der bisherigen Komponente zugeordnet")
        page.fill("#c-name", "NEU"); page.keyboard.press("Tab")
        check(page.evaluate("state.comps.map(c => c.name)") == [na + "X", "NEU"],
              "Eingabe im sichtbaren Formular trifft die ausgewählte Komponente")
        # … ebenso beim Klick auf eine Leitung
        page.evaluate("addWire({comp: state.comps[0].id, port: 'out'}, {comp: state.comps[1].id, port: 'in'}); render();")
        page.mouse.click(*xy(page, 200, 200)); page.click("#c-name"); page.keyboard.type("Y")
        w = page.evaluate("(() => { const q = wirePts(state.wires[0]); return [(q[0].x+q[1].x)/2, (q[0].y+q[1].y)/2]; })()")
        x, y = xy(page, *w); page.mouse.move(x, y); page.mouse.down(); page.mouse.up()
        check(page.evaluate("selection.kind") == "wire" and page.locator("#w-color").count() == 1,
              "Klick auf eine Leitung bei fokussiertem Feld: Inspector zeigt die Leitung")

        # --- Befund 11/12: angefangene Leitung + Löschen, Strg/Cmd+R ------------------------
        fresh(page)
        page.evaluate(f"addComp('{ta}', 200, 200); addComp('{tb}', 400, 200); selection = null; render();")
        page.mouse.click(*xy(page, 200, 200))
        page.mouse.click(*xy(page, 230, 200))                                          # Port out (erster Klick)
        check(page.evaluate("pendingPort && pendingPort.port") == "out", "erster Port-Klick merkt den Port")
        page.keyboard.press("Delete")
        page.mouse.click(*xy(page, 370, 200))                                          # Port in der anderen Komponente
        page.mouse.click(*xy(page, 700, 500))
        check(page.evaluate("state.wires.length") == 0 and "?" not in page.evaluate("exportYAML()") and not errors,
              f"Port anklicken → Komponente löschen → Port anklicken: keine Leitung ins Leere ({errors[:1]})")
        page.mouse.click(*xy(page, 400, 200))
        for combo in ("Control+r", "Meta+r", "Control+Shift+R"):
            page.evaluate(f"window.dispatchEvent(new KeyboardEvent('keydown', {{key: '{combo[-1]}', ctrlKey: {str('Control' in combo).lower()}, metaKey: {str('Meta' in combo).lower()}, shiftKey: {str('Shift' in combo).lower()}, bubbles: true}}))")
        rot_mod = page.evaluate("state.comps[0].rot")
        page.keyboard.press("r")
        check(rot_mod == 0 and page.evaluate("state.comps[0].rot") == 90,
              "Strg/Cmd+R dreht nicht (Seite neu laden), die blanke Taste r dreht um 90°")

        # --- Befund 5: Escaping (echter Browser: es läuft kein Skript) -------------------------
        fresh(page)
        issues = page.evaluate("async t => await importYAML(t)", EVIL[air])
        evil = page.evaluate("state.comps[0].name")
        page.wait_for_timeout(200)
        page.mouse.click(*xy(page, 200, 200)); page.click("#c-name")
        page.mouse.move(*xy(page, 300, 200)); page.wait_for_timeout(100)               # über die Leitung
        # Registerwechsel OHNE Fokuswechsel (Safari/Firefox auf macOS geben Buttons keinen Fokus):
        page.click("#c-name")
        page.evaluate("document.querySelector('#inspector .tab[data-tab=bems]').click()")
        check(page.locator("#bems-add").count() == 1, "Register BEMS-Info öffnet auch bei fokussiertem Eingabefeld")
        desc = page.input_value("input[data-param=description]")
        page.evaluate("document.querySelector('#inspector .tab[data-tab=fluid]').click()")
        page.reload(); page.wait_for_load_state("load"); page.wait_for_timeout(200)     # Autosave-Restore
        check(page.evaluate("window.__pwn") is None and page.evaluate("state.comps[0].name") == evil,
              "bösartige Datei: kein Skript läuft (Import, Klick ins Namensfeld, Mouse-Over, Reload)")
        check(desc.endswith('"Nord" <b>'), f"Beschreibung mit Anführungszeichen steht vollständig im Feld ({desc!r})")
        check(page.evaluate("document.querySelectorAll('#inspector img, #cv img, #palette img').length") == 0,
              "kein eingeschleustes Element im Dokument")
        # Vorlage mit Anführungszeichen im Namen: einfügbar und löschbar
        fresh(page)
        page.evaluate(f"""(() => {{ addComp('{ta}', 200, 200); userTemplates['Kreis "Nord" <1>'] = templateFromSelection();
                          persistTemplates(); paletteTab = 'tpl'; buildPalette(); }})()""")
        page.click("button.pal-item[data-tpl^='Kreis']"); page.mouse.click(*xy(page, 600, 300))
        check(page.evaluate("state.comps.length") == 2 and "eingefügt" in page.inner_text("#status"),
              "Vorlage mit \" im Namen lässt sich einfügen")
        page.click("button.tpl-del")
        check(page.evaluate("Object.keys(userTemplates).length") == 0, "… und löschen")
        # Vorlagen-Datei des anderen Editors: übersprungen, nichts bricht
        fremd = {"Fremd": {"comps": [{"type": T2[not air][0], "name": "x1", "dx": 0, "dy": 0, "params": {}}], "wires": []},
                 "Gut": {"comps": [{"type": ta, "name": "g1", "dx": 0, "dy": 0, "params": {}}], "wires": []}}
        f = S / "vorlagen.json"; f.write_text(json.dumps(fremd), encoding="utf-8")
        page.set_input_files("#tpl-fileinput", str(f)); page.wait_for_timeout(300)
        st = page.inner_text("#status")
        check(page.evaluate("Object.keys(userTemplates)") == ["Gut"] and "Fremd" in st and "1 Vorlage" in st,
              f"Vorlagen-Import prüft die Komponententypen ({st[:90]})")
        with page.expect_download() as dl:
            page.click("#tpl-export")
        check(dl.value.suggested_filename == ("lueftung_vorlagen.json" if air else "hydraulik_vorlagen.json"),
              f"Vorlagen-Datei heißt je Editor verschieden ({dl.value.suggested_filename})")

        # --- Dezimalkomma unabhängig von der Browser-Sprache (hier: en-US) --------------------------
        fresh(page)
        if air:
            page.evaluate("(() => { const c = addComp('aussenluft', 300, 300); c.params = {t_C: 4, rh: 80}; commit(); render(); })()")
            sel, key = "input[data-param=t][data-kind=unit]", "t_C"
        else:
            page.evaluate("(() => { const c = addComp('control_valve', 300, 300); c.params = {kvs_m3h: 4, opening: 0.5}; commit(); render(); })()")
            sel, key = "input[data-param=kvs][data-kind=unit]", "kvs_m3h"
        page.mouse.click(*xy(page, 300, 300))
        got = {}
        for typed in ("1,5", "2.5", "abc", "1,234", "-0,5e1", ""):
            page.evaluate(f"(() => {{ state.comps[0].params.{key} = 4; document.activeElement.blur(); inspectorForce = true; render(); inspectorForce = false; }})()")
            page.click(sel); page.keyboard.press("ControlOrMeta+a")
            page.keyboard.type(typed) if typed else page.keyboard.press("Backspace")
            page.keyboard.press("Tab")
            got[typed] = (page.evaluate(f"state.comps[0].params.{key}"), page.evaluate(f"document.querySelector('{sel}').classList.contains('bad')"))
        check(got["1,5"] == (1.5, False) and got["2.5"] == (2.5, False) and got["-0,5e1"] == (-5, False),
              f"getippt 1,5 → 1.5 (nicht 15), 2.5 → 2.5, -0,5e1 → -5 ({got['1,5']}, {got['2.5']}, {got['-0,5e1']})")
        check(got["abc"] == (4, True) and got["1,234"] == (4, True),
              f"'abc' und das mehrdeutige '1,234' ändern den Wert nicht und markieren das Feld ({got['abc']}, {got['1,234']})")
        check(got[""] == (None, False), "leeres Feld entfernt den Parameter")

        # --- Befund 9: Import ohne Schaltung (Server) ---------------------------------------------------
        dialogs.clear()
        n0 = page.evaluate("state.comps.length")
        page.click("#btn-import"); page.fill("#dlg-text", ""); page.click("#dlg-ok"); page.wait_for_timeout(400)
        check(page.evaluate("state.comps.length") == n0 and any("Import fehlgeschlagen" in d for d in dialogs),
              "OK im leeren Import-Dialog: Meldung, Zeichnung bleibt")
        # Server-Grenze 5 MB: verständliche Meldung statt stillem Rückfall auf den lokalen Parser
        dialogs.clear()
        try:
            page.evaluate("async n => await importYAML('components: {}\\n#' + 'x'.repeat(n))", MAX_BODY_BYTES)
            big = "kein Fehler"
        except Exception as e:
            big = str(e)
        check("zu groß" in big, f"Import über 5 MB: Fehlermeldung des Servers ({big[:70]})")

        # --- Befund 10: Namen mit Punkt rechnen im GUI ----------------------------------------------------
        fresh(page)
        errors.clear()
        if air:
            tpl = page.evaluate("Object.keys(BUILTIN_TEMPLATES)[0]")
            page.evaluate("n => { insertTemplate(deepClone(BUILTIN_TEMPLATES[n]), 800, 500); selection = null; render(); }", tpl)
            page.evaluate("(() => { state.comps.find(c => c.type === 'vorheizer').name = 'VHR 1.OG'; state.comps.find(c => c.type === 'aussenluft').name = 'AUL.1'; commit(); render(); })()")
        else:
            page.evaluate("""(() => { const a = addComp('inflow', 200, 200); a.params = {t_set_C: 60, q_m3h: 1}; a.name = 'in.1';
                const r = addComp('flow_resistance', 400, 200); r.params = {c_Pa_m3h2: 100}; r.name = 'HK 1.OG';
                const b = addComp('outflow', 600, 200); b.params = {p_kPa: 0};
                addWire({comp: a.id, port: 'port'}, {comp: r.id, port: 'in'}); addWire({comp: r.id, port: 'out'}, {comp: b.id, port: 'port'});
                state.wires[0].name = 'LT 1.5'; selection = null; commit(); render(); })()""")
        page.click("#btn-solve"); page.wait_for_function("lastResult !== null || document.getElementById('dlg').open", timeout=20000)
        st = page.inner_text("#status")
        check(page.evaluate("lastResult && lastResult.ok") is True and "Ergebnis" in st,
              f"Namen mit Punkt ('HK 1.OG'): Rechnen im GUI ok ({st[:50]} | {page.input_value('#dlg-text')[:80]})")
        if not air:
            check(labels(page) == [1, 2], f"Ergebnisse an Komponente/Leitungen mit Punkt im Namen ({labels(page)})")
            w = page.evaluate("(() => { const q = wirePts(state.wires[0]); return [(q[0].x+q[1].x)/2, (q[0].y+q[1].y)/2 + 3]; })()")
            page.mouse.move(*xy(page, *w)); page.wait_for_timeout(100)
            check(page.inner_text("#tip").startswith("LT 1.5:"), "Tooltip der Leitung 'LT 1.5'")
        else:
            w = page.evaluate("(() => { const w = state.wires.find(w => compById(w.a.comp).name === 'AUL.1' || compById(w.b.comp).name === 'AUL.1'); const q = wirePts(w); return [(q[0].x+q[1].x)/2, (q[0].y+q[1].y)/2 + 3]; })()")
            page.mouse.move(*xy(page, *w)); page.wait_for_timeout(100)
            check("ϑ =" in page.inner_text("#tip"), "Tooltip am Kanal von 'AUL.1' (stationen-Schlüssel mit Punkt)")

        # --- Befund 8: verworfene Ergebnisse verschwinden überall ---------------------------------------------
        if air:
            c = page.evaluate("(() => { const c = state.comps.find(c => c.type === 'aussenluft'); return [c.x, c.y]; })()")
            page.mouse.click(*xy(page, *c))
            head0 = page.inner_text("#insp-head")
            page.locator("input[data-param=t]").first.fill("-15"); page.keyboard.press("Tab"); page.wait_for_timeout(150)
            head1 = page.inner_text("#insp-head")
            check("Ergebnis" in head0 and "Ergebnis" not in head1 and page.evaluate("lastResult") is None,
                  "Parameter geändert: Ergebniskopf im Inspector verschwindet (stand vorher weiter da)")
        else:
            page.evaluate("(() => { state.wires[0].pipes = [{length_m: 10, d_inner_mm: 26}, {length_m: 500, d_inner_mm: 20}]; commit(); render(); })()")
            page.click("#btn-solve"); page.wait_for_function("lastResult !== null", timeout=20000)
            before = labels(page)
            page.fill("#fl-t", "80"); page.keyboard.press("Tab"); page.wait_for_timeout(150)
            check(before[1] == 2 and labels(page) == [0, 0] and page.evaluate("state.fluid.t_C") == 80,
                  f"Fluidtemperatur geändert: Ergebnis-Beschriftungen und Pfeile verschwinden ({before} → {labels(page)})")
            page.evaluate("document.activeElement.blur()")
            page.click("#btn-solve"); page.wait_for_function("lastResult !== null", timeout=20000)
            page.evaluate("(() => { selection = {kind: 'wire', ref: state.wires[0]}; render(); })()")
            n_arrows = labels(page)[1]
            page.locator("button.pipe-del").nth(1).click(); page.wait_for_timeout(150)
            saved = parse_yaml(page.evaluate(f"localStorage.getItem('{SAVE[air]}')"))["components"]["LT 1.5"]["pipes"]
            check(n_arrows == 2 and labels(page)[1] == 0 and len(saved) == 1,
                  "Rohrabschnitt entfernt: Pfeile verschwinden, Autosave enthält die Änderung sofort")
            page.reload(); page.wait_for_load_state("load")
            check(len(parse_yaml(page.evaluate("exportYAML()"))["components"]["LT 1.5"]["pipes"]) == 1,
                  "… und bleibt nach dem Neuladen entfernt")
        # BEMS-Messpunkt entfernen wird sofort gesichert (beide Editoren)
        page.evaluate("(() => { const c = state.comps[0]; c.bems = [{id: 'a'}, {id: 'b'}]; selection = {kind: 'comp', ref: c}; inspectorTab = 'bems'; commit(); render(); })()")
        page.locator("button.bems-del").nth(1).click(); page.wait_for_timeout(150)
        first = page.evaluate("state.comps[0].name")
        check(parse_yaml(page.evaluate(f"localStorage.getItem('{SAVE[air]}')"))["components"][first]["bems"] == [{"id": "a"}],
              "BEMS-Messpunkt entfernt: Autosave sofort aktuell")
        page.evaluate("inspectorTab = 'fluid'")

        if not air:
            # --- Fluid-Modus per Auswahlfeld (Feld hat den Fokus, wie bei echter Bedienung) ---------------
            fresh(page)
            page.focus("#fl-mode"); page.select_option("#fl-mode", "custom"); page.wait_for_timeout(100)
            check(page.locator("#fl-rho").count() == 1 and page.locator("#fl-t").count() == 0,
                  "Fluid 'ρ/μ/cp direkt' gewählt: die Felder wechseln sofort")
            page.fill("#fl-rho", "1040,5"); page.keyboard.press("Tab")
            page.fill("#fl-name", "Glykol"); page.keyboard.press("Tab")
            fl = parse_yaml(page.evaluate("exportYAML()"))["fluid"]
            check(fl == {"name": "Glykol", "rho": 1040.5, "mu": 0.001, "cp": 4180}, f"… und der Export entspricht den Feldern ({fl})")
            # --- Befund 4: settings der Datei gelten beim Rechnen im GUI ------------------------------------
            page.evaluate("async t => await importYAML(t)", """\
settings: {p_ref: 300000}
components:
  pu1: {type: pump, mode: constant_flow, q_m3h: 1.0}
  r1: {type: flow_resistance, c_Pa_m3h2: 1000}
connections:
  - [pu1.out, r1.in]
  - [r1.out, pu1.in]
""")
            page.click("#btn-solve"); page.wait_for_function("lastResult !== null", timeout=20000)
            pmax = page.evaluate("Math.max(...lastResult.nodes.map(n => n.p_kPa))")
            check(page.evaluate("lastResult.nodes.some(n => Math.abs(n.p_kPa - 300) < 1e-6)"),
                  f"settings: {{p_ref: 300000}} aus der Datei wirkt beim Rechnen im GUI (p = {pmax} kPa)")
            check("p_ref" in page.inner_text("#settings-hint"), "Inspector zeigt den settings-Block der Datei")
            # --- Befund 2: Strg+C / Strg+V per Tastatur, Strg+D ohne Auswahl ----------------------------------
            page.evaluate("async t => await importYAML(t)", """\
components:
  src: {type: inflow, t_set_C: 60, q_m3h: 1.0}
  hk: {type: radiator, q_prescribed_kW: 5, kv_m3h: 2}
  lt1: {type: conduit, pipes: [{length_m: 10, d_inner_mm: 26}], bems: [{id: dp-1}]}
connections:
  - [src.port, lt1.in]
  - [lt1.out, hk.in]
layout:
  components: {src: {x: 200, y: 200}, hk: {x: 500, y: 200}}
""")
            page.mouse.move(*xy(page, 120, 120)); page.mouse.down(); page.mouse.move(*xy(page, 600, 280), steps=5); page.mouse.up()
            page.keyboard.press("ControlOrMeta+c"); page.keyboard.press("ControlOrMeta+v")
            d = parse_yaml(page.evaluate("exportYAML()"))["components"]
            check(d["lt2"] == d["lt1"], f"Strg+C/Strg+V: die kopierte Leitung behält pipes und bems ({d.get('lt2')})")
            page.mouse.click(*xy(page, 900, 600))
            n = page.evaluate("state.comps.length"); page.keyboard.press("ControlOrMeta+d")
            check(page.evaluate("state.comps.length") == n, "Strg+D ohne Auswahl fügt nichts aus der alten Zwischenablage ein")
            # --- .inf über den Server-Parser (Blockskalar): Import bleibt beim Server -------------------------
            issues = page.evaluate("async t => await importYAML(t)", "components:\n  src:\n    type: inflow\n    t_set_C: 60\n    q_m3h: .inf\n    description: |\n      Zulauf\n      Zeile 2\nconnections: []\n")
            check(isinstance(issues, list) and "nicht endliche" in issues[0] and page.evaluate("state.comps[0].params.description") == "Zulauf\nZeile 2\n",
                  "Datei mit .inf: Antwort des Servers bleibt lesbar (null + Hinweis), Blockskalar importiert")
        else:
            # --- Befund 13: Messleitung zuerst, dann Kanal — mit echten Port-Klicks ---------------------------
            fresh(page)
            page.evaluate("addComp('aussenluft', 200, 300); addComp('vorheizer', 400, 300); addComp('temperatursensor_luft', 300, 200); selection = null; render();")
            ports = page.evaluate("state.comps.map(c => Object.fromEntries(Object.entries(portPos(c)).map(([k, q]) => [k, [c.x + q.dx, c.y + q.dy]])))")
            page.mouse.click(*xy(page, *ports[2]["port"])); page.mouse.click(*xy(page, *ports[0]["out"]))     # Fühler → AUL.out
            page.mouse.click(*xy(page, *ports[0]["out"])); page.mouse.click(*xy(page, *ports[1]["in"]))       # Kanal AUL.out → VHR.in
            check(page.evaluate("state.wires.length") == 2, f"Kanal nach der Messleitung am selben Port ({page.inner_text('#status')[:60]})")

        check(not errors, f"keine JS-Fehler ({errors[:3]})")
        ctx.close()

        # --- statischer Editor (ohne Server) -----------------------------------------------------------------------
        print(f"== {name} (statisch)")
        ctx = browser.new_context(viewport={"width": 1500, "height": 900}); page = ctx.new_page()
        errors, dialogs = [], []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
        page.goto(STATIC[air]); page.wait_for_load_state("load"); fresh(page)
        page.evaluate(f"addComp('{ta}', 200, 200); addComp('{tb}', 400, 200); selection = null; render();")
        for text in ("", "nur ein Satz", "- a\n- b\n"):
            page.click("#btn-import"); page.fill("#dlg-text", text); page.click("#dlg-ok"); page.wait_for_timeout(250)
        saved = page.evaluate(f"YamlCore.parse(localStorage.getItem('{SAVE[air]}')).components")
        check(page.evaluate("state.comps.length") == 2 and len(saved) == 2 and len(dialogs) == 3 and all("nichts importiert" in d for d in dialogs),
              "statisch: leerer bzw. unpassender Import lässt Zeichnung und Autosave stehen (3 Meldungen)")
        page.evaluate("async t => await importYAML(t)", EVIL[air]); page.wait_for_timeout(200)
        page.mouse.click(*xy(page, 200, 200)); page.click("#c-name")
        page.reload(); page.wait_for_load_state("load"); page.wait_for_timeout(200)
        check(page.evaluate("window.__pwn") is None and page.evaluate("state.comps.length") == 2, "statisch: bösartige Datei führt kein Skript aus")
        try:
            page.evaluate("async t => await importYAML(t)", "components:\n  a: {type: cap, ts: 9007199254740993}\n")
            msg = "kein Fehler"
        except Exception as e:
            msg = str(e)
        check("2^53" in msg, "statisch: Ganzzahl jenseits 2^53 wird abgelehnt statt ungenau gelesen")
        check(not errors, f"keine JS-Fehler ({errors[:3]})")
        ctx.close()
    browser.close()
srv.shutdown(); srv.server_close()
print("\nERGEBNIS:", "alles ok" if not fails else f"{len(fails)} Fehler")
sys.exit(1 if fails else 0)
