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

#: aus der Zufallsnetz-Kampagne (Seed 3289, T-Stück mit Totaldruck): zwei
#: stabile Lösungen — Vereinigung in Schenkel a (24,6 m³/h, Pumpe 42,2 m³/h)
#: oder in Schenkel c (13,5 m³/h, Pumpe 34,9 m³/h)
MEHRDEUTIG = {
    "fluid": {"name": "oel", "rho": 870.0, "mu": 0.03, "cp": 2000.0},
    "components": {
        "p1": {"type": "pipe", "length_m": 17.07, "zeta": 14.14, "d_inner_mm": 80.0},
        "q1": {"type": "ideal_storage", "t_set_C": 5.085},
        "p2": {"type": "flow_resistance", "c_Pa_m3s2": 1.413e9, "a_Pa_m3s": 1.8e5},
        "pu1": {"type": "pump", "mode": "constant_dp", "dp_kPa": 10.37, "q_nom_m3h": 19.02},
        "q2": {"type": "chiller", "mode": "target_t_out", "t_out_set_C": 7.666,
               "q_max_kW": 20.1, "q_nom_m3h": 10.82},
        "t1": {"type": "tee", "d_run_mm": 40.0, "d_branch_mm": 32.0}},
    "connections": [["q1.out", "p2.out", "pu1.in", "q2.out", "t1.c"],
                    ["p1.out", "p2.in", "pu1.out", "t1.b"],
                    ["p1.in", "q1.in", "q2.in", "t1.a"]]}

#: Seed 1972: vom Standardstartwert aus irrt die Iteration umher (Kennlinie
#: ohne stabiles Gleichgewicht in diesem Bereich), eine stabile Lösung
#: existiert. (Das frühere Netz Seed 2647 hatte die Schenkel b und c über
#: einen Volumenstromsensor kurzgeschlossen und wird seit 2026-10-06 abgelehnt.)
NEUSTART = {
    "fluid": {"name": "water_61C", "rho": 982.9, "mu": 0.0004626, "cp": 4185.0},
    "components": {
        "em1": {"type": "radiator", "q_nom_kW": 0.4962, "t_sup_nom_C": 48.0,
                "t_ret_nom_C": 41.43, "t_room_C": 16.08, "n": 1.126, "kv_m3h": 2.977},
        "p1": {"type": "pipe", "length_m": 17.24, "d_inner_mm": 20.0, "zeta": 13.14},
        "p2": {"type": "conduit", "t_amb_C": 12.0,
               "pipes": [{"length_m": 3.989, "d_inner_mm": 40.0, "roughness_mm": 0.007,
                          "zeta": 2.585}]},
        "em2": {"type": "floor_heating", "area_m2": 59.76, "t_room_C": 18.9, "length_m": 98.95},
        "em3": {"type": "cooling_coil", "ua_ref_W_K": 1624.0, "q_w_ref_m3h": 2.551,
                "m_dot_air_ref_kg_s": 0.2793, "m_dot_air_kg_s": 2.704, "t_air_in_C": 21.91,
                "ua_star_wet_kg_s": 2.375, "rh_air_in": 0.3789},
        "pu1": {"type": "pump", "mode": "constant_flow", "q_m3h": 18.33},
        "t1": {"type": "tee", "d_run_mm": 40.0, "d_branch_mm": 40.0},
        "zu1": {"type": "inflow", "t_set_C": 27.41, "p_kPa": 20.03},
        "ab1": {"type": "outflow", "p_kPa": 80.89, "t_reverse_C": 11.75},
        "tf1": {"type": "temperature_sensor"}},
    "connections": [["p2.out", "pu1.in", "t1.a", "zu1.port"], ["em1.out", "tf1.port"],
                    ["em1.in", "p1.out", "em2.in", "em3.in", "t1.b", "ab1.port"],
                    ["p1.in", "p2.in", "em2.out", "em3.out", "pu1.out", "t1.c"]]}


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
    r = h.load(copy.deepcopy(MEHRDEUTIG)).solve()
    assert r.converged
    assert len(r.alternatives) == 1
    note = next(n for n in r.notices if n.startswith("Hydraulik nicht eindeutig"))
    assert "'t1'" in note and "Anfahrvorgang" in note and "Lösung 2:" in note
    alt = r.alternatives[0]
    assert alt["dq_max_m3h"] > 20.0
    # verschiedene Strömungsbilder: Vereinigung in a bzw. in c
    assert r["t1:a"].q_m3h < -20.0 and r["t1:c"].q_m3h > 0.0
    assert alt["q_m3h"]["t1:a"] > 0.0 and alt["q_m3h"]["t1:c"] < -10.0
    assert alt["q_m3h"]["pu1"] == pytest.approx(34.87, abs=0.05)
    assert r["pu1"].q_m3h == pytest.approx(42.16, abs=0.05)
    assert len(r.to_dict()["alternatives"]) == 1


def test_neustart_von_alternativem_startwert():
    """Konvergiert der Standardstart nicht, löst der Solver bei nicht-
    monotonen Komponenten von den alternativen Startwerten (mit Hinweis);
    ohne Eindeutigkeitsprüfung bleibt es beim Konvergenzfehler."""
    r = h.load(copy.deepcopy(NEUSTART)).solve()
    assert r.converged
    assert any("vom Standardstartwert nicht konvergiert" in n for n in r.notices)
    assert r.alternatives == []                 # nur diese eine Lösung gefunden
    with pytest.raises(h.ConvergenceError):
        h.load(copy.deepcopy(NEUSTART)).solve(SolverSettings(uniqueness_starts=0))


def test_alternativen_erfuellen_die_gleichungen():
    c = h.load(copy.deepcopy(MEHRDEUTIG)).compile()
    hyd = solve_hydraulics(c, SolverSettings())
    alts = uniqueness.find_alternative_solutions(c, hyd, SolverSettings())
    assert len(alts) == 1
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
    assert r.converged and calls == [1] and r.alternatives == []    # nur die Hauptrechnung


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
    assert payload["ok"] and len(payload["alternatives"]) == 1
    assert any(n.startswith("Hydraulik nicht eindeutig") for n in payload["notices"])
