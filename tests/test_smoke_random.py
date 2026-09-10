"""Randomisierter Rauchtest über die Komponentenpalette (feste Seeds).

Deckt Kombinationen ab, die gezielte Tests leicht auslassen: sperrend
angeströmte Rückschlagklappen (Restleckage), fast oder ganz geschlossene
Ventile, laminare Kleinstströmungen, feste Leistungen bei Kleinstdurchfluss,
T-Stücke mit Idelchik-Druckverlust, Druckanker im geschlossenen Kreis.
Geprüft wird nur, was für JEDES Netz gelten muss: Konvergenz, Massen- und
Energiebilanz, JSON-Serialisierbarkeit des Ergebnisses. (Der Heizkörper-
Absturz bei Kleinstdurchfluss wäre hier sofort aufgefallen.)
"""
import json
import random

import pytest

import hydraulik as h


def _strand(net, rng, tag, start_ref, allow_closed):
    """Kette aus 1–3 Armaturen/Leitungen + optionalem Wärmeabgabesystem;
    liefert die Port-Referenz des Strangendes."""
    refs = [start_ref]

    def add(comp):
        net.add(comp)
        net.connect(refs[-1], f"{comp.name}.in")
        refs.append(f"{comp.name}.out")

    for j in range(rng.randint(1, 3)):
        kind = rng.choice(["rv", "rk", "srv", "pipe", "link", "kh", "fr"])
        nm = f"{kind}{tag}_{j}"
        if kind == "rv":
            op = (rng.choice([0.0, 0.01, 0.05, rng.uniform(0.05, 1.0), 1.0])
                  if allow_closed else rng.uniform(0.3, 1.0))
            add(h.ControlValve(nm, kvs_m3h=rng.uniform(0.3, 4), opening=op,
                               rangeability=rng.choice([50, 100, 1000]),
                               characteristic=rng.choice(["linear", "equal_percentage"])))
        elif kind == "rk":
            net.add(h.CheckValve(nm, kvs_m3h=rng.uniform(0.5, 4)))
            if allow_closed and rng.random() < 0.5:          # in Sperrrichtung eingebaut
                net.connect(refs[-1], f"{nm}.out")
                refs.append(f"{nm}.in")
            else:
                net.connect(refs[-1], f"{nm}.in")
                refs.append(f"{nm}.out")
        elif kind == "srv":
            add(h.BalancingValve(nm, kvs_m3h=rng.uniform(0.5, 4), opening=rng.uniform(0.2, 1)))
        elif kind == "pipe":
            add(h.Pipe(nm, length_m=rng.uniform(1, 30), d_inner_mm=rng.choice([10, 13, 16, 20]),
                       zeta=rng.uniform(0, 10)))
        elif kind == "link":
            add(h.Link(nm))
        elif kind == "kh":
            kw = {"kvs_m3h": rng.uniform(2, 20)} if rng.random() < 0.5 else {}
            add(h.BallValve(nm, closed=allow_closed and rng.random() < 0.15, **kw))
        else:
            add(h.FlowResistance(nm, c_Pa_m3h2=rng.uniform(100, 30000)))

    em = rng.choice(["rad", "rad_fix", "fbh", "fbh_c", "hr", "kr", "kr_grey", "none"])
    nm = f"em{tag}"
    if em == "rad":
        add(h.Radiator(nm, q_nom_kW=rng.uniform(0.5, 10), t_sup_nom_C=70, t_ret_nom_C=55,
                       t_room_C=20))
    elif em == "rad_fix":
        add(h.Radiator(nm, q_prescribed_kW=rng.uniform(0.5, 5), kv_m3h=rng.uniform(0.5, 3)))
    elif em == "fbh":
        add(h.FloorHeatingLoop(nm, area_m2=rng.uniform(5, 40), length_m=rng.uniform(30, 150),
                               d_inner_mm=12))
    elif em == "fbh_c":
        add(h.FloorHeatingLoop(nm, area_m2=rng.uniform(5, 40), c_Pa_m3h2=rng.uniform(1e3, 5e4),
                               q_prescribed_kW=rng.uniform(0.3, 3)))
    elif em == "hr":
        add(h.HeatingCoil(nm, ua_ref_W_K=rng.uniform(100, 2000), m_dot_air_kg_s=rng.uniform(0.2, 3),
                          t_air_in_C=rng.uniform(-10, 20), q_w_ref_m3h=rng.uniform(0.5, 3),
                          m_dot_air_ref_kg_s=rng.uniform(0.5, 3)))
    elif em == "kr":
        add(h.CoolingCoil(nm, ua_ref_W_K=rng.uniform(100, 2000), m_dot_air_kg_s=rng.uniform(0.2, 3),
                          t_air_in_C=rng.uniform(22, 35)))
    elif em == "kr_grey":
        add(h.CoolingCoil(nm, ua_ref_W_K=rng.uniform(100, 2000), ua_star_wet_kg_s=rng.uniform(0.1, 2),
                          rh_air_in=rng.uniform(0.3, 0.8), m_dot_air_kg_s=rng.uniform(0.2, 3),
                          t_air_in_C=rng.uniform(22, 35)))
    return refs[-1]


def build_random_network(seed: int) -> h.Network:
    rng = random.Random(seed)
    net = h.Network(fluid=h.water_at(rng.choice([30, 50, 70])))
    sp_kw = {"t_set_C": rng.uniform(45, 75)}
    if rng.random() < 0.3:
        sp_kw["p_out_kPa"] = rng.uniform(50, 300)                # Druckanker im Kreis
    net.add(h.IdealStorage("sp", **sp_kw))
    if rng.random() < 0.5:
        net.add(h.Pump("pu", mode="constant_dp", dp_kPa=rng.uniform(5, 80),
                       q_nom_m3h=rng.uniform(0.3, 5)))
    else:
        net.add(h.Pump("pu", mode="constant_flow", q_m3h=rng.uniform(0.3, 5)))
    for nm in ("vl", "rl"):
        kw = dict(length_m=rng.uniform(2, 80), d_inner_mm=rng.choice([16, 20, 26, 32, 40]))
        if rng.random() < 0.4:
            kw.update(u_linear_W_mK=rng.uniform(0.1, 1.0), t_amb_C=rng.uniform(5, 25))
        net.add(h.Pipe(nm, **kw))
    n_br = rng.randint(1, 5)
    net.add(h.Manifold("vt", n_ports=n_br))
    net.add(h.Manifold("sm", n_ports=n_br))
    net.connect("sp.out", "pu.in")
    net.connect("pu.out", "vl.in")
    net.connect("vl.out", "vt.main")
    net.connect("sm.main", "rl.in")
    net.connect("rl.out", "sp.in")
    for k in range(n_br):
        # Strang 0 bleibt immer offen (Konstantstrom-Pumpe braucht einen Weg)
        if k > 0 and rng.random() < 0.25:
            tee_kw = {} if rng.random() < 0.5 else {"d_run_mm": 32, "d_branch_mm": 25}
            net.add(h.Tee(f"t{k}", **tee_kw))
            net.connect(f"vt.s{k + 1}", f"t{k}.a")
            end_b = _strand(net, rng, f"{k}b", f"t{k}.b", allow_closed=True)
            end_c = _strand(net, rng, f"{k}c", f"t{k}.c", allow_closed=True)
            net.connect(end_b, end_c, f"sm.s{k + 1}")
        else:
            end = _strand(net, rng, str(k), f"vt.s{k + 1}", allow_closed=k > 0)
            net.connect(end, f"sm.s{k + 1}")
    return net


@pytest.mark.parametrize("seed", range(40))
def test_zufallsnetz_loest_und_bilanziert(seed):
    net = build_random_network(seed)
    r = net.solve()
    assert r.converged
    assert r.mass_residual < 1e-7
    assert abs(r.energy_imbalance_W) < 1.0
    json.dumps(r.to_dict())                     # numpy-Typen sauber konvertiert
    for c in r.components:                       # keine NaN/Inf im Ergebnis
        assert abs(c.q_m3h) < 1e6 and abs(c.dp_kPa) < 1e9
