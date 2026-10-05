"""Kommandozeile: `hydraulik run schaltung.yaml|.json [--csv out.csv] [--json]`,
`hydraulik export --json schaltung.yaml [--out datei.json]` (kanonisches JSON),
`hydraulik schema [--luft] [--out datei.json]` (JSON Schema des Eingabeformats)."""
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
    run.add_argument("file", help="YAML- oder JSON-Datei der Schaltung")
    run.add_argument("--csv", help="Komponententabelle als CSV speichern")
    run.add_argument("--json", action="store_true", help="Ergebnis als JSON ausgeben")
    ed = sub.add_parser("editor", help="Schema-Editor als HTML-Datei erzeugen")
    ed.add_argument("--out", default="hydraulik_editor.html", help="Zieldatei")
    ed.add_argument("--luft", action="store_true",
                    help="Lüftungsschema-Editor statt Hydraulik erzeugen")
    ex = sub.add_parser("export", help="Eingabemodell geprüft als kanonisches JSON ausgeben")
    ex.add_argument("file", help="YAML- oder JSON-Datei")
    ex.add_argument("--json", action="store_true", required=True,
                    help="Ausgabeformat JSON (derzeit das einzige)")
    ex.add_argument("--luft", action="store_true", help="Datei ist ein Lüftungsschema")
    ex.add_argument("--out", help="Zieldatei (sonst Ausgabe auf stdout)")
    sc = sub.add_parser("schema", help="JSON Schema (Draft 2020-12) des Eingabeformats ausgeben")
    sc.add_argument("--luft", action="store_true", help="Schema der Lüftungsanlage statt Hydraulik")
    sc.add_argument("--out", help="Zieldatei (sonst Ausgabe auf stdout)")
    sv = sub.add_parser("serve", help="Editor mit Rechen-Endpunkt starten (Rechnen im GUI)")
    sv.add_argument("--port", type=int, default=8091)
    sv.add_argument("--no-open", action="store_true", help="Browser nicht automatisch öffnen")

    args = parser.parse_args(argv)
    if args.cmd == "editor":
        from .editor import build_air_editor, build_editor
        if args.luft and args.out == "hydraulik_editor.html":
            args.out = "lueftung_editor.html"
        path = build_air_editor(args.out) if args.luft else build_editor(args.out)
        print(f"Editor erzeugt: {path}  (im Browser öffnen; Rechnen im GUI: 'editor server')")
        return 0
    if args.cmd == "export":
        from .yamlio import canonical_json, load_document
        try:
            doc = load_document(args.file)
            if args.luft:
                from .air import load_air
                load_air(doc)
            else:
                load(doc)
                load_settings(doc)
        except HydraulikError as exc:
            print(f"FEHLER: {exc}", file=sys.stderr)
            return 1
        _write(canonical_json(doc), args.out, "JSON")
        return 0
    if args.cmd == "schema":
        from .schema import json_schema
        _write(json.dumps(json_schema("air" if args.luft else "hydraulik"),
                          indent=2, ensure_ascii=False) + "\n", args.out, "JSON Schema")
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


def _write(text: str, out: str | None, what: str) -> None:
    """Text in Datei (UTF-8) oder auf stdout."""
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"{what} geschrieben: {out}", file=sys.stderr)
    else:
        sys.stdout.write(text)


def editor_main(argv: list[str] | None = None) -> int:
    """Konsolenskript `editor`: startet beide Schema-Editoren im Browser.

    `editor server` (auch `editor serve` oder einfach `editor`) — Startseite
    unter http://127.0.0.1:<port>/ mit /hydraulik und /lueftung."""
    parser = argparse.ArgumentParser(
        prog="editor",
        description="HVAC-Schema-Editoren starten (Hydraulik + Lüftung, Rechnen im GUI)")
    parser.add_argument("cmd", nargs="?", default="server", choices=("server", "serve"),
                        help="Server starten (Default)")
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--no-open", action="store_true",
                        help="Browser nicht automatisch öffnen")
    args = parser.parse_args(argv)
    from .server import serve
    serve(port=args.port, open_browser=not args.no_open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
