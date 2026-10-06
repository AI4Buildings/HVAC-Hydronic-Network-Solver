# Prüfwerkzeuge (nicht Teil der Testsuite)

Diese Werkzeuge haben die Solver-Prüfung Oktober 2026
(`docs/solver_pruefung_2026-10.md`) getragen. Sie laufen bewusst außerhalb von
`pytest`, weil sie lange rechnen bzw. einen Browser brauchen. Vor Änderungen am
Solver, an Komponentenmodellen oder an den Editoren laufen lassen und mit dem
Referenzstand vergleichen.

## `pruefkampagne/` – Zufallsnetze mit unabhängiger Nachrechnung

| Skript | Zweck | Laufzeit (8 Kerne) |
|---|---|---|
| `kampagne.py START ANZAHL [--quick]` | 3300 Zufallsnetze aus der ganzen Palette (vermascht, offen, mehrpumpig; Wasser/Glykol/Öl). Jede Lösung wird unabhängig nachgerechnet: Kontinuität, Impuls mit neu ausgewerteten Koeffizienten, Kantentemperaturen, Knotenmischung, globale Energiebilanz, Maximumprinzip; ohne `--quick` zusätzlich ein Zweitlauf mit anderen Startwerten | ~10 s (`--quick`), ~20 s |
| `nachrechnung.py` | der unabhängige Prüfer (`check_hydraulics`, `check_thermal`, `solve_raw`) – nutzt nur die Komponentenverträge, nicht die Solver-Residuen | – |
| `eindeutigkeit.py [START ANZAHL]` | alle Netze mit nicht-monotonen Bauteilen (Idelchik-T-Stück) über den produktiven Einstieg `solve_hydraulics_checked`; zählt gemeldete Mehrfachlösungen und Neustarts | ~10 s |
| `stabilitaet.py SEED …` | sammelt Mehrfachlösungen (17 Starts, Nachschärfen) und beurteilt ihre dynamische Stabilität (lineare Maschendynamik, Urteil für jede Trägheitsverteilung) | Sekunden je Netz |
| `netz_als_dokument.py SEED` | Kampagnennetz → JSON-Dokument, z.B. als Regressionsnetz für einen Test | – |

**Referenzstand 2026-10-06** (`kampagne.py 0 3300 --quick`):

| Klasse | Anzahl |
|---|---|
| ok | 2933 |
| ISSUES | 18 |
| expected_no_steady | 89 |
| expected_validation | 258 |
| UNEXPECTED_convergence | 1 |
| generator | 1 |

- **expected_no_steady:** Kreise ohne Wärmequelle/-senke bei fester Leistung; korrekt erkannt.
- **expected_validation:** gewollte Validierungsfehler, z.B. kurzgeschlossenes T-Stück oder widersprüchliche Druckränder.
- **UNEXPECTED_convergence:** Seed 913, pathologisch mit ~5·10¹² Pa (Konstantstrom gegen sperrende Rückschlagklappe).
- **generator:** Seed 3078, ein Parameterfehler des Generators, kein Solverbefund.
- **ISSUES (18, alle erklärt, Stand 2026-10-06):**
  - *10× feste Leistung bei Kleinstdurchfluss*
    - Seeds 399, 437, 453, 706, 732, 757, 807, 1047, 2385, 2414.
    - Mischungs-/Austrittstemperaturen sind absurd (bis −2,4·10⁶ °C), die
      globale Bilanz weicht ab (bis 1 kW, Seed 706).
    - Der Solver meldet beides selbst: Plausibilitätshinweis und
      Bilanzzeile im Bericht. Der Prüfer bestätigt, dass die Bilanz des
      Solvers mit der unabhängigen Nachrechnung übereinstimmt.
  - *6× Konditionsgrenze der Thermik*
    - Seeds 529, 1323, 1328, 1711, 2329, 3098; dazu 3157, 3200 mit
      Bilanzrest ≤ 3 W bei 20–35 kW.
    - Ein großer Umlauf hängt über einen winzigen Teilstrom (Anteil
      ~10⁻⁶) an seiner einzigen Temperaturvorgabe.
    - Der Rest der Knotenbilanz (< 10⁻⁶ K, Abbruchkriterium) wird mit
      ~1/Anteil zum Temperaturfehler verstärkt: Maximumprinzip um
      5·10⁻⁶ … 1,2·10⁻⁴ K überschritten, Bilanzrest ≤ 2·10⁻⁵ relativ.
    - Technisch bedeutungslos. Ein schärferes Kriterium (Fehler statt
      Residuum) wäre möglich, ist aber nicht umgesetzt.
- Thermisch unbestimmte Knoten (Umlauf ohne jede Wärmeübertragung) nimmt
  der Prüfer vom Maximumprinzip aus; ihre Temperatur ist willkürlich und
  wird vom Solver gekennzeichnet.

Ohne `--quick` vergleicht der Zweitlauf die Lösung ohne Nachschärfen mit
Schwellen auf Toleranzniveau. Er meldet daher auch Toleranzreste in
antriebslosen bzw. schwach angetriebenen Maschen: Stand 2026-10-06 sind
1032 weitere Netze nur mit Eindeutigkeitsmeldungen. Das ist ein Rohsignal.
Echte Mehrdeutigkeit wird mit `eindeutigkeit.py` und `stabilitaet.py`
bewertet (mit Nachschärfen, Schwelle wie im Solver).

`eindeutigkeit.py 0 3300` (Stand 2026-10-06):

| Ergebnis | Anzahl |
|---|---|
| Netze mit Idelchik-T-Stück, gelöst | 310 |
| davon als mehrdeutig gemeldet | 59 |
| davon nur per Neustart gelöst | 3 |
| Validierungsfehler (kurzgeschlossenes T-Stück u.a.) | 86 |
| Generatorfehler | 3 |

Alle Lösungen der mehrdeutigen Netze sind dynamisch stabil.

Ergebnisse landen als JSON im aktuellen Verzeichnis (`kampagne_*.json`,
`eindeutigkeit_*.json`). Vergleich alt/neu: Klassen je Seed gegenüberstellen;
jede neue Abweichung erklären, bevor eine Änderung als fertig gilt.

**Lehren aus der Prüfung:**
- Modelländerungen an **allen** Netzen gegenprüfen, nicht nur an den bekannten
  Problemfällen. Beim T-Stück auf Totaldruck verschwanden 70 Mehrdeutigkeiten,
  45 kamen neu dazu.
- `Pool.map` hängt, wenn ein Worker eine nicht picklebare Ausnahme wirft
  (z.B. `ComponentParamError`). Generatorfehler deshalb im Worker abfangen.

## `e2e/` – Browsertests beider Editoren (Playwright)

```bash
pip install -e ".[dev,e2e]" && python -m playwright install chromium
python tools/e2e/editor_e2e.py        # Import/Export/Autosave/Rechnen, Server + statisch
python tools/e2e/vorlagen_e2e.py      # alle Vorlagen: Editor ↔ Python-Loader ↔ JSON Schema
python tools/e2e/eindeutigkeit_e2e.py # Dialog bei mehrdeutiger Hydraulik
```

Jedes Skript endet mit `ERGEBNIS: alles ok` bzw. listet die Fehler.

## `vka_skill_abgleich.py` – Lüftungskern ↔ Skill-Kopie

```bash
python tools/vka_skill_abgleich.py [~/.claude/skills/vka-effizienz-en16798/scripts]
```

Vergleicht `src/hydraulik/air/vka/` mit der Skill-Kopie. Die Importform
(relativ/absolut) und die Zusatzausgabe der Stationszustände werden dabei
ausgeglichen. Stand 2026-10-06: identisch, inklusive der Korrekturen L1–L12.
Liegt der Skill auf einem neuen Rechner in einer älteren Fassung vor, zeigt
der Diff die fehlenden Korrekturen. Nach dem Nachziehen im Skill
`python3 validate_vka.py` laufen lassen.
