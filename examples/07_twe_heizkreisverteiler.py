"""Übung Bsp 6: TWE (Durchlaufprinzip) + Heizkreisverteiler mit 2 Regelgruppen.

Anlage: Speicher (70 °C) → TS1 (14 m³/h, Δp_RE = 5 kPa bei 14 m³/h) →
  a) TWW-Zweig: UWP1 → TS2 → Wärmeübertrager (70 → 25 °C), V̇2 = 14 − 10 = 4 m³/h
  b) UWP2 → TS3 (10 m³/h) → Verteiler (Δp_Vert = 550 mbar am Schlechtpunkt):
     - Regelgruppe 1 (Einspritzschaltung, Daten gegeben): TS4 = 4 m³/h
       Einspritzung mit RG-Ventil (a_V ≥ 0.5), WMZ (kv 40) und SRV;
       TS5 = Kurzschluss (3 m³/h); TS6 = 7 m³/h Sekundärkreis mit UWP3,
       SRV6 und Wärmeabgabesystem, Rücklauf 35 °C.
     - Regelgruppe 2: nimmt aus Kontinuität die restlichen 6 m³/h
       (ϑ_RL-Label 35 °C am Sammlerende schließt einen offenen Endbypass aus);
       ohne innere Daten als konzentrierter Anschluss C = 550 mbar/6² modelliert.

Konventionen (abgeglichen mit handschriftlicher Musterlösung Lsg_Bsp6.pdf):
  ρ = 995 kg/m³, cp = 4.19 kJ/(kg·K);
  SRV6 nach Praxisregel Δp_min = 30 mbar bei Nennvolumenstrom (Messgenauigkeit).

Teil A: Auslegung (Handrechnung) | Teil B: Solver-Verifikation Nennlast |
Teil C: UWP1 abgeschaltet (Rückschlagventil zu), alle Pumpen Δp = const.
"""
import math

import hydraulik as h

RHO, CP = 995.0, 4190.0
G = 9.81


def kv_dp_pa(v_m3h: float, kv: float) -> float:
    """Druckverlust einer Kv-Armatur [Pa]: Δp = (V̇/kv)²·1e5·ρ/1000."""
    return (v_m3h / kv) ** 2 * 1e5 * RHO / 1000.0


def kv_from_dp(v_m3h: float, dp_pa: float) -> float:
    return v_m3h * math.sqrt(RHO / 1000.0 / (dp_pa / 1e5))


# ======================================================================
# Teil A – Auslegung von Hand
# ======================================================================
V1, V2, V3, V4, V6 = 14.0, 4.0, 10.0, 4.0, 7.0
V5 = V6 - V4                      # Kurzschluss 3 m³/h
V_G2 = V3 - V4                    # Regelgruppe 2: 6 m³/h (Kontinuität)
DP_VERT = 55_000.0                # 550 mbar am Schlechtpunkt

# --- Regelventil RG (TS4): kvs aus Normreihe, a_V = Δp_V100/Δp_Vert ≥ 0.5
KVS_REIHE = [4.0, 6.3, 10.0, 16.0]
kvs_rg = next(k for k in KVS_REIHE if kv_dp_pa(V4, k) / DP_VERT >= 0.5
              and kv_dp_pa(V4, k) < DP_VERT)
DP_RG = kv_dp_pa(V4, kvs_rg)
A_V = DP_RG / DP_VERT

# --- TS4-Bilanz inkl. TS5-Kopplung: Δp_Vert + V̇5²C5 = R+E + WMZ + RG + SRV
DP_TS4_RE = (93.75 + 93.75) * V4 ** 2
DP_WMZ = kv_dp_pa(V4, 40.0)
DP_TS5 = (2.5 + 2.5) * V5 ** 2
DP_SRV4 = DP_VERT - DP_TS5 - DP_TS4_RE - DP_WMZ - DP_RG
KV_SRV4 = kv_from_dp(V4, DP_SRV4)

# --- SRV6 (Sekundärkreis): kein Drosselüberschuss → Praxisregel Δp_min = 30 mbar
DP_SRV6 = 3_000.0
KV_SRV6 = kv_from_dp(V6, DP_SRV6)

# --- Pumpen (je Kreis: gemeinsame TS1 + eigener Zweig)
DP_RE = 5_000.0                                   # bei 14 m³/h
DP_TS2 = (312.5 + 312.5) * V2 ** 2 + kv_dp_pa(V2, 12.65)
DP_UWP1 = DP_RE + DP_TS2
DP_TS3 = (39.06 + 39.06) * V3 ** 2
DP_UWP2 = DP_RE + DP_TS3 + DP_VERT
DP_TS6 = (300.0 + 125.0) * V6 ** 2
DP_UWP3 = DP_TS6 + DP_SRV6 + DP_TS5

# --- Fluidleistungen & Temperaturen (Nennlast)
T_VL = (V4 * 70.0 + V5 * 35.0) / V6               # Einspritzmischung: 55 °C
Q_WA = RHO * V6 / 3600.0 * CP * (T_VL - 35.0)
Q_TWW = RHO * V2 / 3600.0 * CP * (70.0 - 25.0)
Q_G2 = RHO * V_G2 / 3600.0 * CP * (70.0 - 35.0)   # Fluidleistung Gruppe 2
T_RL_TS3 = 35.0                                   # beide Gruppen 35 °C, kein Bypass
T_RL_TS1 = (V3 * T_RL_TS3 + V2 * 25.0) / V1


def print_hand():
    print("=" * 78)
    print("TEIL A – Auslegung (Handrechnung, ρ = 995 kg/m³)")
    print("=" * 78)
    print(f"Regelventil RG:   kvs = {kvs_rg} m³/h  →  Δp_V100 = {DP_RG/100:.1f} mbar,"
          f"  a_V = {A_V:.3f}  (≥ 0.5 ✓)")
    print(f"SRV TS4:          Δp = {DP_SRV4/100:.1f} mbar bei 4 m³/h  →  kv = {KV_SRV4:.2f}")
    print(f"SRV TS6:          Δp_min = 30 mbar (Messbarkeitsregel) bei 7 m³/h  →  kv = {KV_SRV6:.2f}")
    print(f"UWP1: Δp = {DP_UWP1/100:.1f} mbar  →  H = {DP_UWP1/(RHO*G):.2f} m")
    print(f"UWP2: Δp = {DP_UWP2/100:.1f} mbar  →  H = {DP_UWP2/(RHO*G):.2f} m")
    print(f"UWP3: Δp = {DP_UWP3/100:.1f} mbar  →  H = {DP_UWP3/(RHO*G):.2f} m")
    print(f"Q̇_WA = {Q_WA/1e3:.1f} kW (Gruppe 1)   Q̇_Gruppe2 = {Q_G2/1e3:.1f} kW   "
          f"Q̇_TWW = {Q_TWW/1e3:.1f} kW")
    print(f"ϑ_VL = {T_VL:.2f} °C   ϑ_RL,TS3 = {T_RL_TS3:.2f} °C   ϑ_RL,TS1 = {T_RL_TS1:.2f} °C")


# ======================================================================
# Teil B/C – Netzwerkmodell
# ======================================================================
def build(uwp1_on: bool = True) -> h.Network:
    """Modellierungskonvention: Teilstrecken-Widerstände hälftig auf Vor- und
    Rücklauf aufgeteilt (C/2 je Richtung) – die Leitungen sind in der Praxis
    parallel und gleich lang verlegt. Für die Kreisvolumenströme ist das
    äquivalent zum konzentrierten C-Wert, liefert aber korrekte Druckniveaus
    an den Knoten, und die Rücklaufäste sind automatisch eigene Kanten
    (thermische Mischreihenfolge am Sammler stimmt ohne link-Hilfskanten)."""
    net = h.Network(fluid=h.Fluid("wasser", rho=RHO, mu=0.4e-3, cp=CP))

    # Speicher: geladen/geschichtet → Vorlauf konstant 70 °C, Rücklauf Ergebnis
    net.add(h.IdealStorage("speicher", t_set_C=70, q_nom_m3h=14))
    net.add(h.FlowResistance("re_vl", dp_kPa=2.5, q_m3h=14, ts=1))   # Δp_RE (TS1), je Hälfte
    net.add(h.FlowResistance("re_rl", dp_kPa=2.5, q_m3h=14, ts=1))

    # TWW-Zweig (TS2)
    net.add(h.Pump("uwp1", mode="constant_dp", dp_Pa=DP_UWP1, q_nom_m3h=4,
                   dp_internal_frac=1e-4))
    net.add(h.FlowResistance("ts2_vl", c_Pa_m3h2=312.5, ts=2))
    net.add(h.FlowResistance("ts2_rl", c_Pa_m3h2=312.5, ts=2))
    # GuA als Kv-Armatur; opening 0 = geschlossenes Rückschlagventil (UWP1 aus)
    net.add(h.BalancingValve("gua2", kvs_m3h=12.65, opening=1.0 if uwp1_on else 0.0, ts=2))
    net.add(h.Radiator("tww_hx", q_prescribed_W=Q_TWW, kv_m3h=1000, t_room_C=15, ts=2))

    # Verteilerzuleitung (TS3)
    net.add(h.Pump("uwp2", mode="constant_dp", dp_Pa=DP_UWP2, q_nom_m3h=10,
                   dp_internal_frac=1e-4))
    net.add(h.FlowResistance("ts3_vl", c_Pa_m3h2=39.06, ts=3))
    net.add(h.FlowResistance("ts3_rl", c_Pa_m3h2=39.06, ts=3))

    # Regelgruppe 1 (Einspritzschaltung, voll aufgelöst)
    net.add(h.FlowResistance("ts4_vl", c_Pa_m3h2=93.75, ts=4))
    net.add(h.FlowResistance("ts4_rl", c_Pa_m3h2=93.75, ts=4))
    net.add(h.BalancingValve("wmz", kvs_m3h=40.0, ts=4))
    net.add(h.ControlValve("rg1", kvs_m3h=kvs_rg, opening=1.0, characteristic="linear", ts=4))
    net.add(h.BalancingValve("srv4", kvs_m3h=KV_SRV4, ts=4))
    net.add(h.Pump("uwp3", mode="constant_dp", dp_Pa=DP_UWP3, q_nom_m3h=7,
                   dp_internal_frac=1e-4))
    net.add(h.BalancingValve("srv6", kvs_m3h=KV_SRV6, ts=6))
    net.add(h.Radiator("wa", q_prescribed_W=Q_WA, kv_m3h=1000, t_room_C=15, ts=6))
    net.add(h.FlowResistance("ts6_re", c_Pa_m3h2=425.0, ts=6))   # Sekundärkreis (in sich)
    net.add(h.FlowResistance("ts5", c_Pa_m3h2=5.0, ts=5))

    # Regelgruppe 2: Anschluss C = Δp_Vert/V̇² (6 m³/h bei 550 mbar), hälftig geteilt;
    # thermisch: Rücklauf 35 °C über feste Fluidleistung
    c_g2_half = (DP_VERT / V_G2 ** 2 - 0.1) / 2.0
    net.add(h.FlowResistance("g2_vl", c_Pa_m3h2=c_g2_half))
    net.add(h.FlowResistance("g2_rl", c_Pa_m3h2=c_g2_half))
    net.add(h.Radiator("g2_last", q_prescribed_W=Q_G2, kv_m3h=1000, t_room_C=15))

    # --- Verbindungen ---
    net.connect("speicher.out", "re_vl.in")
    net.connect("re_vl.out", "uwp1.in", "uwp2.in")
    net.connect("re_rl.out", "speicher.in")
    # TWW
    net.connect("uwp1.out", "ts2_vl.in")
    net.connect("ts2_vl.out", "gua2.in")
    net.connect("gua2.out", "tww_hx.in")
    net.connect("tww_hx.out", "ts2_rl.in")
    net.connect("ts2_rl.out", "re_rl.in")            # TS1-Rücklaufpunkt (Zumischung TWW)
    # Verteiler
    net.connect("uwp2.out", "ts3_vl.in")
    net.connect("ts3_vl.out", "ts4_vl.in", "g2_vl.in")            # VL-Sammler
    net.connect("ts3_rl.out", "ts2_rl.out", "re_rl.in")           # (gleicher RL-Punkt)
    # Gruppe 1
    net.connect("ts4_vl.out", "wmz.in")
    net.connect("wmz.out", "rg1.in")
    net.connect("rg1.out", "srv4.in")
    net.connect("srv4.out", "uwp3.in", "ts5.out")                 # Einspritzpunkt
    net.connect("uwp3.out", "srv6.in")
    net.connect("srv6.out", "wa.in")
    net.connect("wa.out", "ts6_re.in")
    net.connect("ts6_re.out", "ts5.in", "ts4_rl.in")              # Gruppenrücklauf
    # Gruppe 2
    net.connect("g2_vl.out", "g2_last.in")
    net.connect("g2_last.out", "g2_rl.in")
    # RL-Sammler
    net.connect("ts4_rl.out", "g2_rl.out", "ts3_rl.in")
    return net


def report_case(r, label):
    print("-" * 78)
    print(label)
    print("-" * 78)
    v = lambda n: abs(r[n].q_m3h)
    dp_vert = (sum(r[n].dp_kPa for n in ("ts4_vl", "wmz", "rg1", "srv4"))
               - r["ts5"].dp_kPa + r["ts4_rl"].dp_kPa)
    print(f"  V̇_TS1 = {v('re_vl'):6.3f}   V̇_TS2 = {v('gua2'):6.3f}   "
          f"V̇_TS3 = {v('ts3_vl'):6.3f}   V̇_Gruppe2 = {v('g2_vl'):6.3f}  [m³/h]")
    print(f"  Gruppe 1: V̇_TS4 = {v('rg1'):6.3f}   V̇_TS5 = {v('ts5'):6.3f}   "
          f"V̇_TS6 = {v('uwp3'):6.3f}  [m³/h]")
    print(f"  Δp_Vert = {dp_vert:7.2f} kPa   (RG-Ventil: {r['rg1'].dp_kPa:.2f} kPa,"
          f"  SRV6: {r['srv6'].dp_kPa:.2f} kPa)")
    print(f"  ϑ_VL,WA = {r['wa'].t_in_C:6.2f} °C   ϑ_RL,WA = {r['wa'].t_out_C:6.2f} °C   "
          f"ϑ_RL,TS3 = {r['ts3_rl'].t_in_C:6.2f} °C   ϑ_RL,TS1 = {r['speicher'].t_in_C:6.2f} °C")
    print(f"  Q̇_Speicher = {r['speicher'].q_dot_kW:7.1f} kW   Energiebilanz {r.energy_imbalance_W:+.2f} W")


if __name__ == "__main__":
    print_hand()

    print()
    print("=" * 78)
    print("TEIL B – Solver-Verifikation Nennlastfall")
    print("=" * 78)
    r_nenn = build(uwp1_on=True).solve()
    report_case(r_nenn, "Nennlast (UWP1 ein)")
    t_vl_nenn = r_nenn["wa"].t_in_C
    print("\n  Teilstrecken (klassische Nummerierung als Gruppenlabel):")
    for s in r_nenn.teilstrecken:
        print(f"   TS{s.ts}.{s.part}: {', '.join(s.components):<28} "
              f"V̇={s.q_m3h:6.3f} m³/h  ΣΔp={s.dp_kPa:7.3f} kPa  "
              f"p {s.p_in_kPa:6.1f}→{s.p_out_kPa:6.1f} kPa  "
              f"T {s.t_in_C:5.2f}→{s.t_out_C:5.2f} °C")

    print()
    print("=" * 78)
    print("TEIL C – UWP1 abgeschaltet (Rückschlagventil zu), Pumpen Δp = const")
    print("=" * 78)
    r_aus = build(uwp1_on=False).solve()
    report_case(r_aus, "Ohne Trinkwassererwärmung")
    t_vl_aus = r_aus["wa"].t_in_C
    print()
    print(f"  → Vorlauftemperatur WA: {t_vl_nenn:.2f} °C → {t_vl_aus:.2f} °C   "
          f"(Änderung {t_vl_aus - t_vl_nenn:+.2f} K)")
