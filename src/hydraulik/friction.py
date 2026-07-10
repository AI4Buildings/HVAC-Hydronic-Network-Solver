"""Widerstandskorrelationen (SI-Einheiten).

Kantenimpulsgleichung des Solvers:  Δp = a·Q + b·Q·|Q| − Δp_source
mit a [Pa/(m³/s)] (linear/laminar) und b [Pa/(m³/s)²] (quadratisch/turbulent).
"""
from __future__ import annotations

import math


def churchill(re: float, rel_roughness: float) -> float:
    """Darcy-Reibungsbeiwert nach Churchill (1977).

    Glatt über alle Re-Bereiche (laminar/Übergang/turbulent) – vermeidet
    Grenzzyklen des Solvers, die ein hartes Umschalten bei Re≈2300 erzeugt.
    """
    re = max(re, 1e-6)
    a = (2.457 * math.log(1.0 / ((7.0 / re) ** 0.9 + 0.27 * rel_roughness))) ** 16
    b = (37530.0 / re) ** 16
    return 8.0 * ((8.0 / re) ** 12 + 1.0 / (a + b) ** 1.5) ** (1.0 / 12.0)


def swamee_jain(re: float, rel_roughness: float) -> float:
    """Darcy-Reibungsbeiwert nach Swamee-Jain (nur turbulent, für Querchecks)."""
    re = max(re, 4000.0)
    return 0.25 / math.log10(rel_roughness / 3.7 + 5.74 / re ** 0.9) ** 2


def pipe_laminar_a(mu: float, length: float, d_inner: float) -> float:
    """Exakter laminarer (Hagen-Poiseuille-)Koeffizient a [Pa/(m³/s)]."""
    return 128.0 * mu * length / (math.pi * d_inner ** 4)


def pipe_coefficients(q: float, length: float, d_inner: float, roughness: float,
                      zeta: float, rho: float, mu: float) -> tuple[float, float]:
    """(a, b) einer Rohrleitung beim aktuellen Volumenstrom q.

    Der laminare Anteil steckt exakt in a; b enthält nur den turbulenten
    Überschuss (Churchill) plus Einzelwiderstände ζ. Im laminaren Bereich
    wird b_reib ≈ 0, sodass die Linearisierung exakt ist.
    """
    area = math.pi * d_inner ** 2 / 4.0
    a_lam = pipe_laminar_a(mu, length, d_inner)
    b_zeta = rho * zeta / (2.0 * area ** 2)
    qa = abs(q)
    if qa < 1e-9:
        return a_lam, b_zeta
    re = rho * qa * d_inner / (area * mu)
    f = churchill(re, roughness / d_inner)
    dp_f = f * (length / d_inner) * rho * (qa / area) ** 2 / 2.0
    b_fric = max(0.0, (dp_f - a_lam * qa) / qa ** 2)
    return a_lam, b_fric + b_zeta


def kv_to_b(kv_m3h: float, rho: float) -> float:
    """Quadratischer Widerstand b [Pa/(m³/s)²] aus dem Kv-Wert [m³/h].

    Kv-Definition: Q[m³/h] = Kv · sqrt(Δp[bar] · 1000/ρ)
    ⇒ Δp = (3600·Q_SI/Kv)² · 100·ρ  [Pa]
    """
    return (3600.0 / kv_m3h) ** 2 * 100.0 * rho


def zeta_to_b(zeta: float, d_inner: float, rho: float) -> float:
    """Quadratischer Widerstand aus ζ-Wert und Innendurchmesser."""
    area = math.pi * d_inner ** 2 / 4.0
    return rho * zeta / (2.0 * area ** 2)
