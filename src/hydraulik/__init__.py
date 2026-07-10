"""hydraulik – stationäre hydraulisch-thermische Berechnung von HVAC-Schaltungen.

Kurzbeispiel (Python-API):

    from hydraulik import Network, components as c

    net = Network()
    net.add(c.Pump("pu1", mode="constant_dp", dp_kPa=30, q_nom_m3h=1.0))
    net.add(c.HeatPump("wp1", mode="target_t_out", t_out_set_C=45, q_max_kW=10))
    net.add(c.Radiator("hk1", q_nom_kW=8, t_sup_nom_C=45, t_ret_nom_C=40))
    net.connect("wp1.out", "pu1.in")
    net.connect("pu1.out", "hk1.in")
    net.connect("hk1.out", "wp1.in")
    print(net.solve().report())

Oder deklarativ:  hydraulik.load("schaltung.yaml").solve().report()
"""
from . import components
from .components import *  # noqa: F401,F403 – Komponenten auch direkt exportieren
from .exceptions import (ComponentParamError, ConvergenceError, HydraulikError,
                         NetworkValidationError, SingularNetworkError)
from .fluids import Fluid, WATER_DEFAULT, water_at
from .network import Network
from .results import SolutionResult
from .solver.settings import SolverSettings
from .yaml_loader import load, load_settings

__version__ = "0.3.0"

__all__ = [
    "Network", "load", "load_settings", "SolverSettings", "SolutionResult",
    "Fluid", "water_at", "WATER_DEFAULT", "components",
    "HydraulikError", "NetworkValidationError", "SingularNetworkError",
    "ConvergenceError", "ComponentParamError",
    "__version__",
] + list(components.__all__)
