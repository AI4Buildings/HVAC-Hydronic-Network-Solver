"""Eindeutigkeitsprüfung der Hydraulik bei nicht-monotonen Kennlinien.

Netze aus monotonen Kennlinien (Δp steigt mit Q) haben genau eine Lösung.
Komponenten mit nicht-monotoner Kennlinie — derzeit das Idelchik-T-Stück
über den Regimewechsel Trennen ↔ Vereinigen — können mehrere stationäre
Lösungen erzeugen. Die Solver-Prüfung 2026-10 fand dafür in 59 von 310
Zufallsnetzen mit Idelchik-T-Stück ausschließlich dynamisch STABILE
Mehrfachlösungen: welche sich einstellt, hängt vom Anfahrvorgang ab, ein
stationärer Solver kann das nicht entscheiden. Statt still eine auszuwählen,
wird gemeldet. Konvergiert der Standardstart nicht, wird von den
alternativen Startwerten aus neu gestartet (solve_hydraulics_checked).

Vorgehen (nur wenn eine Komponente nonmonotone_hydraulics() meldet):
1. ausgegebene Lösung nachschärfen (Neustart von ihr, Toleranz ×1e-5);
2. von uniqueness_starts reproduzierbaren Startwerten aus lösen (Beträge
   log-gleichverteilt 1e-3…1 × V̇max, Vorzeichen zufällig), ebenso
   nachschärfen;
3. verschieden, wenn max|ΔQ| > max(1e-4·V̇max, 1e-6 m³/s). Toleranzreste
   schwach bestimmter Maschen bleiben nach dem Nachschärfen ≤ 4e-5 m³/h,
   echte Mehrfachlösungen der Kampagne ≥ 0,04 m³/h.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import numpy as np

from ..exceptions import ConvergenceError, HydraulikError
from ..network import CompiledNetwork
from .hydraulic import HydraulicState, solve_hydraulics
from .settings import SolverSettings

#: Unterschiedsschwelle: relativ zum größten Volumenstrom und absolut [m³/s]
_DIFF_REL = 1e-4
_DIFF_ABS = 1e-6
#: Nachschärfen: Toleranzen relativ zu den eingestellten
_TIGHT_FACTOR = 1e-5
#: fester Zufallsstartwert — gleiche Eingabe, gleiches Ergebnis
_RNG_SEED = 20261006


@dataclass
class AlternativeSolution:
    q: np.ndarray            # Kantenvolumenströme [m³/s]
    p: np.ndarray            # Knotendrücke [Pa]
    dq_max: float            # größte Abweichung zur ausgegebenen Lösung [m³/s]


def _solve_from(net: CompiledNetwork, seeds: np.ndarray, settings: SolverSettings):
    saved = [e.q_seed for e in net.edges]
    try:
        for e, v in zip(net.edges, seeds):
            e.q_seed = float(v) if v != 0.0 else 1e-15
        return solve_hydraulics(net, settings)
    except HydraulikError:
        return None
    finally:
        for e, v in zip(net.edges, saved):
            e.q_seed = v


def _restore_component_state(net: CompiledNetwork, q: np.ndarray) -> None:
    """Komponenten mit gekoppelten Kanten (pre_coefficients) auf die
    ausgegebene Lösung zurücksetzen — die Zusatzläufe überschreiben ihn."""
    groups: dict[int, tuple[object, list[int]]] = {}
    for e in net.edges:
        if hasattr(e.component, "pre_coefficients"):
            groups.setdefault(id(e.component), (e.component, []))[1].append(e.index)
    for comp, idxs in groups.values():
        comp.pre_coefficients([float(q[i]) for i in idxs], net.fluid)


def _nonmonotone(net: CompiledNetwork) -> list[str]:
    return sorted(c.name for c in net.components.values() if c.nonmonotone_hydraulics())


def _start_vectors(m: int, scale: float, n: int):
    """Reproduzierbare Startwerte: Beträge log-gleichverteilt 1e-3…1 × scale,
    Vorzeichen zufällig."""
    rng = np.random.default_rng(_RNG_SEED)
    for _ in range(n):
        yield rng.choice([-1.0, 1.0], m) * scale * 10.0 ** rng.uniform(-3.0, 0.0, m)


def solve_hydraulics_checked(net: CompiledNetwork,
                             settings: SolverSettings) -> HydraulicState:
    """Hydraulik lösen, bei nicht-monotonen Komponenten mit Eindeutigkeits-
    prüfung (hyd.alternatives). Konvergiert der Standardstart nicht, wird bei
    solchen Komponenten von den alternativen Startwerten aus neu gestartet —
    ihre Kennlinien können Bereiche ohne stabiles Gleichgewicht haben, in
    denen die Iteration vom Standardstart aus umherirrt, obwohl stabile
    Lösungen existieren (hyd.restarted = True, Hinweis im Ergebnis)."""
    try:
        hyd = solve_hydraulics(net, settings)
    except ConvergenceError as exc:
        names = _nonmonotone(net)
        if not names or settings.uniqueness_starts <= 0 or not net.edges:
            raise
        seeds = [abs(e.q_seed) for e in net.edges if e.q_seed]
        scale = max(max(seeds, default=0.0), settings.q_init) * 10.0
        hyd = None
        for start in _start_vectors(len(net.edges), scale, int(settings.uniqueness_starts)):
            hyd = _solve_from(net, start, settings)
            if hyd is not None:
                break
        if hyd is None:
            raise ConvergenceError(
                f"{exc} Auch von {settings.uniqueness_starts} alternativen Startwerten keine "
                f"stationäre Lösung (nicht-monotone Kennlinie: "
                f"{', '.join(repr(x) for x in names)}) — möglicherweise existiert kein "
                f"stabiler Betriebszustand.", exc.residual_history) from exc
        hyd.restarted = True
    hyd.alternatives = find_alternative_solutions(net, hyd, settings)
    return hyd


def find_alternative_solutions(net: CompiledNetwork, hyd: HydraulicState,
                               settings: SolverSettings) -> list[AlternativeSolution]:
    n_starts = int(settings.uniqueness_starts)
    if n_starts <= 0 or not hyd.converged or not net.edges:
        return []
    if not _nonmonotone(net):
        return []
    tight = dataclasses.replace(settings,
                                tol_mass_rel=settings.tol_mass_rel * _TIGHT_FACTOR,
                                tol_mom_rel=settings.tol_mom_rel * _TIGHT_FACTOR,
                                max_iter=max(settings.max_iter, 4000))
    try:
        ref = _solve_from(net, hyd.q, tight)
        q_ref = ref.q if ref is not None else hyd.q
        qmax = float(np.max(np.abs(q_ref)))
        if qmax <= 0.0:
            return []
        tol = max(_DIFF_REL * qmax, _DIFF_ABS)
        found: list[AlternativeSolution] = []
        for seeds in _start_vectors(len(net.edges), qmax, n_starts):
            first = _solve_from(net, seeds, settings)
            if first is None:
                continue
            sol = _solve_from(net, first.q, tight)
            if sol is None:
                continue
            d = float(np.max(np.abs(sol.q - q_ref)))
            if d <= tol or any(float(np.max(np.abs(sol.q - f.q))) <= tol for f in found):
                continue
            found.append(AlternativeSolution(q=sol.q.copy(), p=sol.p.copy(), dq_max=d))
        return found
    finally:
        _restore_component_state(net, hyd.q)
