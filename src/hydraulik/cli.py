"""Kommandozeile: `hydraulik run schaltung.yaml|.json [--csv out.csv] [--json]`,
`hydraulik export --json schaltung.yaml [--out datei.json]` (kanonisches JSON),
`hydraulik schema [--luft] [--out datei.json]` (JSON Schema des Eingabeformats)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .exceptions import HydraulikError, NetworkValidationError
from .yaml_loader import NOT_A_MAPPING, load_checked
from .yamlio import canonical_json, load_document


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
    try:
        return _dispatch(args)
    except HydraulikError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        # Schreiben/Lesen gescheitert (Zielordner fehlt, keine Rechte, Port belegt …)
        where = f" '{exc.filename}'" if exc.filename else ""
        print(f"FEHLER: Datei-/Systemfehler{where}: {exc.strerror or exc}", file=sys.stderr)
        return 1


def _dispatch(args) -> int:
    if args.cmd == "editor":
        from .editor import build_air_editor, build_editor
        if args.luft and args.out == "hydraulik_editor.html":
            args.out = "lueftung_editor.html"
        path = build_air_editor(args.out) if args.luft else build_editor(args.out)
        print(f"Editor erzeugt: {path}  (im Browser öffnen; Rechnen im GUI: 'editor server')")
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

    # run/export: das Argument ist IMMER ein Dateipfad (nie YAML-Text) und wird
    # genau einmal gelesen — fehlende Datei → „nicht gefunden“, /dev/stdin und
    # Prozess-Substitution funktionieren.
    doc = load_document(Path(args.file))
    if not isinstance(doc, dict):       # gelesenen Inhalt nie erneut als Quelle (Pfad/Text) deuten
        raise NetworkValidationError([NOT_A_MAPPING])
    if args.cmd == "export":
        if args.luft:
            from .air import load_air
            load_air(doc)
        else:
            load_checked(doc)           # Parameter, settings UND Einbindung (compile)
        _write(canonical_json(doc), args.out, "JSON")
        return 0

    # Fehler aus Parametern, settings und Verbindungen in EINER Liste
    net, settings = load_checked(doc)
    result = net.solve(settings)
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
