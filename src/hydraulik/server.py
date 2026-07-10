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

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(self.editor_html)))
            self.end_headers()
            self.wfile.write(self.editor_html)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path != "/solve":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        try:
            payload = solve_payload(body)
        except HydraulikError as exc:
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
    _Handler.editor_html = render_editor().encode("utf-8")
    return ThreadingHTTPServer(("127.0.0.1", port), _Handler)


def serve(port: int = 8091, open_browser: bool = True) -> None:
    httpd = make_server(port)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"hydraulik-Editor läuft: {url}   (Beenden mit Strg+C)")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServer beendet.")
