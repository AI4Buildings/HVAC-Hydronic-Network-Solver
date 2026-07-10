# Status & Roadmap

## Stand v0.2.0 (2026-07-09)

96 Tests, alle grün; 6 YAML-Beispielschaltungen + Lösungs-/Validierungs-
skripte; validiert gegen zwei unabhängige FH-Burgenland-Referenzlösungen;
grafischer Schaltbild-Editor (`hydraulik editor`).

v0.1.0 – alle geplanten Meilensteine M1–M6 umgesetzt:

- [x] M1 Hydraulikkern (Rohr, Pumpe, offene Enden, SIMPLE-Solver)
- [x] M2 verzweigte Netze (Ventile, Mischventil, Verteiler, Inselanalyse, Robustheit)
- [x] M3 Thermik (Upwind-Solver, Heizkörper, WP/KM, Rohrverluste, Energiebilanz)
- [x] M4 Restkomponenten (FBH, Register, Weiche, Puffer)
- [x] M5 YAML-Layer (Einheiten-Suffixe, gesammelte Fehler, Vorschläge)
- [x] M6 Reporting (Textbericht, dict/CSV, CLI) und Doku

v0.2.0 – ergänzt (getrieben durch die Validierungsbeispiele 05–07):
- Ventile: `opening: 0` sperrt exakt (Kante wird Randbedingung V̇ = 0 statt
  Leckage-Kv); Kennlinien-Floor Kvs/Rangeability nur noch im Regelbereich.
- Neuer Typ `flow_resistance` (C-Wert-Eingabe für Teilstrecken aus REGuA-
  Handrechnungen, alternativ Auslegungspunkt dp + q). Achtung: Kv-Äquivalente
  Kvs = sqrt(1e5/C) sind wegen der Dichte in der Kv-Definition nur bei
  ρ = 1000 kg/m³ exakt — Kreuzvalidierung in tests/test_flow_resistance.py.
- Beispiele 05/06: Umlenk- + Einspritzschaltung (Validierungsfall REGuA).
- **Validierung gegen FH-Burgenland-Übung „Modellierung Verteiler"**
  (Übung Verteiler _LSG.pdf, unabhängiges Excel-Referenzmodell):
  Volllast-Volumenströme TS1–TS8 < 0.1 %, Ventilautoritäten a_ULS = 0.2380
  (Ref 0.2381) / a_ESS = 0.1937 (Ref 0.1938), Kennlinien-Anker der
  Lösungsplots getroffen. Tests: tests/test_validation_fh_verteiler.py;
  Plots: examples/validation_fh_verteiler.py. Referenzkonventionen: ideale
  Pumpen (`dp_internal_frac: 1e-4`), ρ = 1000; Ventilautorität als
  Δp_Ventil/Δp_variabler-Zweig am Volllastpunkt.
- Pumpe: interner Widerstand über `dp_internal_frac` einstellbar.
- `Network.solve(thermal=False)` für rein hydraulische Studien.
- Thermik-Solver: Periode-2-Grenzzyklen (q_max-Klemme) werden adaptiv
  gedämpft; thermisch isolierte Umläufe mit fester Leistung (keine
  stationäre Lösung) werden erkannt und verständlich gemeldet.
- Neue Komponenten `link` (widerstandsfreie Verbindung, trennt thermische
  Mischpunkte entlang von Sammlern), `ideal_storage` (Zweitor:
  Austrittstemperatur fest, Rücklauf/Leistung Ergebnis — geladener Speicher). Mehrere Volumenstrom-RB am selben Knoten gehen jetzt mit ihrer
  jeweiligen Zulauftemperatur in die Enthalpiebilanz ein (vorher „last wins").
- Beispiel 07 (TWE + Verteiler, Bsp 6): gegen handschriftliche Musterlösung
  abgeglichen (ρ = 995, 2. Gruppe 6 m³/h aus Kontinuität, SRV6 Δp_min 30 mbar);
  Widerstände hälftig auf Vor-/Rücklauf aufgeteilt (Modellierungsrichtlinie
  im README).
- Teilstrecken-Gruppierung: `ts`-Label je Komponente; Bericht mit TS-Tabelle
  (Ketten in Strömungsrichtung: V̇, ΣΔp, p/T an den Abschnittsenden) und
  Konsistenzprüfung (uneinheitlicher V̇ / Verzweigung = falsch geschnitten).
- **Schaltbild-Editor** (`hydraulik editor`): Single-File-HTML, Palette/Ports/
  Parameterformulare aus Registry+ParamSpec generiert; Export = rechenbares
  YAML + layout-Block, Re-Import rund-trip-fähig; Komponenten in
  90°-Schritten drehbar (Ports rotieren mit); headless (Playwright)
  end-to-end getestet: Zeichnen → Export → Solver. Zeichnung = Modell,
  d.h. kein Interpretationsschritt zwischen Schema und Rechnung.
- `inflow`/`outflow` (je EIN Anschlusspunkt): Zulauf T + (V̇ oder Überdruck);
  Austritt (Überdruck oder Entnahme-V̇, Temperatur ist Ergebnis) —
  Randbedingungen offener Systeme. `ideal_storage` (Zweitor) kann
  zusätzlich zu t_set den Volumenstrom (q, Flusszwang) und/oder den Überdruck
  am Austritt (p_out, Druckanker wie Ausdehnungsgefäß) vorgeben.
  Editor: Datei-Export/-Import (Download/Upload) zusätzlich zur Zwischenablage.
- **`hydraulik serve`**: Editor mit lokalem Rechen-Endpunkt (POST /solve,
  stdlib-HTTP, nur 127.0.0.1). ▶-Rechnen-Button im GUI; Ergebnisse als
  Wertezeilen und Mouse-Over-Tooltips in der Zeichnung (Komponenten: V̇, Δp,
  T, Q̇, v; Leitungen: Knotendruck gauge + T über port_map). Änderungen
  invalidieren die Ergebnisse; Thermik-Fehler fallen auf Hydraulik-only mit
  Hinweis zurück. Editor prüft jetzt auch Wertebereiche (minv/maxv aus den
  ParamSpecs, z.B. opening 0…1 statt %) und hat unsichtbare Trefferflächen
  für Symbole mit Lücken. E2E getestet mit Nutzer-Schaltung (schaltung.yaml).
- Editor-Bedienkomfort: Leitungen per Drag-to-Connect (Gummiband, Einrasten
  im 16-px-Fangradius), Undo/Redo (Snapshot-History, Strg+Z/Strg+Shift+Z),
  Kälteleitungsfarben (VL hellgrün, RL dunkelgrün gestrichelt),
  Zoom (Strg+Mausrad cursorzentriert, 25–300 %, Fläche 4000×2600;
  dabei latenten Scroll-Offset-Fehler in svgPoint behoben),
  Leitungs-Knickpunkte (Doppelklick setzt/entfernt Umlenkungen, Griffe
  ziehbar, wandern bei Gruppen-Move und Kopieren mit; im layout-Block
  persistiert; eigene Doppelklick-Erkennung, da Re-Rendering das native
  dblclick verschluckt),
  Mehrfachauswahl (Rechteck/Shift+Klick) mit Gruppenverschieben und
  Kopieren/Einfügen von Teilschaltungen (Strg+C/V/D; interne Leitungen
  mitkopiert, Namen automatisch eindeutig).
- **`conduit` (Verbindungsleitung)**: universelles Leitungs-Zweitor, im
  Editor als LINIE gezeichnet (jede neu gezogene Leitung ist eine conduit) —
  ideal (Default), C-Wert, Auslegungspunkt oder Rohrmodell inkl.
  Wärmeverlust; Name/ts/Parameter im Inspector, eigene Ergebniswerte im
  Hover, Knickpunkte/Farben wie gehabt. YAML: normale Komponente + zwei
  Verbindungen ([x, lt1.in], [lt1.out, y]); Alt-Dateien mit einfachen
  Verbindungen bleiben unverändert import-/exportierbar. Damit entsteht die
  HVAC-übliche Schema-Darstellung, und das Sammler-Mischproblem entfällt
  bei realen Leitungen von selbst (jede Linie = eigene Kante).
- Wärmeabgabesysteme (radiator, floor_heating, heating_/cooling_coil):
  hydraulischer Widerstand wahlweise als C-Wert (`c_Pa_m3h2`, dichte-
  unabhängig) statt Kv; FBH damit auch ohne Rohrgeometrie parametrierbar.
- `check_valve` (Rückschlagklappe): richtungsabhängiger Widerstand
  (vorwärts Kv, rückwärts Faktor 10⁶ = Restleckage kvs/1000); der Newton-
  Solver findet die Durchflussrichtung selbst — getestet inkl. rückwärts
  angeströmter und antiparalleler Klappen.
- `cap` (dichtes Endstück, V̇ = 0): verschließt Anschlüsse für
  Teilbereichstests; keine Randbedingung nötig (Kontinuität am Sackknoten),
  Strang wird thermisch als stagnierend markiert.
- **Druckkonvention: durchgängig Überdruck (gauge)** — bei inkompressiblem
  Fluid ohne Phasenwechsel physikalisch die richtige Wahl; dokumentiert in
  README, Parametertexten und Berichtskopf. Auto-Referenz 150 kPa(ü)
  ≈ Anlagenfülldruck (über p_ref einstellbar).

## Bewusste Einschränkungen

| Einschränkung | Anmerkung / Weg zur Aufhebung |
|---|---|
| Kühlregister nur trocken (sensibel) | Entfeuchtung/Kondensat: Grey-Box-Ansatz aus vorhandenem Coil-Modell des Nutzers übernehmen; braucht Feuchtluft-Stoffwerte (CoolProp HAPropsSI) |
| Pumpe nur konst. Δp / konst. V̇ | Kennlinie Δp(Q) als Polynom: nur `hydraulic_coefficients` der Pumpe erweitern (dp_source(Q) + konsistente Ableitung in a) |
| Puffer ideal durchmischt | stationär die ehrliche Annahme; der GELADENE geschichtete Speicher ist seit v0.2 als `ideal_storage` abgedeckt; „two_zone"-Modell könnte die Weichen-Topologie (2 Knoten + interne Kante) wiederverwenden |
| T-Stück/Verteiler ohne ζ je Ast | Builder kann heute schon Kanten je Port erzeugen; nur Parameter + b.edge()-Aufrufe ergänzen |
| Fluid konstant (kein Glykol-Preset) | `Fluid`-Dataclass ist beliebig; Presets propylene_glycol_XX in fluids.py ergänzen |
| Keine Regelung (Ventilstellungen fest) | äußere Iteration über net.solve() (Stellgröße anpassen, neu lösen) ist heute schon möglich; ein `Controller`-Wrapper wäre v2 |

## Ideen v2 (priorisiert)

1. **Pumpenkennlinien** (Polynom + Drehzahlskalierung) — kleinster Aufwand, großer Nutzen.
2. **Netzplan-Visualisierung** (graphviz/matplotlib): Drücke, Volumenströme,
   Temperaturen am Graphen; Daten liegen in SolutionResult vollständig vor.
3. **Ventilautorität** im Bericht (Δp_Ventil/Δp_Kreis) — nur Auswertung, kein Solver-Eingriff.
4. **Feuchtes Kühlregister** (s.o.).
5. **Parameterstudien-Helfer**: `sweep(net, "mv1.opening", [...])` mit Ergebnistabelle.
6. **Regelkreis-Iteration**: Thermostatventile (Sollraumtemperatur → Öffnung),
   witterungsgeführte Vorlauftemperatur.

## Wiedereinstieg

1. `pip install -e ".[dev]" && pytest` (muss grün sein).
2. docs/architektur.md (Struktur) und docs/numerik.md (Solver-Herleitung) lesen.
3. Für neue Komponenten: docs/erweitern.md.
4. Plandatei der ursprünglichen Entwicklung:
   `~/.claude/plans/ich-w-rde-gerne-eine-idempotent-tome.md`
