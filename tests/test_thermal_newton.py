"""Energiegleichung (Newton/LM auf der Knotenbilanz): Korrektheit, Stabilität,
Invarianzen — geprüft gegen die Definition der Knotenbilanz, eine unabhängige
gedämpfte Fixpunktiteration als Referenzorakel und geschlossene Lösungen.
"""
import math

import numpy as np
import pytest

import hydraulik as h
from hydraulik.solver.hydraulic import solve_hydraulics
from hydraulik.solver.thermal import solve_thermal
from test_smoke_random import build_random_network

EXAMPLES = sorted((__import__("pathlib").Path(__file__).parent.parent / "examples").glob("*.yaml"))


# ------------------------------------------------------------ unabhängige Referenz

def node_balance(comp, hyd, t):
    """G(T) direkt nach der Definition (docs/numerik.md §2): je durchströmter
    Kante das Komponentenmodell am Upwind-Knoten, je Knoten ideale Mischung
    aller Zuströme, Randzuflüsse und UA-Verluste. Unabhängig vom Solver-Code."""
    fluid, q = comp.fluid, hyd.q
    n = len(comp.nodes)
    num, den, a_q = np.zeros(n), np.zeros(n), np.zeros(n)
    for e in comp.edges:
        a_q[e.node_from] += q[e.index]
        a_q[e.node_to] -= q[e.index]
    for e in comp.edges:
        m = abs(q[e.index]) * fluid.rho
        if m < 1e-7:
            continue
        upn, dn = (e.node_from, e.node_to) if q[e.index] >= 0 else (e.node_to, e.node_from)
        t_out = e.thermal_fn(float(t[upn]), m, fluid).t_out if e.thermal_fn else t[upn]
        num[dn] += m * fluid.cp * t_out
        den[dn] += m * fluid.cp
    for nd in comp.nodes:
        i = nd.index
        for qb, tb in nd.bc_supplies:
            if qb > 0:
                num[i] += fluid.rho * qb * fluid.cp * tb
                den[i] += fluid.rho * qb * fluid.cp
        if nd.pinned and not nd.is_auto_ref:
            env = a_q[i] - nd.flow_bc
            if env > 0 and nd.t_supply is not None:
                num[i] += fluid.rho * env * fluid.cp * nd.t_supply
                den[i] += fluid.rho * env * fluid.cp
        if nd.ua > 0:
            num[i] += nd.ua * nd.t_amb
            den[i] += nd.ua
    g = np.array(t, dtype=float)
    free = den > 1e-12
    g[free] = num[free] / den[free]
    return g


def fixed_point_defect(comp, hyd, th):
    return float(np.max(np.abs(node_balance(comp, hyd, th.t_node) - th.t_node)))


def picard_reference(comp, hyd, t0=20.0, alpha=0.5, tol=1e-10, sweeps=100000):
    """Gedämpfte Fixpunktiteration T ← T + α(G(T) − T) als unabhängiges Orakel."""
    t = np.full(len(comp.nodes), t0)
    for _ in range(sweeps):
        g = node_balance(comp, hyd, t)
        if np.max(np.abs(g - t)) < tol:
            return t
        t = t + alpha * (g - t)
    raise AssertionError("Referenzorakel nicht konvergiert")


def _solve_parts(net, settings=None):
    s = settings or h.SolverSettings()
    comp = net.compile()
    hyd = solve_hydraulics(comp, s)
    th = solve_thermal(comp, hyd, s)
    return comp, hyd, th


# ------------------------------------------------------ Fixpunkt + Orakel

@pytest.mark.parametrize("path", EXAMPLES, ids=[p.stem for p in EXAMPLES])
def test_beispiele_sind_exakte_fixpunkte_und_stimmen_mit_orakel(path):
    net = h.load(path)
    comp, hyd, th = _solve_parts(net, h.load_settings(path))
    assert th.converged
    assert fixed_point_defect(comp, hyd, th) < 1e-5
    ref = picard_reference(comp, hyd)
    assert np.max(np.abs(th.t_node - ref)) < 1e-5


@pytest.mark.parametrize("seed", range(20))
def test_zufallsnetze_stimmen_mit_orakel(seed):
    comp, hyd, th = _solve_parts(build_random_network(seed))
    assert th.converged and th.iterations <= 10
    assert fixed_point_defect(comp, hyd, th) < 1e-5
    ref = picard_reference(comp, hyd)
    scale = max(1.0, float(np.max(np.abs(ref))))            # feste Leistungen → große |T|
    assert np.max(np.abs(th.t_node - ref)) < 1e-5 * scale


# ------------------------------------------------------ geschlossene Lösungen

def test_rezirkulation_mit_rohrverlust_geschlossen():
    """Mischknoten mit Zulauf q_in (T_in), Rezirkulation q_r über ein Rohr mit
    Verlust (Faktor k = e^(−U'L/ṁcp)) und Ablauf q_in:
    T_N = (q_in·T_in + q_r·(1−k)·T_amb) / (q_in + q_r·(1−k))."""
    for ratio in (2, 50, 5000):
        q_in, q_r = 1.0 / ratio, 1.0
        doc = {
            "components": {
                "zu": {"type": "inflow", "t_set_C": 80, "q_m3h": q_in},
                "pu": {"type": "pump", "mode": "constant_flow", "q_m3h": q_r},
                "r": {"type": "pipe", "length_m": 40, "d_inner_mm": 20,
                      "u_linear_W_mK": 2.0, "t_amb_C": 10},
                "ab": {"type": "outflow", "p_kPa": 100}, "t": {"type": "tee"},
            },
            "connections": [["zu.port", "t.a"], ["t.b", "pu.in"], ["pu.out", "r.in"],
                            ["r.out", "t.c"], ["t.c", "ab.port"]],
        }
        net = h.load(doc)
        r = net.solve()
        fluid = net.fluid
        m_r = q_r / 3600 * fluid.rho
        k = math.exp(-2.0 * 40 / (m_r * fluid.cp))
        t_ref = (q_in * 80 + q_r * (1 - k) * 10) / (q_in + q_r * (1 - k))
        t_n = next(n.t_C for n in r.nodes if "ab.port" in n.label)   # = Mischknoten
        assert t_n == pytest.approx(t_ref, abs=1e-6), ratio
        assert r.iterations_thermal <= 3                     # linear: praktisch ein Schritt


@pytest.mark.parametrize("ua_w_k, iters_max", [(50.0, 5), (2e-3, 25), (5e-4, 40)])
def test_isolierter_umlauf_mit_ua_hat_geschlossene_loesung(ua_w_k, iters_max):
    """Erzeuger fester Leistung + Puffer mit UA im Umlauf: T_Puffer = T_amb + Q/UA.
    Bei sehr kleinem UA liegt die Lösung Millionen Kelvin entfernt — der
    Vertrauensbereich muss wachsen, OHNE das als Drift zu deuten."""
    net = h.Network()
    net.add(h.HeatPump("wp", mode="prescribed_q", q_dot_kW=5.0, q_nom_m3h=1.0))
    net.add(h.Pump("pu", mode="constant_flow", q_m3h=1.0))
    net.add(h.BufferStorage("sp", n_ports=2, ua_W_K=ua_w_k, t_amb_C=15))
    net.connect("wp.out", "pu.in")
    net.connect("pu.out", "sp.p1")
    net.connect("sp.p2", "wp.in")
    r = net.solve()
    assert r.converged and r.iterations_thermal <= iters_max
    t_sp = next(n.t_C for n in r.nodes if "sp.p1" in n.label)
    # Konditionierung: das Residuum tol_t = 1e-6 K an der Knotenbilanz
    # entspricht einer Temperaturabweichung von höchstens tol·(ṁcp + UA)/UA
    c = 1.0 / 3600 * net.fluid.rho * net.fluid.cp
    bound = 2 * 1e-6 * (c + ua_w_k) / ua_w_k
    assert t_sp == pytest.approx(15 + 5000 / ua_w_k, rel=1e-9, abs=bound)
    assert abs(r.energy_imbalance_W) < 1e-3 * max(1.0, t_sp / 100)


def test_balancierter_isolierter_umlauf():
    """+5 kW fest, −5 kW fest, kein Anker: jede Temperaturlage ist Lösung —
    kein Fehler, Bilanz null; mit winzigem UA wird die Lage eindeutig T_amb."""
    def net(ua):
        n = h.Network()
        n.add(h.HeatPump("wp", mode="prescribed_q", q_dot_kW=5.0, q_nom_m3h=1.0))
        n.add(h.Pump("pu", mode="constant_flow", q_m3h=1.0))
        n.add(h.Radiator("hk", q_prescribed_kW=5.0, kv_m3h=100))
        n.add(h.BufferStorage("sp", n_ports=2, ua_W_K=ua, t_amb_C=18))
        n.connect("wp.out", "pu.in"); n.connect("pu.out", "hk.in")
        n.connect("hk.out", "sp.p1"); n.connect("sp.p2", "wp.in")
        return n
    r = net(0.0).solve()
    assert r.converged and abs(r.energy_imbalance_W) < 1e-6
    assert r["wp"].q_dot_kW == pytest.approx(5.0) and r["hk"].q_dot_kW == pytest.approx(-5.0)
    assert all(np.isfinite(n.t_C) for n in r.nodes)
    r = net(10.0).solve()
    t_sp = next(n.t_C for n in r.nodes if "sp.p1" in n.label)
    assert t_sp == pytest.approx(18.0, abs=1e-3)              # ΔT ≤ tol·ṁcp/UA ≈ 1e-4 K


def test_lange_rohrkette_ein_schritt_und_exakt():
    """300 Rohre mit Verlust in Reihe: Advektionsfront, für Gauss-Seidel 300
    Sweeps — Newton löst das lineare Netz in einem Schritt, Austritt exakt
    T_amb + (T_ein − T_amb)·e^(−ΣNTU)."""
    net = h.Network()
    net.add(h.Inflow("zu", t_set_C=90, q_m3h=0.3))
    prev = "zu.port"
    for k in range(300):
        net.add(h.Pipe(f"r{k}", length_m=2.0, d_inner_mm=16, u_linear_W_mK=0.4, t_amb_C=12))
        net.connect(prev, f"r{k}.in")
        prev = f"r{k}.out"
    net.add(h.Outflow("ab", p_kPa=0))
    net.connect(prev, "ab.port")
    r = net.solve()
    assert r.converged and r.iterations_thermal == 1
    m = 0.3 / 3600 * net.fluid.rho
    t_ref = 12 + (90 - 12) * math.exp(-0.4 * 600 / (m * net.fluid.cp))
    assert r["r299"].t_out_C == pytest.approx(t_ref, abs=1e-6)   # Hydraulik: tol_mass_rel 1e-8


def test_lineares_netz_in_einem_newton_schritt():
    """Rein lineare/affine Modelle (Rohre mit Verlust, Mischventil, T-Stück,
    Puffer mit UA, idealer Speicher, feste Leistungen): die Jacobi-Matrix ist
    exakt, ein Newton-Schritt genügt — und der Fixpunkt ist exakt."""
    net = h.Network()
    net.add(h.IdealStorage("sp", t_set_C=75))
    net.add(h.Pump("pu", mode="constant_flow", q_m3h=2.0))
    net.add(h.Pipe("vl", length_m=30, d_inner_mm=26, u_linear_W_mK=0.5, t_amb_C=15))
    net.add(h.MixingValve3Way("mv", kvs_m3h=6.0, opening=0.6, characteristic="linear"))
    net.add(h.Pump("pu2", mode="constant_flow", q_m3h=3.0))
    net.add(h.Radiator("hk", q_prescribed_kW=8.0, kv_m3h=100))
    net.add(h.HeatingCoil("hr", q_prescribed_kW=-3.0, m_dot_air_kg_s=1.0, t_air_in_C=0.0))
    net.add(h.Tee("t"))
    net.add(h.BufferStorage("pf", n_ports=2, ua_W_K=12.0, t_amb_C=20))
    net.add(h.Pipe("rl", length_m=30, d_inner_mm=26, u_linear_W_mK=0.5, t_amb_C=15))
    net.connect("sp.out", "pu.in"); net.connect("pu.out", "vl.in"); net.connect("vl.out", "mv.a")
    net.connect("mv.ab", "pu2.in"); net.connect("pu2.out", "hk.in"); net.connect("hk.out", "hr.in")
    net.connect("hr.out", "t.a"); net.connect("t.b", "mv.b"); net.connect("t.c", "pf.p1")
    net.connect("pf.p2", "rl.in"); net.connect("rl.out", "sp.in")
    comp, hyd, th = _solve_parts(net)
    assert th.converged and th.iterations == 1
    assert fixed_point_defect(comp, hyd, th) < 1e-9
    assert abs(th.energy_imbalance) < 1e-2                    # tol_t·Σṁcp ≈ 4e-3 W


# ------------------------------------------------------ Invarianzen / Stabilität

def _hk_loop(order_reversed=False):
    comps = [
        h.IdealStorage("sp", t_set_C=70),
        h.Pump("pu", mode="constant_flow", q_m3h=1.2),
        h.Manifold("vt", n_ports=3), h.Manifold("sm", n_ports=3),
        h.Radiator("hk1", q_nom_kW=3, t_sup_nom_C=70, t_ret_nom_C=55, t_room_C=20),
        h.Radiator("hk2", q_nom_kW=5, t_sup_nom_C=70, t_ret_nom_C=55, t_room_C=22),
        h.FloorHeatingLoop("fb", area_m2=20, length_m=100, d_inner_mm=12, t_room_C=21),
        h.Pipe("rl", length_m=20, d_inner_mm=26, u_linear_W_mK=0.3, t_amb_C=15),
    ]
    net = h.Network()
    for c in (reversed(comps) if order_reversed else comps):
        net.add(c)
    conns = [("sp.out", "pu.in"), ("pu.out", "vt.main"), ("vt.s1", "hk1.in"), ("vt.s2", "hk2.in"),
             ("vt.s3", "fb.in"), ("hk1.out", "sm.s1"), ("hk2.out", "sm.s2"), ("fb.out", "sm.s3"),
             ("sm.main", "rl.in"), ("rl.out", "sp.in")]
    for a, b in (reversed(conns) if order_reversed else conns):
        net.connect(a, b)
    return net


@pytest.mark.parametrize("t_init", [-30.0, 20.0, 21.0, 95.0, 500.0])
def test_loesung_unabhaengig_vom_startwert(t_init):
    """Eindeutige Lösung: jeder Startwert (auch exakt Raumtemperatur = Heiz-
    körper 'aus', singuläre Linearisierung; auch weit daneben) führt zur
    selben Temperaturverteilung."""
    ref = _hk_loop().solve()
    r = _hk_loop().solve(h.SolverSettings(t_init=t_init))
    assert r.converged and r.iterations_thermal <= 15
    for a, b in zip(ref.components, r.components):
        assert b.t_in_C == pytest.approx(a.t_in_C, abs=1e-6)
        assert b.q_dot_kW == pytest.approx(a.q_dot_kW, abs=1e-6)


def test_loesung_unabhaengig_von_komponentenreihenfolge():
    """Knoten-/Kantennummerierung ändert nur die Reihenfolge, nie die Werte."""
    a = {c.name: c for c in _hk_loop().solve().components}
    b = {c.name: c for c in _hk_loop(order_reversed=True).solve().components}
    for name in a:
        assert b[name].t_out_C == pytest.approx(a[name].t_out_C, abs=1e-9)
        assert b[name].q_dot_kW == pytest.approx(a[name].q_dot_kW, abs=1e-9)


def test_wiederholtes_loesen_ist_idempotent():
    net = _hk_loop()
    r1, r2 = net.solve(), net.solve()
    assert [c.t_out_C for c in r1.components] == [c.t_out_C for c in r2.components]
    assert r1.iterations_thermal == r2.iterations_thermal


def test_stroemungsumkehr_liefert_gleiche_temperaturen():
    """Alle Kanten rückwärts durchströmt (Upwind folgt dem Vorzeichen): selbe
    Zustände an den entsprechenden Knoten wie vorwärts."""
    def build(reverse):
        net = h.Network()
        net.add(h.IdealStorage("sp", t_set_C=70))
        net.add(h.Pump("pu", mode="constant_flow", q_m3h=0.8))
        net.add(h.Radiator("hk", q_nom_kW=5, t_sup_nom_C=70, t_ret_nom_C=55, t_room_C=20))
        net.add(h.Pipe("r", length_m=15, d_inner_mm=20, u_linear_W_mK=0.5, t_amb_C=10))
        if not reverse:
            net.connect("sp.out", "pu.in"); net.connect("pu.out", "hk.in")
            net.connect("hk.out", "r.in"); net.connect("r.out", "sp.in")
        else:   # gleiche Umlaufreihenfolge sp→pu→hk→r→sp, alle Nicht-Pumpen-Kanten out→in
            net.connect("sp.in", "pu.in"); net.connect("pu.out", "hk.out")
            net.connect("hk.in", "r.out"); net.connect("r.in", "sp.out")
        return net.solve()
    f, b = build(False), build(True)
    assert b["hk"].q_m3h == pytest.approx(-f["hk"].q_m3h, rel=1e-9)
    for name in ("hk", "r", "sp"):
        assert b[name].t_in_C == pytest.approx(f[name].t_in_C, abs=1e-8)
        assert b[name].t_out_C == pytest.approx(f[name].t_out_C, abs=1e-8)
        assert b[name].q_dot_kW == pytest.approx(f[name].q_dot_kW, abs=1e-8)


def test_toleranz_wird_eingehalten_und_bestimmt_genauigkeit():
    for tol in (1e-3, 1e-6, 1e-9):
        comp, hyd, th = _solve_parts(_hk_loop(), h.SolverSettings(tol_t=tol))
        assert fixed_point_defect(comp, hyd, th) < tol


def test_stagnierende_knoten_bleiben_beim_startwert():
    net = h.Network()
    net.add(h.IdealStorage("sp", t_set_C=70))
    net.add(h.Pump("pu", mode="constant_flow", q_m3h=0.5))
    net.add(h.Radiator("hk", q_nom_kW=5, t_sup_nom_C=70, t_ret_nom_C=55, t_room_C=20))
    net.add(h.Pipe("stich", length_m=5, d_inner_mm=20)); net.add(h.Cap("ende"))
    net.connect("sp.out", "pu.in"); net.connect("pu.out", "hk.in", "stich.in")
    net.connect("stich.out", "ende.port"); net.connect("hk.out", "sp.in")
    r = net.solve(h.SolverSettings(t_init=33.0))
    dead = next(n for n in r.nodes if "ende.port" in n.label)
    assert dead.stagnant and dead.t_C == 33.0
    assert r["hk"].t_in_C == pytest.approx(70.0, abs=1e-9)   # unbeeinflusst


# ------------------------------------------------------ Klemmen, Regime, Größe

def test_erzeuger_loesung_genau_an_der_klemme():
    """Solltemperatur nur mit exakt q_max erreichbar: Lösung liegt am Knick
    (geklemmt und ungeklemmt zugleich) — Newton darf dort nicht pendeln."""
    fluid = h.water_at(50)
    net = h.Network(fluid=fluid)
    m = 1.0 / 3600 * fluid.rho
    q_last = 6.0
    net.add(h.HeatPump("wp", mode="target_t_out", t_out_set_C=60, q_max_kW=q_last, q_nom_m3h=1.0))
    net.add(h.Pump("pu", mode="constant_flow", q_m3h=1.0))
    net.add(h.Radiator("hk", q_prescribed_kW=q_last, kv_m3h=100))
    net.add(h.BufferStorage("pf", n_ports=2, ua_W_K=1e-6, t_amb_C=60 - q_last * 1e3 / (m * fluid.cp)))
    net.connect("wp.out", "pu.in"); net.connect("pu.out", "hk.in")
    net.connect("hk.out", "pf.p1"); net.connect("pf.p2", "wp.in")
    r = net.solve()
    assert r.converged and r.iterations_thermal <= 30
    assert r["wp"].t_out_C == pytest.approx(60.0, abs=1e-4)
    assert r["wp"].q_dot_kW == pytest.approx(q_last, abs=1e-4)


def test_kaeltemaschine_mit_greybox_kuehlregister_im_regelkreis():
    """Kältemaschine (Solltemperatur, q_max) + Greybox-Kühlregister (trocken/
    nass-Umschaltung) + Bypass: nichtlinear mit Knicken, muss Fixpunkt sein."""
    net = h.Network(fluid=h.water_at(10))
    net.add(h.Chiller("km", mode="target_t_out", t_out_set_C=6.0, q_max_kW=40, q_nom_m3h=5))
    net.add(h.Pump("pu", mode="constant_flow", q_m3h=5.0))
    net.add(h.CoolingCoil("kr", ua_ref_W_K=6000, ua_star_wet_kg_s=3.0, rh_air_in=0.6,
                          m_dot_air_kg_s=4.0, t_air_in_C=32.0, q_w_ref_m3h=5.0, kv_m3h=8))
    net.add(h.BalancingValve("byp", kvs_m3h=3.0, opening=0.5))
    net.connect("km.out", "pu.in"); net.connect("pu.out", "kr.in", "byp.in")
    net.connect("kr.out", "byp.out", "km.in")
    comp, hyd, th = _solve_parts(net)
    assert th.converged and th.iterations <= 30
    assert fixed_point_defect(comp, hyd, th) < 1e-5
    ref = picard_reference(comp, hyd)
    assert np.max(np.abs(th.t_node - ref)) < 1e-5
    r = net.solve()
    assert r["kr"].extras["betrieb"] in ("nass", "trocken")
    assert abs(r.energy_imbalance_W) < 1e-3


def test_breites_parallelnetz_konvergiert_schnell():
    """150 Heizkörperstränge parallel: dünne Jacobi-Matrix mit vielen Kanten,
    wenige Schritte, exakter Fixpunkt, Bilanz geschlossen."""
    n = 150
    net = h.Network()
    net.add(h.HeatPump("wp", mode="target_t_out", t_out_set_C=65, q_max_kW=2000, q_nom_m3h=40))
    net.add(h.Pump("pu", mode="constant_flow", q_m3h=n * 0.25))
    net.add(h.Manifold("vt", n_ports=24)); net.add(h.Manifold("sm", n_ports=24))
    net.connect("wp.out", "pu.in"); net.connect("pu.out", "vt.main"); net.connect("sm.main", "wp.in")
    for k in range(n):
        net.add(h.ControlValve(f"rv{k}", kvs_m3h=1.0, opening=0.3 + 0.7 * (k % 7) / 6))
        net.add(h.Radiator(f"hk{k}", q_nom_kW=1 + (k % 5), t_sup_nom_C=65, t_ret_nom_C=50,
                           t_room_C=18 + (k % 4)))
        net.connect(f"vt.s{k % 24 + 1}", f"rv{k}.in"); net.connect(f"rv{k}.out", f"hk{k}.in")
        net.connect(f"hk{k}.out", f"sm.s{k % 24 + 1}")
    comp, hyd, th = _solve_parts(net)
    assert th.converged and th.iterations <= 10
    assert fixed_point_defect(comp, hyd, th) < 1e-5
    assert abs(th.energy_imbalance) < 1e-2


def test_iterationslimit_meldet_sauber():
    with pytest.raises(h.ConvergenceError) as exc:
        _hk_loop().solve(h.SolverSettings(max_iter_thermal=1))
    assert "nicht konvergiert nach 1 Iterationen" in str(exc.value)
    assert _hk_loop().solve().converged                      # Default: kein Problem


def test_steigungen_aller_modelle_nicht_expansiv():
    """Die Globalisierung setzt 0 ≤ ∂T_aus/∂T_ein ≤ 1 voraus (docs/numerik.md).
    Geprüft an allen durchströmten Kanten der Zufallsnetze am Lösungspunkt."""
    for seed in range(20):
        comp, hyd, th = _solve_parts(build_random_network(seed))
        for e in comp.edges:
            m = abs(hyd.q[e.index]) * comp.fluid.rho
            if m < 1e-7 or e.thermal_fn is None:
                continue
            t_in = float(th.t_node[e.node_from if hyd.q[e.index] >= 0 else e.node_to])
            hstep = 1e-3 * max(1.0, abs(t_in))
            s = (e.thermal_fn(t_in + hstep, m, comp.fluid).t_out
                 - e.thermal_fn(t_in - hstep, m, comp.fluid).t_out) / (2 * hstep)
            assert -1e-6 <= s <= 1 + 1e-6, (seed, e.name, s)
