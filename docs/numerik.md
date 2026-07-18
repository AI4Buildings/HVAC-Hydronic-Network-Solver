# Numerik

## 1. Hydraulik: SIMPLE-Druckkorrektur auf dem Netzgraphen

### Gleichungen

- Kantenimpuls (je Komponente e von Knoten i nach j):
  `F_e = a·Q + b·Q·|Q| − (p_i − p_j) − Δp_source = 0`
- Knotenkontinuität: `A·Q = q_quellen` (A = Knoten-Kanten-Inzidenzmatrix, ±1)

Versetzte Anordnung: p, T an Knoten; Q auf Kanten → Checkerboarding
strukturell unmöglich.

### Iterationsschritt (solver/hydraulic.py)

1. **Impulsprädiktor** in Newton-Inkrementform mit der Jacobi-Steigung
   `J = a + 2b·|Q_k|`:

   `Q* = Q_k + α_q · (p_i − p_j + Δp_source − (a·Q_k + b·Q_k|Q_k|)) / J`

2. **Druckkorrektur**: Kontinuitätsdefekt `r = q_quellen − A·Q*`, System

   `K·p' = r`,  `K = A·diag(1/J)·Aᵀ`  (gewichteter Graph-Laplacian, SPD
   nach Pinning der Druck-Randknoten; Jacobi-Skalierung gegen die großen
   SI-Größenordnungen b ~ 1e10…1e12, dann `scipy.sparse.linalg.spsolve`).

3. **Korrektur**: `Q ← Q* + (1/J)·(p'_i − p'_j)` (Kontinuität danach linear
   exakt erfüllt), `p ← p + α_p·p'`.

### Warum diese Form (wichtigste Erkenntnis des Projekts)

Die naive SIMPLE-/Linear-Theory-Form `Q* = Δp/R_lin` mit `R_lin = a + b|Q|`
**divergiert oszillierend** bei quadratischen Widerständen (im ersten
Testlauf reproduziert: Vorzeichenwechsel mit wachsender Amplitude).

Mit der Inkrementform und **derselben Steigung J in Prädiktor und
Korrektur** ist ein Iterationsschritt bei α_p = α_q = 1 algebraisch
identisch mit einem Newton-Schritt des gekoppelten Systems, gelöst über
das Schur-Komplement des Druckblocks:

```
[ J   −Aᵀ ] [δQ]   [−F]          A·J⁻¹·Aᵀ·δp = −G + A·J⁻¹·F
[ A    0  ] [δp] = [−G]    ⇒     δQ = J⁻¹(−F + Aᵀ·δp)
```

→ quadratische Konvergenz, typisch 3–6 Iterationen, Massendefekt ~1e-16.
Defaults α = 1.0; ein Divergenz-Wächter halbiert die Relaxation, wenn der
Impulsdefekt 5× in Folge steigt (bis minimal 0.1).

### Robustheitsmaßnahmen

| Problem | Maßnahme |
|---|---|
| Übergangsbereich Re ≈ 2300 erzeugt Grenzzyklen bei hartem Umschalten | **Churchill (1977)**: glatte f(Re)-Korrelation über alle Bereiche; laminarer Anteil exakt als linearer Term a (friction.pipe_coefficients) |
| Q → 0 bei rein quadratischen Elementen (a = 0) → J → 0 | R-Floor: `J ≥ max(b·q_eps, 1e-3)` mit q_eps = 0.1 % des Seed-Volumenstroms |
| Ventil fast zu → riesiger Widerstand | Kennlinien-Floor `Kv_eff ≥ Kvs/Rangeability` für 0 < opening < 1 (reale Regelbereichsgrenze / Stellverhältnis, valves.valve_kv) |
| Ventil ganz zu (opening = 0, bzw. Endlage beim 3-Wege-Ventil) | KEIN Leckage-Kv: Kante wird zur Randbedingung Q = 0 (`fixed_q`). Druckentkopplung übernimmt die Inselanalyse (Auto-Referenzdruck je Teilnetz); unmögliche Fälle (Konstantstrom-Pumpe gegen zu) fängt der Bilanzcheck mit klarer Meldung ab. Δp über dem Sitz ist Ergebnis |
| Ideale Δp-Pumpe (R = 0) → Q auf der Kante unbestimmt | interner quadratischer Widerstand: 5 % von Δp beim Nennvolumenstrom |
| Geschlossener Kreis: p nur bis auf Konstante bestimmt | Druckinsel-Analyse; Auto-Referenz 150 kPa je Insel ohne Druck-RB (Hinweis im Bericht) |
| Konstantstrom-Pumpen/Fluss-RB unvereinbar | Bilanzcheck je Druckinsel zur **Compile-Zeit** → `SingularNetworkError` mit Komponentennamen (statt kryptischer Singularität im Solver) |

Konstantstrom-Kanten: `Q = fix`, Koeffizient d = 1/J = 0 im Laplacian
(keine Druckkopplung), Δp ist Ergebnis.

### T-Stück mit Idelchik-Druckverlust (components/idelchik.py, separators.Tee)

ζ hängt vom Volumenstromverhältnis der GESCHWISTERKANTEN ab → generischer
Solver-Hook `pre_coefficients(q_eigene_kanten, fluid)`: Komponenten mit
gekoppelten Kanten erhalten vor jeder Koeffizientenauswertung ihre aktuellen
Kantenflüsse (Picard-nachgeführt). Der kombinierte Strang (max |Q|) bleibt
verlustfrei, Abzweig-/Durchgangskante tragen die vollen Pfadbeiwerte
(Vereinigung/Trennung automatisch aus der Flussrichtung; Tabellen bilinear
interpoliert, an den Rändern geklemmt). Die ζ sind TOTALDRUCK-Beiwerte —
je Pfad wird die Bernoulli-Differenz auf statische Knotendrücke umgerechnet:
p_ein − p_aus = ζ·ρw_c²/2 + ρ(w_aus² − w_ein²)/2. Widerstandsartige Anteile
gehen als quadratischer Koeffizient in J ein; Druck-GEWINNE (negative ζ_c.s
der Vereinigung, Injektorwirkung; Diffusor-Rückgewinn) werden als
nachgeführte Druckquelle dp_source behandelt, damit der Laplacian SPD
bleibt. Netz ganz ohne Kanten: trivialer Frühausstieg (Drücke = Anker).
Validierung: tests/test_tee_idelchik.py (Handrechnung Trennung x = 0.4 und
Vereinigung x = 0.1 mit ζ = −0.65 auf 0.2 Pa genau; Tabellenquelle
dokumentiert in docs/idelchik_t_stueck_*.md).

Konvergenzkriterien (relativ): Massendefekt / max|Q| < 1e-8 und
Impulsdefekt / Druckmaßstab < 1e-6.

## 2. Thermik: Upwind-Advektion (solver/thermal.py)

Läuft nach Hydraulik-Konvergenz (exakt entkoppelt, da Stoffwerte konstant).

- **Upwind**: jede Kante erhält T_ein vom stromauf liegenden Knoten
  (Vorzeichen von Q); Strömungsumkehr damit automatisch korrekt.
- **Kante**: Komponentenmodell liefert `T_aus, Q̇ = f(T_ein, |ṁ|)`.
- **Knoten**: ideale Mischung
  `T = (Σ ṁ_zu·cp·T_aus + ṁ_RB·cp·T_zulauf + UA·T_amb) / (Σ ṁ_zu·cp + ṁ_RB·cp + UA)`
- Gauss-Seidel-Sweeps bis max|ΔT| < 1e-6 K; fällt der Fehler am Sweep-Limit
  nachweislich geometrisch (Trendprüfung über Fenstermaxima, z.B. große
  Rezirkulationsverhältnisse über Bypässe → Kontraktionsfaktor nahe 1),
  wird bis 20× max_iter_thermal fortgesetzt; echte Drift (konstante Rate)
  bricht die Trendprüfung wie bisher ab. Konvergiert, weil jedes
  Wärmeübertragungsmodell |∂T_aus/∂T_ein| ≤ 1 hat (Kontraktion in Kreisen).
- **Grenzzyklus-Wächter**: Komponenten mit Verstärkung exakt 1 (feste
  Leistung, geklemmte Erzeuger an der q_max-Grenze) können Periode-2-
  Oszillationen erzeugen (Eigenwert ≈ −1). Erkennung über den Feldvergleich
  |T_k − T_{k−2}| ≪ max|ΔT| → adaptive Dämpfung α = 0.5 … 0.3.
  (Reine Stagnation löst NICHT aus — wandernde Advektionsfronten haben
  konstantes ΔT je Sweep, sind aber konvergent.)
- **Drift-Erkennung**: Bewegt sich das Feld seit dem Halbzeit-Schnappschuss
  weit, obwohl max|ΔT| stagniert, zirkuliert ein thermisch isolierter Umlauf
  (kein Zustrom, kein UA) mit fest vorgegebener Leistung — dafür existiert
  keine stationäre Lösung. Fehlermeldung nennt Abhilfen (UA angeben,
  physikalisches Modell, `solve(thermal=False)`).
- `solve(thermal=False)` überspringt die Energiegleichung (rein hydraulische
  Studien, z.B. Ventilhub-Kennlinien).
- Fluss-Randbedingungen: mehrere je Knoten zulässig; jede geht mit ihrer
  eigenen Zulauftemperatur in die Enthalpiebilanz ein.
- Tote Kanten (|ṁ| < 1e-7 kg/s): Durchreichen, Q̇ = 0; Knoten ganz ohne
  Zustrom behalten T_init und werden als „stagnierend" markiert.
- **Globale Energiebilanz** (Σ Q̇_Kanten + Σ UA·(T_amb − T) + Randenthalpien)
  wird berechnet und im Bericht ausgewiesen; Tests fordern |Bilanz| < 1 W.

### Thermische Komponentenmodelle

| Komponente | Modell |
|---|---|
| Rohr / FBH | exponentielles Abklingen an T_amb bzw. T_raum: `T_aus = T_∞ + (T_ein − T_∞)·e^(−UA/ṁcp)` (analytisch, robust bei kleinem ṁ) |
| Heizkörper | EN-442-Exponentenmodell `Q̇ = Q̇_N·(ΔT_lm/ΔT_lm,N)^n`, gekoppelt mit Enthalpiebilanz; Nullstelle via brentq auf [T_raum, T_ein] (Vorzeichenwechsel garantiert) |
| Register | sensibles ε-NTU (Gegenstrom / Kreuzstrom unvermischt) mit Teillastkorrektur UA = UA_ref·[(V̇g/V̇g,ref)·(V̇w/V̇w,ref)]^n (Gl. 4.2, FH-Skript Wärmetechnik 2; Default n = 0.4, ohne Referenzen konstant); Kühlregister optional als Greybox mit Kondensation (Skill cooling-coil-greybox): Q̇ = max(Q̇_trocken, ε*-NTU*-Nassmodell mit Enthalpietreiber h_ein − h_sat(ϑ_w)), Magnus-Psychrometrie, Kondensatrate in extras — validiert gegen die Skill-Referenzvorhersage (FläktGroup H241611, < 0.3 % Abweichung) |
| WP/KM | feste Leistung oder Solltemperatur (mit q_max-Klemme, nur in Arbeitsrichtung) |
| alle | optional `q_prescribed` statt physikalischem Modell |

## 3. Testabdeckung (tests/, 140 Tests)

Analytische Referenzen: Hagen-Poiseuille, Churchill↔Swamee-Jain,
Kv-Definition (1 m³/h @ 1 bar), Einzelkreis Q = √(Δp/Σb), Serien-/
Parallelwiderstände (Q ∝ Kv, gleiches Δp), Druck-/Fluss-Randbedingungen,
Mischtemperatur, exponentieller Rohrverlust, HK-Nennpunkt (75/65/20 →
exakt Q_nom), HK-Halblast gegen unabhängige Fixpunktiteration, ε-NTU-
Handrechnung, Weichen-Mischformel + Transferstrom, Puffer-Mischknoten,
C-Wert-Widerstand (SI-/m³h-/Auslegungspunkt-Varianten, Strömungsumkehr),
link-Knotentrennung, Temperaturquelle, Mehrfach-Fluss-RB je Knoten,
Teilstrecken-Gruppierung (Kettenauswertung + Konsistenzwarnung).
Verbindungsleitung (conduit: ideal ≡ link, C ≡ flow_resistance, Rohrmodus ≡
Pipe exakt), Rückschlagklappe (vorwärts/rückwärts/antiparallel), Kugelhahn (offen ≈
widerstandsfrei, zu = exakte Absperrung), doppelte YAML-Schlüssel, Editor-Server
(GET/POST /solve, Fehlerpfade, Thermik-Fallback).
Robustheit: Ventil zu (exakte Absperrung, V̇ = 0 als RB), Kennlinien-Floor,
Ventil-Sweep monoton, absurder Startwert, unbilanzierte Konstantstrom-
Pumpen (Compile-Zeit-Fehler), Konstantstrom-Pumpe gegen zu, Drift-Meldung
bei isoliertem Umlauf (langsame Rezirkulations-Konvergenz wird davon
unterschieden und zu Ende iteriert), Ventilautorität (installierte
Kennlinie analytisch), Einheiten-Äquivalenz (alle Suffixe der Register,
Wärmeabgabesysteme, Pumpen und Widerstände → bitidentische Ergebnisse),
Fehlermeldungsqualität des Loaders.

**Validierung gegen unabhängige Referenzlösungen** (FH Burgenland):
- Verteiler-Übung (Umlenk- + Einspritzschaltung, Excel-Modell):
  Volllast-Volumenströme TS1–TS8 < 0.1 %, Ventilautoritäten 0.2380/0.1937
  (Ref. 0.2381/0.1938), Kennlinien-Anker der Lösungsplots
  (tests/test_validation_fh_verteiler.py; Plots examples/validation_fh_verteiler.py).
- TWE + Verteiler (Bsp 6, handschriftliche Musterlösung): Auslegung
  (RG kvs 6.3, SRVs, Pumpenförderhöhen) und Abschaltfall V̇₁′/V̇₄′ auf
  3 Nachkommastellen (examples/07_twe_heizkreisverteiler.py).
- Kv↔C-Wert-Konvention: Kvs = √(1e5/C) ist nur bei ρ = 1000 kg/m³ exakt;
  Kreuzvalidierung Beispiel 05 ↔ 06 in tests/test_flow_resistance.py.
