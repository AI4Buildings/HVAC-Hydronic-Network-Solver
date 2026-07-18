"""Lokaler Editor-Server: GUI + Rechen-Endpunkt (Human in the Loop).

    hydraulik serve [--port 8091] [--no-open]

Serviert den Schaltbild-Editor unter http://127.0.0.1:<port>/ und stellt
POST /solve bereit (Body: YAML der Schaltung → JSON-Ergebnis). Damit kann
der „Rechnen"-Button im GUI den Solver direkt aufrufen und die Ergebnisse
(p, V̇, v, T, Q̇) in die Zeichnung zurückspielen. Nur lokal gebunden
(127.0.0.1), kein Zugriff von außen.
"""
from __future__ import annotations

import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .editor import render_editor
from .exceptions import ConvergenceError, HydraulikError
from .results import build_result
from .solver.hydraulic import solve_hydraulics
from .solver.thermal import skipped_thermal, solve_thermal
from .yaml_loader import load, load_settings


def solve_payload(yaml_text: str) -> dict:
    """YAML → Ergebnis-JSON inkl. port_map (Port-Referenz → Knotenindex),
    damit das GUI Knotenzustände an den Leitungen anzeigen kann.
    Schlägt nur die Thermik fehl (z.B. isolierter Umlauf mit fester Leistung),
    werden die Hydraulikergebnisse mit Hinweis zurückgegeben."""
    net = load(yaml_text)
    settings = load_settings(yaml_text)
    compiled = net.compile()
    hyd = solve_hydraulics(compiled, settings)
    try:
        th = solve_thermal(compiled, hyd, settings)
    except ConvergenceError as exc:
        th = skipped_thermal(compiled, settings)
        compiled.notices.append(f"Thermik nicht gelöst – Temperaturen zeigen den Startwert. ({exc})")
    result = build_result(compiled, hyd, th, settings)
    payload = result.to_dict()
    payload["port_map"] = {el: nd.index for nd in compiled.nodes
                           for el in nd.elements if "::" not in el}
    payload["ok"] = True
    return payload


class _Handler(BaseHTTPRequestHandler):
    editor_html: bytes = b""
    air_html: bytes = b""

    def _send_html(self, html: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    _INDEX = """<!DOCTYPE html><html lang="de"><head><meta charset="utf-8">
<title>hydraulik – Editoren</title><style>
 body { font: 16px system-ui; max-width: 40em; margin: 4em auto; color: #222; }
 a { display: block; padding: 1em 1.2em; margin: 1em 0; border: 1px solid #ccc;
     border-radius: 10px; text-decoration: none; color: #0b3d91; }
 a:hover { background: #f4f3ee; } small { color: #666; }
</style></head><body>
<h1>HVAC-Schema-Editoren</h1>
<a href="/hydraulik"><b>Hydraulikschema-Editor</b><br>
 <small>1D-Netzwerk-Solver (SIMPLE-Druckkorrektur), Heiz-/Kühlkreise,
 Sensoren + BEMS</small></a>
<a href="/lueftung"><b>Lüftungsschema-Editor</b><br>
 <small>Vollklimaanlagen (VKA) nach EN 16798-5-1, energieoptimale
 WRG-Regelung, Sensoren + BEMS</small></a>
</body></html>"""

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send_html(self._INDEX.encode("utf-8"))
        elif self.path in ("/hydraulik", "/hydraulik/"):
            self._send_html(self.editor_html)
        elif self.path in ("/lueftung", "/lueftung/"):
            self._send_html(self.air_html)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path not in ("/solve", "/hydraulik/solve",
                             "/solve_air", "/lueftung/solve_air"):
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        try:
            if self.path.endswith("/solve") or self.path == "/solve":
                payload = solve_payload(body)
            else:
                from .air import solve_air
                payload = solve_air(body)
        except (HydraulikError, ValueError) as exc:
            payload = {"ok": False, "error": str(exc)}
        except Exception as exc:                       # nie den Server reißen lassen
            payload = {"ok": False, "error": f"Interner Fehler: {exc!r}"}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):                 # keine Zugriffs-Logs
        pass


def make_server(port: int = 8091) -> ThreadingHTTPServer:
    from .editor import render_air_editor
    _Handler.editor_html = render_editor().encode("utf-8")
    _Handler.air_html = render_air_editor().encode("utf-8")
    return ThreadingHTTPServer(("127.0.0.1", port), _Handler)


def serve(port: int = 8091, open_browser: bool = True) -> None:
    httpd = make_server(port)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"HVAC-Editoren laufen: {url}   ({url}hydraulik · {url}lueftung; Beenden mit Strg+C)")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServer beendet.")
