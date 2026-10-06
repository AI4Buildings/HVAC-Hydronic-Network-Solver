"""Zufallsnetz-Kampagne: allgemeine (vermaschte, offene, mehrpumpige) Netze aus der
ganzen Palette, unabhängige Nachrechnung jeder Lösung, Eindeutigkeit, Maximumprinzip.

    python3 tools/pruefkampagne/kampagne.py START ANZAHL [--quick]

Schreibt kampagne_START_ANZAHL.json (eine Zeile je Netz: Klasse, Meldung,
Iterationen) ins aktuelle Verzeichnis. Klassen: ok | ISSUES (Nachrechnung
oder Eindeutigkeit auffällig) | expected_validation | expected_no_steady |
UNEXPECTED_convergence | UNEXPECTED_model | UNEXPECTED_crash | generator.
Referenzstand 2026-10-06 (0 3300 --quick): ok 2822, ISSUES 17 (erklärt in
tools/README.md), expected_no_steady 84, expected_validation 375,
UNEXPECTED_convergence 1 (Seed 913, ~5e12 Pa), generator 1.
--quick überspringt den Zweitlauf mit anderen Startwerten (Eindeutigkeit).
Vor jeder Solver-Änderung laufen lassen und mit dem Referenzstand vergleichen.
"""
from __future__ import annotations

import json
import random
import sys
import traceback
from collections import Counter
from multiprocessing import Pool

import numpy as np

import hydraulik as h
from hydraulik.exceptions import (ComponentModelError, ConvergenceError,
                                  NetworkValidationError, SingularNetworkError)
from hydraulik.solver.settings import SolverSettings

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from nachrechnung import check_hydraulics, check_thermal, solve_raw  # noqa: E402

ACTIVE_TYPES = {"heat_pump", "chiller"}


def passive(rng, nm, allow_closed=True):
    k = rng.choice(["pipe", "pipe_loss", "fr", "rv", "srv", "kh", "rk", "link", "conduit",
                    "conduit_pipes", "flow_sensor"])
    if k == "pipe":
        return h.Pipe(nm, length_m=rng.uniform(0.5, 120), d_inner_mm=rng.choice([8, 13, 20, 32, 50, 80]),
                      zeta=rng.uniform(0, 15), roughness_mm=rng.choice([0.0, 0.007, 0.05, 0.5]))
    if k == "pipe_loss":
        return h.Pipe(nm, length_m=rng.uniform(1, 80), d_inner_mm=rng.choice([13, 20, 32]),
                      u_linear_W_mK=rng.uniform(0.05, 2), t_amb_C=rng.uniform(-10, 30))
    if k == "fr":
        return h.FlowResistance(nm, c_Pa_m3h2=10 ** rng.uniform(1, 5), a_Pa_m3h=rng.choice([0, 0, 50]))
    if k == "rv":
        op = rng.choice([0.0, 1e-3, 0.02, rng.uniform(0, 1), 1.0]) if allow_closed else rng.uniform(0.2, 1)
        return h.ControlValve(nm, kvs_m3h=10 ** rng.uniform(-0.5, 1.5), opening=op,
                              characteristic=rng.choice(["linear", "equal_percentage"]),
                              rangeability=rng.choice([25, 100, 1000]))
    if k == "srv":
        return h.BalancingValve(nm, kvs_m3h=10 ** rng.uniform(-0.5, 1.5), opening=rng.uniform(0.05, 1))
    if k == "kh":
        kw = {"kvs_m3h": 10 ** rng.uniform(0, 2)} if rng.random() < 0.5 else {}
        return h.BallValve(nm, closed=allow_closed and rng.random() < 0.1, **kw)
    if k == "rk":
        return h.CheckValve(nm, kvs_m3h=10 ** rng.uniform(-0.3, 1.5))
    if k == "link":
        return h.Link(nm)
    if k == "conduit":
        return rng.choice([h.Conduit(nm), h.Conduit(nm, c_Pa_m3h2=10 ** rng.uniform(1, 4)),
                           h.Conduit(nm, length_m=rng.uniform(1, 50), d_inner_mm=rng.choice([16, 26, 40]),
                                     u_linear_W_mK=rng.choice([0, 0.3]), t_amb_C=15)])
    if k == "conduit_pipes":
        segs = [{"length_m": rng.uniform(1, 20), "d_inner_mm": rng.choice([16, 26, 40]),
                 "zeta": rng.uniform(0, 3)} for _ in range(rng.randint(1, 3))]
        return h.Conduit(nm, pipes=segs, u_linear_W_mK=rng.choice([0, 0.4]), t_amb_C=12)
    return h.FlowSensor(nm)


def emitter(rng, nm):
    k = rng.choice(["rad", "rad_fix", "fbh", "hr", "kr", "kr_grey", "hr_fix"])
    if k == "rad":
        ts = rng.uniform(40, 80)
        return h.Radiator(nm, q_nom_kW=rng.uniform(0.3, 20), t_sup_nom_C=ts, t_ret_nom_C=ts - rng.uniform(3, 20),
                          t_room_C=rng.uniform(15, 24), n=rng.uniform(1.1, 1.45),
                          **({"kv_m3h": rng.uniform(0.2, 5)} if rng.random() < 0.5 else {}))
    if k == "rad_fix":
        return h.Radiator(nm, q_prescribed_kW=rng.uniform(0.1, 5), kv_m3h=rng.uniform(0.3, 5))
    if k == "fbh":
        return h.FloorHeatingLoop(nm, area_m2=rng.uniform(5, 60), length_m=rng.uniform(20, 150),
                                  d_inner_mm=rng.choice([10, 12, 14]), t_room_C=rng.uniform(18, 24))
    if k == "hr":
        return h.HeatingCoil(nm, ua_ref_W_K=rng.uniform(50, 5000), m_dot_air_kg_s=rng.uniform(0.1, 5),
                             t_air_in_C=rng.uniform(-15, 25), q_w_ref_m3h=rng.uniform(0.3, 5),
                             m_dot_air_ref_kg_s=rng.uniform(0.2, 5), n=rng.uniform(0, 1),
                             arrangement=rng.choice(["counterflow", "crossflow_unmixed"]))
    if k == "hr_fix":
        return h.HeatingCoil(nm, q_prescribed_kW=-rng.uniform(0.5, 10), m_dot_air_kg_s=1, t_air_in_C=0)
    if k == "kr":
        return h.CoolingCoil(nm, ua_ref_W_K=rng.uniform(50, 5000), m_dot_air_kg_s=rng.uniform(0.1, 5),
                             t_air_in_C=rng.uniform(20, 38))
    return h.CoolingCoil(nm, ua_ref_W_K=rng.uniform(100, 3000), ua_star_wet_kg_s=rng.uniform(0.05, 3),
                         rh_air_in=rng.uniform(0.2, 0.95), m_dot_air_kg_s=rng.uniform(0.2, 4),
                         t_air_in_C=rng.uniform(20, 36), q_w_ref_m3h=rng.uniform(0.5, 5),
                         m_dot_air_ref_kg_s=rng.uniform(0.2, 4))


def source(rng, nm):
    k = rng.choice(["sp", "sp_q", "wp", "wp_qmax", "wp_fix", "km", "km_qmax"])
    qn = {"q_nom_m3h": rng.uniform(0.5, 20)}
    if k == "sp":
        return h.IdealStorage(nm, t_set_C=rng.uniform(5, 80))
    if k == "sp_q":
        return h.IdealStorage(nm, t_set_C=rng.uniform(5, 80), q_m3h=rng.uniform(0.2, 5))
    if k == "wp":
        return h.HeatPump(nm, mode="target_t_out", t_out_set_C=rng.uniform(30, 75), **qn)
    if k == "wp_qmax":
        return h.HeatPump(nm, mode="target_t_out", t_out_set_C=rng.uniform(30, 75),
                          q_max_kW=rng.uniform(1, 50), **qn)
    if k == "wp_fix":
        return h.HeatPump(nm, mode="prescribed_q", q_dot_kW=rng.uniform(1, 30), **qn)
    if k == "km":
        return h.Chiller(nm, mode="target_t_out", t_out_set_C=rng.uniform(4, 16), **qn)
    return h.Chiller(nm, mode="target_t_out", t_out_set_C=rng.uniform(4, 16), q_max_kW=rng.uniform(1, 40), **qn)


def pump(rng, nm):
    if rng.random() < 0.55:
        return h.Pump(nm, mode="constant_dp", dp_kPa=rng.uniform(5, 150), q_nom_m3h=rng.uniform(0.3, 30),
                      dp_internal_frac=rng.choice([0.05, 0.01, 1e-4]))
    return h.Pump(nm, mode="constant_flow", q_m3h=rng.uniform(0.1, 20))


def random_net(seed: int):
    rng = random.Random(seed)
    fluid = rng.choice([h.water_at(rng.uniform(10, 90)), h.Fluid("glykol", 1050.0, 4e-3, 3600.0),
                        h.Fluid("oel", 870.0, 3e-2, 2000.0)])
    net = h.Network(fluid=fluid)
    J = rng.randint(2, 8)
    groups = {j: [] for j in range(J)}
    cnt = Counter()

    def name(prefix):
        cnt[prefix] += 1
        return f"{prefix}{cnt[prefix]}"

    def two(comp, u, v):
        net.add(comp)
        groups[u].append(f"{comp.name}.in")
        groups[v].append(f"{comp.name}.out")

    order = list(range(J))
    rng.shuffle(order)
    for k in range(1, J):                       # zusammenhängendes Gerüst
        u, v = order[k], order[rng.randrange(k)]
        if rng.random() < 0.5:
            u, v = v, u
        r = rng.random()
        if r < 0.55:
            two(passive(rng, name("p"), allow_closed=rng.random() < 0.3), u, v)
        elif r < 0.85:
            two(emitter(rng, name("em")), u, v)
        else:
            two(source(rng, name("q")), u, v)
    for _ in range(rng.randint(0, J + 2)):     # Maschen
        u, v = rng.sample(range(J), 2)
        r = rng.random()
        if r < 0.5:
            two(passive(rng, name("p")), u, v)
        elif r < 0.85:
            two(emitter(rng, name("em")), u, v)
        else:
            two(source(rng, name("q")), u, v)
    for _ in range(rng.choice([0, 1, 1, 1, 2, 3])):    # Pumpen
        u, v = rng.sample(range(J), 2)
        two(pump(rng, name("pu")), u, v)
    if rng.random() < 0.6:                       # Wärmequelle sicherstellen
        u, v = rng.sample(range(J), 2)
        two(source(rng, name("q")), u, v)
    # Mehrtore
    if J >= 4 and rng.random() < 0.3:
        c = net.add(h.HydraulicSeparator(name("hw"), q_nom_m3h=rng.uniform(0.5, 10), ua_W_K=rng.choice([0, 3])))
        for port in c.port_names():
            groups[rng.randrange(J)].append(f"{c.name}.{port}")
    if J >= 3 and rng.random() < 0.3:
        c = net.add(h.MixingValve3Way(name("mv"), kvs_m3h=10 ** rng.uniform(0, 1.3),
                                      opening=rng.choice([0.0, 1.0, rng.uniform(0, 1)]),
                                      characteristic=rng.choice(["linear", "equal_percentage"])))
        for port in c.port_names():
            groups[rng.randrange(J)].append(f"{c.name}.{port}")
    if J >= 3 and rng.random() < 0.3:
        kw = rng.choice([{}, {"d_run_mm": 40, "d_branch_mm": rng.choice([20, 32, 40])}])
        c = net.add(h.Tee(name("t"), **kw))
        js = rng.sample(range(J), 3)
        for port, j in zip(c.port_names(), js):
            groups[j].append(f"{c.name}.{port}")
    if rng.random() < 0.25:
        c = net.add(h.BufferStorage(name("ps"), n_ports=rng.randint(2, 4), ua_W_K=rng.choice([0, 2, 10]),
                                    t_amb_C=rng.uniform(10, 25)))
        for port in c.port_names():
            groups[rng.randrange(J)].append(f"{c.name}.{port}")
    if rng.random() < 0.2:
        c = net.add(h.EnergyMeter(name("wmz")))
        u, v, w = (rng.randrange(J) for _ in range(3))
        if u == v:
            v = (u + 1) % J
        groups[u].append(f"{c.name}.in")
        groups[v].append(f"{c.name}.out")
        groups[w].append(f"{c.name}.t_ref")
    # Randbedingungen: offen (Zulauf/Ablauf) oder Druckanker
    r = rng.random()
    if r < 0.25:
        c = net.add(h.Inflow(name("zu"), t_set_C=rng.uniform(5, 70),
                             **rng.choice([{"p_kPa": rng.uniform(20, 300)}, {"q_m3h": rng.uniform(0.1, 5)}])))
        groups[rng.randrange(J)].append(f"{c.name}.port")
        c = net.add(h.Outflow(name("ab"), p_kPa=rng.uniform(0, 100), t_reverse_C=rng.uniform(5, 30)))
        groups[rng.randrange(J)].append(f"{c.name}.port")
    elif r < 0.4:
        c = net.add(h.OpenEnd(name("oe"), bc="pressure", p_kPa=rng.uniform(50, 300),
                              t_supply_C=rng.uniform(5, 40)))
        groups[rng.randrange(J)].append(f"{c.name}.port")
    for j, refs in groups.items():
        if len(refs) == 1:                       # einzelner Port: Fühler anzapfen
            s = net.add(h.TemperatureSensor(name("tf")))
            refs.append(f"{s.name}.port")
        if len(refs) >= 2:
            net.connect(*refs)
    return net


def has_active(net):
    for c in net.components.values():
        if c.type_name in ACTIVE_TYPES:
            return True
        if getattr(c, "q_prescribed", None) is not None:
            return True
    return False


def imposed_temps(net, s):
    ts = [s.t_init]
    for c in net.components.values():
        for attr in ("t_room", "t_amb", "t_air_in", "t_set", "t_supply", "t_reverse"):
            v = getattr(c, attr, None)
            if isinstance(v, (int, float)):
                ts.append(float(v))
    return min(ts), max(ts)


def run_one(seed: int, quick=False) -> dict:
    out = {"seed": seed}
    try:
        net = random_net(seed)
    except Exception as exc:                     # Generatorfehler (Parameter) – kein Solverbefund
        return {**out, "class": "generator", "msg": repr(exc)}
    s = SolverSettings()
    try:
        comp, hyd, th = solve_raw(net, s)
    except (NetworkValidationError, SingularNetworkError) as exc:
        return {**out, "class": "expected_validation", "msg": str(exc)[:200]}
    except ConvergenceError as exc:
        msg = str(exc)
        if "keine stationäre Lösung" in msg:
            return {**out, "class": "expected_no_steady", "msg": msg[:150]}
        return {**out, "class": "UNEXPECTED_convergence", "msg": msg[:300]}
    except ComponentModelError as exc:
        return {**out, "class": "UNEXPECTED_model", "msg": str(exc)[:300]}
    except Exception as exc:
        return {**out, "class": "UNEXPECTED_crash", "msg": "".join(traceback.format_exception(exc))[-800:]}
    issues = check_hydraulics(comp, hyd) + check_thermal(comp, hyd, th, s)
    # Maximumprinzip (nur passive Netze)
    if not has_active(net):
        lo, hi = imposed_temps(net, s)
        # thermisch unbestimmte Knoten (Umlauf ohne Wärmeübertragung) haben
        # eine willkürliche Temperatur — für das Maximumprinzip nicht maßgeblich
        skip = set(th.undetermined_nodes) | set(th.stagnant_nodes)
        bad = [nd.label for nd in comp.nodes if nd.index not in skip
               and not (lo - 1e-6 <= th.t_node[nd.index] <= hi + 1e-6)]
        if bad:
            issues.append(f"Maximumprinzip verletzt [{lo:.2f},{hi:.2f}]: {bad[:3]} "
                          f"T={[round(float(th.t_node[nd.index]), 3) for nd in comp.nodes if nd.label in bad[:3]]}")
    # Eindeutigkeit: andere Startwerte
    if not quick:
        s2 = SolverSettings(q_init=2e-3, p_ref=0.0, t_init=55.0)
        try:
            comp2, hyd2, th2 = solve_raw(net, s2)
            qs = max(float(np.max(np.abs(hyd.q))), 1e-9)
            dq = float(np.max(np.abs(hyd.q - hyd2.q))) / qs
            if dq > 1e-5:
                issues.append(f"Eindeutigkeit V̇: rel. Abweichung {dq:.2e} bei anderen Startwerten")
            # Temperaturen nur an durchströmten/versorgten Knoten vergleichen (stagnierende = t_init)
            stag = set(th.stagnant_nodes) | set(th2.stagnant_nodes)
            dt = max([abs(th.t_node[i] - th2.t_node[i]) for i in range(len(comp.nodes)) if i not in stag] or [0.0])
            if dt > 1e-3:
                issues.append(f"Eindeutigkeit T: max. Abweichung {dt:.2e} K bei anderen Startwerten")
            # Druckdifferenzen (Druckniveau je Insel ist Konvention) über Kanten vergleichen
            ddp = max([abs((hyd.p[e.node_from] - hyd.p[e.node_to]) - (hyd2.p[e.node_from] - hyd2.p[e.node_to]))
                       for e in comp.edges if not e.is_fixed] or [0.0])
            if ddp > 1e-3 * max(1e3, float(np.max(np.abs(hyd.p)))):
                issues.append(f"Eindeutigkeit Δp: {ddp:.3e} Pa")
        except Exception as exc:
            issues.append(f"zweite Lösung (andere Startwerte) scheitert: {type(exc).__name__}: {str(exc)[:150]}")
    if issues:
        return {**out, "class": "ISSUES", "msg": " | ".join(issues[:6]),
                "it": [hyd.iterations, th.iterations]}
    return {**out, "class": "ok", "it": [hyd.iterations, th.iterations]}


if __name__ == "__main__":
    start, count = int(sys.argv[1]), int(sys.argv[2])
    quick = "--quick" in sys.argv
    with Pool(8) as pool:
        results = pool.starmap(run_one, [(s, quick) for s in range(start, start + count)])
    cls = Counter(r["class"] for r in results)
    print("Klassen:", dict(cls))
    its = [r["it"] for r in results if r.get("it")]
    if its:
        hy = [a for a, _ in its]
        tt = [b for _, b in its]
        print(f"Iterationen hydraulisch: median {np.median(hy):.0f}, max {max(hy)}; thermisch median {np.median(tt):.0f}, max {max(tt)}")
    with open(f"kampagne_{start}_{count}.json", "w") as f:
        json.dump(results, f, indent=1)
    for r in results:
        if r["class"].startswith(("UNEXPECTED", "ISSUES", "generator")):
            print(r["seed"], r["class"], r["msg"][:400].replace("\n", " ⏎ "))
