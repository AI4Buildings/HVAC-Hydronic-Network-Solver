"""Adapter: gezeichnetes Lüftungsschema → VKA-Rechenkern (simulate/simulate_room).

Ablauf: Stränge aus den Portverbindungen ablaufen (Zuluft: aussenluft → … →
zuluft; Abluft: abluft_raum → … → fortluft; WRG koppelt beide), daraus die
plant-Konfiguration inkl. expliziter Komponentenreihenfolge (order-Tokens in
Zeichenreihenfolge) und die Sollband-Argumente je Regelungsart ableiten,
rechnen und die Ergebnisse den gezeichneten Komponenten zuordnen.
"""
from __future__ import annotations

import math

from ..exceptions import NetworkValidationError
from .loader import AirPlant, load_air
from .vka import simulate, simulate_room

#: Zeichentyp → order-Token des Rechenkerns (deklarative Typen fehlen bewusst)
TOKEN = {"ventilator_luft": "Vent_ZUL", "frostschutz": "FS", "wrg": "WRG",
         "umluft": "UML_Byp", "vorheizer": "VHR", "befeuchter": "Bef",
         "kuehler": "KR", "nachheizer": "NHR"}


def _walk(plant: AirPlant, start_ref: str, end_type: str, via: dict) -> list:
    """Kette ab start_ref ablaufen; liefert Komponenten in Strangreihenfolge."""
    peer = {}
    for a, b in plant.connections:
        peer[a] = b
        peer[b] = a
    chain, ref, seen = [], start_ref, set()
    while True:
        if ref not in peer:
            raise NetworkValidationError(
                [f"Strang unterbrochen: Port '{ref}' ist nicht verbunden."])
        nxt = peer[ref]
        cname = nxt.split(".", 1)[0]
        comp = plant.components[cname]
        if cname in seen:
            raise NetworkValidationError(
                [f"Strang läuft im Kreis (Komponente '{cname}' doppelt erreicht)."])
        seen.add(cname)
        chain.append(comp)
        if comp.type_name == end_type:
            return chain
        outs = via.get(comp.type_name)
        if outs is not None:
            out_port = outs.get(nxt.split(".", 1)[1])
            if out_port is None:
                raise NetworkValidationError(
                    [f"'{nxt}': falscher Eintrittsport für den Strang."])
        else:
            out_port = "out"
        ref = f"{cname}.{out_port}"


def _one(plant: AirPlant, type_name: str, required: bool = False):
    found = [c for c in plant.components.values() if c.type_name == type_name]
    if len(found) > 1:
        raise NetworkValidationError(
            [f"Mehr als eine Komponente vom Typ '{type_name}' — genau eine erwartet."])
    if required and not found:
        raise NetworkValidationError(
            [f"Pflichtkomponente '{type_name}' fehlt im Schema."])
    return found[0] if found else None


def solve_air(source) -> dict:
    plant = source if isinstance(source, AirPlant) else load_air(source)
    aul = _one(plant, "aussenluft", required=True)
    abl = _one(plant, "abluft_raum", required=True)
    zul = _one(plant, "zuluft", required=True)
    fol = _one(plant, "fortluft", required=True)
    wrg = _one(plant, "wrg", required=True)

    via = {"wrg": {"zul_in": "zul_out", "abl_in": "abl_out"}}
    zul_chain = _walk(plant, f"{aul.name}.out", "zuluft", via)
    abl_chain = _walk(plant, f"{abl.name}.out", "fortluft", via)
    if wrg not in zul_chain or wrg not in abl_chain:
        raise NetworkValidationError(
            ["Die WRG muss in BEIDEN Strängen liegen (zul_in/zul_out und abl_in/abl_out)."])

    # Anlagenkonfiguration aus dem Zuluftstrang (Reihenfolge = Zeichnung)
    order = [TOKEN[c.type_name] for c in zul_chain if c.type_name in TOKEN]
    frost = _one(plant, "frostschutz")
    bef = _one(plant, "befeuchter")
    uml = _one(plant, "umluft")
    fans = [c for c in plant.components.values() if c.type_name == "ventilator_luft"]
    present = {c.type_name for c in zul_chain}
    cfg = {
        "wrg": wrg.typ,
        "components": [tok for name, tok in (("vorheizer", "VHR"), ("kuehler", "KR"),
                                             ("nachheizer", "NHR")) if name in present],
        "humidifier": bef.typ if bef is not None else "none",
        "frost": frost.modus if frost is not None else "none",
        "V_nom_m3h": wrg.v_nom * 3600.0,
        "adiab_exhaust": bool(wrg.adiab_exhaust),
    }
    if frost is not None:
        cfg["T_FS"] = frost.t_fs
    if wrg.eta_hr_n:
        cfg["eta_hr_N"] = wrg.eta_hr_n
    if wrg.eta_xr_n:
        cfg["eta_xr_N"] = wrg.eta_xr_n
    if getattr(wrg, "rwz_n", None):
        cfg["RWZ_N"] = wrg.rwz_n
    if uml is not None:
        cfg["recirculation_m3h"] = uml.v * 3600.0
    if fans:
        cfg["SFP"] = sum(f.sfp for f in fans)
    if order:
        cfg["order"] = order

    v_sup = zul.v * 3600.0
    v_exh = fol.v * 3600.0 if fol.v is not None else None

    hinweise: list[str] = []
    if zul.regelung == "raum":
        out = simulate_room(cfg, aul.t, aul.rh, T_room=abl.t,
                            room_rh_min=zul.raum_rh_min, room_rh_max=zul.raum_rh_max,
                            T_sup_min=zul.t_min, T_sup_max=zul.t_max,
                            moisture_g_h=zul.feuchtelast, V_sup_m3h=v_sup)
    else:
        if abl.rh is None:
            raise NetworkValidationError(
                ["abluft_raum: 'rh' (rel. Feuchte) fehlt — bei Regelung "
                 "'fest'/'band' ist die Abluftfeuchte Pflicht."])
        if zul.regelung == "fest":
            t_lo = t_hi = zul.t
            rh_lo = rh_hi = zul.rh
        else:
            t_lo, t_hi, rh_lo, rh_hi = zul.t_min, zul.t_max, zul.rh_min, zul.rh_max
        out = simulate(cfg, aul.t, aul.rh, abl.t, abl.rh,
                       t_lo, t_hi, rh_lo, rh_hi,
                       V_sup_m3h=v_sup, V_exh_m3h=v_exh)

    def f(key, idx=0):
        v = out.get(key)
        if v is None:
            return None
        x = float(v[idx]) if hasattr(v, "__len__") else float(v)
        return None if math.isnan(x) else round(x, 6)

    # Ergebnisse je gezeichneter Komponente (für Tooltips/Bericht)
    per_comp: dict[str, dict] = {}
    fan_p_total = (cfg.get("SFP", 0.0) * v_sup / 3600.0) / 1e3   # kW, ZUL+ABL gesamt
    for c in plant.components.values():
        r: dict = {}
        t = c.type_name
        if t == "vorheizer":
            r["q_heizen_kW"] = f("Q_heat_VHR_kW")
        elif t == "nachheizer":
            r["q_heizen_kW"] = f("Q_heat_NHR_kW")
        elif t == "frostschutz":
            r["q_heizen_kW"] = f("Q_heat_FS_kW")
        elif t == "kuehler":
            r["q_kuehlen_kW"] = f("Q_cool_KR_kW")
        elif t == "befeuchter":
            r["q_befeuchter_kW"] = f("Q_humid_Bef_kW")
            r["wasser_kg_h"] = f("water_kg_h")
        elif t == "wrg":
            r["q_wrg_kW"] = f("Q_recovery_WRG_kW")
            r["eta_hr"] = f("eta_hr")
            r["eta_xr"] = f("eta_xr")
            r["n_rot"] = f("n_rot")
        elif t == "ventilator_luft" and fans:
            r["p_el_kW"] = round(fan_p_total * c.sfp / sum(x.sfp for x in fans), 6)
        elif t == "zuluft":
            r.update({"t_C": f("T_sup_C"), "x_gkg": f("x_sup_gkg"),
                      "phi_pct": f("phi_sup_pct"), "v_m3h": round(v_sup, 3)})
        if r:
            per_comp[c.name] = r

    payload = {
        "ok": True,
        "regelung": zul.regelung,
        "plant": cfg,
        "zuluft": per_comp.get(zul.name, {}),
        "leistungen": {
            "heizen_gesamt_kW": f("Q_heat_total_kW"),
            "kuehlen_kW": f("Q_cool_KR_kW"),
            "befeuchter_kW": f("Q_humid_Bef_kW"),
            "befeuchter_wasser_kg_h": f("water_kg_h"),
            "wrg_kW": f("Q_recovery_WRG_kW"),
            "ventilatoren_el_kW": round(fan_p_total, 6) if fans else None,
        },
        "komponenten": per_comp,
        "hinweise": hinweise,
    }
    if zul.regelung == "raum" and "room_rh_pct" in out:
        payload["raum"] = {"rh_pct": f("room_rh_pct")}
    return payload
