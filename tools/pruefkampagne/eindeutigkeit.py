"""Eindeutigkeitsprüfung über alle Kampagnennetze mit nicht-monotonen Bauteilen.

    python3 tools/pruefkampagne/eindeutigkeit.py [START ANZAHL]   (Default 0 3300)

Rechnet jedes Netz mit Idelchik-T-Stück über den produktiven Einstieg
solve_hydraulics_checked (Neustart bei Nichtkonvergenz, Zusatzstarts) und
zählt gemeldete Mehrfachlösungen. Referenzstand 2026-10-06 (0 3300, T-Stück
mit Totaldruck, Kurzschlüsse auch über widerstandsfreie Verbindungen
abgelehnt): 193 gelöst, 26 mehrdeutig (13,5 %), 1 per Neustart, 203
Validierungsfehler; alle Lösungen dynamisch stabil (stabilitaet.py).
Schreibt eindeutigkeit_START_ANZAHL.json.
"""
from __future__ import annotations

import collections
import json
import sys
import warnings
from multiprocessing import Pool

import numpy as np

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from kampagne import random_net                                   # noqa: E402
from nachrechnung import check_hydraulics                         # noqa: E402
from hydraulik.exceptions import NetworkValidationError            # noqa: E402
from hydraulik.solver.settings import SolverSettings               # noqa: E402
from hydraulik.solver.uniqueness import solve_hydraulics_checked   # noqa: E402


def one(seed: int):
    warnings.filterwarnings("ignore")
    try:
        net = random_net(seed)
    except Exception:                       # Generatorfehler (vor dem Pool abfangen!)
        return None
    if not any(c.nonmonotone_hydraulics() for c in net.components.values()):
        return None
    try:
        c = net.compile()
    except NetworkValidationError:
        return dict(seed=seed, status="validierung")
    except Exception:
        return dict(seed=seed, status="generator")
    try:
        hyd = solve_hydraulics_checked(c, SolverSettings())
    except Exception as exc:
        return dict(seed=seed, status="nicht konvergiert", msg=str(exc)[:120])
    return dict(seed=seed, status="falsch" if check_hydraulics(c, hyd) else "ok",
                it=hyd.iterations, neustart=hyd.restarted, n=len(hyd.alternatives),
                dq_m3h=[a.dq_max * 3600 for a in hyd.alternatives])


if __name__ == "__main__":
    start, count = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else (0, 3300)
    with Pool(8) as p:
        res = [r for r in p.map(one, range(start, start + count)) if r]
    json.dump(res, open(f"eindeutigkeit_{start}_{count}.json", "w"), indent=1)
    print("Status:", dict(collections.Counter(r["status"] for r in res)))
    ok = [r for r in res if r["status"] == "ok"]
    if ok:
        fl = [r for r in ok if r["n"]]
        print(f"gelöst {len(ok)}, mehrdeutig gemeldet {len(fl)} ({100 * len(fl) / len(ok):.1f} %), "
              f"per Neustart {sum(r['neustart'] for r in ok)}")
        its = [r["it"] for r in ok]
        print("Iterationen Median/95%/max:", int(np.median(its)), int(np.percentile(its, 95)), max(its))
    for r in res:
        if r["status"] in ("nicht konvergiert", "falsch"):
            print("  ", r)
