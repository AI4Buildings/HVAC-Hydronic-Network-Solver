"""Mehrfachlösungen sammeln und ihre dynamische Stabilität beurteilen.

    python3 tools/pruefkampagne/stabilitaet.py SEED [SEED …]
    python3 tools/pruefkampagne/stabilitaet.py --datei seeds.json

Je Kampagnennetz (Seed aus kampagne.py):
1. Hydraulik von 17 Startwerten lösen (Standard, q_init 1e-5…1e-2 m³/s,
   12 zufällige Vektoren), jede Lösung unabhängig nachrechnen und mit
   10⁵-fach engerer Toleranz NACHSCHÄRFEN — erst dann zusammenfassen
   (verschieden, wenn max|ΔQ| > max(1e-5·V̇max, 1e-9 m³/s)). Ohne Nach-
   schärfen erscheinen Toleranzreste schwach angetriebener Maschen als
   „Lösungen".
2. Stabilität der linearisierten Maschendynamik L·dQ/dt = Δp_Knoten − G(Q),
   A_u·Q = 0. Mit Q = Z·ξ (Z = Kern der Kontinuität auf den freien Kanten):
   (ZᵀLZ)·ξ' = −M·ξ, M = Zᵀ·J·Z, J = ∂G/∂Q als Richtungsableitungen entlang
   Z (volle Kopplung, z.B. der T-Stück-Schenkel). Urteile:
     stabil      sym(M) positiv definit → für JEDE Trägheitsverteilung (Lyapunov)
     instabil    det(M) < 0 → für JEDE Trägheitsverteilung
     neutral     Nullmode (antriebslose Masche)
     … (L-abh. geprüft) / trägheitsabhängig: Test mit zufälligen Trägheiten
Validierung des Kriteriums: S-Kennlinie mit drei Gleichgewichten, das
mittlere wird als instabil erkannt (siehe Stand 2026-10-06 im Prüfbericht).
"""
from __future__ import annotations

import json
import sys
import warnings

import numpy as np
from scipy.linalg import eig, eigh, null_space

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from kampagne import random_net                                   # noqa: E402
from nachrechnung import check_hydraulics                         # noqa: E402
from hydraulik.solver.hydraulic import solve_hydraulics            # noqa: E402
from hydraulik.solver.settings import SolverSettings               # noqa: E402

TOL_REL = 1e-9
TIGHT = dict(max_iter=4000, tol_mass_rel=1e-13, tol_mom_rel=1e-11)


def edge_G(net, q):
    """Kantenkennlinie G_e(Q) = a·Q + b·Q|Q| − dp_source für alle Kanten."""
    fluid = net.fluid
    groups = {}
    for e in net.edges:
        if hasattr(e.component, "pre_coefficients"):
            groups.setdefault(id(e.component), (e.component, []))[1].append(e.index)
    for comp, idxs in groups.values():
        comp.pre_coefficients([float(q[i]) for i in idxs], fluid)
    G = np.empty(len(q))
    for e in net.edges:
        qe = float(q[e.index])
        c = e.coeff_fn(qe, fluid)
        G[e.index] = c.a * qe + c.b * qe * abs(qe) - c.dp_source
    return G


def loop_basis(net, free):
    pinned = np.array([nd.pinned for nd in net.nodes])
    B = np.zeros((len(net.nodes), len(net.edges)))
    for e in net.edges:
        B[e.node_from, e.index] -= 1.0
        B[e.node_to, e.index] += 1.0
    Bu = B[~pinned][:, free]
    Zf = null_space(Bu) if Bu.size else np.eye(int(free.sum()))
    Z = np.zeros((len(net.edges), Zf.shape[1]))
    Z[free] = Zf
    return Z


def stability(net, q, rng) -> dict:
    free = np.array([not e.is_fixed for e in net.edges])
    Z = loop_basis(net, free)
    k = Z.shape[1]
    if k == 0:
        return dict(verdict="keine Maschen", k=0)
    h = 1e-6 * max(float(np.max(np.abs(q))), 1e-6)
    JZ = np.empty((len(q), k))
    for j in range(k):
        JZ[:, j] = (edge_G(net, q + h * Z[:, j]) - edge_G(net, q - h * Z[:, j])) / (2 * h)
    edge_G(net, q)                                   # Komponentenzustand zurücksetzen
    M = Z.T @ JZ
    d = np.abs(np.diag(M))
    D = np.diag(1.0 / np.sqrt(np.maximum(d, 1e-300 + 1e-12 * max(d.max(), 1e-300))))
    Ms = D @ M @ D                                   # Kongruenz: Definitheit/det-Vorzeichen bleiben
    ev = eigh(0.5 * (Ms + Ms.T), eigvals_only=True)
    scale = max(np.max(np.abs(ev)), 1e-300)
    sign, _ = np.linalg.slogdet(Ms)
    asym = float(np.max(np.abs(Ms - Ms.T)) / max(np.max(np.abs(Ms)), 1e-300))
    if ev.min() > TOL_REL * scale:
        verdict = "stabil"
    elif sign < 0:
        verdict = "instabil"
    elif ev.min() > -TOL_REL * scale and asym < 1e-9:
        verdict = "neutral"
    else:
        res = []
        for _ in range(20):
            L = np.diag(10 ** rng.uniform(-1, 1, len(q)))
            lam = eig(Ms, D @ (Z.T @ L @ Z) @ D, right=False)
            lam = lam[np.isfinite(lam)]
            res.append(bool(np.all(lam.real > -TOL_REL * np.max(np.abs(lam)))))
        verdict = ("stabil (L-abh. geprüft)" if all(res)
                   else "instabil (L-abh. geprüft)" if not any(res) else "trägheitsabhängig")
    return dict(verdict=verdict, k=k)


def _starts(m, rng, n_rand=12):
    yield "Standard", None
    for qi in (1e-5, 1e-4, 1e-3, 1e-2):
        yield f"q_init={qi:g}", dict(q_init=qi)
    for r in range(n_rand):
        yield f"zufällig{r}", dict(seeds=rng.choice([-1.0, 1.0], m) * 10 ** rng.uniform(-5, -2, m))


def collect(seed: int) -> list[dict]:
    rng = np.random.default_rng(seed)
    m = len(random_net(seed).compile().edges)
    sols = []
    for tag, opt in _starts(m, rng):
        c = random_net(seed).compile()
        if opt and "seeds" in opt:
            for e, v in zip(c.edges, opt["seeds"]):
                e.q_seed = float(v)
        if opt and "q_init" in opt:
            for e in c.edges:
                e.q_seed = None
        try:
            hyd = solve_hydraulics(c, SolverSettings(max_iter=1500, **(
                {"q_init": opt["q_init"]} if opt and "q_init" in opt else {})))
        except Exception:
            continue
        if check_hydraulics(c, hyd):
            continue
        cp = random_net(seed).compile()                 # nachschärfen
        for e, v in zip(cp.edges, hyd.q):
            e.q_seed = float(v) if abs(v) > 1e-15 else 1e-15
        try:
            hyd = solve_hydraulics(cp, SolverSettings(**TIGHT))
        except Exception:
            continue
        q = hyd.q.copy()
        qmax = float(np.max(np.abs(q)))
        for S in sols:
            if np.max(np.abs(S["q"] - q)) <= max(1e-5 * qmax, 1e-9):
                S["starts"].append(tag)
                break
        else:
            sols.append(dict(q=q, starts=[tag], **stability(cp, q, rng)))
    return sols


if __name__ == "__main__":
    warnings.filterwarnings("ignore")
    if sys.argv[1:2] == ["--datei"]:
        seeds = json.load(open(sys.argv[2]))
    else:
        seeds = [int(x) for x in sys.argv[1:]]
    for sd in seeds:
        sols = collect(sd)
        ref = next((S for S in sols if "Standard" in S["starts"]), sols[0] if sols else None)
        print(f"Seed {sd}: {len(sols)} Lösung(en)")
        for S in sols:
            dq = float(np.max(np.abs(S["q"] - ref["q"]))) * 3600 if ref else 0.0
            print(f"   {S['verdict']:24} Starts {len(S['starts']):2d}  ΔV̇ zur Standardlösung "
                  f"{dq:.4g} m³/h" + ("  (Standard)" if "Standard" in S["starts"] else ""))
