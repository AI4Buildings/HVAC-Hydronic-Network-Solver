# Idelchik-Korrelation für ein 90°-T-Stück: Sammlung

Quelle: I. E. Idelchik, *Handbook of Hydraulic Resistance*, 3. Auflage,
Diagramm 7-10, Seite 438.

Gültige Quellgeometrie:

$$
\alpha=90^\circ,\qquad F_{st}=F_c,\qquad F_s+F_{st}>F_c.
$$

## 1. Nomenklatur und Strömungszuordnung

| Symbol | Bedeutung |
|---|---|
| $c$ | kombinierter Strang; bei Sammlung Gesamtstrom-Auslauf |
| $s$ | Seitenstrang; bei Sammlung Seiten-Zulauf |
| $st$ | gerader Strang; bei Sammlung gerader Zulauf |
| $Q_i$ | Volumenstrombetrag im Strang $i$ |
| $F_i$ | Strömungsquerschnitt im Strang $i$ |
| $w_i=Q_i/F_i$ | mittlere Geschwindigkeit im Strang $i$ |
| $p_{t,i}$ | Totaldruck im Strang $i$ |
| $\rho$ | Fluiddichte |
| $\zeta_{c.s}$ | Beiwert für den Pfad $s\rightarrow c$, bezogen auf $w_c$ |
| $\zeta_{c.st}$ | Beiwert für den Pfad $st\rightarrow c$, bezogen auf $w_c$ |
| $\zeta_s$ | Beiwert für $s\rightarrow c$, bezogen auf $w_s$ |
| $\zeta_{st}$ | Beiwert für $st\rightarrow c$, bezogen auf $w_{st}$ |

Strömungsrichtung:

$$
\begin{cases}
s,\\
st
\end{cases}
\longrightarrow c.
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
\boxed{\Delta p_s=p_{t,s}-p_{t,c}}
$$

$$
\boxed{\Delta p_{st}=p_{t,st}-p_{t,c}}.
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

Negative Tabellenwerte von $\zeta_{c.s}$ sind vorzeichenbehaftete
Teilwegbeiwerte und bleiben unverändert.

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
| 0.09 | -0.50 | 2.97 | 9.90 | 19.70 | 32.40 | 48.80 | 66.50 | 86.90 | 110.00 | 136.00 |
| 0.19 | -0.53 | 0.53 | 2.14 | 4.23 | 7.30 | 11.40 | 15.60 | 20.30 | 25.80 | 31.80 |
| 0.27 | -0.69 | 0.00 | 1.11 | 2.18 | 3.76 | 5.90 | 8.38 | 11.30 | 14.60 | 18.40 |
| 0.35 | -0.65 | -0.09 | 0.59 | 1.31 | 2.24 | 3.52 | 5.20 | 7.28 | 9.23 | 12.20 |
| 0.44 | -0.80 | -0.27 | 0.26 | 0.84 | 1.59 | 2.66 | 4.00 | 5.73 | 7.40 | 9.60 |
| 0.55 | -0.88 | -0.48 | 0.00 | 0.53 | 1.15 | 1.89 | 2.92 | 4.00 | 5.36 | 6.60 |
| 1.00 | -0.65 | -0.40 | -0.24 | 0.10 | 0.50 | 0.83 | 1.13 | 1.47 | 1.86 | 2.30 |

## 5. Tabelle für den geraden Pfad $\zeta_{c.st}$

Die Werte gelten für alle tabellierten $F_s/F_c$.

| $Q_s/Q_c$ | 0.1 | 0.2 | 0.3 | 0.4 | 0.5 | 0.6 | 0.7 | 0.8 | 0.9 | 1.0 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| $\zeta_{c.st}$ | 0.70 | 0.64 | 0.60 | 0.65 | 0.75 | 0.85 | 0.92 | 0.96 | 0.99 | 1.00 |

## 6. Gesamtbeiwert

Volumenstromgewichteter Gesamtbeiwert, bezogen auf $w_c$:

$$
\boxed{\bar{\zeta}_{comb}
=x\,\zeta_{c.s}+(1-x)\,\zeta_{c.st}}.
$$
