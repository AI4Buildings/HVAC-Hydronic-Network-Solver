# Idelchik-Korrelation für ein 90°-T-Stück: Verteilung

Quelle: I. E. Idelchik, *Handbook of Hydraulic Resistance*, 3. Auflage,
Diagramm 7-21, Seite 456.

Gültige Quellgeometrie:

$$
\alpha=90^\circ,\qquad F_{st}=F_c,\qquad F_s+F_{st}>F_c.
$$

## 1. Nomenklatur und Strömungszuordnung

| Symbol | Bedeutung |
|---|---|
| $c$ | gemeinsamer Strang; bei Verteilung Gesamtstrom-Zulauf |
| $s$ | Seitenstrang; bei Verteilung Seiten-Auslauf |
| $st$ | gerader Strang; bei Verteilung gerader Auslauf |
| $Q_i$ | Volumenstrombetrag im Strang $i$ |
| $F_i$ | Strömungsquerschnitt im Strang $i$ |
| $w_i=Q_i/F_i$ | mittlere Geschwindigkeit im Strang $i$ |
| $p_{t,i}$ | Totaldruck im Strang $i$ |
| $\rho$ | Fluiddichte |
| $\zeta_{c.s}$ | Beiwert für den Pfad $c\rightarrow s$, bezogen auf $w_c$ |
| $\zeta_{c.st}$ | Beiwert für den Pfad $c\rightarrow st$, bezogen auf $w_c$ |
| $\zeta_s$ | Beiwert für $c\rightarrow s$, bezogen auf $w_s$ |
| $\zeta_{st}$ | Beiwert für $c\rightarrow st$, bezogen auf $w_{st}$ |

Strömungsrichtung:

$$
c\longrightarrow
\begin{cases}
s,\\
st.
\end{cases}
$$

Kontinuität und Volumenstromanteile:

$$
\boxed{Q_c=Q_s+Q_{st}}
$$

$$
\boxed{x=\frac{Q_s}{Q_c}},\qquad
\boxed{1-x=\frac{Q_{st}}{Q_c}}.
$$

Flächenverhältnis:

$$
\boxed{r_A=\frac{F_s}{F_c}}
$$

Da $F_{st}=F_c$:

$$
\frac{F_s}{F_{st}}=\frac{F_s}{F_c}=r_A.
$$

Für kreisförmige Rohre:

$$
\boxed{r_A=\left(\frac{d_s}{d_c}\right)^2}.
$$

## 2. Geschwindigkeitsverhältnisse

$$
w_c=\frac{Q_c}{F_c},\qquad
w_s=\frac{Q_s}{F_s},\qquad
w_{st}=\frac{Q_{st}}{F_{st}}.
$$

$$
\boxed{\frac{w_s}{w_c}
=\frac{Q_sF_c}{Q_cF_s}
=\frac{x}{r_A}}
$$

$$
\boxed{\frac{w_{st}}{w_c}
=\left(1-x\right)\frac{F_c}{F_{st}}}
$$

Mit $F_{st}=F_c$:

$$
\boxed{\frac{w_{st}}{w_c}=1-x}.
$$

## 3. Definition der Widerstandsbeiwerte

Für gleiche geodätische Höhe und kinetischen Korrekturfaktor eins:

$$
p_t=p+\frac{\rho w^2}{2}.
$$

Gerichtete Totaldruckverluste:

$$
\boxed{\Delta p_s=p_{t,c}-p_{t,s}}
$$

$$
\boxed{\Delta p_{st}=p_{t,c}-p_{t,st}}.
$$

Bezugs-Geschwindigkeitsdruck:

$$
\boxed{q_{dyn,c}=\frac{\rho w_c^2}{2}}.
$$

Idelchik-Beiwerte mit gemeinsamer Bezugsgeschwindigkeit:

$$
\boxed{\zeta_{c.s}
=\frac{\Delta p_s}{\rho w_c^2/2}
=f\!\left(\frac{Q_s}{Q_c},\frac{F_s}{F_c}\right)}
$$

$$
\boxed{\zeta_{c.st}
=\frac{\Delta p_{st}}{\rho w_c^2/2}
=g\!\left(\frac{Q_s}{Q_c}\right)}.
$$

Druckverluste:

$$
\boxed{\Delta p_s=\zeta_{c.s}\frac{\rho w_c^2}{2}}
$$

$$
\boxed{\Delta p_{st}=\zeta_{c.st}\frac{\rho w_c^2}{2}}.
$$

Umrechnung auf lokale Bezugsgeschwindigkeiten:

$$
\boxed{\zeta_s
=\frac{\zeta_{c.s}}
{\left(Q_sF_c/(Q_cF_s)\right)^2}
=\zeta_{c.s}\left(\frac{r_A}{x}\right)^2}
$$

$$
\boxed{\zeta_{st}
=\frac{\zeta_{c.st}}
{\left(1-x\right)^2\left(F_c/F_{st}\right)^2}}.
$$

Mit $F_{st}=F_c$:

$$
\boxed{\zeta_{st}=\frac{\zeta_{c.st}}{(1-x)^2}}.
$$

## 4. Tabelle für den Seitenpfad $\zeta_{c.s}$

Zeilenvariable:

$$
\frac{F_s}{F_c}=r_A
$$

Spaltenvariable:

$$
\frac{Q_s}{Q_c}=x
$$

| $F_s/F_c$ \ $Q_s/Q_c$ | 0.1 | 0.2 | 0.3 | 0.4 | 0.5 | 0.6 | 0.7 | 0.8 | 0.9 | 1.0 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.09 | 2.80 | 4.50 | 6.00 | 7.88 | 9.40 | 11.10 | 13.00 | 15.80 | 20.00 | 24.70 |
| 0.19 | 1.41 | 2.00 | 2.50 | 3.20 | 3.97 | 4.95 | 6.50 | 8.45 | 10.80 | 13.30 |
| 0.27 | 1.37 | 1.81 | 2.30 | 2.83 | 3.40 | 4.07 | 4.80 | 6.00 | 7.18 | 8.90 |
| 0.35 | 1.10 | 1.54 | 1.90 | 2.35 | 2.73 | 3.22 | 3.80 | 4.32 | 5.28 | 6.53 |
| 0.44 | 1.22 | 1.45 | 1.67 | 1.89 | 2.11 | 2.38 | 2.58 | 3.04 | 3.84 | 4.75 |
| 0.55 | 1.09 | 1.20 | 1.40 | 1.59 | 1.65 | 1.77 | 1.94 | 2.20 | 2.68 | 3.30 |
| 1.00 | 0.90 | 1.00 | 1.13 | 1.20 | 1.40 | 1.50 | 1.60 | 1.80 | 2.06 | 2.80 |

## 5. Tabelle für den geraden Pfad $\zeta_{c.st}$

Die Werte gelten für alle tabellierten $F_s/F_c$.

| $Q_s/Q_c$ | 0.1 | 0.2 | 0.3 | 0.4 | 0.5 | 0.6 | 0.7 | 0.8 | 0.9 | 1.0 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| $\zeta_{c.st}$ | 0.70 | 0.64 | 0.60 | 0.57 | 0.55 | 0.51 | 0.49 | 0.55 | 0.62 | 0.70 |

## 6. Gesamtbeiwert

Volumenstromgewichteter Gesamtbeiwert, bezogen auf $w_c$:

$$
\boxed{\bar{\zeta}_{div}
=x\,\zeta_{c.s}+(1-x)\,\zeta_{c.st}}.
$$
