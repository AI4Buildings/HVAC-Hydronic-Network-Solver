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
Impulsdefekt 5× in Folge steigt (bis minimal 0.1), und verdoppelt sie nach
3 Abnahmen in Folge wieder bis zum eingestellten Wert (die Dämpfung ist nur
vorübergehend — dauerhaft gedämpft konvergierte Newton nur noch linear,
≈ 1 % je Iteration).

**Jacobi-Steigung.** `a + 2b·|Q|` ist die exakte Ableitung nur für
Q-unabhängige a, b. Im laminar-turbulenten Übergang (Churchill, Re ≈
2200–3000) unterschätzt sie dΔp/dQ bis Faktor 2.8 → Newton schießt über,
Grenzzyklus. Verwendet wird daher `J = max(a + 2b|Q|, dR/dQ)` mit dem
Differenzenquotienten der Kantenkennlinie R(Q) = a·Q + b·Q|Q| − Δp_source
aus `coeff_fn` (generisch, kein neuer Vertrag; das Maximum ist nie zu flach).

**Konsistente Konvergenzprüfung.** Residuen werden am Iterationsanfang mit
den BEIM AKTUELLEN Zustand ausgewerteten Koeffizienten geprüft, erst danach
folgt das Newton-Update. (Bis Oktober 2026 wurde nach dem Update mit den
Koeffizienten des alten Q geprüft: Rohre ohne Startwert beginnen bei q_init
laminar, d.h. linear; Newton löste das lineare Modell exakt und der Solver
meldete nach EINER Iteration Konvergenz mit laminarer Stromaufteilung —
Fehler bis 17 % ohne Warnung.) Zusätzlich muss die letzte Volumenstrom-
Korrektur klein sein (≤ 1e-6·max|Q| bzw. R-Floor-Auflösung): das Impuls-
residuum relativ zum GLOBALEN Druckmaßstab legt Ströme mit kleinem Δp nicht
fest (widerstandsarme oder antriebslose Maschen, doppelte Nullstelle von
b·Q|Q| = 0 → nur lineare Konvergenz).

**Eigenschleifen** (Ein- und Austritt am selben Knoten, kurzgeschlossenes
Bauteil): Δp ≡ 0, die Kante ist entkoppelt; ihre skalare Gleichung
R(Q) = Δp_Quelle wird vorab exakt gelöst (Bisektion: passiv V̇ = 0, Pumpe
Kurzschluss-Zirkulation √(dp/b_int)).

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
| Komponentenmodell wirft eine Ausnahme (Betriebspunkt außerhalb des Modellbereichs) | Beide Solver hüllen sie in `ComponentModelError` ein: Komponente, Modell (hydraulisch/thermisch), Betriebspunkt (V̇ bzw. T_ein, ṁ, Iteration) und Ursache — statt rohem Traceback bzw. „Interner Fehler" im Server |
| Feste Leistung bei Kleinstdurchfluss (Leckage, Ventil fast zu) → formal korrekte, physikalisch sinnlose Temperaturen | Nachlaufprüfung in `build_result`: Austrittstemperaturen durchströmter Kanten außerhalb `t_plausible_min/max` (Default −50…200 °C) werden als Hinweis gemeldet |

Konstantstrom-Kanten: `Q = fix`, Koeffizient d = 1/J = 0 im Laplacian
(keine Druckkopplung), Δp ist Ergebnis.

### T-Stück mit Idelchik-Druckverlust (components/idelchik.py, separators.Tee)

ζ hängt vom Volumenstromverhältnis der GESCHWISTERKANTEN ab → generischer
Solver-Hook `pre_coefficients(q_eigene_kanten, fluid)`: Komponenten mit
gekoppelten Kanten erhalten vor jeder Koeffizientenauswertung ihre aktuellen
Kantenflüsse. Der kombinierte Strang (max |Q|) bleibt verlustfrei,
Abzweig-/Durchgangskante tragen die vollen Pfadbeiwerte (Vereinigung/Trennung
automatisch aus der Flussrichtung; Abszissen wie im Buch: Seitenpfad Q_s/Q_c,
gerader Pfad der Trennung Q_st/Q_c, der Vereinigung Q_s/Q_c).

**Totaldruck im Netz.** Je Pfad wirkt der Idelchik-TOTALDRUCKverlust
ζ·ρw_c²/2 — wie bei allen anderen Bauteilen: Rohre, Ventile, Register rechnen
ihren Druckabfall als Totaldruckverlust, Knoten kennen keine Geschwindigkeit
(Querschnittswechsel an Knoten werden nirgends mit Bernoulli bilanziert).
Die frühere Umrechnung auf statische Knotendrücke, p_ein − p_aus = ζ·ρw_c²/2
+ ρ(w_aus² − w_ein²)/2, war die einzige im Netz: der Zulaufschenkel bekam
seine kinetische Energie geschenkt. Der statische Druck an jedem Anschluss
(p_Knoten − ρw_Schenkel²/2, z.B. der Druckrückgewinn im geraden Auslauf)
ist Ergebnis (`extras["p_static_port_kPa"]`, Hook `edge_result_extras`);
Drucksensoren zeigen den Knotendruck. Wirkung (310 Zufallsnetze mit
Idelchik-T-Stück): mehrdeutig 83 → 59; beseitigt 70, neu 45 — der Bernoulli-
Term des AUSTRITTSschenkels (+ρw²/2, mit dem eigenen Strom steigend) hatte
nicht-monotone Tabellenbereiche teilweise überdeckt; die verbleibende
Mehrwertigkeit stammt aus den Tabellen (negatives ζ_c.s, U-förmige
Durchgangstabelle). 3 Netze konvergieren nur von alternativen Startwerten
(Neustart, s. Eindeutigkeitsprüfung).

**Regimewechsel (stetig).** Jeder Wechsel — Trennen ↔ Vereinigen, kombinierter
Strang wechselt — liegt bei Strom 0 eines Schenkels, also unter dessen
Tabellengrenze x = 0.1. Die Grenzwerte beider Regime unterscheiden sich dort
um bis zu ~ρw²; die frühere Klemmung ergab eine springende Kennlinie (keine
Lösung im Sprungintervall → Stillstand bei V̇ = 0) und, über den geklemmten
Seiten-ζ < 1, einen statischen Druckgewinn ∝ w_c² auf einem fast
stagnierenden Schenkel (Pumpwirkung aus dem Hauptstrom → NaN-Divergenz).
Jetzt wird für x_min < 0.1 linear im vorzeichenbehafteten Schenkelstrom
zwischen den beiden angrenzenden Regimen interpoliert, je ausgewertet an
ihrer Tabellengrenze x = 0.1 bei gleichem Durchgangsstrom: die Kennlinie
ist stetig, und die Tabellen werden nur im Buchbereich [0.1, 1] benutzt.

**Linearisierung (Tangente).** Jede Schenkelkante meldet ihre lokale
Steigung als Linearterm a = dS/dQ (Eigenstrom gestört, Kontinuität über den
größten anderen Schenkel; mind. ¼ der Sekante S/Q und 2·b_idle·|Q|) und die
nachgeführte Quelle dp_source = a·Q − S. Damit ist R(Q) am Arbeitspunkt
exakt S, J = a > 0 hält den Laplacian SPD, und auch der Differenzenquotient
des Solvers sieht dζ/dx (vorher ignorierte die Kennlinie den Eigenstrom →
Picard-artige 2-Zyklen über Tabellenstützstellen). Der kleine Schenkel im
Überblendbereich hat S(0) ≠ 0; dort gilt die Linearform mit der Steigung der
Überblendung plus der Steifigkeit eines Staudrucks bei x = 0.1 (q|q|-Formen
hätten bei Q → 0 die Steigung 0 bzw. ∞). Früher lag die Druckgewinn-Quelle
auf der quasi-idealen Restkante (1 Pa bei 10 m³/h) — Newton sagte riesige
Ströme voraus, der Hauptstrom sprang zwischen den Schenkeln.

**Kurzschluss.** Zwei Schenkel am selben Knoten werden beim Kompilieren
abgelehnt (Hook `check_topology`): die Aufteilung über die beiden Schenkel
bestimmt dann allein die Tabellenkennlinie, oft nicht eindeutig (Totaldruck-
Modell: 17 von 69 solchen Kampagnennetzen) — praktisch immer ein
Zeichenfehler.

Validierung: tests/test_tee_idelchik.py (Handrechnung Trennung x = 0.4 und
Vereinigung x = 0.1 mit ζ = −0.65 auf 0.2 Pa genau; Stetigkeit an jedem
Regimewechsel; Tabellen nur im Buchbereich; Druckabtastung über den
Vorzeichenwechsel des Abzweigs monoton; Tabellenquelle dokumentiert in
docs/idelchik_t_stueck_*.md). Zufallsnetz-Kampagne: alle 137 vorher nicht
konvergierenden Netze mit Idelchik-T-Stück lösen (≤ 96 Iterationen).

**Mehrdeutigkeit → Eindeutigkeitsprüfung** (solver/uniqueness.py). Netze mit
Idelchik-T-Stück können mehrere stationäre Lösungen haben: dieselben
Portdrücke lassen verschiedene Strömungsbilder zu (Kennlinie nicht
umkehrbar eindeutig). Stabilitätstest (lineare Maschendynamik
L·dQ/dt = Δp − G(Q), volle Jacobi-Matrix inkl. Schenkelkopplung, Urteil für
jede Trägheitsverteilung): in allen mehrdeutigen Netzen sind ALLE gefundenen
Lösungen stabil (Totaldruck-Modell: 119 Lösungen in 59 Netzen) — keine lässt
sich physikalisch ausschließen; welche sich einstellt, hängt vom
Anfahrvorgang ab (Anfangswertproblem). Daher: `solve_hydraulics_checked`
(einziger Einstieg für Network.solve und Server) löst bei Komponenten mit
`nonmonotone_hydraulics()` die Hydraulik zusätzlich von `uniqueness_starts`
(Default 8) reproduzierbaren Startwerten (Beträge log-gleichverteilt
1e-3…1·V̇max, Vorzeichen zufällig), schärft jede Lösung und die ausgegebene
nach (Toleranzen ×1e-5) und meldet Lösungen mit max|ΔQ| > max(1e-4·V̇max,
1e-6 m³/s) — Toleranzreste schwach bestimmter Maschen bleiben danach
≤ 4e-5 m³/h, echte Mehrfachlösungen ≥ 5 l/h. Konvergiert schon der
Standardstart nicht, wird von denselben Startwerten aus neu gestartet (in
Bereichen ohne stabiles Gleichgewicht irrt die Iteration sonst umher, obwohl
stabile Lösungen existieren). Ergebnis: Hinweis „Hydraulik nicht eindeutig“
mit den größten Abweichungen, `SolutionResult.alternatives`, im Editor ein
Dialog; die ausgegebene Lösung bleibt unverändert. Kampagne: 8 Starts
erkennen alle bekannten Fälle (4 Starts: 34/37), kein Fehlalarm; die 13
Smoke-Netze mit Verteiler-Strang-Sammler-Struktur sind eindeutig. Aufwand
≈ 135 ms je Netz, nur bei nicht-monotonen Komponenten.

Konvergenzkriterien (relativ): Massendefekt / max|Q| < 1e-8, Impulsdefekt /
Druckmaßstab < 1e-6 (beide mit den Koeffizienten des geprüften Zustands) und
letzte Volumenstrom-Korrektur ≤ 1e-6·max|Q|.

Plausibilitätshinweise nach dem Lösen (Hook `result_notices`): Pumpen melden,
wenn der interne Regularisierungswiderstand > 15 % der Druckerhöhung aufzehrt
(typisch q_nom nicht angegeben), Erzeuger, wenn ihr Default-Nennpunkt
(15 kPa bei 1 m³/h) bei viel größerem Volumenstrom einen großen
Innendruckverlust ergibt.

## 2. Thermik: Newton auf der Knotenbilanz (solver/thermal.py)

Läuft nach Hydraulik-Konvergenz (exakt entkoppelt, da Stoffwerte konstant).

- **Upwind**: jede Kante erhält T_ein vom stromauf liegenden Knoten
  (Vorzeichen von Q); Strömungsumkehr damit automatisch korrekt.
- **Kante**: Komponentenmodell liefert `T_aus, Q̇ = f(T_ein, |ṁ|)`.
- **Knoten**: ideale Mischung
  `G_j(T) = (Σ ṁ_zu·cp·T_aus + ṁ_RB·cp·T_zulauf + UA·T_amb) / (Σ ṁ_zu·cp + ṁ_RB·cp + UA)`
- **Gleichung**: Fixpunkt `F(T) = G(T) − T = 0` in den Knotentemperaturen.
  Statt der früheren Gauss-Seidel-Iteration (Kontraktionsfaktor nahe 1 bei
  großen Rezirkulationsverhältnissen → hunderte Sweeps, Trendfenster,
  Grenzzyklus-Dämpfung, Drift-Heuristik) ein **Newton-Verfahren**:
  `(I − ∂G/∂T)·δ = F`. ∂G/∂T ist dünn besetzt — ein Eintrag
  `ṁ_e·cp·f_e'/D_j` je durchströmter Kante an (Knoten stromab, Knoten
  stromauf); die Steigung `f_e' = ∂T_aus/∂T_ein` kommt generisch per
  Differenzenquotient (h = max(1e-3 K, 1e-6·|T|)) aus dem Komponentenmodell,
  ohne neuen Vertrag. Lineare Netze (Rohre, Mischung, Speicher) sind damit in
  EINEM Schritt exakt, unabhängig vom Rezirkulationsverhältnis; nichtlineare
  Modelle konvergieren quadratisch (typisch 2–8 Iterationen).
- **Globalisierung (Levenberg–Marquardt, Vertrauensbereich Δ)**: Ist die
  Linearisierung singulär oder der Newton-Schritt länger als Δ — typisch an
  Umläufen aus lauter Kanten mit Steigung 1 (Erzeuger an der q_max-Klemme,
  Heizkörper „aus" am Startfeld 20 °C = Raumtemperatur, feste Leistungen) —
  wird `(I − ∂G/∂T + μI)·δ = F` mit `μ = |F|_∞/Δ` gelöst: im singulären
  Unterraum wird δ zum gedämpften Fixpunktschritt, sonst bleibt es Newton.
  Armijo-Liniensuche auf `max|F|` (λ = 1, ½, … 1/128) entscheidet über die
  Annahme. Da alle Modelle nicht-expansiv sind (0 ≤ f' ≤ 1, Mischung konvex,
  also ‖∂G/∂T‖_∞ ≤ 1), kann ein kleiner gedämpfter Schritt das Residuum nie
  vergrößern — ein Grenzzyklus-Wächter ist überflüssig. Wächst das Residuum
  in jede Richtung, schrumpft Δ (¼); war die ungedämpfte Newton-Richtung
  selbst unbrauchbar, wird der nächste Schritt gedämpft.
- **Stillstand und Drift**: Ein voller gedämpfter Schritt, der `max|F|`
  praktisch unverändert lässt, heißt: G ist entlang δ affin-identisch
  (Klemme oder isolierter Umlauf). Bewegung ist dort frei — der Schritt wird
  angenommen und Δ verdoppelt, bis eine Klemme verlassen ist (der geklemmte
  Erzeuger erreicht seine Solltemperatur; die alte Iteration scheiterte an
  genau diesem Fall). Übersteigt die Verschiebung 1e6 K ohne Änderung der
  Bilanz, existiert keine stationäre Lösung: thermisch isolierter Umlauf mit
  fester Leistung (q_prescribed/prescribed_q oder Erzeuger dauerhaft an
  q_max). Die `ConvergenceError`-Meldung nennt die betroffenen Knoten und
  Abhilfen (UA angeben, physikalisches Modell, `solve(thermal=False)`).
  Gezählt wird die Verschiebung aller angenommenen Schritte seit dem letzten
  echten Fortschritt (Residuum ≥ 1 % unter dem Referenzwert) — bei |T| bis
  1e6 K erzeugt Rundung scheinbare Mini-Abstiege, die den Zähler sonst
  ständig zurücksetzten (Kreise ohne Wärmequelle mit großem Umlauf endeten
  undiagnostiziert an der Iterationsgrenze).
- **Genauigkeitsgrenze**: Endet die Iteration an `max_iter_thermal` mit
  Residuum ≤ 100·tol_t und ohne aufgelaufene Drift (< 1 K), wird das
  Ergebnis mit Hinweis „Thermik auf … K genau“ geliefert statt verworfen
  (Rundungsgrenze bei stark unterschiedlichen Kapazitätsströmen).
- **Unbestimmte Umläufe**: Knoten, deren Temperatur nur über Kanten mit
  (einseitiger) Steigung 1 von sich selbst abhängt — adiabate Umläufe ohne
  Quelle, nur heizende WP im verlustfreien Kreis —, werden gekennzeichnet;
  der Bericht meldet „Lösung zum Startwert t_init“.
- `solve(thermal=False)` überspringt die Energiegleichung (rein hydraulische
  Studien, z.B. Ventilhub-Kennlinien).
- Fluss-Randbedingungen: mehrere je Knoten zulässig; jede geht mit ihrer
  eigenen Zulauftemperatur in die Enthalpiebilanz ein.
- Tote Kanten (|ṁ| < 1e-7 kg/s): Durchreichen, Q̇ = 0; Knoten ganz ohne
  Zustrom behalten T_init und werden als „stagnierend" markiert (F ≡ 0).
- **Globale Energiebilanz** (Σ Q̇_Kanten + Σ UA·(T_amb − T) + Randenthalpien)
  wird berechnet und im Bericht ausgewiesen; Tests fordern |Bilanz| < 1 W.
- Vergleich alt/neu (Iterationen bis 1e-6 K): Beispiele 13–347 Sweeps →
  1–8 Newton-Schritte; Bypass-Rezirkulation 1:20000 1019 → 5; Weiche mit
  Sekundär > Primär 58 → 4; geklemmter Erzeuger mit fester Last im Umlauf
  (q_max knapp über Last) vorher Konvergenzfehler, jetzt 7–24 Schritte;
  Ergebnisse identisch (max |ΔT| < 1e-4 K über 40 Zufallsnetze).

### Thermische Komponentenmodelle

| Komponente | Modell |
|---|---|
| Rohr / FBH | exponentielles Abklingen an T_amb bzw. T_raum: `T_aus = T_∞ + (T_ein − T_∞)·e^(−UA/ṁcp)` (analytisch, robust bei kleinem ṁ) |
| Heizkörper | EN-442-Exponentenmodell `Q̇ = Q̇_N·(ΔT_lm/ΔT_lm,N)^n`, gekoppelt mit Enthalpiebilanz; Nullstelle via brentq auf [T_raum, T_ein] (Vorzeichenwechsel garantiert) |
| Register | sensibles ε-NTU (Gegenstrom / Kreuzstrom unvermischt) mit Teillastkorrektur UA = UA_ref·[(V̇g/V̇g,ref)·(V̇w/V̇w,ref)]^n (Gl. 4.2, FH-Skript Wärmetechnik 2; Default n = 0.4, ohne Referenzen konstant); Kühlregister optional als Greybox mit Kondensation (Skill cooling-coil-greybox): Q̇ = max(Q̇_trocken, ε*-NTU*-Nassmodell mit Enthalpietreiber h_ein − h_sat(ϑ_w)), Magnus-Psychrometrie, Kondensatrate in extras — validiert gegen die Skill-Referenzvorhersage (FläktGroup H241611, < 0.3 % Abweichung); Nassbetrieb begrenzt auf den Gleichgewichtszustand h_sat(T_w,aus) ≤ h_Luft,ein (zweiter Hauptsatz; die konstante Sättigungs-Wärmekapazität c_s überschätzte sonst bei kleinem Wasserstrom die Leistung); Gegenstrom-ε numerisch stabil für jedes Kapazitätsverhältnis |
| WP/KM | feste Leistung oder Solltemperatur (mit q_max-Klemme, nur in Arbeitsrichtung) |
| alle | optional `q_prescribed` statt physikalischem Modell |

## 3. Testabdeckung (tests/, 835 Tests)

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
Eingabeformat (siehe architektur.md): YAML-1.2-Typauflösung und YAML-1.1-
Altlasten, Parität Editor-Parser ↔ Loader (node; Korpus, Zufallsskalare,
Zufallsdokumente, bitgenauer Export-Round-Trip), JSON Schema ↔ Loader je
Typ/Parameter/Suffix, JSON-Ein-/Ausgabe (YAML → JSON → identische Lösung).
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
