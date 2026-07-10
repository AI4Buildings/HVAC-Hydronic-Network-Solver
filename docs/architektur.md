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
| `link` | quasi-widerstandsfreie Kante (1 Pa bei q_nom): trennt thermische Mischpunkte entlang einer Leitung, wo ein gemeinsamer Knoten falsch mischen würde |
| `inflow` / `outflow` | je 1 Port + Randbedingung: Zulauf T + (Fluss ODER Druck, gauge); Austritt (Druck ODER Entnahme) — Randbedingungen offener Systeme |
| `cap` | 1 Port, keine RB: dichtes Endstück, V̇ = 0 aus der Kontinuität am Sackknoten |
| `ideal_storage` | Kante, die dem austretenden Wasser t_set aufprägt (Q̇ = ṁcp·(t_set − t_ein) ist Ergebnis) — geladener Speicher im geschlossenen Kreis; optional Flusszwang (fixed_q) und/oder Druckanker p_out |

**Verschmelzungssemantik beachten:** „verbinden" heißt „einen Knoten bilden"
(ein Druck, EINE Mischtemperatur). Anschlüsse entlang einer Leitung
(Sammler) brauchen getrennte Knoten in Strömungsreihenfolge — durch
aufgeteilte Vor-/Rücklaufwiderstände (empfohlen, siehe README-Richtlinie)
oder `link`. Sonst „sieht" eine Zapfstelle stromab eingemischtes Wasser.

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
YAML/dict ──load()──> Network ──compile()──> CompiledNetwork
                                              │
                       solve_hydraulics()  ←──┘   (p, Q)        solver/hydraulic.py
                       solve_thermal(hyd)         (T, Q̇)        solver/thermal.py
                       build_result()             SolutionResult results.py
```

Da ρ, μ, cp konstant sind, ist die Hydraulik exakt von der Temperatur
entkoppelt — die sequentielle Reihenfolge ist keine Näherung.

## Parametersystem (params.py)

`Param("dp", "pressure", required=True, ...)` deklariert einen Parameter
einmal; daraus entstehen automatisch:
- akzeptierte Schlüssel `dp_Pa | dp_kPa | dp_bar | dp_mbar` (YAML **und** Python-API),
- SI-Konvertierung, Bereichs-/Typprüfung,
- Fehlermeldungen mit gültiger Schlüsselliste und difflib-Vorschlag.

Neue Einheitengruppen in `UNIT_GROUPS` ergänzen, nie ad hoc konvertieren.

Reserviertes Konstruktor-Kwarg (vor der Param-Auswertung abgegriffen):
`ts=<label>` — Teilstrecken-Gruppierung für den Bericht. Die Ergebnis-
aggregation (`results._ts_segments`) verfolgt je Gruppe die Kanten als
Ketten in Strömungsrichtung (V̇, ΣΔp, p/T an den Abschnittsenden) und prüft
die klassische TS-Definition (konstanter Volumenstrom): uneinheitliche
Volumenströme oder Verzweigungen innerhalb einer Gruppe → Hinweis
„Teilstrecke neu schneiden".
