"""Alle mitgelieferten Vorlagen beider Editoren: einfügen → Export →
Python-Loader (Hydraulik bzw. Luft) + Editor-Re-Import identisch."""
import sys, threading
from playwright.sync_api import sync_playwright
from hydraulik.server import make_server
from hydraulik.yamlio import parse_yaml
from hydraulik.exceptions import HydraulikError
import hydraulik as h
from hydraulik.air import load_air
from hydraulik.schema import json_schema
from jsonschema import Draft202012Validator
VAL = {False: Draft202012Validator(json_schema()), True: Draft202012Validator(json_schema('air'))}
srv = make_server(0); port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
fails = 0
with sync_playwright() as p:
    b = p.chromium.launch()
    for path, air in (("hydraulik", False), ("lueftung", True)):
        pg = b.new_page(); errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(f"http://127.0.0.1:{port}/{path}"); pg.wait_for_load_state("load")
        for name in pg.evaluate("Object.keys(BUILTIN_TEMPLATES)"):
            pg.evaluate("""n => { state = {fluid: {mode:"preset", t_C:50, rho:998, mu:0.001, cp:4180}, comps: [], wires: []};
                               insertTemplate(deepClone(BUILTIN_TEMPLATES[n]), 300, 300); }""", name)
            y = pg.evaluate("exportYAML()")
            doc = parse_yaml(y)
            # Editor-Re-Import (lokaler Parser) == Python
            js = pg.evaluate("t => JSON.stringify(YamlCore.parse(t))", y)
            import json
            same = json.loads(js) == json.loads(json.dumps(doc))
            try:
                if air: load_air(doc); status = "load_air ok"
                else: h.load(doc); h.load_settings(doc); status = "load ok"
            except HydraulikError as e:
                status = "Loader-Hinweise: " + "; ".join(getattr(e, "messages", [str(e)])[:2])
            serr = [e.message for e in VAL[air].iter_errors(doc)]
            status += f' | Schema: {"ok" if not serr else serr[:2]}'
            ok = same and not serr
            fails += not ok
            print(f"  {'ok' if ok else 'FEHLER'}  {path:9} {name:38} JS==Python: {same}  {status[:110]}")
        if errs: print("  JS-Fehler:", errs); fails += 1
    b.close()
srv.shutdown()
print("ERGEBNIS:", "alles ok" if not fails else f"{fails} Fehler"); sys.exit(1 if fails else 0)
