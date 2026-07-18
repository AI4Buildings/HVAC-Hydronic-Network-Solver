# hydraulik – Projektleitfaden

Python-Paket zur stationären hydraulisch-thermischen Berechnung von
HVAC-Hydraulikschaltungen (1D-Netzwerk, SIMPLE-artiger Druckkorrektur-Solver)
mit grafischem Hydraulikschema-Editor (Rechnen im GUI, Human in the Loop).
Doppelzweck: Rechenmodell UND maschinenlesbare semantische Karte für die
BEMS-Betriebsdatenanalyse (Aedifion-Datenpunkt-IDs an jeder Komponente).
Stand: v0.5.0 (Juli 2026); validiert gegen zwei unabhängige
FH-Burgenland-Referenzlösungen (Verteiler-Übung, TWE-Übung Bsp 6) sowie
gegen die Skill-Referenz cooling-coil-greybox (FläktGroup-Register).
GitHub (public): https://github.com/AI4Buildings/HVAC-Hydronic-Network-Solver
— Änderungen nach Abschluss committen und pushen (Co-Authored-By-Trailer).

## Befehle

```bash
pip install -e ".[dev]"                  # Installation (editable)
pytest                                   # Testsuite (141 Tests)
pytest tests/test_hydraulics.py -k parallel   # einzelner Test
hydraulik run examples/04_heatpump_separator.yaml [--json] [--csv out.csv]
hydraulik editor --out hydraulik_editor.html   # Schaltbild-Editor generieren (statisch)
hydraulik serve [--port 8091]                  # Startseite / → /hydraulik + /lueftung (Rechnen im GUI)
hydraulik editor --luft --out lueftung_editor.html   # Lüftungsschema-Editor (statisch)
python3 examples/run_examples.py         # alle YAML-Beispiele mit Bericht
python3 examples/07_twe_heizkreisverteiler.py    # Auslegung + Verifikation + Abschaltfall
python3 examples/validation_fh_verteiler.py      # Validierungs-Kennlinienplots
python3 examples/08_ventilautoritaet.py          # Wirkung der Ventilautorität (Plots)
python3 examples/09_energetikum_lueftungsregister.py  # reale Anlage + BEMS-IDs (Aedifion)
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
    pipe/pump/resistance/valves (inkl. check_valve, ball_valve=Kugelhahn:
    ohne Kvs druckverlustfrei, closed=Revisionsfall)/emitters/
    coils (Register: Teillast-UA = UA_ref·[(V̇g/V̇g,ref)·(V̇w/V̇w,ref)]^n, Gl. 4.2
    FH-Skript; Kühlregister zusätzlich Greybox mit Kondensation — Betriebsarten
    feste Leistung | ε-NTU | Greybox)/plants/
    sensors.py       Sensoren (T/p/Δp/V̇/WMZ): rückwirkungsfreie Messstellen →
                     result.sensors; Messleitung = reine Verbindung. BEMS generisch:
                     JEDE Komponente hat bems: [{id,key,description},…] (reserviert,
                     base._parse_bems) + description; Editor-Register Fluid-/BEMS-Info
    idelchik.py      ζ-Tabellen 90°-T-Stück (Diagramm 7-10/7-21) für tee mit
                     Druckverlust (d_run+d_branch; Regime aus Strömungsrichtung)
    storage/separators/connectors (link)/conduit (Verbindungsleitung = Linie
    im Editor: ideal|C-Wert|Auslegungspunkt|Rohrmodell; Rohrmodell wahlweise
    als pipes-Liste beliebig vieler Abschnitte in Reihe, je Abschnitt
    length/d_inner/roughness/zeta — reserviertes Kwarg wie bems)/boundaries
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
  editor_template.html  Single-File-Hydraulikschema-Editor (__CATALOG_JSON__);
                     jede gezogene Linie = conduit (Sensor-Messleitungen = reine
                     Verbindung); Zoom, Undo/Redo, Multi-Select, Knickpunkte,
                     Drag-to-Connect, Ergebnis-Tooltips; Palette-Register
                     Komponenten/Vorlagen (7 Grundschaltungen mitgeliefert,
                     eigene via localStorage/JSON); Inspector-Register
                     Fluid-Info/BEMS-Info; modusabhängige Eingabefelder
                     (PARAM_MODES: nur Felder des gewählten Modus, Werte-
                     erhalt via mstash); Symboltexte rotationsfest;
                     Strömungsrichtungspfeile auf conduits nach dem Rechnen;
                     automatisch mitwachsende Zeichenfläche (updateCanvasSize);
                     conduit-Rohrmodell als Abschnittsliste (pipesForm)
  server.py          hydraulik serve: Editor + POST /solve (nur 127.0.0.1)
  air/               Luftseite (Lüftungsanlage, in Arbeit — Phase 1 fertig):
    vka/             integrierter VKA-Rechenkern EN 16798-5-1 (aus Skill
                     vka-effizienz-en16798 übernommen; simulate/simulate_room,
                     energieoptimale Rotorregelung, 1:1 MATLAB-verifiziert;
                     einzige dokumentierte Abweichung: Rotor-ε auf ≤ 1
                     begrenzt — f_q-Unbalance-Korrektur trieb ε über 1,
                     Skill-Kopie identisch gepatcht)
    components.py    Luft-Registry (AIR_REGISTRY, gleiche Param-/BEMS-Mechanik):
                     aussenluft/abluft_raum/zuluft (regelung fest|band|raum)/
                     fortluft, wrg (5 Bauarten), frostschutz, vor-/nachheizer,
                     kuehler, befeuchter, ventilator_luft, umluft + deklarative
                     (filter, schalldaempfer, Luft-Sensoren)
    loader.py        YAML-Loader (Ketten-Semantik: 2 Stränge, Ports 1× verbunden)
    adapter.py       solve_air(): Stränge ablaufen → plant-Config inkl. order
                     in Zeichenreihenfolge (Ventilator wird an den Stranganfang
                     normiert — validierter Kern-Pfad; Hinweis im Ergebnis) →
                     simulate/simulate_room → Ergebnisse je Komponente +
                     stationen (ϑ/x/φ/V̇ je Kanalabschnitt aus den Ketten-
                     zuständen des Kerns; Fortluft über WRG-Bilanz) für
                     Leitungs-/Sensor-Tooltips
  air_editor_template.html  Lüftungsschema-Editor (Fork des Hydraulik-Editors,
                     Ketten-Semantik: Ports 1× verbunden, keine conduits;
                     2 Vorlagen Vollklima/KVS; Ergebnispanel + Tooltips;
                     hydraulik serve → /lueftung, POST /solve_air)
  results.py         SolutionResult: report(), to_dict(), to_csv(), result["name"],
                     Teilstrecken-Tabelle (ts-Gruppen als Ketten in Strömungsrichtung)
  cli.py             Konsolenskript `hydraulik`
docs/                architektur.md, numerik.md, erweitern.md, roadmap.md
examples/            YAML-Schaltungen 01–06 + 09 (Energetikum, echte BEMS-IDs),
                     Lösungs-/Validierungsskripte 07/08 + FH-Verteiler
tests/               141 Tests: analytische Referenzen + Validierung gegen Musterlösungen
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
