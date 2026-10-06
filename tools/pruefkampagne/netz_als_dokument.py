"""Kampagnennetz → lesbares JSON-Dokument (für Regressionstests).

    python3 tools/pruefkampagne/netz_als_dokument.py SEED > netz.json

Parameter werden auf 4 signifikante Stellen gerundet und in übliche Einheiten
(m³/h, kPa, kW, mm) gebracht; Widerstandsbeiwerte (a_…, c_…) bleiben in SI.
Defaultwerte entfallen. Achtung: Betriebsarten, die sich aus den angegebenen
Parametern ergeben (z.B. conduit ideal/C-Wert/Rohrmodell), können sich beim
Rückschreiben ändern — das Verhalten des Dokuments daher immer nachprüfen
(z.B. Mehrdeutigkeit bzw. Konvergenz wie beim Original).
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from kampagne import random_net                 # noqa: E402
from hydraulik.params import UNIT_GROUPS         # noqa: E402


def _si_key(spec):
    if spec.group in ("none", "str", "int", "bool"):
        return spec.name
    sfx = next(k for k, f in UNIT_GROUPS[spec.group].items() if f == 1.0)
    return f"{spec.name}_{sfx}"


def _rnd(v):
    return float(f"{v:.4g}") if isinstance(v, float) and v != 0 else v


def to_doc(net) -> dict:
    comps = {}
    for name, c in net.components.items():
        d = {"type": c.type_name}
        for spec in c.PARAMS:
            v = getattr(c, spec.name, None)
            if v is None or (spec.default is not None and v == spec.default):
                continue
            k, v = _si_key(spec), _rnd(v)
            if k.startswith(("a_", "c_")):
                d[k] = v
            elif k.endswith("_m3s"):
                d[k[:-4] + "_m3h"] = _rnd(v * 3600)
            elif k.endswith("_Pa"):
                d[k[:-3] + "_kPa"] = _rnd(v / 1e3)
            elif k.endswith("_W") and k.startswith("q"):
                d[k[:-2] + "_kW"] = _rnd(v / 1e3)
            elif k.startswith("d_") and k.endswith("_m"):
                d[k[:-2] + "_mm"] = _rnd(v * 1e3)
            else:
                d[k] = v
        comps[name] = d
    f = net.fluid
    return {"fluid": {"name": f.name, "rho": _rnd(f.rho), "mu": _rnd(f.mu), "cp": _rnd(f.cp)},
            "components": comps, "connections": [list(c) for c in net.connections]}


if __name__ == "__main__":
    print(json.dumps(to_doc(random_net(int(sys.argv[1]))), ensure_ascii=False, indent=1))
