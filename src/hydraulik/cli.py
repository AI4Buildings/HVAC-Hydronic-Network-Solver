"""Kommandozeile: `hydraulik run schaltung.yaml [--csv out.csv] [--json]`."""
from __future__ import annotations

import argparse
import json
import sys

from .exceptions import HydraulikError
from .yaml_loader import load, load_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hydraulik",
                                     description="Stationäre hydraulisch-thermische Netzberechnung")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="Schaltung berechnen und Bericht ausgeben")
    run.add_argument("file", help="YAML-Datei der Schaltung")
    run.add_argument("--csv", help="Komponententabelle als CSV speichern")
    run.add_argument("--json", action="store_true", help="Ergebnis als JSON ausgeben")
    ed = sub.add_parser("editor", help="Schema-Editor als HTML-Datei erzeugen")
    ed.add_argument("--out", default="hydraulik_editor.html", help="Zieldatei")
    ed.add_argument("--luft", action="store_true",
                    help="Lüftungsschema-Editor statt Hydraulik erzeugen")
    sv = sub.add_parser("serve", help="Editor mit Rechen-Endpunkt starten (Rechnen im GUI)")
    sv.add_argument("--port", type=int, default=8091)
    sv.add_argument("--no-open", action="store_true", help="Browser nicht automatisch öffnen")

    args = parser.parse_args(argv)
    if args.cmd == "editor":
        from .editor import build_air_editor, build_editor
        if args.luft and args.out == "hydraulik_editor.html":
            args.out = "lueftung_editor.html"
        path = build_air_editor(args.out) if args.luft else build_editor(args.out)
        print(f"Editor erzeugt: {path}  (im Browser öffnen; Rechnen im GUI: 'hydraulik serve')")
        return 0
    if args.cmd == "serve":
        from .server import serve
        serve(port=args.port, open_browser=not args.no_open)
        return 0
    try:
        net = load(args.file)
        settings = load_settings(args.file)
        result = net.solve(settings)
    except HydraulikError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(result.report())
    if args.csv:
        result.to_csv(args.csv)
        print(f"CSV gespeichert: {args.csv}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
