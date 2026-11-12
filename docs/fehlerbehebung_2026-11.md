# Fehleranalyse und Fehlerbehebung (2026-11-12)

Vollständige Durchsicht des Projekts auf Fehler (Rechenkern, Luftseite,
Editoren/Server, Ein-/Ausgabe/CLI/Tools) und Behebung der reproduzierten
Befunde. Stand danach: 1256 Tests grün, alle vier e2e-Skripte „alles ok“,
Kampagne und Eindeutigkeitsprüfung unverändert zum Referenzstand
(`tools/README.md`) — kein Zufallsnetz hat die Klasse gewechselt.

Der SIMPLE-/Newton-Kern selbst (solver/hydraulic.py, thermal.py,
uniqueness.py) war unauffällig und ist unverändert. Die Fehler lagen in
ungetesteten Pfaden darum herum.

## Nutzerentscheidungen dieser Runde

| Frage | Entscheidung |
|---|---|
| Drucksensor ohne Querschnitt am direkten Partner | wie Druckrand: liegt an der Messstelle genau EIN Anschluss mit bekanntem Querschnitt, gilt dessen Geschwindigkeit; sonst Knotendruck |
| Vier Kernfehler, vermutlich schon im MATLAB-Original | alle korrigieren, als dokumentierte Abweichungen L13–L16 (PDF-Referenz bleibt exakt, Skill-Kopie identisch) |
| Raumregelung: Raumfeuchteband nicht erreichbar | bilanzkonsistenter erreichbarer Zustand + Hinweis (kein Fehler, nie leere Werte) |
| Komponentennamen mit Punkt („HK 1.OG“) | erlaubt; Portreferenzen werden am letzten Punkt getrennt |

## Rechenkern (tests/test_bugfix_kern_2026_11.py)

| Befund | Korrektur |
|---|---|
| Name mit Punkt → `KeyError` im Netzaufbau (Editor ließ ihn zu) | `rsplit(".", 1)` in network.py; Schema, Luft-Loader und Editoren gleichgezogen |
| `type` als Nicht-Text, `name:`/`self:` in einer Komponente → roher `TypeError` | gesammelte deutsche Meldungen (`component_from_dict`) |
| `settings.p_ref` wirkungslos, Referenzdruck fest 150 kPa | `Network.compile(settings)`; `solve()`, CLI und Server reichen die Einstellungen durch |
| Fühleranschluss `t_ref` des WMZ am Knoten eines Druckrands: Rand galt als Knotendruck, V̇ −10,6 % | Hook `is_tap_port(port)` je Port; Messanschlüsse zählen nie als Bauteil |
| Drucksensor an Bauteilport (im Editor der Regelfall) zeigte Totaldruck: 5,70 bzw. 7,45 kPa am statischen Rand von 5,0 kPa | Regel wie beim Druckrand (s. o.); Bericht weist Werte ohne Geschwindigkeit als „Knotendruck“ aus |
| Drucksensor mit `d_inner` an einem Rand mit Volumenstrom-Vorgabe: p_dyn = 0 | Volumenstrom des einzigen durchströmten Anschlusses der Messstelle |
| `same_node_ports` hing von der Baureihenfolge ab (Verteiler vor/nach dem Rand) | Verbindungsgruppe je Port, vor den alias-Verschmelzungen festgehalten |
| Heizkörper `q_prescribed_kW: 0` ohne `q_nom`/`kv`/`c` → Division durch 0 | Parameterfehler mit Abhilfe |
| `[x.out, x.out]` galt als verbunden | Port ist erst mit einem ANDEREN Port verbunden |
| Bericht: `m_dot_kg=… kg/s` | `m_dot=… kg/s` |

## Ein-/Ausgabe, CLI (tests/test_bugfix_io_2026_11.py)

- `load()` mit einzeiligem JSON-/YAML-Text → `OSError: File name too long`;
  leerer Text → „'.' ist ein Verzeichnis“. Behoben (`yamlio.is_path`).
- `import hydraulik` veränderte die modulglobale Resolver-Liste von ruamel
  (fremde YAML-Nutzer im selben Prozess lasen anders). Die Core-Regeln
  hängen jetzt nur noch an der eigenen Resolver-Klasse.
- Fehler kamen in bis zu drei Durchgängen (Parameter, settings,
  Verbindungen). Neu: `hydraulik.load_checked(source) → (Network,
  SolverSettings)` sammelt alles in EINER Liste; CLI `run`/`export` und der
  Server (`/solve`, `/normalize`) nutzen es. `export --json` schreibt nichts
  mehr, wenn das Modell nicht kompiliert.
- CLI: Dateiargument ist immer ein Pfad und wird einmal gelesen (fehlende
  Datei → „nicht gefunden“, `/dev/stdin` funktioniert); Kodierungs-, Lese-
  und Schreibfehler geben `FEHLER: …` statt Traceback.
- Knotenbudget `yamlio.MAX_NODES = 200 000` (Alias-Kaskaden), rekursive
  Aliase, überlange Ganzzahlen und zu tiefe Schachtelung → Validierungsfehler.
  `canonical_json` lehnt inf/nan (auch im `layout`) ab.
- Zeichenvertrag yamlio ↔ yaml_core.js: Leerraum ist nur Leerzeichen/Tab;
  rohe Steuerzeichen (C0 außer TAB/LF/CR, DEL, C1 inkl. NEL, U+2028/U+2029)
  sind überall ein Fehler; `%YAML` nur in Version 1.2; einzelne Surrogate
  sind ein Fehler; JSON mit BOM wird gelesen.
- Python-API: numpy-Skalare (int64, float32, bool_) werden angenommen;
  Werte genau an einer Bereichsgrenze scheitern nicht mehr an der Rundung
  der Einheitenumrechnung (0,36 kg/h bei Minimum 1e-4 kg/s).
- Fehlerklassen sind pickle-/copy-fest (`__reduce__`) — Ursache hängender
  `multiprocessing`-Pools.
- `conduit`: Fehler der `pipes`-Liste werden mit den übrigen
  Parameterfehlern gesammelt.
- Schema: Namen mit Punkt erlaubt (nicht leer), `layout` ohne Typvorgabe;
  Loader lehnt den leeren Komponentennamen ab.

## Luftseite (tests/test_bugfix_luft_2026_11.py, test_air_vka_pruefung.py)

Neue dokumentierte Abweichungen vom MATLAB-Original (im Code markiert,
Skill-Kopie identisch, PDF-Referenz bit-identisch):

| # | Befund | Korrektur |
|---|---|---|
| L13 | Sprühbefeuchter-Lücke: heiße, trockene Luft blieb unkonditioniert (36,5 °C/12,5 % → Zuluft 28,3 °C, Lasten 0); dazu ein 0,01-kJ/kg-Streifen in Zweig 2 | Zweig 3 gilt auch über T_max; einheitliche Toleranz |
| L14 | Frostschutz-Vorerhitzer mit vollem Massenstrom trotz Umluft stromab (22,3 statt 7,4 kW) | Massenstrom an der Einbaustelle |
| L15 | KVS: Rasterkollision (z. B. 4190/4000 m³/h) und Überlauf schalteten die WRG still ab; unmögliche Auslegung rechnete mit NaN weiter (WRG 0 kW) | Zuluft-Maske, überlauffreie Gegenstromformel; unmögliche Auslegung → Fehler mit dem nötigen Sole-Volumenstrom |
| L16 | Heizer ohne die 0,03-g/kg-Toleranz des Kühlers (Zuluft 62 % bei Bandmaximum 55 %) | gleiche Toleranz in Vor- und Nachheizer |

Weitere Korrekturen:

- **Raumregelung (`simulate_room`)**: Kandidaten müssen selbstkonsistent sein
  (angenommene Abluftfeuchte = erreichte Raumfeuchte). Ohne zulässigen
  Kandidaten wird die stationäre Feuchtebilanz gelöst und der Zustand mit
  Hinweis „Raumfeuchteband nicht erreichbar“ ausgegeben (`raum.band_erreicht`).
  Beispiel Winter −5 °C/80 %, Sorptionsrotor, kein Befeuchter, keine Last:
  12,1 % statt gemeldeter 40,3 %. Stationen stammen vom selben Betriebspunkt
  wie die Leistungen.
- **Adapter**: doppelte Register je Strang → Fehler; nicht angeschlossene
  Komponenten beeinflussen das Ergebnis nicht mehr (Hinweis); zwei
  Zuluftventilatoren verfälschen die Zuluftstation nicht mehr; KVS/Platte
  melden die erreichte Rückwärmzahl in `eta_hr`; an der WRG vertauschte
  Stränge sind ein Fehler.
- **Meldungen**: richtungsbewusste Strangfehler, deutsche Band- und
  Kernfehler, Kernwarnungen und „Zuluft-Sollband nicht erreicht“ als
  Hinweise (ab 0,2 K bzw. 1,5 % rF).
- **Kern-API**: `humidity="x"` in `simulate_room`, `p_atm`, Reihenlängen,
  NaN-Lücken (NaN-Lasten, keine Betriebsstunde), klare Meldung für das nur
  im Skill vorhandene `weather`-Modul.
- KVS-Vorlage des Editors: untere Feuchtegrenze 1 % (ohne Befeuchter).

## Editoren und Server (tests/test_bugfix_editor_2026_11.py, tools/e2e/bugfix_2026_11_e2e.py)

- Inspector blieb nach einem Auswahlwechsel am alten Bauteil (Eingaben
  landeten beim falschen Bauteil) — Formular wird je Auswahl neu gebaut,
  offene Eingaben vorher übernommen.
- Kopieren/Einfügen verlor `pipes`/`bems` einer Leitung (Kopie wurde ideal).
- Import: Verbindungsgruppen mit Leitungsende behalten alle Mitglieder;
  `settings` und Fluidname überleben Import/Export; BEMS/`pipes` als
  einzelnes Mapping, `ts: ~`, `id: 0` wie im Loader; Text ohne
  `components`-Abschnitt wird abgelehnt statt die Zeichnung zu leeren.
- Durchgängiges Escaping (kein Skript aus importierten Dateien, keine
  abgeschnittenen Texte); Geometrie aus Dateien wird auf Zahlen geprüft.
- Hängende Linie nach Löschen, Strg/Cmd+R als Drehen, veraltete Ergebnisse
  und fehlendes Autosave nach einzelnen Änderungen, Kanal an Port mit
  Messleitung (Luft), Dezimalkomma unabhängig von der Browsersprache,
  Vorlagen mit unbekanntem Typ, Leitungen an entfernten Ports (Prüfliste).
- yaml_core.js: Zeichenvertrag wie yamlio (s. o.); Ganzzahlen über 2^53
  werden abgelehnt statt ungenau gelesen.
- Server: 5-MB-Grenze, `Content-Length` Pflicht, Host-/Origin-Prüfung (403),
  striktes JSON (nicht-endliche Zahlen → null + Hinweis), belegter Port mit
  Meldung, Fehler in einem Durchgang.

## Offene Punkte

Fachfragen (bewusst nicht angefasst, Entscheidung beim Nutzer):

- Plattenwärmetauscher ohne Bypass (heizt bei 21 °C außen auf 24 °C, der
  Kühler nimmt 3 kW wieder heraus).
- Vorheizer VOR der WRG heizt auf den Zuluft-Sollwert (46,7 statt 8,1 kW).
- Feuchte-Rückzahl des Rotors springt bei T_Außen = T_Abluft (0,58 → 0,70).
- Entfeuchtung an der 6-°C-Taupunktgrenze ist alles-oder-nichts.
- Ventilatorwärme am Stranganfang; Ventilator mit Umluft stromab rechnet
  P_el und Wärme mit dem vollen Volumenstrom.
- L16-Folge: im 0,03-g/kg-Fenster läuft der Rotor weiter auf Mindestdrehzahl
  (Register 6,4 kW, Nachbarpunkte rund 2,2 kW).
- Statischer Druckrand an engem Eintritt mit weitem Austritt und kurzem Rohr
  hat im Modell keine Lösung (verlustfreier Diffusor); der Solver meldet nur
  allgemeine Nichtkonvergenz.

Technisch:

- Zwei Leitungen in Reihe (`[lt1.out, lt2.in]`) sind im Editor nicht
  darstellbar; der Import meldet sie und lässt den Rest stehen.
- Leitungen haben im Editor kein Formular für BEMS-Punkte (Import/Export
  und Kopieren erhalten sie).
- Browsertests laufen nur mit Chromium.
- `simulate_room` braucht rund 3,5 statt 2 Minuten je Simulationsjahr.
