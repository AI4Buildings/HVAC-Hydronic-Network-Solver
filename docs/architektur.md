# Architektur

## Grundidee

Eine hydraulische Schaltung wird als Graph abgebildet:

- **Knoten** = Verbindungsstellen (ideale Mischpunkte). Zustandsgrößen: Druck p, Temperatur T.
- **Kanten** = durchströmte Komponenten (Zweitor). Zustandsgröße: Volumenstrom Q.

Diese Anordnung ist das Netzwerk-Analogon eines versetzten Gitters
(staggered/MAC): p und Q liegen nie am selben Ort → kein Checkerboarding,
kein Rhie-Chow nötig.

Mehrtor-Komponenten zerfallen beim Kompilieren in Knoten und Kanten:

| Komponente | interne Struktur |
|---|---|
| `mixing_valve_3way` | 2 Kv-Kanten a→ab und b→ab (komplementäre Öffnung); Mischung entsteht am ab-Knoten |
| `hydraulic_separator` | 2 Knoten (oben: prim_in+sec_out, unten: sec_in+prim_out) + vertikale Niederwiderstandskante. Reproduziert das reale Weichenverhalten allein aus der Knotenmischung |
| `manifold`, `tee`, `buffer_storage` | alle Ports auf einen Mischknoten aliasiert (Puffer zusätzlich mit UA-Verlust am Knoten) |
| `open_end` | 1 Port + Randbedingung (Druck-Pin oder Quellterm; mehrere Fluss-RB je Knoten gehen mit ihrer jeweiligen Zulauftemperatur in die Enthalpiebilanz ein) |
| `conduit` (Verbindungsleitung) | Kante mit wählbarem Δp-Modell: ideal (Default) / C-Wert / Auslegungspunkt / Rohrmodell inkl. Wärmeverlust — im Editor als gezogene LINIE; jede Linie = eigene Kante mit bekanntem V̇ |
| `link` | quasi-widerstandsfreie Kante (1 Pa bei q_nom): trennt thermische Mischpunkte entlang einer Leitung, wo ein gemeinsamer Knoten falsch mischen würde |
| `inflow` / `outflow` | je 1 Port + Randbedingung: Zulauf T + (Fluss ODER Druck, gauge); Austritt (Druck ODER Entnahme) — Randbedingungen offener Systeme |
| `cap` | 1 Port, keine RB: dichtes Endstück, V̇ = 0 aus der Kontinuität am Sackknoten |
| `ideal_storage` | Kante, die dem austretenden Wasser t_set aufprägt (Q̇ = ṁcp·(t_set − t_ein) ist Ergebnis) — geladener Speicher im geschlossenen Kreis; optional Flusszwang (fixed_q) und/oder Druckanker p_out |

**Verschmelzungssemantik beachten:** „verbinden" heißt „einen Knoten bilden"
(ein Druck, EINE Mischtemperatur). Anschlüsse entlang einer Leitung
(Sammler) brauchen getrennte Knoten in Strömungsreihenfolge — durch
aufgeteilte Vor-/Rücklaufwiderstände (empfohlen, siehe README-Richtlinie)
oder `link`.
Sonst „sieht" eine Zapfstelle stromab eingemischtes Wasser. Mit dem
Schaltbild-Editor entsteht das Problem nicht mehr: Jede gezogene Linie ist
eine `conduit`-Kante, gemischt wird nur an den Geräteports.

## Die zwei Solver-Verträge (components/base.py)

Jede Komponente implementiert maximal zwei Funktionen:

1. **Hydraulisch** — `hydraulic_coefficients(q, fluid) -> EdgeCoefficients(a, b, dp_source)`
   für die Kantenimpulsgleichung `Δp = a·Q + b·Q·|Q| − Δp_source`.
   Alternativ `fixed_q` (Konstant-Volumenstrom-Pumpe): Kante wird zur
   Flusszwangsbedingung, ihr Δp ist Ergebnis.
2. **Thermisch** — `thermal_outlet(t_in, m_dot, fluid) -> ThermalResult(t_out, q_dot, extras)`
   mit q_dot > 0 = Wärme ins Wasser. Default: adiabat.

Neue Physik = neue Komponente = eine Datei mit diesen zwei Methoden
(siehe erweitern.md). Solver und Komponentenbibliothek sind vollständig
entkoppelt; die Solver kennen nur (a, b, dp_source) und thermal_fn.

## Kompilieren (network.py)

`Network.compile()` macht aus Komponenten + Verbindungsliste ein
`CompiledNetwork`:

1. Port-Referenzen validieren (Tippfehler → difflib-Vorschlag).
2. **Union-Find** über alle Port-Elemente; jede Verbindung merged ihre Ports.
   Ein Port in mehreren Verbindungen (oder ≥3 Ports je Eintrag) ergibt
   implizit eine Verzweigung — kein explizites T-Stück nötig.
3. `component.build(builder)` registriert interne Aliase, Kanten,
   UA-Verluste und Randbedingungen.
4. Unverbundene Ports → Fehler (alle gesammelt).
5. Union-Find-Wurzeln → Knotenliste; Kanten erhalten Knotenindizes.
6. **Druckinsel-Analyse**: Zusammenhangskomponenten des Teilgraphen aus
   druckempfindlichen Kanten (fixed_q-Kanten zählen nicht). Jede Insel ohne
   Druck-Randbedingung bekommt automatisch einen Referenzdruck (150 kPa,
   „Ausdehnungsgefäß") + Hinweis im Bericht. Inseln ohne echte Druck-RB
   müssen ihre festen Volumenströme bilanzieren, sonst
   `SingularNetworkError` mit Nennung der beteiligten Komponenten
   (häufigster LLM-Eingabefehler!).

## Datenfluss beim Lösen

```
YAML/JSON ──load_document()──> dict ──load()──> Network ──compile()──> CompiledNetwork
                                              │
                       solve_hydraulics()  ←──┘   (p, Q)        solver/hydraulic.py
                       solve_thermal(hyd)         (T, Q̇)        solver/thermal.py
                       build_result()             SolutionResult results.py
```

Da ρ, μ, cp konstant sind, ist die Hydraulik exakt von der Temperatur
entkoppelt — die sequentielle Reihenfolge ist keine Näherung.

## Eingabeformat: YAML 1.2, JSON, JSON Schema (yamlio.py, yaml_core.js, schema.py)

**Eine Ladefunktion für alles.** `yamlio.load_document(source)` (dict, Pfad
oder Text) ist die einzige Stelle, die YAML/JSON parst — Hydraulik-Loader,
Luft-Loader, Server (`/solve`, `/normalize[_air]`) und CLI nutzen sie (ein
Test verbietet direkte `yaml`-/`ruamel`-Importe anderswo). Grundlage ist
ruamel.yaml (`typ="safe"`, `pure=True`) mit eigenem Resolver und
Constructor:

- **Resolver = YAML 1.2.2 Core Schema** (Kap. 10.3.2), strikter als ruamels
  Standard (der auch `1_000`, `0b101` und Datumswerte umdeutet). Die
  regulären Ausdrücke `CORE_*` in yamlio.py und `RE_*` in yaml_core.js sind
  identisch.
- **Constructor nur für Core-Typen**: Rückgabe ausschließlich dict, list,
  str, int, float, bool, None. Mapping-Schlüssel sind der Originaltext des
  Skalars (`true:` → `"true"`, `08:` → `"08"`) — Namen werden nie zu Zahlen
  oder Wahrheitswerten, und `**spec` bekommt immer String-Schlüssel.
  Doppelte Schlüssel, nicht unterstützte Tags und Merge-Schlüssel `<<`
  werden GESAMMELT gemeldet; Syntaxfehler mit Zeile/Spalte und Kontext.
- **JSON**: `.json`-Dateien liest `parse_json` strikt (doppelte Schlüssel mit
  Zeilen, kein NaN/Infinity); `canonical_json` schreibt das Eingabemodell
  (`hydraulik export --json`).
- **Server**: Request-Bodys werden nur mit `parse_yaml` gelesen — ein Body
  wird nie als lokaler Dateipfad gedeutet.

**Werteprüfung** (params.py, components/base.py, yaml_loader.py): Bool-
Parameter melden YAML-1.1-Wörter (`yes/no/on/off`) mit Hinweis;
Ganzzahl-Parameter akzeptieren ganzzahlige Floats (deckungsgleich mit JSON
Schema `integer`); nicht endliche Zahlen und Überläufe sind unzulässig;
Labels (`ts`, BEMS-Felder) sind Zeichenketten (Ganzzahlen werden übernommen,
Float/bool sind ein Fehler mit Quoting-Hinweis). Der `fluid`-Block ist wie
Komponentenparameter deklariert (`FLUID_PRESET_PARAMS` / `FLUID_CUSTOM_PARAMS`).

**Editoren** (yaml_core.js, von editor.py in beide Templates eingesetzt):
`YamlCore.parse` liest Block- und Flow-Stil mit ruamels Token-Regeln
(Kommentare nach Strukturzeichen, `:` im Flow, Klammern nur am Wertanfang)
und lehnt Nicht-Unterstütztes (Anker, Tags, Block-Skalare, mehrzeilige
Werte, Tabulatoren) mit Verweis auf den Server-Parser ab — es liest nie still
etwas anderes als yamlio. `YamlCore.scalar` schreibt Zahlen eindeutig
(`5.0e-7`) und quotet missverständliche Strings. Absicherung:
`tests/test_editor_paritaet.py` (node) mit Korpus-, Zufallsskalar- und
Zufallsdokument-Parität sowie bitgenauem Export-Round-Trip.

**JSON Schema** (`hydraulik schema [--luft]`): generiert aus Registry,
Param-Deklarationen, `LIST_PARAMS` (z.B. conduit.pipes), FLUID_*_PARAMS und
SolverSettings — nichts wird von Hand gepflegt; `tests/test_schema.py` prüft
je Typ/Parameter/Suffix, dass Schema und Loader dieselben Werte ablehnen.
Nicht Ausdrückbares (Port-Existenz, Portanzahl, check_params) bleibt beim
Loader und ist im Schema per `description` gekennzeichnet.

## Sensoren & BEMS-Integration (components/sensors.py)

Das Schema ist zugleich semantische Karte für die Betriebsdatenanalyse
(BEMS, z.B. Aedifion). Fühler (temperature/pressure/pressure_diff_sensor)
sind reine Knotenanzapfungen: build() erzeugt nur Ports, keine Kante —
Verbinden = Knoten verschmelzen, die Hydraulik bleibt unberührt (im Editor
als dünne Messleitung = reine Verbindung, kein conduit). Volumenstromsensor
und Wärmemengenzähler sitzen als quasi-ideale Zweitore in der Leitung
(1 Pa bei q_nom); der WMZ hat zusätzlich den Fühlerport t_ref in der
Gegenleitung und misst Q̇ = ṁ·cp·(ϑ_ref − ϑ_Leitung).

Messwerte entstehen NACH dem Lösen über den duck-typed Hook
`measure(net, hyd, th, node_of)` (results.build_result sammelt sie in
SolutionResult.sensors / to_dict()["sensors"]; der Editor zeigt sie als
Tooltip). BEMS-Zuordnung: JEDE Komponente trägt die reservierten Attribute
`bems: [{id, key, description}, …]` (base._parse_bems, beliebig viele
Messpunkte) und `description` (Param, zentral im @register-Dekorator
angehängt) — rein deklarativ, ohne Einfluss auf die Berechnung.

## Register-Teillast & Greybox (components/coils.py)

Betriebsarten je Register: feste Leistung (q_prescribed, kein UA nötig) |
ε-NTU mit Teillastkorrektur UA = UA_ref·[(V̇g/V̇g,ref)·(V̇w/V̇w,ref)]^n
(Gl. 4.2 FH-Skript Wärmetechnik 2; Default n = 0.4, ohne Referenzströme
konstant) | nur Kühlregister: Greybox MIT Kondensation (Q̇ = max aus
trockenem ε-NTU und nassem ε*-NTU* mit Enthalpietreiber h_ein − h_sat(ϑ_w);
Magnus-Psychrometrie im Modul, Kondensatrate/Betriebsmodus in extras).
Der Wasserstrom kommt in jeder Iteration aus der Hydraulik; die Luftseite
ist Parameter. Im Editor wählt PARAM_MODES die sichtbaren Felder je Modus.

## Parametersystem (params.py)

`Param("dp", "pressure", required=True, ...)` deklariert einen Parameter
einmal; daraus entstehen automatisch:
- akzeptierte Schlüssel `dp_Pa | dp_kPa | dp_bar | dp_mbar` (YAML/JSON **und** Python-API),
- SI-Konvertierung, Bereichs-/Typprüfung,
- Fehlermeldungen mit gültiger Schlüsselliste und difflib-Vorschlag,
- Editor-Formular (Katalog) und JSON-Schema-Eintrag (Grenzen je Suffix-Einheit).

Neue Einheitengruppen in `UNIT_GROUPS` ergänzen, nie ad hoc konvertieren.

Reserviertes Konstruktor-Kwarg (vor der Param-Auswertung abgegriffen):
`ts=<label>` — Teilstrecken-Gruppierung für den Bericht. Die Ergebnis-
aggregation (`results._ts_segments`) verfolgt je Gruppe die Kanten als
Ketten in Strömungsrichtung (V̇, ΣΔp, p/T an den Abschnittsenden) und prüft
die klassische TS-Definition (konstanter Volumenstrom): uneinheitliche
Volumenströme oder Verzweigungen innerhalb einer Gruppe → Hinweis
„Teilstrecke neu schneiden".
