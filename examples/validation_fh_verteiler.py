"""Validierung gegen die FH-Burgenland-Übung „Modellierung Verteiler" (06.11.2021).

Reproduziert die Volumenstromkennlinien der Lösungsplots (Aufgaben 2 und 3)
und legt die aus dem PDF abgelesenen Ankerpunkte darüber:

    python3 examples/validation_fh_verteiler.py
    → examples/validierung_aufgabe2.png, examples/validierung_aufgabe3.png

Alle extrahierten Referenzwerte (exakt gedruckte Zahlen und aus den Plots
abgelesene Ankerpunkte inkl. Ablesegenauigkeit) sind dokumentiert in
examples/referenzwerte_fh_verteiler.txt.

Randbedingungen wie im Excel-Referenzmodell: ideale Δp-Pumpen, Kv-Formel
ohne Dichtekorrektur (ρ = 1000). Kennlinien sind rein hydraulisch
(thermal=False; im Fall „Einspritzventil zu" existiert wegen der fest
vorgegebenen Leistung im isolierten Sekundärkreis keine stationäre
Temperaturlösung).
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import hydraulik as h

EXAMPLE = Path(__file__).parent / "06_umlenk_einspritz_flow_resistance.yaml"
TS = [f"ts{i}" for i in range(1, 9)]

# Kategoriale Farbzuordnung (validierte Palette, feste Reihenfolge)
COLORS = {"ts1": "#2a78d6", "ts2": "#eb6834", "ts3": "#52514e", "ts4": "#eda100",
          "ts5": "#1baf7a", "ts6": "#008300", "ts7": "#4a3aa7", "ts8": "#e34948"}
TEXT, MUTED = "#0b0b0b", "#52514e"


def solve(mv=1.0, dv=1.0):
    net = h.load(EXAMPLE)
    net.fluid = h.Fluid("rho1000", rho=1000.0, mu=0.6e-3, cp=4180.0)
    for pu in ("pu_haupt", "pu_sek"):
        net.components[pu].dp_internal_frac = 1e-4
    net.components["mv1"].opening = mv
    net.components["dv1"].opening = dv
    return net.solve(thermal=False)


def sweep(valve: str) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    hub = np.linspace(0.0, 1.0, 21)
    flows = {ts: [] for ts in TS}
    for x in hub:
        r = solve(mv=x, dv=1.0) if valve == "mv1" else solve(mv=1.0, dv=x)
        for ts in TS:
            flows[ts].append(abs(r[ts].q_m3h))
    return hub * 100.0, {ts: np.asarray(v) for ts, v in flows.items()}


def styled_axes(ax, title):
    ax.set_title(title, color=TEXT, fontsize=11, loc="left")
    ax.set_xlabel("Ventilhub H [%]", color=MUTED)
    ax.set_ylabel(r"$\dot V_i(H)\,/\,\dot V_{i,\mathrm{norm}}$", color=MUTED)
    ax.grid(True, color="#e6e5e1", linewidth=0.8)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#c3c2b7")
    ax.tick_params(colors=MUTED)
    ax.set_xlim(0, 100)


def plot_group(ax, hub, curves, ref_points):
    for name, (values, label, label_h, dy) in curves.items():
        c = COLORS[name]
        ax.plot(hub, values, color=c, linewidth=2, marker="o", markersize=3.5)
        k = int(np.argmin(np.abs(hub - label_h)))
        ax.annotate(label, (hub[k], values[k]), xytext=(0, dy),
                    textcoords="offset points", color=c, fontsize=9,
                    ha="center", va="bottom" if dy > 0 else "top")
    if ref_points:
        hx, hy = zip(*ref_points)
        ax.scatter(hx, hy, marker="x", s=60, color=TEXT, zorder=5,
                   label="Referenz (aus Lösungsplots abgelesen)")
        ax.legend(loc="lower right", frameon=False, fontsize=8, labelcolor=MUTED)


def main():
    out_dir = Path(__file__).parent

    # ---- Aufgabe 2: ULS-Ventil 100→0 %, ESS Volllast --------------------
    hub, f = sweep("mv1")
    base = {ts: f[ts][-1] for ts in TS}           # H = 100 %
    v3_0 = f["ts3"][0]                            # V3 normiert auf H = 0 (V3(100) = 0)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.6), facecolor="white")
    plot_group(ax1, hub, {
        "ts2": (f["ts2"] / base["ts2"], "TS2 (Summentor)", 50, 8),
        "ts3": (f["ts3"] / v3_0, "TS3* (Bypass)", 75, 10),
        "ts4": (f["ts4"] / base["ts4"], "TS4 (Regeltor)", 75, -12),
    }, ref_points=[(50, 1.21), (50, 0.61), (10, 0.90), (10, 0.19)])
    styled_axes(ax1, "Umlenkschaltung: Regeltor (TS4), Bypass (TS3*), Summentor (TS2)")
    plot_group(ax2, hub, {
        "ts1": (f["ts1"] / base["ts1"], "TS1", 30, 8),
        "ts5": (f["ts5"] / base["ts5"], "TS5 = TS6", 25, -14),
        "ts6": (f["ts6"] / base["ts6"], "", 50, -12),
        "ts7": (f["ts7"] / base["ts7"], "TS7", 30, 8),
        "ts8": (f["ts8"] / base["ts8"], "TS8", 15, 8),
    }, ref_points=[(50, 1.19), (50, 0.89)])
    styled_axes(ax2, "Rückwirkung auf Hauptverteilung und Einspritzkreis")
    fig.suptitle("Aufgabe 2 – Regelventil Umlenkschaltung schließt (ESS Volllast)   "
                 "* TS3 normiert auf H=0", color=TEXT, fontsize=12, x=0.02, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(out_dir / "validierung_aufgabe2.png", dpi=150)

    # ---- Aufgabe 3: ESS-Ventil 100→0 %, ULS Volllast --------------------
    hub, f = sweep("dv1")
    base = {ts: f[ts][-1] for ts in TS}
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4.6), facecolor="white")
    plot_group(ax1, hub, {
        "ts2": (f["ts2"] / base["ts2"], "TS2 = TS4", 30, 8),
        "ts4": (f["ts4"] / base["ts4"], "", 50, 8),
    }, ref_points=[(0, 1.40)])
    styled_axes(ax1, "Umlenkschaltung (TS3 = 0: Bypass dicht bei ULS-Volllast)")
    plot_group(ax2, hub, {
        "ts1": (f["ts1"] / base["ts1"], "TS1", 25, -14),
        "ts5": (f["ts5"] / base["ts5"], "TS5 = TS6", 60, -14),
        "ts6": (f["ts6"] / base["ts6"], "", 50, -12),
        "ts7": (f["ts7"] / base["ts7"], "TS7", 20, 10),
        "ts8": (f["ts8"] / base["ts8"], "TS8", 40, 8),
    }, ref_points=[(0, 2.70), (0, 0.0)])
    styled_axes(ax2, "Hauptverteilung und Einspritzkreis")
    fig.suptitle("Aufgabe 3 – Regelventil Einspritzschaltung schließt (ULS Volllast)",
                 color=TEXT, fontsize=12, x=0.02, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(out_dir / "validierung_aufgabe3.png", dpi=150)

    # ---- Aufgabe 1 & 5 als Tabelle --------------------------------------
    r = solve()
    ref = {"ts1": 1.472, "ts2": 0.7361, "ts3": 0.0, "ts4": 0.7361,
           "ts5": 0.7363, "ts6": 0.7363, "ts7": 0.4313, "ts8": 1.168}
    print("Aufgabe 1 – Volllast:      Solver   Referenz")
    for ts in TS:
        print(f"  {ts.upper()}: {abs(r[ts].q_m3h):10.4f} {ref[ts]:10.4f} m³/h")
    dp_uls = r["ts4"].dp_kPa + r["ea1"].dp_kPa + r["mv1:a"].dp_kPa
    dp_ess = r["ts5"].dp_kPa + r["ts6"].dp_kPa + r["dv1"].dp_kPa
    print(f"Aufgabe 5 – Ventilautorität: a_ULS = {r['mv1:a'].dp_kPa/dp_uls:.4f} (Ref 0.2381), "
          f"a_ESS = {r['dv1'].dp_kPa/dp_ess:.4f} (Ref 0.1938)")
    print(f"Plots: {out_dir/'validierung_aufgabe2.png'}, {out_dir/'validierung_aufgabe3.png'}")


if __name__ == "__main__":
    main()
