"""Lokaler Editor-Server: GUI + Rechen-Endpunkt (Human in the Loop).

    hydraulik serve [--port 8091] [--no-open]

Serviert den Schaltbild-Editor unter http://127.0.0.1:<port>/ und stellt
POST /solve bereit (Body: YAML der Schaltung → JSON-Ergebnis). Damit kann
der „Rechnen"-Button im GUI den Solver direkt aufrufen und die Ergebnisse
(p, V̇, v, T, Q̇) in die Zeichnung zurückspielen. POST /normalize bzw.
/normalize_air (Body: YAML beliebiger Form → JSON-Dokument) versorgt den
Editor-Import mit dem vollständigen YAML-1.2-Parser (yamlio; Block- und
Inline-Stil, doppelte Schlüssel werden gemeldet) samt Loader-Hinweisen.
Der Body ist immer YAML-TEXT, nie ein Dateipfad.

Absicherung (der Server rechnet für jeden, der ihn erreicht):
- nur lokal gebunden (127.0.0.1), kein Zugriff von außen;
- Anfragen nur mit eigenem Host (127.0.0.1/localhost + eigener Port) und —
  falls der Browser einen Origin mitschickt — nur von der eigenen Seite;
  fremde Webseiten (Cross-Site-POST, DNS-Rebinding) erhalten 403;
- Body höchstens MAX_BODY_BYTES, Content-Length Pflicht und geprüft;
  fehlerhafte Anfragen erhalten immer eine JSON-Fehlermeldung;
- Antworten sind striktes JSON: nicht endliche Zahlen (inf/nan) werden als
  null übertragen und in den Hinweisen gemeldet.
"""
from __future__ import annotations

import json
import math
import numbers
import re
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .editor import render_editor
from .exceptions import ConvergenceError, HydraulikError, NetworkValidationError
from .results import build_result
from .solver.thermal import skipped_thermal, solve_thermal
from .solver.uniqueness import solve_hydraulics_checked
from .yaml_loader import load_checked
from .yamlio import parse_yaml

#: Obergrenze für den Request-Body (YAML-/JSON-Text einer Schaltung)
MAX_BODY_BYTES = 5 * 1024 * 1024
#: größte Ganzzahl, die der Editor (JavaScript, IEEE-754 double) exakt liest
MAX_SAFE_INTEGER = 2 ** 53 - 1


def solve_payload(yaml_text: str) -> dict:
    """YAML → Ergebnis-JSON inkl. port_map (Port-Referenz → Knotenindex),
    damit das GUI Knotenzustände an den Leitungen anzeigen kann.
    Schlägt nur die Thermik fehl (z.B. isolierter Umlauf mit fester Leistung),
    werden die Hydraulikergebnisse mit Hinweis zurückgegeben."""
    doc = _parse_body(yaml_text)
    net, settings = load_checked(doc)               # alle Eingabefehler in EINER Liste (wie CLI)
    compiled = net.compile(settings)                # settings.p_ref: Referenzdruck geschlossener Kreise
    hyd = solve_hydraulics_checked(compiled, settings)
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


def _parse_body(text: str) -> dict:
    """Request-Body (YAML-Text) → Mapping. Bewusst parse_yaml statt
    load_document: ein Body wird nie als lokaler Dateipfad gedeutet."""
    doc = parse_yaml(text)
    if not isinstance(doc, dict):
        raise NetworkValidationError(
            ["Eingabe muss ein Mapping mit 'components' und 'connections' sein."])
    return doc


def _unsafe_integers(value, path: str = "") -> list[str]:
    """Pfade aller Ganzzahlen jenseits von ±(2^53 − 1) — der Editor kann sie
    nicht exakt lesen (JavaScript rechnet mit double)."""
    if isinstance(value, bool):
        return []
    if isinstance(value, int):
        return [f"{path or 'Wert'} = {value}"] if abs(value) > MAX_SAFE_INTEGER else []
    if isinstance(value, dict):
        return [hit for k, v in value.items()
                for hit in _unsafe_integers(v, f"{path}.{k}" if path else str(k))]
    if isinstance(value, (list, tuple)):
        return [hit for i, v in enumerate(value) for hit in _unsafe_integers(v, f"{path}[{i}]")]
    return []


def normalize_payload(yaml_text: str, kind: str = "hydraulik") -> dict:
    """YAML beliebiger Form (Block- oder Inline-Stil) → JSON-Dokument für den
    Editor-Import (fluid/settings/components/connections/layout unverändert).
    Doppelte Schlüssel werden gemeldet statt stillschweigend überschrieben.
    'issues' enthält die Meldungen des jeweiligen Loaders (Hydraulik oder
    Luft) — rein informativ, damit auch unvollständige Dateien geladen und im
    Editor ergänzt werden können. Ganzzahlen jenseits von ±2^53 werden
    abgelehnt (wie im lokalen Editor-Parser): der Editor läse sie ungenau."""
    doc = _parse_body(yaml_text)
    unsafe = _unsafe_integers(doc)
    if unsafe:
        raise NetworkValidationError(
            [f"Ganzzahl {hit} liegt außerhalb des exakt darstellbaren Bereichs (±2^53) – der "
             f"Editor kann sie nicht unverändert übernehmen. Kennungen bitte als Zeichenkette "
             f"in Anführungszeichen, große Zahlenwerte als Gleitkommazahl (z.B. 1.0e+20) schreiben."
             for hit in unsafe[:20]])
    issues: list[str] = []
    try:
        if kind == "air":
            from .air import load_air
            load_air(doc)
        else:
            load_checked(doc)           # Parameter, settings und Einbindung in einer Liste
    except HydraulikError as exc:
        issues = list(getattr(exc, "messages", None) or [str(exc)])
    return {"ok": True, "doc": doc, "issues": issues}


# --- striktes JSON ----------------------------------------------------------------

def _strict(value):
    """Nicht endliche Zahlen (inf/nan) → None. Liefert (bereinigt, Anzahl)."""
    count = 0

    def walk(v):
        nonlocal count
        if isinstance(v, bool) or v is None or isinstance(v, (str, int)):
            return v
        if isinstance(v, numbers.Integral):          # numpy-Ganzzahlen
            return int(v)
        if isinstance(v, numbers.Real):              # float, numpy-Gleitkommazahlen
            f = float(v)
            if math.isfinite(f):
                return f
            count += 1
            return None
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [walk(x) for x in v]
        return v

    return walk(value), count


def encode_payload(payload: dict) -> bytes:
    """Antwort als striktes JSON (UTF-8): NaN/Infinity gibt es in JSON nicht —
    der Browser könnte die Antwort sonst nicht lesen. Solche Werte werden als
    null übertragen und in den Hinweisen der Antwort gemeldet."""
    clean, n = _strict(payload)
    if n and isinstance(clean, dict):
        hint = (f"{n} nicht endliche(r) Zahlenwert(e) (inf/nan) – als null übertragen, weil JSON "
                f"solche Werte nicht kennt. Bitte Eingaben und Ergebnis prüfen.")
        # /solve: notices, /normalize: issues (vorn – der Editor zeigt den ersten), Luft: hinweise
        for key in ("notices", "issues", "hinweise"):
            if isinstance(clean.get(key), list):
                clean[key].insert(0, hint)
                break
        else:
            clean["hinweise"] = [hint]
    try:
        return json.dumps(clean, ensure_ascii=False, allow_nan=False, default=str).encode("utf-8")
    except UnicodeEncodeError:
        # einzelne Surrogate (z.B. "\ud800" als YAML-Escape) sind kein gültiges
        # UTF-8 — als JSON-Escape übertragen
        return json.dumps(clean, ensure_ascii=True, allow_nan=False, default=str).encode("ascii")


# --- HTTP ------------------------------------------------------------------------

class _RequestError(Exception):
    """Fehlerhafte Anfrage: HTTP-Status + deutsche Meldung (JSON-Fehlerantwort)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


_POST_PATHS = frozenset({"/solve", "/hydraulik/solve",
                         "/solve_air", "/lueftung/solve_air",
                         "/normalize", "/hydraulik/normalize",
                         "/normalize_air", "/lueftung/normalize_air"})


class _Handler(BaseHTTPRequestHandler):
    editor_html: bytes = b""
    air_html: bytes = b""
    #: Sekunden je Socket-Operation: ein Client, der weniger Bytes schickt als
    #: angekündigt, blockiert den Bearbeitungs-Thread nicht dauerhaft
    timeout = 30

    def _send_html(self, html: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def _send_json(self, status: int, payload: dict) -> None:
        try:
            data = encode_payload(payload)
        except Exception as exc:                       # nie ohne Antwort enden
            data = json.dumps({"ok": False,
                               "error": f"Interner Fehler beim Kodieren der Antwort: {exc!r}"}
                              ).encode("ascii")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _foreign_request(self) -> str | None:
        """Meldung, falls die Anfrage nicht von der eigenen Seite stammt
        (Host bzw. Origin fremd), sonst None."""
        port = self.server.server_address[1]
        own = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if port == 80:
            own |= {"127.0.0.1", "localhost"}
        host = (self.headers.get("Host") or "").strip().lower()
        if host not in own:
            return (f"Anfrage abgelehnt: Host '{host or '(fehlt)'}' ist nicht dieser lokale "
                    f"Editor-Server (erwartet 127.0.0.1:{port} bzw. localhost:{port}).")
        origin = self.headers.get("Origin")
        if origin is not None and origin.strip().lower() not in {f"http://{h}" for h in own}:
            return (f"Anfrage abgelehnt: Origin '{origin.strip()}' ist nicht die Seite dieses "
                    f"Editor-Servers – fremde Webseiten dürfen den lokalen Solver nicht aufrufen.")
        return None

    def _read_body(self) -> str:
        raw = self.headers.get("Content-Length")
        if raw is None:
            raise _RequestError(411, "Anfrage ohne Content-Length – der Inhalt (YAML-Text) muss "
                                     "mit Längenangabe gesendet werden (kein 'chunked').")
        if not re.fullmatch(r"[0-9]+", raw.strip()):
            raise _RequestError(400, f"Ungültiger Content-Length '{raw.strip()}' – erwartet "
                                     f"eine nicht negative ganze Zahl.")
        length = int(raw.strip())
        if length > MAX_BODY_BYTES:
            raise _RequestError(413, f"Anfrage zu groß ({length} Bytes) – erlaubt sind höchstens "
                                     f"{MAX_BODY_BYTES // (1024 * 1024)} MB je Schaltung.")
        try:
            data = self.rfile.read(length)
        except OSError:                                # inkl. Zeitüberschreitung
            raise _RequestError(408, "Anfrage unvollständig: Zeitüberschreitung beim Lesen "
                                     "des Inhalts.") from None
        if len(data) < length:
            raise _RequestError(400, f"Anfrage unvollständig: {len(data)} statt {length} Bytes "
                                     f"empfangen.")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _RequestError(400, f"Die Anfrage ist kein gültiges UTF-8 (Byte {exc.start}) – "
                                     f"YAML-/JSON-Text bitte UTF-8-kodiert senden.") from None

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
        foreign = self._foreign_request()
        if foreign:
            self._send_json(403, {"ok": False, "error": foreign})
            return
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send_html(self._INDEX.encode("utf-8"))
        elif path in ("/hydraulik", "/hydraulik/"):
            self._send_html(self.editor_html)
        elif path in ("/lueftung", "/lueftung/"):
            self._send_html(self.air_html)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path not in _POST_PATHS:
            self.send_response(404)
            self.end_headers()
            return
        self.close_connection = True                   # nie auf weitere Daten warten
        foreign = self._foreign_request()
        if foreign:
            self._send_json(403, {"ok": False, "error": foreign})
            return
        try:
            body = self._read_body()
        except _RequestError as exc:
            self._send_json(exc.status, {"ok": False, "error": exc.message})
            return
        try:
            if path.endswith("/normalize"):
                payload = normalize_payload(body, "hydraulik")
            elif path.endswith("/normalize_air"):
                payload = normalize_payload(body, "air")
            elif path.endswith("/solve"):
                payload = solve_payload(body)
            else:
                from .air import solve_air
                payload = solve_air(_parse_body(body))
        except (HydraulikError, ValueError) as exc:
            payload = {"ok": False, "error": str(exc)}
        except RecursionError:
            payload = {"ok": False, "error": "Eingabe ist zu tief verschachtelt."}
        except Exception as exc:                       # nie den Server reißen lassen
            payload = {"ok": False, "error": f"Interner Fehler: {exc!r}"}
        self._send_json(200, payload)

    def log_message(self, fmt, *args):                 # keine Zugriffs-Logs
        pass


def make_server(port: int = 8091) -> ThreadingHTTPServer:
    from .editor import render_air_editor
    _Handler.editor_html = render_editor().encode("utf-8")
    _Handler.air_html = render_air_editor().encode("utf-8")
    return ThreadingHTTPServer(("127.0.0.1", port), _Handler)


def serve(port: int = 8091, open_browser: bool = True) -> None:
    try:
        httpd = make_server(port)
    except OSError as exc:
        print(f"Der Editor-Server kann Port {port} auf 127.0.0.1 nicht belegen "
              f"({exc.strerror or exc}).\nLäuft bereits ein 'hydraulik serve'? Dann dessen "
              f"Adresse http://127.0.0.1:{port}/ nutzen oder einen anderen Port wählen, z.B.: "
              f"hydraulik serve --port {port + 1}", file=sys.stderr)
        raise SystemExit(1) from None
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"HVAC-Editoren laufen: {url}   ({url}hydraulik · {url}lueftung; Beenden mit Strg+C)")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServer beendet.")
