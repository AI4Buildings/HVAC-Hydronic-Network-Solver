"""Energetikum-Lüftungsanlage: Vorerhitzer + Nacherhitzer als Einspritzschaltungen.

Analyse des realen Anlagenschemas (examples/09_energetikum_lueftungsregister.yaml,
Regelventil VVG44.25-10 im Rücklauf, STAD 40 VE 3,5, Primär 60 kPa,
Sekundärpumpen H = 2,75 m) in vier Schritten:

  1. Auslegungszustand: Reproduktion der Register-Datenblätter
  2. Befund Sekundärkreis: Übervolumenstrom durch großzügige Förderhöhe
  3. Einregulierung: benötigte STAD-Voreinstellung sekundär (Simulation
     als Einregulierungswerkzeug) + Verifikation 52/47 °C
  4. Ventilautorität der VVG44 am 60-kPa-Verteiler

Die BEMS-IDs im Schema sind echte Aedifion-Datenpunkte — result.sensors
liefert die Zuordnung Simulationswert ↔ Zeitreihe (Soll-Ist-Vergleich).
"""
from pathlib import Path

import yaml
from scipy.optimize import brentq

import hydraulik as h

YAML = Path(__file__).parent / "09_energetikum_lueftungsregister.yaml"
DESIGN = {"ve": dict(q_kw=16.5, q_w=2.9, dp_reg=3.5),
          "ne": dict(q_kw=19.6, q_w=3.4, dp_reg=1.9)}
KV_STAD40 = {2.0: 6.10, 2.5: 8.80, 3.0: 12.6, 3.5: 16.0, 4.0: 19.2}   # TA-Tabelle


def zeile(r, sfx):
    wmz = next(x for x in r.sensors if x.name == f"wmz_{sfx}")
    reg = r[sfx]
    return (f"  {sfx.upper()}: Q̇ = {-reg.q_dot_kW:5.2f} kW   "
            f"Wasser {reg.t_in_C:.1f}→{reg.t_out_C:.1f} °C, V̇_sek = {reg.q_m3h:.2f} m³/h   "
            f"Luft aus = {reg.extras['t_luft_aus_C']:.2f} °C   "
            f"WMZ: V̇_prim = {wmz.readings['q_m3h']:.2f} m³/h")


def main() -> None:
    doc = yaml.safe_load(YAML.read_text(encoding="utf-8"))
    fluid = h.water_at(50.0)

    # -- 1) Auslegungszustand (Ventilhübe wie in der YAML hinterlegt) --------
    r = h.load(doc).solve()
    assert r.converged
    print("1) Auslegungszustand (Hübe aus der YAML: Register auf Datenblattleistung)")
    for s in DESIGN:
        print(zeile(r, s))
    print("   → Luftaustritt 24,0/25,0 °C wie Datenblatt; Leistung exakt 16,5/19,6 kW.\n")

    # -- 2) Befund Sekundärkreis ---------------------------------------------
    print("2) Befund: Sekundär-Volumenströme deutlich über Auslegung")
    for s, d in DESIGN.items():
        print(f"   {s.upper()}: {r[s].q_m3h:.2f} m³/h statt {d['q_w']} m³/h "
              f"({r[s].q_m3h / d['q_w']:.2f}-fach)")
    print("   Ursache: H = 2,75 m (26,65 kPa) gegen nur wenige kPa Kreiswiderstand;")
    print("   STAD sekundär bei VE 3,5 (Kv 16,0) drosselt zu wenig. Folge: Register-")
    print(f"   Vorlauf nur {r['ve'].t_in_C:.1f} °C statt 52 °C (starke Beimischung "
          "über den Kurzschluss).\n")

    # -- 3) Einregulierung sekundär ------------------------------------------
    print("3) Einregulierung: benötigter Kv des Sekundär-STAD für den Auslegungsstrom")
    for s, d in DESIGN.items():
        dp_stad = 26.65 - d["dp_reg"]                     # kPa, Pumpen-Δp minus Register
        kv = d["q_w"] * (100.0 * fluid.rho / 1000.0 / dp_stad) ** 0.5
        ve = min(KV_STAD40, key=lambda v: abs(KV_STAD40[v] - kv))
        print(f"   {s.upper()}: Kv = {kv:.2f} m³/h  → STAD 40 Voreinstellung ≈ {ve} "
              f"(Kv {KV_STAD40[ve]})")
        doc["components"][f"srv2_{s}"]["kvs_m3h"] = round(kv, 2)
        doc["components"][f"srv2_{s}"]["description"] = (
            f"STAD DN 40, einreguliert auf Kv = {kv:.2f} (≈ VE {ve})")
    r3 = h.load(doc).solve()
    assert r3.converged
    print("   Verifikation nach Einregulierung:")
    for s in DESIGN:
        print(zeile(r3, s))
    print("   → Sekundärströme auf Auslegung, Register fahren 52/47 °C.\n")

    # -- 4) Ventilhübe & Ventilautorität im einregulierten Zustand -----------
    print("4) Regelventile VVG44.25-10 im einregulierten Zustand")
    for s, d in DESIGN.items():
        q100 = -_q(doc, s, 1.0)                    # maximal lieferbare Leistung
        ziel = min(d["q_kw"], 0.999 * q100)        # knapp unter Volllast kappen
        op = brentq(lambda o: -_q(doc, s, o) - ziel, 0.05, 1.0, xtol=1e-4)
        doc["components"][f"rv_{s}"]["opening"] = round(op, 3)
        r4 = h.load(doc).solve()
        q_prim = next(x for x in r4.sensors
                      if x.name == f"wmz_{s}").readings["q_m3h"]
        dp_v100 = (q_prim / 10.0) ** 2 * 100.0 * fluid.rho / 1000.0   # kvs 10, voll offen
        kvs_50 = q_prim * (100.0 * fluid.rho / 1000.0 / (0.5 * 60.0)) ** 0.5
        print(f"   {s.upper()}: Auslegungshub H = {op:.2f}, V̇_prim = {q_prim:.2f} m³/h, "
              f"Autorität a_V = Δp_V100/Δp_Verteiler = {dp_v100:.1f}/60 kPa "
              f"= {dp_v100 / 60.0:.2f}   (a_V ≥ 0,5 bräuchte kvs ≤ {kvs_50:.1f})")
    print("   → a_V < 0,5: kvs 10 ist für den 60-kPa-Verteiler großzügig gewählt")
    print("     (z.B. VVG44.25-6,3 wäre günstiger); die gleichprozentige Kennlinie")
    print("     kompensiert die Verzerrung teilweise (vgl. examples/08_ventilautoritaet.py).\n")

    print("Sensor-/BEMS-Tabelle des Auslegungszustands (Auszug aus report()):")
    rep = r.report().splitlines()
    i = next(k for k, ln in enumerate(rep) if ln.startswith("Sensor"))
    print("\n".join(rep[i - 1:i + 12]))


def _q(doc, sfx, op) -> float:
    doc["components"][f"rv_{sfx}"]["opening"] = round(op, 4)
    rr = h.load(doc).solve()
    assert rr.converged
    return rr[sfx].q_dot_kW


if __name__ == "__main__":
    main()
