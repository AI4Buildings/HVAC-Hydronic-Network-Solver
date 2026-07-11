# hydraulik – Projektleitfaden

Python-Paket zur stationären hydraulisch-thermischen Berechnung von
HVAC-Hydraulikschaltungen (1D-Netzwerk, SIMPLE-artiger Druckkorrektur-Solver)
mit grafischem Schaltbild-Editor (Rechnen im GUI, Human in the Loop).
Stand: v0.3.0 (Juli 2026); validiert gegen zwei unabhängige
FH-Burgenland-Referenzlösungen (Verteiler-Übung, TWE-Übung Bsp 6).
GitHub (public): https://github.com/AI4Buildings/HVAC-Hydronic-Network-Solver
— Änderungen nach Abschluss committen und pushen (Co-Authored-By-Trailer).

## Befehle

```bash
pip install -e ".[dev]"                  # Installation (editable)
pytest                                   # Testsuite (113 Tests)
pytest tests/test_hydraulics.py -k parallel   # einzelner Test
hydraulik run examples/04_heatpump_separator.yaml [--json] [--csv out.csv]
hydraulik editor --out hydraulik_editor.html   # Schaltbild-Editor generieren (statisch)
hydraulik serve [--port 8091]                  # Editor + Rechen-Endpunkt (Rechnen im GUI)
python3 examples/run_examples.py         # alle YAML-Beispiele mit Bericht
python3 examples/07_twe_heizkreisverteiler.py    # Auslegung + Verifikation + Abschaltfall
python3 examples/validation_fh_verteiler.py      # Validierungs-Kennlinienplots
python3 examples/08_ventilautoritaet.py          # Wirkung der Ventilautorität (Plots)
```

## Struktur

```
src/hydraulik/
  fluids.py          Fluid (ρ, μ, cp konstant); water_at(T) mit VDI-Stoffwerttabelle
  params.py          Param-Deklarationen + Einheiten-Suffixe (dp_kPa, q_m3h, …) → SI
  friction.py        Churchill, Swamee-Jain, Kv→b, ζ→b, Rohrkoeffizienten (a, b)
  exceptions.py      NetworkValidationError (sammelt ALLE Fehler), SingularNetworkError, …
  components/        Eine Datei je Komponentengruppe; registry.py: @register("typname")
    base.py          Solver-Verträge: EdgeCoefficients (a, b, dp_source), ThermalResult;
                     reserviertes kwarg ts=<label> (Teilstrecken-Gruppierung)
    pipe/pump/resistance/valves (inkl. check_valve, ball_valve)/emitters/coils/plants/
    storage/separators/connectors (link)/conduit (Verbindungsleitung = Linie
    im Editor: ideal|C-Wert|Auslegungspunkt|Rohrmodell)/boundaries
    (inflow/outflow/cap/open_end)
  network.py         Network (API) + compile(): Union-Find-Portmerge, Validierung,
                     Druckinsel-Analyse, Bilanzcheck fester Volumenströme
  solver/
    hydraulic.py     SIMPLE-Loop (Newton-konsistent, s. docs/numerik.md)
    thermal.py       Upwind-Advektion + Gauss-Seidel; Grenzzyklus-Dämpfung,
                     Drift-Erkennung (isolierte Umläufe), skipped_thermal
    settings.py      SolverSettings (alle Defaults)
  yaml_loader.py     load(), load_settings(); LLM-taugliche Fehlermeldungen;
                     toleriert 'layout:'-Block des Editors
  editor.py          Katalogexport (Registry+ParamSpec → JSON) + render/build_editor()
  editor_template.html  Single-File-Schaltbild-Editor (Platzhalter __CATALOG_JSON__);
                     jede gezogene Linie = conduit; Zoom, Undo/Redo, Multi-Select,
                     Knickpunkte, Drag-to-Connect, Ergebnis-Tooltips
  server.py          hydraulik serve: Editor + POST /solve (nur 127.0.0.1)
  results.py         SolutionResult: report(), to_dict(), to_csv(), result["name"],
                     Teilstrecken-Tabelle (ts-Gruppen als Ketten in Strömungsrichtung)
  cli.py             Konsolenskript `hydraulik`
docs/                architektur.md, numerik.md, erweitern.md, roadmap.md
examples/            YAML-Schaltungen 01–06, Lösungs-/Validierungsskripte 07 + FH-Verteiler
tests/               113 Tests: analytische Referenzen + Validierung gegen Musterlösungen
```

## Konventionen

- **Intern strikt SI** (Pa, m³/s, kg/s, W, m); Temperaturen in °C. Umrechnung
  ausschließlich in params.py über Suffixe. Neue Parameter IMMER als
  `Param(...)`-Deklaration (Single Source of Truth für Python-API UND YAML).
- **Kantenvorzeichen**: positive Flussrichtung ist von Port `in` nach `out`;
  Q < 0 (Rückströmung) ist überall zulässig (Upwind folgt dem Vorzeichen).
- **Q̇-Vorzeichen**: positiv = Wärme INS Wasser (Heizkörper liefert q_dot < 0).
- **Kv enthält die Dichte** (Δp = (V̇/kv)²·1e5·ρ/1000); C-Werte
  (`flow_resistance`) sind dichteunabhängig — bei Abgleich mit Handrechnungen
  auf deren Konvention achten (Kvs = √(1e5/C) ist nur bei ρ = 1000 exakt).
- **Modellierung**: Teilstrecken-Widerstände hälftig auf Vor-/Rücklauf
  aufteilen (README-Richtlinie); `link` nur für echt widerstandsfreie
  Kopplungen; klassische TS-Nummern als `ts`-Label, nie als Recheneinheit.
- Fehlermeldungen deutsch, gesammelt (nie beim ersten Fehler abbrechen),
  mit difflib-Korrekturvorschlägen — sie werden von LLMs konsumiert.
- Tests gegen analytische Referenzen bzw. dokumentierte Musterlösungen,
  nicht gegen ungeprüfte Regressionszahlen.

## Wichtig für Solver-Änderungen

- **Nur generische Verbesserungen.** Jede Solver-Änderung muss für beliebige
  Netztopologien und Komponenten gelten — nie Sonderbehandlung für einen
  konkreten Fall oder ein Validierungsbeispiel (keine If-Abfragen auf
  Komponentennamen, keine beispiel-abgestimmten Konstanten). Fallspezifische
  Konventionen (z.B. ideale Pumpe, ρ = 1000 für einen Excel-Abgleich) gehören
  in Parameter bzw. das Beispielskript, nie in den Solver.
- Die Konvergenz hängt an der Newton-Konsistenz von Prädiktor und
  Druckkorrektur (beide nutzen J = a + 2b|Q|). Die naive Picard-Form
  Q* = Δp/R_lin divergiert oszillierend — nicht „vereinfachen"!
  Details und Herleitung: docs/numerik.md.
- Verbindungssemantik: „verbinden = Knoten verschmelzen" (ein Druck, EINE
  Temperatur). Anschlüsse entlang einer Leitung brauchen getrennte Knoten
  (aufgeteilte Widerstände oder `link`), sonst mischt der Solver stromab
  eingemischtes Wasser in stromauf liegende Zapfstellen.

## Offene Punkte (v2-Kandidaten)

Siehe docs/roadmap.md: Pumpenkennlinien, Netzplan-Visualisierung,
Ventilautorität im Bericht, feuchtes Kühlregister, Parametersweep-Helfer,
Regelkreis-Iteration.
