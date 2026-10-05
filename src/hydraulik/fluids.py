"""Fluiddefinition. Inkompressibel, konstante Stoffwerte (ρ, μ, cp)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .params import Param


@dataclass(frozen=True)
class Fluid:
    name: str
    rho: float   # kg/m³
    mu: float    # Pa·s
    cp: float    # J/(kg·K)


#: fluid-Block der Eingabedatei — Variante 1: Wasser aus der Stoffwerttabelle.
#: Wie Komponentenparameter deklariert (Single Source of Truth für Loader UND
#: JSON Schema): fluid: {preset: water, t_C: 50}
FLUID_PRESET_PARAMS = (
    Param("preset", "str", required=True, choices=("water",),
          help="Stoffwerttabelle Wasser (VDI-Wärmeatlas, 1 bar)"),
    Param("t", "temperature", default=50.0,
          help="mittlere Netztemperatur für die Stoffwerte (Tabelle 10–90 °C, außerhalb geklemmt)"),
)
#: Variante 2: konstante Stoffwerte frei vorgegeben:
#: fluid: {name: glykol, rho: 1040, mu: 3.5e-3, cp: 3600}
FLUID_CUSTOM_PARAMS = (
    Param("name", "str", default="custom", help="Bezeichnung (nur Bericht)"),
    Param("rho", "none", required=True, minv=1e-3, help="Dichte [kg/m³]"),
    Param("mu", "none", required=True, minv=1e-9, help="dynamische Viskosität [Pa·s]"),
    Param("cp", "none", required=True, minv=1.0, help="spezifische Wärmekapazität [J/(kg·K)]"),
)


# Stoffwerte Wasser bei 1 bar (VDI-Wärmeatlas, gerundet)
_WATER_TABLE = {
    # T °C: (rho, mu, cp)
    10: (999.7, 1.306e-3, 4195.0),
    20: (998.2, 1.002e-3, 4184.0),
    30: (995.7, 0.798e-3, 4180.0),
    40: (992.2, 0.653e-3, 4179.0),
    50: (988.0, 0.547e-3, 4181.0),
    60: (983.2, 0.466e-3, 4185.0),
    70: (977.8, 0.404e-3, 4190.0),
    80: (971.8, 0.355e-3, 4197.0),
    90: (965.3, 0.315e-3, 4205.0),
}


def water_at(t_c: float) -> Fluid:
    """Wasser-Stoffwerte bei mittlerer Netztemperatur t_c (10–90 °C, linear interpoliert)."""
    ts = np.array(sorted(_WATER_TABLE))
    t = float(np.clip(t_c, ts[0], ts[-1]))
    rho = float(np.interp(t, ts, [_WATER_TABLE[k][0] for k in ts]))
    mu = float(np.interp(t, ts, [_WATER_TABLE[k][1] for k in ts]))
    cp = float(np.interp(t, ts, [_WATER_TABLE[k][2] for k in ts]))
    return Fluid(name=f"water_{t:.0f}C", rho=rho, mu=mu, cp=cp)


WATER_DEFAULT = water_at(50.0)
