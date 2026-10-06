"""Editor: mehrdeutige Hydraulik → Statuszeile + Dialog mit dem Hinweis."""
import json, sys, threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
from playwright.sync_api import sync_playwright
from hydraulik.server import make_server
from hydraulik.yamlio import canonical_json
from test_eindeutigkeit import MEHRDEUTIG
srv = make_server(0); port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
ok = True
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(); errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("dialog", lambda d: d.accept())
    pg.goto(f"http://127.0.0.1:{port}/hydraulik"); pg.wait_for_load_state("load")
    for doc, expect in ((MEHRDEUTIG, True), ({"components": {
            "zu": {"type": "inflow", "t_set_C": 50.0, "q_m3h": 2.0},
            "t1": {"type": "tee", "d_run_mm": 32.0, "d_branch_mm": 25.0},
            "ab_b": {"type": "outflow", "p_kPa": 150}, "ab_c": {"type": "outflow", "q_m3h": 0.8}},
            "connections": [["zu.port", "t1.a"], ["t1.b", "ab_b.port"], ["t1.c", "ab_c.port"]]}, False)):
        pg.evaluate("() => { const d = document.getElementById('dlg'); if (d.open) d.close(); }")
        pg.evaluate("async t => await importYAML(t)", canonical_json(doc))
        pg.evaluate("async () => await solveNow()")
        pg.wait_for_timeout(300)
        status = pg.evaluate("document.getElementById('status').textContent")
        is_open = pg.evaluate("document.getElementById('dlg').open")
        title = pg.evaluate("document.getElementById('dlg-title').textContent")
        text = pg.evaluate("document.getElementById('dlg-text').value")
        good = (("NICHT eindeutig" in status) == expect and is_open == expect
                and (not expect or ("2 stationäre Lösungen" in title and "Anfahrvorgang" in text)))
        ok &= good
        print("  ok " if good else "  FEHLER", "mehrdeutig" if expect else "eindeutig ",
              "| Status:", status[:110], "| Dialog:", is_open, title)
    print("  JS-Fehler:", errs); ok &= not errs
    b.close()
print("ERGEBNIS:", "alles ok" if ok else "FEHLER")
