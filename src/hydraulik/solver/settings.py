"""Solver-Einstellungen mit robusten Defaults."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SolverSettings:
    # SIMPLE-Hydraulik. Mit α=1 ist der Schritt ein exakter Newton-Schritt
    # (Schur-Komplement); der Divergenz-Wächter halbiert bei Bedarf automatisch.
    alpha_p: float = 1.0          # Unterrelaxation Druck
    alpha_q: float = 1.0          # Unterrelaxation Volumenstrom
    max_iter: int = 400
    tol_mass_rel: float = 1e-8    # Massendefekt relativ zum Volumenstrom-Maßstab
    tol_mom_rel: float = 1e-6     # Impulsdefekt relativ zum Druck-Maßstab
    q_init: float = 5e-5          # Startvolumenstrom [m³/s], falls Komponente keinen Seed liefert
    q_eps_frac: float = 1e-3      # R-Floor: Anteil des Seed-Volumenstroms
    p_ref: float = 150e3          # Referenz-/Startdruck [Pa Überdruck] – alle Drücke
                                  # sind gauge; 1.5 bar(ü) ≈ typischer Anlagenfülldruck

    # Thermik
    t_init: float = 20.0          # Starttemperatur [°C]
    max_iter_thermal: int = 500
    tol_t: float = 1e-6           # max. Temperaturänderung je Sweep [K]
    m_dot_eps: float = 1e-7       # Massenstromschwelle "stagnierend" [kg/s]
