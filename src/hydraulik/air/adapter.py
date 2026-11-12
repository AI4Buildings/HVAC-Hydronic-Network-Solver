"""Adapter: gezeichnetes Lüftungsschema → VKA-Rechenkern (simulate/simulate_room).

Ablauf: Stränge aus den Portverbindungen ablaufen (Zuluft: aussenluft → … →
zuluft; Abluft: abluft_raum → … → fortluft; WRG koppelt beide), daraus die
plant-Konfiguration inkl. expliziter Komponentenreihenfolge (order-Tokens in
Zeichenreihenfolge) und die Sollband-Argumente je Regelungsart ableiten,
rechnen und die Ergebnisse den gezeichneten Komponenten zuordnen.

Gerechnet wird nur, was in einem der beiden Stränge liegt: nicht
angeschlossene Komponenten beeinflussen das Ergebnis nicht und werden als
Hinweis gemeldet. Je Zuluftstrang bildet der Kern jeden Konditionierungstyp
genau einmal ab (mehrere Ventilatoren sind zulässig, ihre SFP addieren sich).
"""
from __future__ import annotations

import math
import warnings

from ..exceptions import NetworkValidationError
from .components import AIR_SENSOR_TYPES
from .loader import AirPlant, load_air, split_ref
from .vka import moist_air as ma
from .vka import simulate, simulate_room
from .vka.kvs import min_medium_flow

#: Zeichentyp → order-Token des Rechenkerns (deklarative Typen fehlen bewusst)
TOKEN = {"ventilator_luft": "Vent_ZUL", "frostschutz": "FS", "wrg": "WRG",
         "umluft": "UML_Byp", "vorheizer": "VHR", "befeuchter": "Bef",
         "kuehler": "KR", "nachheizer": "NHR"}

#: Typen, die der Rechenkern je Zuluftstrang genau EINMAL bilanziert (Lasten
#: und Kettenzustände sind je Token abgelegt — eine zweite Komponente
#: desselben Typs überschriebe die erste: 0 kW statt 13 kW)
EINMAL = ("frostschutz", "vorheizer", "befeuchter", "kuehler", "nachheizer", "umluft")

STRANG = {"zul": "Zuluftstrang", "abl": "Abluftstrang"}

#: Toleranzen des Hinweises „Sollband nicht erreicht“ — oberhalb der
#: Regeltoleranzen des Kerns (Kühler 0,03 g/kg ≈ 0,2 % r.F.; Sprühbefeuchter-
#: Näherungen bis ~0,12 K bzw. ~1,2 % r.F.)
TOL_T_BAND = 0.2          # K
TOL_RH_BAND = 1.5         # %-Punkte
#: größter zulässiger Rest zwischen angesetzter Abluft- und resultierender
#: Raumfeuchte (raumgekoppelt), darüber Hinweis auf einen Regelungssprung
TOL_RH_REST = 0.5         # %-Punkte


def _walk(plant: AirPlant, start_ref: str, end_type: str, seite: str) -> list:
    """Kette ab start_ref ablaufen; liefert (Komponente, Eintrittsport-Ref)
    in Strangreihenfolge. seite = 'zul' | 'abl' bestimmt die WRG-Ports und
    die Meldungstexte; falsch herum angeschlossene Komponenten und an der WRG
    vertauschte Stränge werden als solche benannt."""
    strang = STRANG[seite]
    peer = {}
    for a, b in plant.connections:
        peer[a] = b
        peer[b] = a
    chain, ref, seen = [], start_ref, set()
    while True:
        if ref not in peer:
            raise NetworkValidationError(
                [f"{strang} unterbrochen: Port '{ref}' ist nicht verbunden."])
        nxt = peer[ref]
        cname, port = split_ref(nxt)
        comp = plant.components[cname]
        typ = comp.type_name
        if typ == "wrg":
            if port != f"{seite}_in":
                if port == f"{seite}_out":
                    raise NetworkValidationError(
                        [f"{strang}: die WRG '{cname}' ist verkehrt herum angeschlossen — "
                         f"der Strang trifft auf den Austritt '{nxt}'; Eintritt ist "
                         f"'{cname}.{seite}_in'."])
                andere = "Abluft" if seite == "zul" else "Zuluft"
                raise NetworkValidationError(
                    [f"{strang} läuft durch die {andere}seite der WRG ('{nxt}') — die "
                     f"Stränge sind an der WRG vertauscht; bitte '{cname}.{seite}_in' → "
                     f"'{cname}.{seite}_out' verwenden."])
            out_port = f"{seite}_out"
        elif typ == end_type:
            chain.append((comp, nxt))
            return chain
        elif typ in ("zuluft", "fortluft"):
            raise NetworkValidationError(
                [f"{strang} endet an '{cname}' ({typ}) statt an einer Komponente vom Typ "
                 f"'{end_type}' — Zuluft- und Abluftstrang sind falsch verbunden."])
        elif port != "in":
            if "in" in comp.port_names():
                raise NetworkValidationError(
                    [f"{strang}: '{cname}' ({typ}) ist verkehrt herum angeschlossen — der "
                     f"Strang trifft auf den Austritt '{nxt}'; Eintritt ist '{cname}.in'."])
            raise NetworkValidationError(
                [f"{strang} trifft auf '{nxt}' ({typ}) — eine Luftquelle kann nicht "
                 f"mitten im Strang liegen."])
        else:
            out_port = "out"
        if cname in seen:
            raise NetworkValidationError(
                [f"{strang} läuft im Kreis (Komponente '{cname}' doppelt erreicht)."])
        seen.add(cname)
        chain.append((comp, nxt))
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
                 fan_exakt, v_sup: float, v_exh: float,
                 rho: float) -> dict:
    """Luftzustand (ϑ, x, φ, V̇) je Kanalabschnitt beider Stränge.

    Geschlüsselt über BEIDE Port-Refs jedes Abschnitts ('comp.port'), damit
    Leitungs- und Sensor-Tooltips im Editor direkt nachschlagen können.
    Quelle sind die Kettenzustände des Rechenkerns (Zustand nach jedem
    order-Token) DESSELBEN Betriebspunkts wie die Lasten; der Fortluftzustand
    folgt aus der WRG-Bilanz (Wärme- und Feuchteübertrag an die Zuluft)."""
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
    order = list(cfg.get("order") or [])
    if v_uml > 0.0 and "UML_Byp" in order and "Vent_ZUL" in order:
        # wirksame Reihenfolge des Kerns bei Umluft (L19): der Ventilator
        # folgt der Mischkammer — sein Kettenzustand ist dann NICHT der
        # Zustand am Stranganfang
        order.remove("Vent_ZUL")
        order.insert(order.index("UML_Byp") + 1, "Vent_ZUL")

    # Zuluftstrang: Zustand fortschreiben; das gezeichnete Token liefert den
    # Zustand NACH der Komponente. Den Kernzustand „nach Ventilator“ trägt nur
    # der Ventilator, der dort gezeichnet ist, wo der Kern ihn bilanziert
    # (fan_exakt: ohne Umluft am Stranganfang, mit Umluft direkt nach der
    # Mischkammer — L19). Jeder andere gezeichnete Ventilator ändert den
    # angezeigten Zustand nicht — seine Wärme steckt modellbedingt bereits in
    # den stromab liegenden Zuständen. (Ein zweiter Ventilator setzte die
    # Station früher auf „nach dem ersten Ventilator“ zurück: Zuluftstation
    # −4 °C statt 20 °C.)
    x_aul = float(ma.rh_to_x(aul.t, aul.rh, p))
    t_cur, x_cur = aul.t, x_aul
    uml_seen = False
    mit_umluft = v_uml > 0.0
    for comp, in_ref in zul_pairs:
        v_seg = v_sup - (0.0 if (uml_seen or uml is None) else v_uml)
        put(in_ref, t_cur, x_cur, v_seg)
        if comp.type_name == "umluft":
            uml_seen = True
        tok = TOKEN.get(comp.type_name)
        if tok == "Vent_ZUL":
            if comp is fan_exakt and tok in ch_t:
                t_cur, x_cur = float(ch_t[tok]), float(ch_x[tok])
            continue
        if tok == "UML_Byp" and mit_umluft and fan_exakt is None:
            # Ventilator nicht direkt nach der Mischkammer gezeichnet: der
            # Abschnitt nach der Umluft trägt bereits den Kernzustand nach
            # Mischung UND Ventilator
            tok = "Vent_ZUL"
        if tok and tok in ch_t:
            t_cur, x_cur = float(ch_t[tok]), float(ch_x[tok])

    # Abluftstrang: Raumzustand bis zur WRG (nach gezeichnetem Abluftventilator
    # der WRG-Eintrittszustand des Kerns), danach Fortluft aus der Bilanz.
    v_ex = (v_exh - v_uml) if uml is not None else v_exh
    x_abl = float(ma.rh_to_x(abl_t, abl_rh, p))
    t_eta = _f0(out, "T_eta_wheel_C")
    x_eta = _f0(out, "x_eta_wheel_gkg")
    t_cur, x_cur = abl_t, x_abl
    wrg_seen = False
    for comp, in_ref in abl_pairs:
        put(in_ref, t_cur, x_cur, v_ex)
        if comp.type_name == "ventilator_luft" and t_eta is not None and not wrg_seen:
            # Abluftventilator VOR der WRG: Zustand = WRG-Eintritt des Kerns.
            # Danach (Fortluftseite) ändert er den angezeigten Zustand nicht —
            # früher sprang die Fortluftstation auf den Raumzustand zurück.
            t_cur, x_cur = t_eta, x_eta
        elif comp.type_name == "wrg":
            wrg_seen = True
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


def _genau_eine(plant: AirPlant, type_name: str, errors: list[str]):
    """Rand-/Koppelkomponente, die im ganzen Schema genau einmal vorkommt."""
    found = [c for c in plant.components.values() if c.type_name == type_name]
    if len(found) > 1:
        errors.append(f"Mehr als eine Komponente vom Typ '{type_name}' "
                      f"({', '.join(c.name for c in found)}) — genau eine erwartet.")
        return None
    if not found:
        errors.append(f"Pflichtkomponente '{type_name}' fehlt im Schema.")
        return None
    return found[0]


#: englische Kernmeldungen → deutsche Fehlermeldung (Teilstring-Treffer)
_KERN_FEHLER = (
    ("T_sup_min must be <= T_sup_max",
     "Zuluft-Temperaturband verkehrt herum (untere Grenze über der oberen)."),
    ("hum_sup_min must be <= hum_sup_max",
     "Zuluft-Feuchteband verkehrt herum (untere Grenze über der oberen)."),
    ("room_rh_min must be <= room_rh_max",
     "Raumfeuchteband verkehrt herum (untere Grenze über der oberen)."),
    ("room_rh_min/room_rh_max must be in",
     "Raumfeuchteband außerhalb 0…100 %."),
    ("must be in 0..100",
     "Feuchteangabe außerhalb 0…100 %."),
    ("phi_min/phi_max must be in",
     "Zuluft-Feuchteband außerhalb 0…100 %."),
    ("KVS design point infeasible",
     "KVS-Auslegung physikalisch nicht möglich: die Rückwärmzahl je Wärmetauscher "
     "verlangt ε·C_Luft < C_Sole — Sole-Nennvolumenstrom (v_m_kvs) erhöhen oder "
     "rwz_n senken."),
    ("KVS: non-finite",
     "KVS: Rechenfehler im Solestrom-Raster (nicht endliche Austrittstemperatur) — "
     "Auslegungsdaten der KVS prüfen."),
    ("must be >= 0",
     "Negativer Wert, wo nur Werte ≥ 0 zulässig sind."),
)

#: englische Kernwarnungen → deutscher Hinweis (Teilstring-Treffer)
_KERN_WARNUNG = (
    ("phi_min <",
     "Zuluft-Sollband: eine untere Feuchte unter 1 % ist nicht physikalisch — der "
     "Rechenkern rechnet mit 1 %."),
    ("reaches ~saturation",
     "Zuluft-Sollband reicht bis zur Sättigung (obere Feuchte ≥ 98 %): die "
     "energieoptimale Rotorregelung läuft dann langsamer und die Rückgewinnung sinkt "
     "(Verhalten des Referenzwerkzeugs). Für die Auslegung ein realistisches Band "
     "wählen (z.B. 40…60 %); zum Nachrechnen eines Messwerts die Feuchte fest vorgeben."),
)


def _kern_fehler(exc: Exception) -> str:
    text = str(exc)
    for teil, deutsch in _KERN_FEHLER:
        if teil in text:
            return f"Lüftungskern: {deutsch} (Kernmeldung: {text})"
    return f"Lüftungskern lehnt die Eingabe ab: {text}"


def _kern_hinweise(caught: list) -> list[str]:
    """Warnungen des Rechenkerns als deutsche Hinweise (ohne Dubletten)."""
    hinweise: list[str] = []
    for w in caught:
        text = str(w.message)
        if issubclass(w.category, RuntimeWarning):
            msg = (f"Numerische Warnung im Lüftungskern ({text}) — Ergebnis auf "
                   f"Plausibilität prüfen.")
        else:
            msg = next((de for teil, de in _KERN_WARNUNG if teil in text),
                       f"Hinweis des Lüftungskerns: {text}")
        if msg not in hinweise:
            hinweise.append(msg)
    return hinweise


def _run_kernel(cfg: dict, aul, abl, zul, v_sup: float, v_exh):
    """Rechenkern je Regelungsart aufrufen; liefert (Ausgabe, T_Soll_Mitte)."""
    if zul.regelung == "raum":
        out = simulate_room(cfg, aul.t, aul.rh, T_room=abl.t,
                            room_rh_min=zul.raum_rh_min, room_rh_max=zul.raum_rh_max,
                            T_sup_min=zul.t_min, T_sup_max=zul.t_max,
                            moisture_g_h=zul.feuchtelast, V_sup_m3h=v_sup,
                            V_exh_m3h=v_exh)
        return out, 0.5 * (zul.t_min + zul.t_max)
    t_lo, t_hi, rh_lo, rh_hi = _sollband(zul)
    out = simulate(cfg, aul.t, aul.rh, abl.t, abl.rh, t_lo, t_hi, rh_lo, rh_hi,
                   V_sup_m3h=v_sup, V_exh_m3h=v_exh)
    return out, 0.5 * (t_lo + t_hi)


def _sollband(zul) -> tuple:
    """(t_lo, t_hi, rh_lo, rh_hi) der Zuluft für Regelung fest/band."""
    if zul.regelung == "fest":
        return zul.t, zul.t, zul.rh, zul.rh
    return zul.t_min, zul.t_max, zul.rh_min, zul.rh_max


def _fmt(v: float, nd: int = 1) -> str:
    return f"{v:.{nd}f}".replace(".", ",")


def solve_air(source) -> dict:
    plant = source if isinstance(source, AirPlant) else load_air(source)
    errors: list[str] = []
    aul = _genau_eine(plant, "aussenluft", errors)
    abl = _genau_eine(plant, "abluft_raum", errors)
    zul = _genau_eine(plant, "zuluft", errors)
    fol = _genau_eine(plant, "fortluft", errors)
    wrg = _genau_eine(plant, "wrg", errors)
    if errors:
        raise NetworkValidationError(errors)

    # beide Stränge ablaufen; Fehler beider Stränge gemeinsam melden
    pairs = {}
    for seite, start, end_type in (("zul", aul, "zuluft"), ("abl", abl, "fortluft")):
        try:
            pairs[seite] = _walk(plant, f"{start.name}.out", end_type, seite)
        except NetworkValidationError as exc:
            errors += exc.messages
    if errors:
        raise NetworkValidationError(errors)
    zul_pairs, abl_pairs = pairs["zul"], pairs["abl"]
    zul_chain = [c for c, _ in zul_pairs]
    abl_chain = [c for c, _ in abl_pairs]
    if wrg not in zul_chain or wrg not in abl_chain:
        raise NetworkValidationError(
            ["Die WRG muss in BEIDEN Strängen liegen (zul_in/zul_out und abl_in/abl_out)."])

    hinweise: list[str] = []
    # Nicht angeschlossene Komponenten (in keinem der beiden Stränge) werden
    # nicht gerechnet und dürfen das Ergebnis nicht beeinflussen — früher
    # setzte z.B. eine lose Umluft-Komponente den Umluftstrom für die ganze
    # Anlage (Heizlast 5,4 → 1,3 kW). Fühler sind Anzapfungen, keine
    # Strangkomponenten.
    im_strang = {id(c) for c in [aul, abl] + zul_chain + abl_chain}
    for c in plant.components.values():
        if id(c) not in im_strang and c.type_name not in AIR_SENSOR_TYPES:
            hinweise.append(f"'{c.name}' ({c.type_name}) ist nicht angeschlossen und "
                            f"wird nicht berechnet.")

    # Anlagenkonfiguration aus dem Zuluftstrang (Reihenfolge = Zeichnung).
    # WICHTIG: Der Rechenkern ist für die Ventilatorposition am STRANGANFANG
    # validiert (Energy-Rotorregelung findet sonst u.U. keine zulässige
    # Drehzahl und fällt auf n_rot = 0 zurück). Die Ventilatorwärme wird
    # daher ohne Umluft immer am Stranganfang bilanziert, mit Umluft direkt
    # nach der Mischkammer (L19); die gezeichnete Reihenfolge der
    # Konditionierungskomponenten bleibt erhalten.
    tokens = [TOKEN[c.type_name] for c in zul_chain if c.type_name in TOKEN]
    order = ((["Vent_ZUL"] if "Vent_ZUL" in tokens else [])
             + [t for t in tokens if t != "Vent_ZUL"])

    # Je Typ höchstens eine Konditionierungskomponente im Zuluftstrang: der
    # Kern legt Lasten und Kettenzustände je Token ab — eine zweite Komponente
    # überschriebe die erste (zwei Nachheizer: 0 kW statt 13 kW gemeldet).
    je_typ: dict[str, list] = {}
    for c in zul_chain:
        je_typ.setdefault(c.type_name, []).append(c)
    for typ in EINMAL:
        mehrere = je_typ.get(typ, [])
        if len(mehrere) > 1:
            errors.append(
                f"Mehr als eine Komponente vom Typ '{typ}' im Zuluftstrang "
                f"({', '.join(c.name for c in mehrere)}) — der Rechenkern bilanziert je "
                f"Typ genau eine; bitte zu einer Komponente zusammenfassen.")

    def erste(typ: str):
        return je_typ[typ][0] if typ in je_typ else None

    frost, bef, uml = erste("frostschutz"), erste("befeuchter"), erste("umluft")
    kr = erste("kuehler")
    present = set(je_typ)
    # Der Vorheizer ist im Modell das Register NACH der WRG. Vor der WRG
    # gezeichnet heizte er die Außenluft bis zum Zuluft-Sollwert vor und legte
    # die Rückgewinnung still (46,7 statt 8,1 kW Heizlast, WRG aus) — der
    # Frostschutz der WRG ist die eigene Komponente 'frostschutz'.
    wrg_pos_zul = zul_chain.index(wrg)
    errors += [f"'{c.name}' (vorheizer) liegt im Zuluftstrang vor der WRG — im Modell ist "
               f"der Vorheizer das Register NACH der WRG und heizt auf den Zuluft-"
               f"Sollwert. Bitte hinter der WRG einzeichnen; für den Frostschutz der "
               f"WRG die Komponente 'frostschutz' (Modus 'preheater') verwenden."
               for c in je_typ.get("vorheizer", []) if zul_chain.index(c) < wrg_pos_zul]
    # Aktive Komponenten wirken nur im Zuluftstrang (Rechenkern); im
    # Abluftstrang gezeichnet wurden sie bisher halb angewandt (z.B. Befeuchter
    # global gesetzt, aber nie gerechnet — änderte trotzdem die VHR/NHR-
    # Aufteilung). Jetzt: klarer Fehler.
    errors += [f"'{c.name}' ({c.type_name}) liegt im Abluftstrang — aktive "
               f"Komponenten wirken nur im Zuluftstrang (zwischen Außenluft und "
               f"Zuluft); bitte dort einzeichnen."
               for c in abl_chain
               if c.type_name in TOKEN and c.type_name not in ("ventilator_luft", "wrg")]
    zul_fans = [c for c in zul_chain if c.type_name == "ventilator_luft"]
    abl_fans = [c for c in abl_chain if c.type_name == "ventilator_luft"]
    wrg_pos_abl = abl_chain.index(wrg)
    # Wo bilanziert der Kern den Zuluftventilator? Ohne Umluft am Strang-
    # anfang, mit Umluft direkt nach der Mischkammer (L19). fan_exakt ist der
    # Ventilator, der genau dort gezeichnet ist (deklarative Elemente wie
    # Filter dürfen dazwischen liegen); sonst ist die gezeichnete Position
    # dokumentarisch (Hinweis).
    mit_umluft = uml is not None and uml.v > 0.0
    aktive = [c for c in zul_chain if c.type_name in TOKEN]
    fan_exakt = None
    if mit_umluft:
        nach_uml = aktive[aktive.index(uml) + 1:]
        if nach_uml and nach_uml[0].type_name == "ventilator_luft":
            fan_exakt = nach_uml[0]
    elif aktive and aktive[0].type_name == "ventilator_luft":
        fan_exakt = aktive[0]
    fan_moved = bool(zul_fans) and fan_exakt is None
    cfg = {
        "wrg": wrg.typ,
        "components": [tok for name, tok in (("vorheizer", "VHR"), ("kuehler", "KR"),
                                             ("nachheizer", "NHR")) if name in present],
        "humidifier": bef.typ if bef is not None else "none",
        "frost": frost.modus if frost is not None else "none",
        # Auslegungsvolumenstrom: ohne Angabe der Zuluftvolumenstrom (früher
        # fest 4500 m³/h — bei 1359 m³/h lagen die Übertragungsgrade 16–21 %
        # über den angegebenen Referenzwerten)
        "V_nom_m3h": (wrg.v_nom if wrg.v_nom is not None else zul.v) * 3600.0,
        "adiab_exhaust": bool(wrg.adiab_exhaust),
    }
    if frost is not None:
        cfg["T_FS"] = frost.t_fs
    if kr is not None:
        cfg["T_KW"] = kr.t_kw           # Kaltwasser-Vorlauftemperatur (L18)
    if wrg.eta_hr_n:
        cfg["eta_hr_N"] = wrg.eta_hr_n
    if wrg.eta_xr_n:
        cfg["eta_xr_N"] = wrg.eta_xr_n
    if getattr(wrg, "rwz_n", None):
        cfg["RWZ_N"] = wrg.rwz_n
    if wrg.typ == "KVS":
        cfg["V_M_KVS_N"] = wrg.v_m_kvs * 3600.0
        # Auslegungspunkt der KVS prüfen: die Rückwärmzahl je Wärmetauscher ist
        # nur möglich, wenn ε·C_Luft < C_Sole. Sonst lieferte der Kern NaN und
        # die WRG fiel stillschweigend auf 0 kW (Vorlage ab ~11 500 m³/h).
        rwz = cfg.get("RWZ_N", 0.76)
        v_m_min = min_medium_flow(rwz, cfg["V_nom_m3h"])
        if not cfg["V_M_KVS_N"] > v_m_min:
            errors.append(
                f"'{wrg.name}': KVS-Auslegung nicht möglich — die Rückwärmzahl "
                f"{_fmt(rwz, 2)} je Wärmetauscher verlangt bei einem Auslegungs-"
                f"Luftvolumenstrom von {cfg['V_nom_m3h']:.0f} m³/h einen Sole-"
                f"Nennvolumenstrom über {_fmt(v_m_min, 3)} m³/h (angegeben: v_m_kvs = "
                f"{_fmt(cfg['V_M_KVS_N'], 3)} m³/h; ε·C_Luft muss kleiner als C_Sole "
                f"sein). Bitte 'v_m_kvs_m3h' erhöhen oder 'rwz_n' senken.")
    if uml is not None:
        cfg["recirculation_m3h"] = uml.v * 3600.0
    # Ventilatoren: SFP = Summe beider Stränge (Kern-Konvention), Anteil des
    # Abluftventilators für P_el und — beim KVS mit Abluftventilator vor der
    # WRG — für die Aufteilung der Motorwärme. Ohne gezeichneten Ventilator
    # SFP = 0 (früher Kern-Default 1250 W/(m³/s): Ventilatorwärme ohne Ventilator).
    sfp_zul = sum(f.sfp for f in zul_fans)
    sfp_abl = sum(f.sfp for f in abl_fans)
    cfg["SFP"] = sfp_zul + sfp_abl
    cfg["SFP_exh_frac"] = sfp_abl / (sfp_zul + sfp_abl) if (sfp_zul + sfp_abl) > 0 else 0.0
    if wrg.typ == "KVS":
        cfg["exhaust_fan_before_wrg"] = any(abl_chain.index(f) < wrg_pos_abl for f in abl_fans)
    if order:
        cfg["order"] = order

    v_sup = zul.v * 3600.0
    # Abluft-Volumenstrom: Eingabe bei 'abluft_raum' (fortluft.v = Altbestand)
    if (abl.v is not None and fol.v is not None
            and abs(abl.v - fol.v) > 1e-9):
        errors.append("Abluft-Volumenstrom doppelt und widersprüchlich angegeben "
                      "(abluft_raum.v und fortluft.v) — bitte nur bei 'abluft_raum' angeben.")
    v_abl = abl.v if abl.v is not None else fol.v
    v_exh = v_abl * 3600.0 if v_abl is not None else None
    if v_exh is not None and v_exh <= 0.0:
        errors.append("abluft_raum: Abluft-Volumenstrom muss > 0 sein (ohne Abluft "
                      "arbeitet keine WRG; Anlage aus = Zuluft-Volumenstrom 0).")
    if uml is not None:
        v_lim = min(v_sup, v_exh if v_exh is not None else v_sup)
        if uml.v * 3600.0 > v_lim + 1e-9:
            errors.append(f"Umluft-Volumenstrom {uml.v * 3600.0:.0f} m³/h größer als Zu- bzw. "
                          f"Abluft-Volumenstrom ({v_lim:.0f} m³/h) — Außenluftanteil wäre "
                          f"negativ.")
    if zul.regelung != "raum" and abl.rh is None:
        errors.append("abluft_raum: 'rh' (rel. Feuchte) fehlt — bei Regelung "
                      "'fest'/'band' ist die Abluftfeuchte Pflicht.")
    if errors:
        raise NetworkValidationError(errors)

    v_exh_eff = v_exh if v_exh is not None else v_sup
    if v_sup > 0:
        ratio = v_exh_eff / v_sup
        if ratio > 1.3 or ratio < 0.77:
            hinweise.append(
                f"Volumenstromverhältnis Abluft/Zuluft = {ratio:.2f} — außerhalb "
                "des typischen Gültigkeitsbereichs der WRG-Kennlinienkorrektur "
                "(≈ ±30 %); Übertragungsgrade werden physikalisch auf "
                "≤ min(1, V̇_Abluft/V̇_Zuluft) der WRG-Ströme begrenzt.")
    if fan_moved and mit_umluft:
        hinweise.append("Bei Anlagen mit Umluft wird der Zuluftventilator nach der "
                        "Mischkammer bilanziert (Außenluft → Mischkammer → Ventilator → "
                        "Zuluft; Modellkonvention des Rechenkerns): er fördert den vollen "
                        "Zuluftstrom, seine Wärme geht in die Mischluft. Die gezeichnete "
                        "Ventilatorposition ist dokumentarisch.")
    elif fan_moved:
        hinweise.append("Ventilatorwärme wird am Stranganfang bilanziert "
                        "(Modellkonvention des Rechenkerns); die gezeichnete "
                        "Ventilatorposition ist dokumentarisch.")
    # Warnungen des Kerns (Band-Hinweise, numerische Warnungen) gehen als
    # Hinweise in die Antwort statt nach stderr; Eingabefehler deutsch
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            out, t_set_mean = _run_kernel(cfg, aul, abl, zul, v_sup, v_exh)
        except ValueError as exc:                          # Sollband/Eingaben des Kerns
            raise NetworkValidationError([_kern_fehler(exc)]) from exc
    hinweise += _kern_hinweise(caught)

    def f(key, idx=0):
        v = out.get(key)
        if v is None:
            return None
        x = float(v[idx]) if hasattr(v, "__len__") else float(v)
        return None if math.isnan(x) else round(x, 6)

    t_zul, x_zul, phi_zul = f("T_sup_C"), f("x_sup_gkg"), f("phi_sup_pct")
    if None in (t_zul, x_zul, phi_zul):
        # darf nach den Prüfungen oben nicht vorkommen — nie „ok“ ohne Werte
        raise NetworkValidationError(
            ["Der Lüftungskern lieferte keinen Betriebspunkt (Zuluftzustand "
             "unbestimmt) — bitte Eingaben prüfen."])

    # Stationskarte aus den Kettenzuständen DESSELBEN Betriebspunkts: simulate()
    # liefert sie direkt, simulate_room() reicht die des gewählten Kandidaten
    # durch (früher gepinnter Nachlauf mit anderer Abluftfeuchte — Stationen
    # und Lasten widersprachen sich in jedem dritten Fall).
    abl_rh_st = abl.rh
    raum = None
    if zul.regelung == "raum":
        abl_rh_st = f("room_rh_pct")
        band_ok = bool(out["room_band_ok"][0])
        raum = {"rh_pct": abl_rh_st, "band_erreicht": band_ok}
        if not band_ok:
            zu_trocken = abl_rh_st < zul.raum_rh_min
            hinweise.append(
                f"Raumfeuchteband nicht erreichbar: die Raumfeuchte stellt sich auf "
                f"{_fmt(abl_rh_st)} % ein (Band {zul.raum_rh_min:g}…{zul.raum_rh_max:g} %) — "
                + ("zu trocken; mit den gezeichneten Komponenten ist keine ausreichende "
                   "Befeuchtung möglich (Befeuchter fehlt oder reicht nicht)."
                   if zu_trocken else
                   "zu feucht; mit den gezeichneten Komponenten ist keine ausreichende "
                   "Entfeuchtung möglich (Kühler fehlt oder reicht nicht).")
                + " Angegeben ist der stationäre Zustand der Feuchtebilanz "
                  "x_Raum = x_Zuluft + Feuchtelast/ṁ_Zuluft.")
        rest = f("rh_residual_pct")
        if rest is not None and rest > TOL_RH_REST:
            hinweise.append(
                f"Raumfeuchte nicht eindeutig bestimmbar: an diesem Betriebspunkt springt "
                f"die Regelung, zwischen angesetzter Abluft- und resultierender "
                f"Raumfeuchte bleibt ein Rest von {_fmt(rest)} %-Punkten.")
    m_dot = _f0(out, "m_dot_kg_s")
    rho = (m_dot / (v_sup / 3600.0) if m_dot and v_sup > 0
           else 1e5 / (287.0 * (273.0 + t_set_mean)))
    stationen = _station_map(plant, zul_pairs, abl_pairs, cfg, out,
                             aul, abl.t, abl_rh_st if abl_rh_st is not None else 50.0,
                             uml, fan_exakt, v_sup, v_exh_eff, rho)

    # Hinweis, wenn der erreichte Zuluftzustand außerhalb des Sollbands liegt
    if zul.regelung == "raum":
        t_lo, t_hi, rh_lo, rh_hi = zul.t_min, zul.t_max, None, None
    else:
        t_lo, t_hi, rh_lo, rh_hi = _sollband(zul)
    # Kühler an der Kaltwasser-Grenze (L18): der Feuchte-Sollwert verlangt
    # einen Taupunkt unter der Kaltwasser-Vorlauftemperatur
    if kr is not None and (f("KR_dew_limited") or 0.0) > 0.5:
        x_grenz = float(ma.xs(kr.t_kw, 1e5)) * 1e3
        taupunkt = ""
        if rh_hi is not None:
            t_tau = float(ma.Ts(float(ma.x(t_hi, rh_hi / 100.0, 1e5)), 1e5))
            taupunkt = f" von {_fmt(t_tau)} °C"
        hinweise.append(
            f"'{kr.name}': der Feuchte-Sollwert verlangt einen Taupunkt{taupunkt} unter "
            f"der Kaltwasser-Vorlauftemperatur ({_fmt(kr.t_kw)} °C) — der Kühler "
            f"entfeuchtet nur bis zur Sättigung bei dieser Temperatur "
            f"({_fmt(x_grenz, 2)} g/kg), die Luft bleibt feuchter als die obere "
            f"Feuchtegrenze. Kaltwasser-Vorlauftemperatur ('t_kw_C') senken oder die "
            f"obere Feuchtegrenze anheben.")
    t_aus = t_zul < t_lo - TOL_T_BAND or t_zul > t_hi + TOL_T_BAND
    rh_aus = rh_lo is not None and (phi_zul < max(rh_lo, 1.0) - TOL_RH_BAND
                                    or phi_zul > rh_hi + TOL_RH_BAND)
    if t_aus or rh_aus:
        def band(lo, hi, nd=1):
            return _fmt(lo, nd) if lo == hi else f"{_fmt(lo, nd)}…{_fmt(hi, nd)}"
        soll = f"{band(t_lo, t_hi)} °C" + ("" if rh_lo is None else f" / {band(rh_lo, rh_hi)} %")
        hinweise.append(
            f"Zuluft-Sollband nicht erreicht: Zuluft {_fmt(t_zul)} °C / {_fmt(phi_zul)} % "
            f"statt {soll} — mit den gezeichneten Komponenten und ihrer Reihenfolge "
            f"ist der Sollzustand an diesem Betriebspunkt nicht erreichbar.")

    # Ergebnisse je gezeichneter Komponente (für Tooltips/Bericht) — nur für
    # Komponenten, die in einem Strang liegen
    per_comp: dict[str, dict] = {}
    # elektrische Leistung je Strang mit dessen Volumenstrom (früher SFP-Summe
    # × V̇_Zuluft — bei V̇_Abluft ≠ V̇_Zuluft falsch)
    fan_v = {f.name: v_sup for f in zul_fans} | {f.name: v_exh_eff for f in abl_fans}
    fan_p = {name: c_sfp * fan_v[name] / 3.6e6
             for name, c_sfp in ((f.name, f.sfp) for f in zul_fans + abl_fans)}
    fan_p_total = sum(fan_p.values())                               # kW
    for c in zul_chain + [c for c in abl_chain if c is not wrg]:
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
            if wrg.typ in ("KVS", "PLATE"):
                # KVS/Plattentauscher haben keine Rotorgrößen: unter eta_hr die
                # erreichte Rückwärmzahl (Zuluftseite, (ϑ_aus − ϑ_ein)/(ϑ_Abluft −
                # ϑ_ein)); früher 0 bei arbeitender WRG. eta_xr/n_rot bleiben
                # als 0 erhalten (rein sensibel, kein Rotor).
                r["eta_hr"] = f("RWZ_KVS") or 0.0
                r["eta_xr"] = 0.0
                r["n_rot"] = 0.0
            else:
                r["eta_hr"] = f("eta_hr")
                r["eta_xr"] = f("eta_xr")
                r["n_rot"] = f("n_rot")
        elif t == "ventilator_luft" and c.name in fan_p:
            r["p_el_kW"] = round(fan_p[c.name], 6)
        elif t == "zuluft":
            r.update({"t_C": t_zul, "x_gkg": x_zul, "phi_pct": phi_zul,
                      "v_m3h": round(v_sup, 3)})
        if r:
            per_comp[c.name] = r

    # Übersättigte Stationszustände (x > x_s(T)): das Modell bilanziert die WRG
    # trocken bzw. klemmt φ auf 100 % — real Nebel/Kondensat, Rückgewinnung
    # eher unterschätzt
    sat = sorted({split_ref(ref)[0] for ref, st in stationen.items()
                  if st["x_gkg"] > float(ma.xs(st["t_C"], 1e5)) * 1e3 * 1.001})
    if sat:
        hinweise.append(
            "Übersättigter Luftzustand (x > x_s, angezeigt φ = 100 %) an: "
            + ", ".join(sat[:6]) + " — das Modell bilanziert hier trocken (keine "
            "Kondensation); real Nebel/Kondensat, Fortluft wärmer als angezeigt.")

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
            "ventilatoren_el_kW": (round(fan_p_total, 6)
                                   if (zul_fans or abl_fans) else None),
        },
        "komponenten": per_comp,
        "stationen": stationen,
        "hinweise": hinweise,
    }
    if raum is not None:
        payload["raum"] = raum
    return payload
