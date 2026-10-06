# hydraulik – Projektleitfaden

Python-Paket zur stationären hydraulisch-thermischen Berechnung von
HVAC-Hydraulikschaltungen (1D-Netzwerk, SIMPLE-artiger Druckkorrektur-Solver)
mit grafischem Hydraulikschema-Editor (Rechnen im GUI, Human in the Loop).
Doppelzweck: Rechenmodell UND maschinenlesbare semantische Karte für die
BEMS-Betriebsdatenanalyse (Aedifion-Datenpunkt-IDs an jeder Komponente).
Stand: v0.6.0 (Juli 2026) plus Robustheitsrunde 2026-09-10 (Bugfixes,
Fehlerpfade, Newton-Energiegleichung, Zufallsnetz-Rauchtest, CI) und
Eingabeformat-Härtung 2026-10-05 (YAML 1.2 überall gleich in Solver und
Editoren, JSON-Ein-/Ausgabe, JSON Schema aus der Registry) und Solver-
Prüfung 2026-10-06 (2 Runden, 3300 Zufallsnetze unabhängig nachgerechnet;
docs/solver_pruefung_2026-10.md — Details in den obersten Blöcken von
docs/roadmap.md); validiert
gegen zwei unabhängige FH-Burgenland-Referenzlösungen (Verteiler-Übung,
TWE-Übung Bsp 6) sowie gegen die Skill-Referenz cooling-coil-greybox
(FläktGroup-Register).
GitHub (public): https://github.com/AI4Buildings/HVAC-Hydronic-Network-Solver
— Änderungen nach Abschluss committen und pushen (Co-Authored-By-Trailer).

## Befehle

```bash
pip install -e ".[dev]"                  # Installation (editable)
pytest                                   # Testsuite (847 Tests; Paritätstests brauchen node)
pytest tests/test_hydraulics.py -k parallel   # einzelner Test
hydraulik run examples/04_heatpump_separator.yaml [--json] [--csv out.csv]   # auch .json
hydraulik export --json schaltung.yaml [--out schaltung.json]   # geprüft, kanonisches JSON
hydraulik schema [--luft] [--out hydraulik.schema.json]         # JSON Schema aus der Registry
hydraulik editor --out hydraulik_editor.html   # Schaltbild-Editor generieren (statisch)
editor server [--port 8091]              # Startseite / → /hydraulik + /lueftung (Rechnen im GUI)
hydraulik serve                          # dasselbe (Alias)
hydraulik editor --luft --out lueftung_editor.html   # Lüftungsschema-Editor (statisch)
python3 examples/run_examples.py         # alle YAML-Beispiele mit Bericht
python3 examples/07_twe_heizkreisverteiler.py    # Auslegung + Verifikation + Abschaltfall
python3 examples/validation_fh_verteiler.py      # Validierungs-Kennlinienplots
python3 examples/08_ventilautoritaet.py          # Wirkung der Ventilautorität (Plots)
python3 examples/09_energetikum_lueftungsregister.py  # reale Anlage + BEMS-IDs (Aedifion)
python3 tools/pruefkampagne/kampagne.py 0 3300 --quick   # Prüfkampagne (vor Solver-Änderungen)
python3 tools/vka_skill_abgleich.py      # VKA-Kern ↔ Skill-Kopie identisch?
```

## Struktur

```
src/hydraulik/
  fluids.py          Fluid (ρ, μ, cp konstant); water_at(T) mit VDI-Stoffwerttabelle
  params.py          Param-Deklarationen + Einheiten-Suffixe (dp_kPa, q_m3h, …) → SI
  friction.py        Churchill, Swamee-Jain, Kv→b, ζ→b, Rohrkoeffizienten (a, b)
  exceptions.py      NetworkValidationError (sammelt ALLE Fehler), SingularNetworkError,
                     ComponentModelError (Modellfehler mit Komponente + Betriebspunkt), …
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
                     Druckverlust (d_run+d_branch; Regime aus Strömungsrichtung;
                     unter x = 0.1 stetige Überblendung zwischen den Regimen,
                     Tangenten-Linearisierung; Kurzschluss zweier Schenkel
                     → Validierungsfehler über Hook check_topology; im Netz
                     TOTALDRUCK wie alle Bauteile — statische Anschlussdrücke
                     nur als Ergebnis (edge_result_extras: p_static_port_kPa))
    storage/separators/connectors (link)/conduit (Verbindungsleitung = Linie
    im Editor: ideal|C-Wert|Auslegungspunkt|Rohrmodell; Rohrmodell wahlweise
    als pipes-Liste beliebig vieler Abschnitte in Reihe, je Abschnitt
    length/d_inner/roughness/zeta — reserviertes Kwarg wie bems)/boundaries
    (inflow/outflow/cap/open_end)
  network.py         Network (API) + compile(): Union-Find-Portmerge, Validierung,
                     Druckinsel-Analyse, Bilanzcheck fester Volumenströme
  solver/
    hydraulic.py     SIMPLE-Loop (Newton-konsistent, s. docs/numerik.md)
    thermal.py       Upwind-Advektion; Newton auf der Knotenbilanz (dünne
                     Jacobi-Matrix per Differenzenquotient je Kante, Levenberg-
                     Marquardt-Vertrauensbereich, Armijo-Liniensuche); Stillstand
                     → Δ-Verdopplung (Klemmen), > 1e6 K = keine stationäre Lösung
                     (isolierter Umlauf, Drift-Meldung); skipped_thermal
    settings.py      SolverSettings (alle Defaults; t_plausible_min/max für den
                     Plausibilitätshinweis im Bericht; uniqueness_starts)
    uniqueness.py    solve_hydraulics_checked (EINZIGER Hydraulik-Einstieg für
                     Network.solve/Server): bei Komponenten mit
                     nonmonotone_hydraulics() (Idelchik-T-Stück) Neustart von
                     alternativen Startwerten, falls der Standardstart nicht
                     konvergiert; Zusatzstarts +
                     Nachschärfen → Hinweis „Hydraulik nicht eindeutig“ +
                     SolutionResult.alternatives (Editor: Dialog); stabile
                     Mehrfachlösungen sind ein Anfangswertproblem → melden,
                     nie still auswählen
  yamlio.py          EINZIGE Parse-Stelle für YAML/JSON: load_document(),
                     parse_yaml() (ruamel, YAML 1.2 Core Schema strikt, Schlüssel
                     = Originaltext, nur reine Python-Typen, Duplikate/Tags
                     gesammelt), parse_json() (strikt), canonical_json()
  yaml_loader.py     load(), load_settings() (typ-/bereichsgeprüft, gesammelt);
                     fluid über FLUID_*_PARAMS; LLM-taugliche Fehlermeldungen;
                     toleriert 'layout:'-Block
  schema.py          JSON Schema (Draft 2020-12) nur aus Registry/Param/
                     LIST_PARAMS/FLUID_*_PARAMS/SolverSettings (`hydraulik schema`)
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
                     conduit-Rohrmodell als Abschnittsliste (pipesForm);
                     YAML-Import: Server-Parser (yamlio) mit lokalem
                     YAML-1.2-Parser (yaml_core.js) als Fallback ohne Server
  yaml_core.js       YamlCore: YAML-1.2-Parser/-Serializer beider Editoren
                     (Platzhalter /*__YAML_CORE_JS__*/), Semantik = yamlio,
                     lehnt Nicht-Unterstütztes ab statt anders zu lesen
  server.py          hydraulik serve: Editor + POST /solve + POST /normalize[_air]
                     (YAML → JSON-Dokument + Loader-Hinweise für den Import; Body
                     nur als Text, nie als Pfad; nur 127.0.0.1)
  air/               Luftseite (Lüftungsanlage; Kern + GUI fertig, v0.6.0):
    vka/             integrierter VKA-Rechenkern EN 16798-5-1 (aus Skill
                     vka-effizienz-en16798 übernommen; simulate/simulate_room,
                     energieoptimale Rotorregelung, 1:1 MATLAB-verifiziert;
                     dokumentierte Abweichungen (Physik-Korrekturen L1–L12,
                     Solver-Prüfung 2026-10: ε ≤ min(1, V̇_ab/V̇_zu),
                     Sprühbefeuchter, Kühler-Zweig 5, Index-0-Semantik, …;
                     im Code "Abweichung vom MATLAB-Original" markiert) —
                     PDF-Referenz bleibt exakt (test_air_vka_matlab.py);
                     Skill-Kopie IMMER identisch patchen)
    components.py    Luft-Registry (AIR_REGISTRY, gleiche Param-/BEMS-Mechanik):
                     aussenluft/abluft_raum/zuluft (regelung fest|band|raum)/
                     fortluft, wrg (5 Bauarten), frostschutz, vor-/nachheizer,
                     kuehler, befeuchter, ventilator_luft, umluft + deklarative
                     (filter, schalldaempfer; 8 Luft-Sensoren als ANZAPFUNGEN:
                     ein Messanschluss 'port', Δp plus/minus, AIR_SENSOR_TYPES);
                     Abluft-V̇ an abluft_raum (fortluft.v Altbestand), wrg mit
                     rwz_n (KVS+Platte) und v_m_kvs (KVS-Solekreis)
    loader.py        YAML-Loader (Ketten-Semantik: 2 Stränge, Kanalports 1×
                     verbunden; Sensor-Messleitungen separat in
                     AirPlant.measurements, Sensorport selbst 1×)
    adapter.py       solve_air(): Stränge ablaufen → plant-Config inkl. order
                     in Zeichenreihenfolge (Ventilator wird an den Stranganfang
                     normiert — validierter Kern-Pfad; Hinweis im Ergebnis) →
                     simulate/simulate_room → Ergebnisse je Komponente +
                     stationen (ϑ/x/φ/V̇ je Kanalabschnitt aus den Ketten-
                     zuständen des Kerns; Fortluft über WRG-Bilanz) für
                     Leitungs-/Sensor-Tooltips
  air_editor_template.html  Lüftungsschema-Editor (Fork des Hydraulik-Editors,
                     Ketten-Semantik: Ports 1× verbunden, keine conduits;
                     3 Vorlagen Vollklima/GEA-Energetikum (Winterfall, Datenblatt)/KVS; Ergebnispanel + Tooltips;
                     hydraulik serve → /lueftung, POST /solve_air)
  results.py         SolutionResult: report(), to_dict(), to_csv(), result["name"],
                     Teilstrecken-Tabelle (ts-Gruppen als Ketten in Strömungsrichtung),
                     Plausibilitätshinweis bei Austrittstemperaturen außerhalb
                     t_plausible_min/max (feste Leistung bei Kleinstdurchfluss)
  cli.py             Konsolenskript `hydraulik`
docs/                architektur.md, numerik.md, erweitern.md, roadmap.md,
                     solver_pruefung_2026-10.md (Prüfbericht, offene Punkte)
examples/            YAML-Schaltungen 01–06 + 09 (Energetikum, echte BEMS-IDs),
                     Lösungs-/Validierungsskripte 07/08 + FH-Verteiler
tests/               847 Tests: analytische Referenzen + Validierung gegen Musterlösungen;
                     test_yamlio.py / test_yaml12_kompat.py: Loader + YAML-1.1-
                     Altlasten; test_editor_paritaet.py: JS ↔ Python (node,
                     Korpus tests/data/, Zufallsskalare/-dokumente, Round-Trip);
                     test_schema.py: Schema ↔ Loader je Parameter; test_json_io.py;
                     test_smoke_random.py: 40 Zufallsnetze (fester Seed) über die Palette;
                     test_thermal_newton.py: Energiegleichung gegen Knotenbilanz-Definition,
                     unabhängiges Fixpunkt-Orakel, geschlossene Lösungen, Invarianzen;
                     test_solver_pruefung.py: Befunde der Solver-Prüfung (B1–B16);
                     test_air_vka_matlab.py: VKA-Kern gegen MATLAB/PDF-Referenz;
                     test_air_vka_pruefung.py: Lüftungsbefunde L1–L12 (Invarianten);
                     test_eindeutigkeit.py: Mehrdeutigkeit melden + Neustart
                     (Kampagnennetze 3289, 2647); test_tee_idelchik.py:
                     T-Stück gegen Handrechnung; test_statischer_druck.py:
                     Druckbegriff (Ränder/Sensoren statisch) gegen Bisektion
tools/               Prüfwerkzeuge außerhalb von pytest (tools/README.md):
                     pruefkampagne/ (3300 Zufallsnetze + unabhängige
                     Nachrechnung, Eindeutigkeit, Stabilität; Referenzstand
                     dort), e2e/ (Playwright-Browsertests beider Editoren,
                     Extra ".[e2e]"), vka_skill_abgleich.py
.github/workflows/   CI: pytest auf Python 3.10–3.12 bei Push/PR
```

## Konventionen

- **Intern strikt SI** (Pa, m³/s, kg/s, W, m); Temperaturen in °C. Umrechnung
  ausschließlich in params.py über Suffixe. Neue Parameter IMMER als
  `Param(...)`-Deklaration (Single Source of Truth für Python-API UND YAML).
- **Kantenvorzeichen**: positive Flussrichtung ist von Port `in` nach `out`;
  Q < 0 (Rückströmung) ist überall zulässig (Upwind folgt dem Vorzeichen).
- **Q̇-Vorzeichen**: positiv = Wärme INS Wasser (Heizkörper liefert q_dot < 0).
- **Druckbegriff**: Knotendrücke = Totaldrücke (gauge); alle Bauteile
  rechnen Totaldruckverluste. Druckrandbedingungen und Drucksensoren =
  STATISCHER Überdruck am Anschluss, p_stat = p_Knoten − ρw²/2. Den
  Querschnitt liefert der Hook `port_flow_area` (Rohr, Rohrmodell-conduit,
  Idelchik-T-Stück) bzw. `d_inner`: Sensoren nehmen den direkt verbundenen
  Partner (Builder.partners), Druckränder nur das Ende GENAU EINER Leitung
  (Builder.same_node_ports, Messanschlüsse ausgenommen) — an Knotenpunkten
  ist die Randgeschwindigkeit undefiniert, Druck dort direkt vorgeben (sonst
  Nichtkonvergenz und verdeckte Widerspruchsfehler). Neue Bauteile mit
  definiertem Querschnitt: `port_flow_area` implementieren.
- **Kv enthält die Dichte** (Δp = (V̇/kv)²·1e5·ρ/1000); C-Werte
  (`flow_resistance`) sind dichteunabhängig — bei Abgleich mit Handrechnungen
  auf deren Konvention achten (Kvs = √(1e5/C) ist nur bei ρ = 1000 exakt).
- **Modellierung**: Teilstrecken-Widerstände hälftig auf Vor-/Rücklauf
  aufteilen (README-Richtlinie); `link` nur für echt widerstandsfreie
  Kopplungen; klassische TS-Nummern als `ts`-Label, nie als Recheneinheit.
- Fehlermeldungen deutsch, gesammelt (nie beim ersten Fehler abbrechen),
  mit difflib-Korrekturvorschlägen — sie werden von LLMs konsumiert.
- **Eingabeformat**: YAML 1.2 Core Schema bzw. JSON, geparst NUR über
  yamlio (kein `import yaml`/`ruamel` anderswo — Test). Änderungen an der
  Leseregel immer in yamlio.py UND yaml_core.js; test_editor_paritaet.py
  hält beide deckungsgleich. Labels (`ts`, BEMS) sind Zeichenketten; neue
  Listen-Kwargs als LIST_PARAMS deklarieren (sonst fehlen sie im Schema).
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
  Druckkorrektur (beide nutzen dieselbe Steigung J = max(a + 2b|Q|,
  Differenzenquotient der Kantenkennlinie) — der Differenzenquotient fängt
  Q-abhängige Koeffizienten ab, z.B. Churchill im Übergang). Die naive Picard-Form
  Q* = Δp/R_lin divergiert oszillierend — nicht „vereinfachen"!
  Details und Herleitung: docs/numerik.md.
- Konvergenz NUR mit den Koeffizienten des geprüften Zustands melden
  (Residuen am Iterationsanfang, dann Update) und erst bei kleiner
  Volumenstrom-Korrektur — sonst falsche "Konvergenz" mit veralteten
  Koeffizienten (Solver-Prüfung Oktober 2026, tests/test_solver_pruefung.py).
- Thermik: Newton mit LM-Vertrauensbereich statt Fixpunkt-Sweeps. Die
  Stillstand-Verdopplung ist KEINE Heuristik für einen Sonderfall, sondern
  die Behandlung des singulären Unterraums (Kanten mit Steigung 1); der
  Differenzenschritt muss relativ zu |T| skalieren, sonst macht Rundung
  singuläre Umläufe fälschlich regulär (Drift wird dann nicht erkannt).
- Jacobi-Floor je Kante adaptiv (schrumpft bei stabiler Richtung, Reset
  bei Vorzeichenwechsel). Eine an |p| gekoppelte absolute Untergrenze wurde
  getestet und verworfen (266 Kampagnennetze wurden langsam) — vor neuen
  Floor-Ideen immer die Zufallsnetz-Kampagne laufen lassen.
- Komponenten mit Kennlinien aus Tabellen/Regimen (Idelchik-T-Stück): stetig
  machen und die Tangente als Linearterm a melden (dp_source = a·Q − S);
  eine Kennlinie, die den Eigenstrom ignoriert, lässt Newton zu Picard
  degenerieren (2-Zyklen).
- Verbindungssemantik: „verbinden = Knoten verschmelzen" (ein Druck, EINE
  Temperatur). Anschlüsse entlang einer Leitung brauchen getrennte Knoten
  (aufgeteilte Widerstände oder `link`), sonst mischt der Solver stromab
  eingemischtes Wasser in stromauf liegende Zapfstellen.

## Arbeitsweise und Nutzerentscheidungen

- Bewusst gewählt (nicht „modernisieren"): SIMPLE-artiger Druckkorrektur-
  Solver (didaktischer 1D-CFD-Charakter, Newton-konsistent gemacht);
  physikalische Wärmeübertragungsmodelle statt nur fester Q̇; YAML/JSON +
  Python-API mit Einheiten-Suffixen.
- Modellfragen mit fachlicher Tragweite entscheidet der Nutzer — vorher
  fragen: T-Stück-Druckbegriff (seit 2026-10-06 Totaldruck), Sammler-
  Konvention c (Idelchik 7-10, offen), Ablehnung kurzgeschlossener T-Stücke
  (könnte zum Hinweis herabgestuft werden), Druckbegriff an Rändern/Sensoren
  (statischer Überdruck, Nutzerwunsch 2026-10-06).
- Mehrere stabile stationäre Lösungen sind ein Anfangswertproblem: melden,
  nie still auswählen (Stabilitätstest: tools/pruefkampagne/stabilitaet.py).
- Jede Modell- oder Solver-Änderung an ALLEN Kampagnennetzen gegenprüfen,
  nicht nur an den bekannten Problemfällen (Prognose „32 von 37 eindeutig"
  war zu optimistisch: real 70 beseitigt, 45 neu); Ergebnis mit dem
  Referenzstand in tools/README.md vergleichen und Abweichungen erklären.
- Referenzunterlagen im Arbeitsverzeichnis (Übungs-PDFs, Idelchik-Scans,
  GEA-Datenblatt) sind urheberrechtlich geschützt: NIE committen (.gitignore);
  das Repo ist öffentlich.
- VKA-Kern (air/vka/) und Skill-Kopie ~/.claude/skills/vka-effizienz-en16798
  physikalisch identisch halten; Prüfung mit tools/vka_skill_abgleich.py.

## Offene Punkte (v2-Kandidaten)

Siehe docs/roadmap.md: Pumpenkennlinien, Netzplan-Visualisierung,
Ventilautorität im Bericht, feuchtes Kühlregister, Parametersweep-Helfer,
Regelkreis-Iteration. Offene Fachentscheidungen und der pathologische
Kampagnenfall Seed 913: docs/solver_pruefung_2026-10.md.
