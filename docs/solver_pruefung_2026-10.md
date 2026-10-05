# Prüfung des physikalischen und numerischen Solvers (Oktober 2026)

Branch `fix/solver-pruefung` (nicht gemergt). Jeder Befund ist reproduziert;
Korrekturen sind generisch (keine Fallunterscheidung nach Komponentennamen)
und durch Tests gegen unabhängige Referenzen abgesichert
(`tests/test_solver_pruefung.py`).

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
| B3 | mittel | 3-Wege-Mischventil: gleiche Kennlinie auf A und B | **offen (Entscheidung)** |
| B7 | mittel | Idelchik-T-Stück: keine Lösung oder mehrdeutige Lösung | **offen** |
| B14 | mittel | Jacobi-Floor bremst fast geschlossene Ventile (400–4300 It.) | **offen** |
| B10 | gering | Thermisch unbestimmte Umläufe nicht gekennzeichnet | **offen** |

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

### Offene Punkte (Entscheidung bzw. Weiterentwicklung)

- **B3 – 3-Wege-Ventil.** Mit dem Standard *gleichprozentig* auf beiden Wegen
  ist der Gesamt-Kv bei Mittelstellung nur 0,20·Kvs (EQ/linear: 0,60,
  linear/linear: 1,00). Übliche Produkte sind A–AB gleichprozentig und
  B–AB linear.
  - Vorschlag: Parameter `characteristic_b`.
  - Der Standardwert ändert bestehende Ergebnisse, daher ist eine
    Entscheidung nötig.
- **B7 – Idelchik-T-Stück.**
  - In der Kampagne gab es 137 Nicht-Konvergenzen von 3300 (49 davon
    NaN-Divergenz) sowie startwertabhängige Lösungen (z. B. −10,3 bzw.
    −19,9 m³/h).
  - Ursache: Der Regimewechsel Trennen↔Vereinigen bei Schenkelstrom 0 macht
    ζ unstetig. Bernoulli-Umrechnung und explizit nachgeführte
    Druckgewinne machen die Kennlinie nicht-monoton.
  - Übliche Verteil-/Sammel- und Einrohr-Konfigurationen konvergieren.
    Kritisch sind Maschen durch zwei Schenkel.
  - Vorschlag: zwei Schenkel am selben Knoten beim Kompilieren ablehnen;
    ζ am Regimewechsel stetig machen; Dämpfung bzw. Liniensuche.
- **B14 – Jacobi-Floor.** Der Floor J ≥ b·10⁻³·Startwert bremst Kanten,
  deren Gleichgewichtsstrom darunter liegt (fast geschlossene Ventile,
  gesperrte Rückschlagklappen).
  - Mit `q_eps_frac = 1e-6` brauchen diese Netze 22–77 statt 400–4300
    Iterationen.
  - Als Standard ist der Wert aber ungeeignet: Zwei Zufallsnetz-Tests mit
    wechselnder Strömungsrichtung an Rückschlagklappen konvergieren damit
    nicht mehr.
  - Abhilfe heute: die Einstellung `q_eps_frac=1e-6` oder ein höheres
    `max_iter`.
  - Vorschlag: adaptiver Floor.
- **B10 – Thermisch unbestimmte Umläufe.** Betroffen sind adiabate Umläufe
  ohne Quelle sowie nur heizende WP in einem verlustfreien Kreis. Das
  Ergebnis ist dann der Startwert `t_init`, ohne Kennzeichnung.
  - Vorschlag: Hinweis bei singulärer Linearisierung.
- **Kreuzstrom-Näherung.** Die Incropera-Korrelation weicht bis 3,5 % ab
  (kleines NTU, Cr = 1). Das ist bekannt und akzeptabel.

## Lüftungs-Rechenkern (VKA)

Der Kern ist laut Projektdoku 1:1 gegen MATLAB verifiziert und mit dem Skill
identisch. Deshalb ist **nichts geändert**: Abweichungen von der Referenz
erfordern eine Entscheidung. Reproduziert sind:

- **L1 (hoch) Sprühbefeuchter**, Zweig 2 (`vka_chain.py:304`):
  - T_aus wird mit `x_min_Tmin` statt `x_aus` berechnet.
  - Folge: bis 7,1 kW Enthalpie aus dem Nichts (64 von 780 Punkten),
    Zuluft außerhalb des Sollbands.
- **L2 (hoch) Kühler**, Zweig 5 (`vka_chain.py:254`):
  - Die Bedingung ist invertiert, bei heißer, trockener Luft wird nicht
    gekühlt.
  - Beispiel ROT_NH bei 35 °C/20 %: Zuluft 35,6 °C, Q_KR = 0, Rotor aus.
- **L3 (hoch) ε-Kappe**:
  - Sie begrenzt nur auf ε ≤ 1, nicht auf ε·ṁ_zu ≤ ṁ_ab.
  - Folge bei unbalancierten Volumenströmen: Fortluft-Feuchte
    −2,37 g/kg (φ = −206 %).
- **L4 (hoch) Ohne gezeichneten Ventilator** (Adapter):
  - Die WRG wird übersprungen (`order[0] ≠ Vent_ZUL`).
  - Folge: Heizlast 9,86 statt 0 kW.

Weitere Punkte L5–L12 (Fortluft-Station, VHR-Leistung 0, Ventilatorwärme
KVS, adiabate Abluftkühlung, Validierungslücken, Dampfbefeuchter-Wassermenge
u. a.) stehen im Agentenbericht. Geprüft und korrekt sind:

- **Psychrometrie** (gegen CoolProp): ≤ 0,26 %
- **Komponentenbilanzen:** VHR, NHR, KR, WRG stimmen mit ṁ·Δh überein
- **KVS-Kreis:** schließt in der Bilanz
- **Umluft-Mischung:** korrekt
- **Robustheit:** 190 512 Extrempunkte ohne NaN

## Wirkung der Korrekturen (Kampagne, unabhängig nachgerechnet)

| Kennzahl | vorher | nachher |
|---|---|---|
| Impulsrest bei Nachrechnung (Lösung falsch, aber „konvergiert“) | 24 / 300 | 0 / 3300 |
| Signifikante Startwertabhängigkeit der Ströme (ohne Idelchik-T) | 21 / 226 | nur Kleinstströme ≤ 15 l/h |
| Hydraulik-Abbrüche ohne Idelchik-T | 10 / 300 | 12 / 3300 (11 davon nur langsam, B14) |
| Modellabstürze (`OverflowError`) | 9 / 300 | 0 |

Weitere Ergebnisse:
- Die Beispielergebnisse sind unverändert (≤ 2,5·10⁻⁶ relativ).
- Iterationen der Kampagne (Median / 95 % / Maximum): hydraulisch 11 / 33 /
  382, thermisch 1 / 8 / 33.
