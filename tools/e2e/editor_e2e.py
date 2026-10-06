"""Browser-E2E (Chromium/Playwright) für beide Editoren: Import → Export →
Python-Loader, Autosave-Restore nach Reload, Rechnen, Fehlermeldungen."""
import sys, tempfile, threading, json, math
from pathlib import Path
from playwright.sync_api import sync_playwright
from hydraulik.server import make_server
from hydraulik.editor import build_editor, build_air_editor
from hydraulik.yamlio import parse_yaml
import hydraulik as h

S = Path(tempfile.mkdtemp(prefix="hydraulik_e2e_"))       # statische Editor-Dateien
ROOT = Path(__file__).resolve().parents[2]
BLOCK = (ROOT / "tests/data/blockstil_kreis.yaml").read_text(encoding="utf-8")
OLD = (ROOT / "tests/data/alt_editor_export.yaml").read_text(encoding="utf-8")
AIR = """\
components:
  no: {type: aussenluft, t_C: -1.2e1, rh: 8.0e1, ts: "07"}
  off: {type: fortluft}
connections:
  - [no.out, off.in]
"""
TRICKY = """\
fluid: {preset: water, t_C: 4.5e1}
components:
  no: {type: inflow, t_set_C: 60, p_kPa: 1e1, ts: 08, description: "Kreis #2: it's"}
  "true": {type: conduit, length_m: 12, d_inner_mm: 26, roughness_mm: 5e-7}
  off: {type: outflow, p_kPa: 0}
connections:
  - [no.port, true.in]
  - [true.out, off.port]
"""
fails = []
def check(cond, msg):
    print(("  ok   " if cond else "  FEHLER ") + msg)
    if not cond: fails.append(msg)

srv = make_server(0); port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
static_h = build_editor(S / "static_hydraulik.html"); static_a = build_air_editor(S / "static_lueftung.html")

with sync_playwright() as p:
    browser = p.chromium.launch()
    for label, url, air in (("Hydraulik/Server", f"http://127.0.0.1:{port}/hydraulik", False),
                            ("Hydraulik/statisch", static_h.as_uri(), False),
                            ("Lüftung/Server", f"http://127.0.0.1:{port}/lueftung", True),
                            ("Lüftung/statisch", static_a.as_uri(), True)):
        print(f"== {label}")
        ctx = browser.new_context(); page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("dialog", lambda d: (errors.append("DIALOG: " + d.message), d.dismiss()))
        page.goto(url); page.wait_for_load_state("load")
        server = url.startswith("http")
        check(page.evaluate("typeof YamlCore.parse") == "function", "YamlCore geladen")
        src = AIR if air else TRICKY
        issues = page.evaluate("async t => await importYAML(t)", src)
        check((issues == []) if server else (issues is None), f"Import über {'Server' if server else 'lokalen Parser'} (issues={issues})")
        names = page.evaluate("state.comps.map(c => c.name)")
        check("no" in names and "off" in names, f"Namen no/off erhalten: {names}")
        y = page.evaluate("exportYAML()")
        doc = parse_yaml(y)
        if not air:
            check(doc["fluid"]["t_C"] == 45.0, "fluid t_C 4.5e1 → 45")
            check(doc["components"]["no"]["p_kPa"] == 10.0 and doc["components"]["no"]["ts"] == "8", "1e1 → 10, ts 08 → '8' (gequotet exportiert)")
            check(doc["components"]["true"]["roughness_mm"] == 5e-7, "5e-7 bleibt 5e-7 (Export: %s)" % [l for l in y.splitlines() if "roughness" in l])
            check(doc["components"]["no"]["description"] == "Kreis #2: it's", "String mit # und ' unverändert")
            net = h.load(doc); r = net.solve()
            check(r.converged, "Export rechenbar (Python)")
            if server:
                res = page.evaluate("async () => (await (await fetch('solve', {method: 'POST', body: exportYAML()})).json())")
                check(res.get("ok") is True, f"Rechnen im GUI ok ({res.get('error')})")
        else:
            check(doc["components"]["no"]["t_C"] == -12.0 and doc["components"]["no"]["ts"] == "07", "Luft: -1.2e1 → -12, ts '07'")
        # Autosave → Reload → Restore (lokaler Parser, synchron)
        page.evaluate("autosave()"); page.reload(); page.wait_for_load_state("load")
        y2 = page.evaluate("exportYAML()")
        check(parse_yaml(y2) == doc, "Autosave/Restore nach Reload identisch")
        # Block-Stil + alter Export (Hydraulik), Ablehnung nicht unterstützter Merkmale
        if not air:
            for name, txt in (("Block-Stil", BLOCK), ("alter Export", OLD)):
                page.evaluate("async t => await importYAML(t)", txt)
                d = parse_yaml(page.evaluate("exportYAML()"))
                ref = parse_yaml(txt)
                check(set(d["components"]) == set(ref["components"]), f"{name}: Komponenten {sorted(d['components'])}")
                check(h.load(d).solve().converged, f"{name}: nach Editor-Round-Trip rechenbar")
        try:
            page.evaluate("async t => await importYAML(t)", "components:\n  a: &x {type: %s}\n" % ("fortluft" if air else "cap"))
            msg = "kein Fehler"
        except Exception as e:
            msg = str(e)
        if server:
            check("kein Fehler" in msg, "Server-Parser liest Anker (vollständiges YAML 1.2)")
        else:
            check("hydraulik serve" in msg, "lokaler Parser lehnt Anker mit Hinweis ab")
        check(page.evaluate("round6(1.5e-6)") == 1.5e-6 and page.evaluate("round6(0.1+0.2)") == 0.3, "round6 relativ (1.5e-6 bleibt, 0.30000000000000004 → 0.3)")
        page.evaluate("state.comps[0].params.zz = Infinity")
        check(any("endliche Zahl" in i["msg"] for i in page.evaluate("validate()")), "validate meldet inf")
        errors = [e for e in errors if not (not server and "URL scheme \"file\" is not supported" in e)]   # gewollter Fallback
        check(not errors, f"keine JS-Fehler/Dialoge ({errors[:3]})")
        ctx.close()
    browser.close()
srv.shutdown()
print("\nERGEBNIS:", "alles ok" if not fails else f"{len(fails)} Fehler")
sys.exit(1 if fails else 0)
