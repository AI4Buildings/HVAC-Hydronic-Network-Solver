"""Eindeutigkeitsprüfung der Hydraulik (solver/uniqueness.py).

Netze mit Idelchik-T-Stück können mehrere stationäre Lösungen haben, die
alle dynamisch stabil sind (Solver-Prüfung 2026-10): welche sich einstellt,
hängt vom Anfahrvorgang ab. Der Solver wählt nicht still eine aus, sondern
meldet die weiteren Lösungen.
"""
import copy

import numpy as np
import pytest

import hydraulik as h
from hydraulik.solver import uniqueness
from hydraulik.solver.hydraulic import solve_hydraulics
from hydraulik.solver.settings import SolverSettings

#: aus der Zufallsnetz-Kampagne (Seed 3128): Schenkel a und b sind über den
#: widerstandsfreien Speicher sp2 fast kurzgeschlossen — neben der Haupt-
#: lösung zwei spiegelbildliche Lösungen mit 7,6 m³/h Umlauf a ↔ b
MEHRDEUTIG = {
    "fluid": {"name": "oel", "rho": 870.0, "mu": 0.03, "cp": 2000.0},
    "components": {
        "sp1": {"type": "ideal_storage", "t_set_C": 73.6, "q_m3h": 3.235},
        "r1": {"type": "pipe", "length_m": 33.0, "d_inner_mm": 13},
        "r2": {"type": "pipe", "length_m": 31.3, "d_inner_mm": 32},
        "rk": {"type": "check_valve", "kvs_m3h": 2.98},
        "pu": {"type": "pump", "mode": "constant_flow", "q_m3h": 11.83},
        "sp2": {"type": "ideal_storage", "t_set_C": 50.5},
        "t1": {"type": "tee", "d_run_mm": 40, "d_branch_mm": 32}},
    "connections": [["sp1.out", "r1.in", "r2.in", "sp2.out", "t1.b"],
                    ["sp1.in", "rk.in", "pu.out"],
                    ["r1.out", "sp2.in", "t1.a"],
                    ["r2.out", "rk.out", "pu.in", "t1.c"]]}


def _residuen(c, q, p):
    """Unabhängige Nachrechnung: Kontinuität an freien Knoten, Impuls je
    freier Kante mit den beim Zustand q ausgewerteten Koeffizienten."""
    groups = {}
    for e in c.edges:
        if hasattr(e.component, "pre_coefficients"):
            groups.setdefault(id(e.component), (e.component, []))[1].append(e.index)
    for comp, idxs in groups.values():
        comp.pre_coefficients([float(q[i]) for i in idxs], c.fluid)
    bal = np.zeros(len(c.nodes))
    mom = 0.0
    for e in c.edges:
        bal[e.node_from] -= q[e.index]
        bal[e.node_to] += q[e.index]
        if not e.is_fixed:
            co = e.coeff_fn(float(q[e.index]), c.fluid)
            G = co.a * q[e.index] + co.b * q[e.index] * abs(q[e.index]) - co.dp_source
            mom = max(mom, abs(p[e.node_from] - p[e.node_to] - G))
    for nd in c.nodes:
        bal[nd.index] += nd.flow_bc
    free = [nd.index for nd in c.nodes if not nd.pinned]
    return float(np.max(np.abs(bal[free]))) / float(np.max(np.abs(q))), mom


def test_mehrdeutiges_netz_wird_gemeldet():
    r = h.load(copy.deepcopy(MEHRDEUTIG)).solve(thermal=False)
    assert r.converged
    assert len(r.alternatives) == 2
    note = next(n for n in r.notices if n.startswith("Hydraulik nicht eindeutig"))
    assert "'t1'" in note and "Anfahrvorgang" in note
    assert "Lösung 2:" in note and "Lösung 3:" in note
    # Hauptstrom (Pumpe) gleich, Umlauf durch das T-Stück verschieden
    for a in r.alternatives:
        assert a["q_m3h"]["pu"] == pytest.approx(11.83, rel=1e-6)
        assert a["dq_max_m3h"] > 5.0
    # spiegelbildlich: Umlauf a → b bzw. b → a
    sp2 = sorted(a["q_m3h"]["sp2"] for a in r.alternatives)
    assert sp2[0] < -5.0 < 5.0 < sp2[1]
    assert "alternatives" in r.to_dict() and len(r.to_dict()["alternatives"]) == 2


def test_alternativen_erfuellen_die_gleichungen():
    c = h.load(copy.deepcopy(MEHRDEUTIG)).compile()
    hyd = solve_hydraulics(c, SolverSettings())
    alts = uniqueness.find_alternative_solutions(c, hyd, SolverSettings())
    assert len(alts) == 2
    for a in alts:
        mass, mom = _residuen(c, a.q, a.p)
        assert mass < 1e-9 and mom < 1e-3                  # [-], [Pa]
    # Komponentenzustand auf die ausgegebene Lösung zurückgesetzt
    mass, mom = _residuen(c, hyd.q, hyd.p)
    assert mass < 1e-7


def test_reproduzierbar_und_abschaltbar():
    r1 = h.load(copy.deepcopy(MEHRDEUTIG)).solve(thermal=False)
    r2 = h.load(copy.deepcopy(MEHRDEUTIG)).solve(thermal=False)
    assert r1.notices == r2.notices and r1.alternatives == r2.alternatives
    r0 = h.load(copy.deepcopy(MEHRDEUTIG)).solve(SolverSettings(uniqueness_starts=0), thermal=False)
    assert r0.alternatives == [] and not any("nicht eindeutig" in n for n in r0.notices)
    # Ergebnis der ausgegebenen Lösung durch die Prüfung unverändert
    assert [c.q_m3h for c in r0.components] == [c.q_m3h for c in r1.components]


def test_eindeutiges_idelchik_netz_ohne_hinweis():
    doc = {"components": {
               "zu": {"type": "inflow", "t_set_C": 50.0, "q_m3h": 2.0},
               "t1": {"type": "tee", "d_run_mm": 32.0, "d_branch_mm": 25.0},
               "ab_b": {"type": "outflow", "p_kPa": 150},
               "ab_c": {"type": "outflow", "q_m3h": 0.8}},
           "connections": [["zu.port", "t1.a"], ["t1.b", "ab_b.port"], ["t1.c", "ab_c.port"]]}
    r = h.load(doc).solve(thermal=False)
    assert r.alternatives == [] and not any("nicht eindeutig" in n for n in r.notices)


def test_ohne_nichtmonotone_komponente_keine_zusatzrechnung(monkeypatch):
    calls = []
    monkeypatch.setattr(uniqueness, "solve_hydraulics",
                        lambda *a, **k: calls.append(1) or solve_hydraulics(*a, **k))
    net = h.Network(fluid=h.water_at(50))
    net.add(h.Pump("pu", mode="constant_dp", dp_kPa=40, q_nom_m3h=3))
    net.add(h.Tee("t"))                                   # ideales T-Stück: monoton
    net.add(h.Pipe("r1", length_m=20, d_inner_mm=26))
    net.add(h.Pipe("r2", length_m=10, d_inner_mm=20))
    net.connect("pu.out", "t.a"); net.connect("t.b", "r1.in"); net.connect("t.c", "r2.in")
    net.connect("r1.out", "r2.out", "pu.in")
    r = net.solve(thermal=False)
    assert r.converged and calls == [] and r.alternatives == []


def test_einstellung_validiert():
    doc = copy.deepcopy(MEHRDEUTIG)
    doc["settings"] = {"uniqueness_starts": -1}
    with pytest.raises(h.NetworkValidationError, match="uniqueness_starts"):
        h.load_settings(doc)
    doc["settings"] = {"uniqueness_starts": 3}
    assert h.load_settings(doc).uniqueness_starts == 3


def test_server_liefert_alternativen():
    from hydraulik.server import solve_payload
    from hydraulik.yamlio import canonical_json
    payload = solve_payload(canonical_json(copy.deepcopy(MEHRDEUTIG)))
    assert payload["ok"] and len(payload["alternatives"]) == 2
    assert any(n.startswith("Hydraulik nicht eindeutig") for n in payload["notices"])
