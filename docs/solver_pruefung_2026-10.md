# Prüfung des physikalischen und numerischen Solvers (Oktober 2026)

Branch `fix/solver-pruefung`, in zwei Runden (Nachtprüfung 5./6. Okt.,
offene Punkte 6. Okt. 2026), in `main` gemergt. Jeder Befund ist
reproduziert; Korrekturen sind generisch (keine Fallunterscheidung nach
Komponentennamen) und durch Tests gegen unabhängige Referenzen abgesichert
(`tests/test_solver_pruefung.py`, `tests/test_tee_idelchik.py`,
`tests/test_air_vka_pruefung.py`, `tests/test_air_vka_matlab.py`).

## Vorgehen

1. **Code-Review** des gesamten Rechenkerns: Hydraulik (SIMPLE/Newton),
   Thermik (Newton/LM), Netzaufbau, alle Komponentenmodelle,
   Ergebnisaufbereitung. Der Lüftungs-Rechenkern (VKA) wurde von einem
   separaten Agenten geprüft. Die schwersten vier Lüftungsbefunde sind
   selbst nachgerechnet.
2. **Geschlossene Lösungen.** Komponenten und kleine Netze wurden gegen
   unabhängige Referenzen geprüft (numerisch gelöstes Randwertproblem des
   Gegenstrom-WÜT, Kreuzstrom-Zellmodell, eigene EN-442-Bisektion,
   Mischtemperaturen der Weiche, Rohrverlust, Kv-Leckage, offene Systeme).
   Alle stimmen exakt überein, mit Ausnahme der bekannten
   Kreuzstrom-Näherung (bis 3,5 %).
3. **Zufallsnetz-Kampagne.** 3300 vermaschte, offene und mehrpumpige Netze
   aus der gesamten Palette, mit Wasser, Glykol und Öl. Jede gemeldete
   Lösung wird **unabhängig nachgerechnet**:
   - Kontinuität
   - Impuls mit den *bei der Lösung neu ausgewerteten* Koeffizienten
   - Kantentemperaturen aus den Modellen
   - Knotenmischung und globale Energiebilanz
   - Maximumprinzip für passive Netze
   - Eindeutigkeit bei anderen Startwerten
4. Unterstützend: Mutationstests und Iterationsverläufe der
   Nicht-Konvergenzen; Krasnoselskii-Mann-Iteration als unabhängiges Orakel
   der Thermik.

## Befunde Hydraulik/Thermik

| # | Schwere | Befund | Status |
|---|---|---|---|
| B5 | **kritisch** | Konvergenz mit veralteten Koeffizienten gemeldet (siehe unten) | behoben |
| B6 | hoch | Keine Konvergenz im laminar-turbulenten Übergang (Re ≈ 2200–3000) | behoben |
| B8 | hoch | Kurzgeschlossene Bauteile/antriebslose Maschen: Reststrom bis 0,4 m³/h | behoben |
| B9 | hoch | Divergenz-Wächter dämpft dauerhaft → nur lineare Konvergenz | behoben |
| B4 | hoch | Greybox-Kühlregister: `OverflowError` bei kleinem Wasserstrom | behoben |
| B11 | mittel | Greybox-Kühlregister: Wasser wärmer als die Luft (2. Hauptsatz) | behoben |
| B1 | hoch | Δp-Pumpe ohne `q_nom`: bis 83 % der Förderhöhe still verloren | Hinweis im Ergebnis |
| B2 | hoch | Erzeuger ohne `q_nom`: 15 kPa @ 1 m³/h → Durchfluss bricht ein | Hinweis im Ergebnis |
| B13 | mittel | Weiche ohne `q_nom`: 5,35 kPa Querdruckverlust, Entkopplung gestört | Hinweis im Ergebnis |
| B12 | gering | Thermik-Abbrüche ohne Ursachenhinweis (pathologische Festleistung) | Meldung ergänzt |
| — | gering | Psychrometrie-Überlauf bei absurden Newton-Zwischenständen | behoben |
| B3 | mittel | 3-Wege-Mischventil: gleiche Kennlinie auf A und B | behoben (2. Runde) |
| B7 | mittel | Idelchik-T-Stück: keine Lösung oder mehrdeutige Lösung | behoben (2. Runde); Mehrdeutigkeit in Maschen **offen (Modellentscheidung)** |
| B7a | mittel | Idelchik-Trennung: gerader Pfad über falsche Abszisse (Q_s statt Q_st/Q_c) | behoben (2. Runde) |
| B14 | mittel | Jacobi-Floor bremst fast geschlossene Ventile (400–4300 It.) | behoben (2. Runde) |
| B10 | gering | Thermisch unbestimmte Umläufe nicht gekennzeichnet | behoben (2. Runde) |
| B15 | mittel | Thermik: Drift-Erkennung durch Rundungs-Mini-Abstiege ausgehebelt; Rundungsgrenze knapp über tol_t als Fehler | behoben (2. Runde) |
| B16 | mittel | Heizkörper: Abschaltschwelle t_room + 0,01 K → unstetige Kennlinie, Thermik „festgefahren“ | behoben (2. Runde) |

### B5 – Falsche „Konvergenz“ (kritisch)

- **Ursache:** Die Residuen wurden *nach* dem Update mit den Koeffizienten
  des *alten* Volumenstroms geprüft. Rohre ohne Startwert beginnen bei
  `q_init`; große Nennweiten sind dort laminar, ihr Modell also linear.
  Newton löst dieses lineare Modell exakt, das Residuum mit den alten
  Koeffizienten ist null, und der Solver meldete nach **einer** Iteration
  Konvergenz.
- **Repro:** Konstantstrom-Pumpe 20 m³/h, zwei parallele Rohre DN80
  (10 m / 40 m), Wasser 50 °C. Ergebnis 16,0 / 4,0 statt 13,67 / 6,33 m³/h,
  also **+17 % ohne jede Warnung**.
- **Korrektur:** Die Residuen werden am Iterationsanfang mit den
  Koeffizienten des aktuellen Zustands geprüft. Zusätzlich muss die letzte
  Volumenstrom-Korrektur klein sein.

### B6, B8, B9 – Konvergenzverhalten

- **B6:** Im Übergangsbereich unterschätzt J = a + 2b|Q| die wahre Steigung
  (Churchill) bis Faktor 2,8, und Newton pendelt. Jetzt gilt J = max(a + 2b|Q|,
  Differenzenquotient der Kantenkennlinie).
- **B8:** Eigenschleifen (Ein- und Austritt am selben Knoten) werden vorab
  exakt gelöst: passiv V̇ = 0, eine Pumpe bekommt ihre
  Kurzschluss-Zirkulation.
- **B9:** α wird nach drei Residuen-Abnahmen in Folge wieder
  hochgesetzt.

### B4, B11 – Greybox-Kühlregister

- **B4:** Bei m* ≫ 1 läuft `exp(−NTU*(1−m*))` über. Ersetzt durch eine
  algebraisch identische, stabile Form für jedes Kapazitätsverhältnis.
- **B11:** Die konstante Sättigungs-Wärmekapazität c_s (kalibriert für
  Kaltwasser) überschätzte bei kleinem Wasserstrom die Leistung. Beispiel:
  Wasser 12,6 → 25,0 °C bei Luft 23,1 °C/91 %; die Gleichgewichtsgrenze
  liegt bei T* = 22,0 °C.
  - Jetzt gilt Q̇_nass ≤ ṁ_w·cp·(T* − T_w,ein).
  - Im validierten Bereich greift die Grenze nicht (FläktGroup-Validierung
    unverändert).

### B1, B2, B13 – Stille Default-Referenzwiderstände

Pumpe, Erzeuger und Weiche haben interne Referenzwiderstände, deren
Standardwert auf q_nom = 1 bzw. 2 m³/h bezogen ist. Bei größeren Anlagen
verfälschen sie die Lösung erheblich:

| Bauteil | Ohne `q_nom` | Mit passendem `q_nom` |
|---|---|---|
| 50-kPa-Pumpe | 4,1 m³/h | 10 m³/h |
| 50-kW-WP | 2,0 m³/h | 12,6 m³/h |

Die Numerik bleibt unverändert; es gibt jetzt Plausibilitätshinweise über den
Hook `result_notices`.

**Nebenbefund:** Im mitgelieferten **Beispiel 09** laufen die Sekundärpumpen
bei 5,3 bzw. 6,3 statt 2,9 bzw. 3,4 m³/h. Dadurch gehen 16–17 % der
Förderhöhe im 5-%-Regularisierungswiderstand verloren.

### Zweite Runde: die offenen Punkte

- **B3 – 3-Wege-Ventil.** Neuer Parameter `characteristic_b`. Default:
  A–AB gleichprozentig, B–AB linear (übliche Produkte). Gesamt-Kv bei
  Mittelstellung jetzt 0,60·Kvs statt 0,20·Kvs.
- **B14 – Jacobi-Floor.** Der Floor schrumpft je Kante ×0,1 pro Iteration,
  solange er aktiv ist und die Strömungsrichtung stabil bleibt (Untergrenze
  10⁻⁴ des Standards bzw. 1 Pa/(m³/s)). Ein Vorzeichenwechsel (pendelnde
  Rückschlagklappe) setzt ihn zurück.
  - Fast geschlossenes Ventil parallel zu einem Link: 415–487 → 19–20
    Iterationen.
- **B10 – Unbestimmte Umläufe.** Knoten, deren Temperatur nur über Kanten mit
  Steigung 1 (einseitig) von sich selbst abhängt, werden gekennzeichnet. Der
  Bericht nennt sie mit dem Hinweis „Lösung zum Startwert t_init“.
- **B15 – Thermik-Robustheit.**
  - Iterationsgrenze knapp über `tol_t` (≤ 100·tol_t, ohne Drift) liefert das
    Ergebnis mit Genauigkeitshinweis. Kampagne: 2 Fälle mit ~10⁻⁵ K.
  - Die Drift-Erkennung summiert die Verschiebung seit dem letzten
    Fortschritt von mindestens 1 %. Bei |T| ~ 10⁶ K setzten Rundungs-
    „Abstiege“ den Zähler sonst ständig zurück, und Kreise ohne Wärmequelle
    endeten undiagnostiziert an der Iterationsgrenze.
- **B16 – Heizkörper.** Bis t_room + 0,01 K galt er als „aus“, knapp
  darüber kühlte das Wasser bei kleinem Massenstrom fast auf
  Raumtemperatur ab: Sprung in der Kennlinie, Newton ohne Abstieg. Jetzt
  „aus“ erst bei T_ein ≤ t_room, Klammer relativ zur Übertemperatur.
- **B7 – Idelchik-T-Stück.**
  - **Stetigkeit:** Jeder Regimewechsel (Trennen ↔ Vereinigen, kombinierter
    Strang wechselt) liegt bei Strom 0 eines Schenkels, also unter dessen
    Tabellengrenze x = 0,1. Dort wird jetzt linear zwischen den beiden
    angrenzenden Regimen interpoliert, je an ihrer Tabellengrenze
    ausgewertet. Die Tabellen werden nur im Buchbereich [0,1; 1] benutzt.
    - Vorher sprang die Kennlinie; zwischen den Sprungwerten gab es keine
      Lösung (Stillstand bei V̇ = 0).
    - Vorher erzeugte das geklemmte Seiten-ζ < 1 einen Druckgewinn ∝ w_c²
      auf stagnierenden Schenkeln (Pumpwirkung aus dem Hauptstrom) und
      damit NaN-Divergenz.
  - **Linearisierung:** Jede Schenkelkante meldet ihre Tangente dS/dQ
    (Eigenstrom gestört, Ausgleich über den größten anderen Schenkel).
    Vorher war die Kennlinie blind für den Eigenstrom (Picard-artige
    2-Zyklen über Tabellenstützstellen).
  - **B7a Abszisse:** Laut überarbeiteten Quellnotizen (Tab. 5, Prüfplot)
    gehört der gerade Pfad der Trennung über Q_st/Q_c, nicht Q_s/Q_c.
    Übernommen ist auch die überarbeitete Scan-Lesart ζ_c.s(0,55; 1,0) = 6,00
    (vorher 6,60).
  - **Kurzschluss:** Zwei Schenkel am selben Knoten werden beim Kompilieren
    abgelehnt (neuer generischer Hook `check_topology`). Grund: Der
    statische Druckrückgewinn wirkt dort wie eine Pumpe; 20 von 68 solchen
    Netzen hatten eine zweite Lösung.
  - **Wirkung:** Alle 137 vorher nicht konvergierenden Netze mit
    Idelchik-T-Stück lösen (≤ 96 Iterationen).

### Verbleibende offene Punkte (Entscheidung)

- **Mehrdeutigkeit in Maschen durch zwei T-Stück-Schenkel.** 14,9 % der
  gelösten Netze mit Idelchik-T-Stück (getrennte Schenkel) liefern bei
  anderen Startwerten eine zweite Lösung.
  - Ursache 1: Bernoulli-Rückgewinn ohne Gegenbuchung am Netzknoten. Knoten
    kennen nur den statischen Druck, die Beschleunigung in einen Schenkel
    kostet nichts. Ohne Bernoulli-Umrechnung (reine Totaldruckverluste)
    sinkt die Quote auf 5,5 %.
  - Ursache 2: nicht-monotones ζ(x) (negative ζ_c.s, U-förmige
    Durchgangstabelle).
  - Entscheidung nötig: die statische Umrechnung beibehalten (Quellnotizen,
    Handrechnungsvalidierung) oder auf Totaldruck-Knoten umstellen.
- **Sammler-Konvention (Idelchik 7-10).** Die überarbeiteten Quellnotizen
  lesen c als geraden Zulauf (Q_st = Q_c + Q_s). Sie vermerken selbst, dass
  dies der gedruckten (1 − Q_s/Q_c)²-Umrechnung widerspricht und „vor einer
  Implementierung fachlich freizugeben“ ist. Implementiert bleibt c =
  kombinierter Strang (siehe docs/idelchik_t_stueck_sammlung.md, Abschn. 7).
- **Kampagnen-Seed 913 (pathologisch).** Eine Konstantstrom-Pumpe drückt
  18,8 m³/h rückwärts durch eine sperrende Rückschlagklappe; die
  Absolutdrücke liegen bei ~5·10¹² Pa.
  - Druckdifferenzen einer Messleitung in eine Sackgasse liegen dann unter
    der Gleitkomma-Auflösung, der Massendefekt stagniert bei 7·10⁻⁶.
  - Ein an die Druckauflösung gekoppelter Jacobi-Floor löste den Fall,
    verlangsamte aber 266 gewöhnliche Netze und wurde verworfen.
- **Kreuzstrom-Näherung.** Die Incropera-Korrelation weicht bis 3,5 % ab
  (kleines NTU, Cr = 1). Das ist bekannt und akzeptabel.

## Lüftungs-Rechenkern (VKA)

Der Kern war 1:1 gegen MATLAB verifiziert und identisch mit dem Skill. Die
Befunde L1–L12 sind in der zweiten Runde als **dokumentierte Abweichungen
vom MATLAB-Original** behoben, jeweils im Code mit „Abweichung vom
MATLAB-Original (Solver-Prüfung 2026-10, L#)“ gekennzeichnet. Die
Skill-Kopie `vka-effizienz-en16798` ist identisch gepatcht.

Die PDF-Referenzanlagen (2 × 7 Betriebspunkte, `validate_vka.py`, jetzt
auch als `tests/test_air_vka_matlab.py`) bleiben **exakt** (max. Δ 0,0000).
Das GEA-Datenblatt (Winterfall) ist ebenfalls unverändert.

| # | Befund | Korrektur |
|---|---|---|
| L1 | Sprühbefeuchter: T_aus bei x_min_Tmin statt x_aus — bis 7,1 kW Enthalpie aus dem Nichts | T_aus beim tatsächlichen x_aus |
| L2 | Kühler-Zweig 5 invertiert (heiße, trockene Luft ungekühlt); Rotor schaltet ab, wenn keine Drehzahl die Feuchte im Band hält | Bedingung korrigiert (mit Befeuchter stromab nur über T_max); Rotor: beste Temperatur im Band, sonst reine Temperaturregelung. ROT_NH 35 °C/20 %: Kühler 41,9 → 15,3 kW |
| L3 | ε nur ≤ 1 begrenzt: Fortluft x = −2,37 g/kg bei V̇_ab/V̇_zu = 0,5 | ε ≤ min(1, V̇_ab/V̇_zu) der WRG-Ströme; Rotoraustritt nicht übersättigt |
| L4 | Ohne gezeichneten Ventilator entfiel die WRG; Default-SFP 1250 ohne Ventilator | Token wird übersprungen, wo es steht; SFP = 0 ohne Ventilator |
| L5 | Fortluftstation hinter Abluftventilator nach der WRG = Raumluft | Ventilator setzt den Zustand nur vor der WRG zurück |
| L6 | Vorheizer heizt, meldet 0 kW (Zweig „zu feucht, kein Kühler“) | Leistung m·Δh |
| L7 | KVS: Summen-SFP in Zu- UND Abluft → Wärme 120 % von P_el; Energiesumme doppelt | `SFP_exh_frac` teilt die Summe auf; Abluftventilator vor der WRG aus der Zeichnung |
| L8 | Adiabate Abluftkühlung (Zweig D) mit Basis 0: Abluft 26 → 42,7 °C, 9,6 → 3,0 g/kg | Basis x_ABL |
| L9 | rwz_n ≥ 1 (ZeroDivision/NaN/2. HS), Umluft > Zuluft, V̇_ab = 0 | rwz_n ≤ 0,99; Umluft ≤ min(V̇_zu, V̇_ab); V̇_ab > 0 — Validierungsfehler |
| L10 | Dampfbefeuchter, Sättigungszweig: Wassermenge 3,88 statt 2,42 g/s | dx_Bef aktualisiert |
| L11 | Aktive Komponenten im Abluftstrang halb angewandt; Befeuchter/NHR-„danach“ ohne Reihenfolge | Ablehnung mit Meldung; Flags aus der Reihenfolge |
| L12 | MATLAB-Index 0 = aus als −1 (= volle Leistung) in KVS und Rotor; P_el ohne V̇_ab; Kreuzstrom-NTU-Rangfolge; Taupunkt bei 0 °C geklemmt | alle korrigiert (Taupunkt-Suche bis −60 °C) |
| (b) | WRG-Auslegungsvolumenstrom fest 4500 m³/h (η 16–21 % über Referenz bei 1359 m³/h); übersättigte Zustände unsichtbar | Default = Zuluftvolumenstrom; Hinweis bei x > x_s |

Neu gefunden beim Robustheits-Sweep:
- **Befund:** Übersättigte Umluftmischung (Nebel) wurde vom Kühler durch
  Heizen „entfeuchtet“; Folge: negative Kühllasten bis −18 kW.
- **Korrektur:** Die übersättigte Mischung kondensiert bei gleicher Enthalpie
  aus, und der Kühler heizt nie.
- **Ergebnis des Sweeps:** 190 512 Extrempunkte, 0 NaN, 0 Ausnahmen,
  0 negative Lasten (vorher 198 Spezifikationen mit negativer Last).

Wirkung auf die Editor-Vorlagen:
- Die beiden Rotor-Vorlagen sind unverändert.
- KVS-Vorlage: Heizlast 16,32 → 17,26 kW. Das ist der Wegfall der doppelt
  gezählten Ventilatorwärme: 0,6 · 3,125 kW · 0,5 = 0,94 kW.

Geprüft und korrekt (unverändert):
- **Psychrometrie** (gegen CoolProp): ≤ 0,26 %
- **Komponentenbilanzen:** VHR, NHR, KR, WRG stimmen mit ṁ·Δh überein
- **KVS-Kreis:** schließt in der Bilanz
- **Umluft-Mischung:** korrekt

Konventionen des Referenzwerkzeugs (dokumentiert, nicht geändert):
- Die gesamte Ventilatorwärme geht bei Rotoren in die Zuluft.
- Luftdichte bei T_Soll,Mitte.
- Platte und KVS bilanzieren trocken; das wird jetzt per Übersättigungs-
  hinweis sichtbar.
- Die Sprühbefeuchter-„Leistung“ ist die Enthalpie des Wassers.
- p = 10⁵ Pa fest.

## Wirkung der Korrekturen (Kampagne, unabhängig nachgerechnet)

3300 Zufallsnetze, gleicher Generator und Prüfer wie in der ersten Runde.

| Kennzahl | vor der Prüfung | nach Runde 1 | nach Runde 2 |
|---|---|---|---|
| Impulsrest bei Nachrechnung (Lösung falsch, aber „konvergiert“) | 24 / 300 | 0 / 3300 | 0 / 3300 |
| Nichtkonvergenz Hydraulik | 10 / 300 (ohne Idelchik-T) | 149 / 3300 (137 Idelchik-T) | **1** / 3300 (Seed 913, pathologisch) |
| Nichtkonvergenz Thermik (ohne Diagnose) | — | 14 / 3300 | **0** |
| Modellabstürze (`OverflowError`) | 9 / 300 | 0 | 0 |
| gelöste Netze | — | 2688 | 2947 |
| signifikante Startwertabhängigkeit der Ströme | — | 109 (32 Idelchik-T) | 95 (44 Idelchik-T, s. offene Punkte) |
| davon ohne Idelchik-T | 21 / 226 | 77, nur ≤ 15 l/h in antriebslosen Maschen | 51, nur ≤ 15 l/h |

Weitere Ergebnisse:
- Die Beispielergebnisse sind unverändert (≤ 2,5·10⁻⁶ relativ).
- Iterationen hydraulisch (Median / 95 % / Maximum): Runde 1 11 / 32 / 382,
  Runde 2 11 / 19 / 122.
- Iterationen thermisch (Median / 95 %): 1 / 8. Zwei Fälle enden an der
  Iterationsgrenze (500) und werden mit Genauigkeitshinweis (~10⁻⁵ K)
  geliefert.
- 69 Netze mit kurzgeschlossenem Idelchik-T-Stück werden jetzt mit
  Validierungsfehler abgelehnt.
