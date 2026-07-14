"""Idelchik-Widerstandsbeiwerte für 90°-T-Stücke (Vereinigung/Trennung).

Quelle: I. E. Idelchik, Handbook of Hydraulic Resistance, 3. Aufl.,
Diagramm 7-10 (Vereinigung, S. 438) und 7-21 (Trennung, S. 456);
Geometrie: α = 90°, F_st = F_c, F_s + F_st > F_c.

Konventionen (siehe idelchik_t_stueck_*_llm.md des Nutzers):
- c = kombinierter Strang (führt den Gesamtstrom), s = Abzweig, st = gerader
  Strang; x = Q_s/Q_c, r_A = F_s/F_c.
- ζ_c.s und ζ_c.st sind TOTALDRUCK-Beiwerte, bezogen auf ρ·w_c²/2.
- Negative ζ_c.s bei der Vereinigung (kleines x) sind physikalisch
  (Injektorwirkung) und bleiben vorzeichenbehaftet erhalten.

Tabellenwerte außerhalb des Gitters werden auf den Rand geklemmt
(x ∈ [0.1, 1.0], r_A ∈ [0.09, 1.0]).
"""
from __future__ import annotations

X_GRID = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
RA_GRID = (0.09, 0.19, 0.27, 0.35, 0.44, 0.55, 1.0)

# Vereinigung (Sammlung), Seitenpfad ζ_c.s  [Zeile = r_A, Spalte = x]
ZETA_CS_CONV = (
    (-0.50, 2.97, 9.90, 19.70, 32.40, 48.80, 66.50, 86.90, 110.00, 136.00),
    (-0.53, 0.53, 2.14, 4.23, 7.30, 11.40, 15.60, 20.30, 25.80, 31.80),
    (-0.69, 0.00, 1.11, 2.18, 3.76, 5.90, 8.38, 11.30, 14.60, 18.40),
    (-0.65, -0.09, 0.59, 1.31, 2.24, 3.52, 5.20, 7.28, 9.23, 12.20),
    (-0.80, -0.27, 0.26, 0.84, 1.59, 2.66, 4.00, 5.73, 7.40, 9.60),
    (-0.88, -0.48, 0.00, 0.53, 1.15, 1.89, 2.92, 4.00, 5.36, 6.60),
    (-0.65, -0.40, -0.24, 0.10, 0.50, 0.83, 1.13, 1.47, 1.86, 2.30),
)
# Vereinigung, Durchgangspfad ζ_c.st (für alle r_A)
ZETA_CST_CONV = (0.70, 0.64, 0.60, 0.65, 0.75, 0.85, 0.92, 0.96, 0.99, 1.00)

# Trennung (Verteilung), Seitenpfad ζ_c.s
ZETA_CS_DIV = (
    (2.80, 4.50, 6.00, 7.88, 9.40, 11.10, 13.00, 15.80, 20.00, 24.70),
    (1.41, 2.00, 2.50, 3.20, 3.97, 4.95, 6.50, 8.45, 10.80, 13.30),
    (1.37, 1.81, 2.30, 2.83, 3.40, 4.07, 4.80, 6.00, 7.18, 8.90),
    (1.10, 1.54, 1.90, 2.35, 2.73, 3.22, 3.80, 4.32, 5.28, 6.53),
    (1.22, 1.45, 1.67, 1.89, 2.11, 2.38, 2.58, 3.04, 3.84, 4.75),
    (1.09, 1.20, 1.40, 1.59, 1.65, 1.77, 1.94, 2.20, 2.68, 3.30),
    (0.90, 1.00, 1.13, 1.20, 1.40, 1.50, 1.60, 1.80, 2.06, 2.80),
)
# Trennung, Durchgangspfad ζ_c.st (für alle r_A)
ZETA_CST_DIV = (0.70, 0.64, 0.60, 0.57, 0.55, 0.51, 0.49, 0.55, 0.62, 0.70)


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def _interp1(xg, yg, x: float) -> float:
    x = _clamp(x, xg[0], xg[-1])
    for i in range(len(xg) - 1):
        if x <= xg[i + 1]:
            f = (x - xg[i]) / (xg[i + 1] - xg[i])
            return yg[i] + f * (yg[i + 1] - yg[i])
    return yg[-1]


def zeta_side(x: float, r_a: float, converging: bool) -> float:
    """ζ_c.s des Seitenpfads (bilinear interpoliert, an den Rändern geklemmt)."""
    tab = ZETA_CS_CONV if converging else ZETA_CS_DIV
    r_a = _clamp(r_a, RA_GRID[0], RA_GRID[-1])
    for i in range(len(RA_GRID) - 1):
        if r_a <= RA_GRID[i + 1]:
            f = (r_a - RA_GRID[i]) / (RA_GRID[i + 1] - RA_GRID[i])
            lo = _interp1(X_GRID, tab[i], x)
            hi = _interp1(X_GRID, tab[i + 1], x)
            return lo + f * (hi - lo)
    return _interp1(X_GRID, tab[-1], x)


def zeta_straight(x: float, converging: bool) -> float:
    """ζ_c.st des Durchgangspfads (gilt für alle r_A)."""
    return _interp1(X_GRID, ZETA_CST_CONV if converging else ZETA_CST_DIV, x)
