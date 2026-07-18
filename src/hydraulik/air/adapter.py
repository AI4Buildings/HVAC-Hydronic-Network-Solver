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
from .vka import moist_air as ma
from .vka import simulate, simulate_room

#: Zeichentyp → order-Token des Rechenkerns (deklarative Typen fehlen bewusst)
TOKEN = {"ventilator_luft": "Vent_ZUL", "frostschutz": "FS", "wrg": "WRG",
         "umluft": "UML_Byp", "vorheizer": "VHR", "befeuchter": "Bef",
         "kuehler": "KR", "nachheizer": "NHR"}


def _walk(plant: AirPlant, start_ref: str, end_type: str, via: dict) -> list:
    """Kette ab start_ref ablaufen; liefert (Komponente, Eintrittsport-Ref)
    in Strangreihenfolge."""
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
        chain.append((comp, nxt))
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


def _f0(out: dict, key: str):
    """Erstes Element einer Kern-Ausgabe als float (None bei NaN/fehlend)."""
    v = out.get(key)
    if v is None:
        return None
    x = float(v[0]) if hasattr(v, "__len__") else float(v)
    return None if math.isnan(x) else x


def _station_map(plant: AirPlant, zul_pairs: list, abl_pairs: list, cfg: dict,
                 out: dict, aul, abl_t: float, abl_rh: float, uml,
                 fan_moved: bool, v_sup: float, v_exh: float,
                 t_set_mean: float) -> dict:
    """Luftzustand (ϑ, x, φ, V̇) je Kanalabschnitt beider Stränge.

    Geschlüsselt über BEIDE Port-Refs jedes Abschnitts ('comp.port'), damit
    Leitungs- und Sensor-Tooltips im Editor direkt nachschlagen können.
    Quelle sind die Kettenzustände des Rechenkerns (Zustand nach jedem
    order-Token); der Fortluftzustand folgt aus der WRG-Bilanz (Wärme- und
    Feuchteübertrag an die Zuluft)."""
    chain = out.get("chain") or [{}]
    ch_t = chain[0].get("T") or {}
    ch_x = chain[0].get("x_gkg") or {}
    if not ch_t:
        return {}
    p = 1e5
    peer: dict[str, str] = {}
    for a, b in plant.connections:
        peer[a] = b
        peer[b] = a
    stations: dict[str, dict] = {}

    def put(ref: str, t, x_gkg: float, v: float) -> None:
        if t is None or math.isnan(t):
            return
        rec = {"t_C": round(float(t), 3), "x_gkg": round(float(x_gkg), 4),
               "phi_pct": round(float(ma.phi(float(t), float(x_gkg) / 1000.0, p))
                               * 100.0, 2),
               "v_m3h": round(v, 3)}
        stations[ref] = rec
        if ref in peer:
            stations[peer[ref]] = rec

    v_uml = (uml.v * 3600.0) if uml is not None else 0.0
    order = cfg.get("order") or []

    # Zuluftstrang: Zustand fortschreiben; das gezeichnete Token liefert den
    # Zustand NACH der Komponente. Ein an den Stranganfang normierter
    # Ventilator (fan_moved) ändert den angezeigten Zustand nicht — seine
    # Wärme steckt modellbedingt bereits in den stromab liegenden Zuständen.
    x_aul = float(ma.rh_to_x(aul.t, aul.rh, p))
    t_cur, x_cur = aul.t, x_aul
    uml_seen = False
    for comp, in_ref in zul_pairs:
        v_seg = v_sup - (0.0 if (uml_seen or uml is None) else v_uml)
        put(in_ref, t_cur, x_cur, v_seg)
        if comp.type_name == "umluft":
            uml_seen = True
        tok = TOKEN.get(comp.type_name)
        if tok and tok in ch_t and not (tok == "Vent_ZUL" and fan_moved):
            t_cur, x_cur = float(ch_t[tok]), float(ch_x[tok])

    # Abluftstrang: Raumzustand bis zur WRG (nach gezeichnetem Abluftventilator
    # der WRG-Eintrittszustand des Kerns), danach Fortluft aus der Bilanz.
    rho = p / (287.0 * (273.0 + t_set_mean))
    v_ex = (v_exh - v_uml) if uml is not None else v_exh
    x_abl = float(ma.rh_to_x(abl_t, abl_rh, p))
    t_eta = _f0(out, "T_eta_wheel_C")
    x_eta = _f0(out, "x_eta_wheel_gkg")
    t_cur, x_cur = abl_t, x_abl
    for comp, in_ref in abl_pairs:
        put(in_ref, t_cur, x_cur, v_ex)
        if comp.type_name == "ventilator_luft" and t_eta is not None:
            t_cur, x_cur = t_eta, x_eta
        elif comp.type_name == "wrg":
            q_wrg = _f0(out, "Q_recovery_WRG_kW") or 0.0
            base_t = t_eta if t_eta is not None else t_cur
            base_x = x_eta if x_eta is not None else x_cur
            m_ex = v_ex / 3600.0 * rho
            uml_vor_wrg = ("UML_Byp" in order and "WRG" in order
                           and order.index("UML_Byp") < order.index("WRG"))
            v_sup_w = v_sup - (0.0 if (uml is None or uml_vor_wrg) else v_uml)
            m_sup_w = v_sup_w / 3600.0 * rho
            x_vor_wrg = x_aul
            if "WRG" in order:
                for tokp in reversed(order[:order.index("WRG")]):
                    if tokp in ch_x:
                        x_vor_wrg = float(ch_x[tokp])
                        break
            if m_ex > 0:
                dx_sup = float(ch_x.get("WRG", x_vor_wrg)) - x_vor_wrg
                x_fol = base_x - dx_sup * m_sup_w / m_ex
                h_fol = float(ma.h(base_t, base_x / 1000.0)) - q_wrg / m_ex
                t_cur = float(ma.T_from_hx(h_fol, x_fol / 1000.0))
                x_cur = x_fol
    return stations


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
    zul_pairs = _walk(plant, f"{aul.name}.out", "zuluft", via)
    abl_pairs = _walk(plant, f"{abl.name}.out", "fortluft", via)
    zul_chain = [c for c, _ in zul_pairs]
    abl_chain = [c for c, _ in abl_pairs]
    if wrg not in zul_chain or wrg not in abl_chain:
        raise NetworkValidationError(
            ["Die WRG muss in BEIDEN Strängen liegen (zul_in/zul_out und abl_in/abl_out)."])

    # Anlagenkonfiguration aus dem Zuluftstrang (Reihenfolge = Zeichnung).
    # WICHTIG: Der Rechenkern ist für die Ventilatorposition am STRANGANFANG
    # validiert (Energy-Rotorregelung findet sonst u.U. keine zulässige
    # Drehzahl und fällt auf n_rot = 0 zurück). Die Ventilatorwärme wird
    # daher immer am Stranganfang bilanziert; die gezeichnete Reihenfolge
    # der Konditionierungskomponenten bleibt erhalten.
    tokens = [TOKEN[c.type_name] for c in zul_chain if c.type_name in TOKEN]
    order = ((["Vent_ZUL"] if "Vent_ZUL" in tokens else [])
             + [t for t in tokens if t != "Vent_ZUL"])
    fan_moved = "Vent_ZUL" in tokens and tokens[0] != "Vent_ZUL"
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
    if fan_moved:
        hinweise.append("Ventilatorwärme wird am Stranganfang bilanziert "
                        "(Modellkonvention des Rechenkerns); die gezeichnete "
                        "Ventilatorposition ist dokumentarisch.")
    if zul.regelung == "raum":
        t_set_mean = 0.5 * (zul.t_min + zul.t_max)
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
        t_set_mean = 0.5 * (t_lo + t_hi)
        out = simulate(cfg, aul.t, aul.rh, abl.t, abl.rh,
                       t_lo, t_hi, rh_lo, rh_hi,
                       V_sup_m3h=v_sup, V_exh_m3h=v_exh)

    def f(key, idx=0):
        v = out.get(key)
        if v is None:
            return None
        x = float(v[idx]) if hasattr(v, "__len__") else float(v)
        return None if math.isnan(x) else round(x, 6)

    # Kettenzustände für die Stationskarte: simulate() liefert sie direkt;
    # beim raumgekoppelten Fall wird der gefundene Betriebspunkt dafür einmal
    # gepinnt nachgerechnet (Zuluft-ϑ/x und Raumfeuchte des Optimums).
    chain_out = out
    abl_rh_st = abl.rh
    if zul.regelung == "raum":
        t_s, x_s = f("T_sup_C"), f("x_sup_gkg")
        abl_rh_st = f("room_rh_pct")
        if None not in (t_s, x_s, abl_rh_st):
            chain_out = simulate(cfg, aul.t, aul.rh, abl.t, abl_rh_st,
                                 t_s, t_s, x_s, x_s, supply_band="x",
                                 V_sup_m3h=v_sup, V_exh_m3h=v_exh)
        else:
            chain_out = {}
    stationen = _station_map(plant, zul_pairs, abl_pairs, cfg, chain_out,
                             aul, abl.t, abl_rh_st if abl_rh_st is not None else 50.0,
                             uml, fan_moved, v_sup,
                             v_exh if v_exh is not None else v_sup, t_set_mean)

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
        "stationen": stationen,
        "hinweise": hinweise,
    }
    if zul.regelung == "raum" and "room_rh_pct" in out:
        payload["raum"] = {"rh_pct": f("room_rh_pct")}
    return payload
