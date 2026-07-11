"""Wirkung der Ventilautorität auf die installierte Ventilkennlinie.

Die Ventilautorität a_V = Δp_V100 / Δp_gesamt beschreibt, welcher Anteil des
(konstanten) Kreis-Differenzdrucks bei voll geöffnetem Ventil über dem Ventil
abfällt. Bei kleiner Autorität dominiert der Netzwiderstand: schließt das
Ventil, steigt sein Δp stark an und der Volumenstrom sinkt viel langsamer,
als die inhärente Kennlinie es verspricht — die installierte Kennlinie
verzerrt Richtung „Schnellöffnung", der Regelkreis wird im oberen Hubbereich
wirkungslos und im unteren nervös. Faustregel der Praxis: a_V ≥ 0.5.

Aufbau je Autorität (gleicher Auslegungspunkt V̇₁₀₀ = 1 m³/h bei Δp = 40 kPa):
    Zulauf (190 kPa ü) → Regelventil (Kvs aus a_V) → Netzwiderstand
    (C = (1−a_V)·Δp/V̇²) → Austritt (150 kPa ü)

    python3 examples/08_ventilautoritaet.py
    → examples/ventilautoritaet_kennlinien.png + Tabelle auf der Konsole
"""
from pathlib import Path

import hydraulik as h

DP_TOTAL_KPA = 40.0          # konstanter Differenzdruck über Ventil + Netz
Q_100 = 1.0                  # Auslegungsvolumenstrom [m³/h] bei H = 1
AUTORITAETEN = (0.1, 0.3, 0.5, 0.9)
HUB = [i / 20 for i in range(21)]


def kvs_for(authority: float, rho: float) -> float:
    """Kvs so, dass bei V̇₁₀₀ genau a_V·Δp über dem Ventil abfällt."""
    dp_v100 = authority * DP_TOTAL_KPA * 1000.0
    return Q_100 * (1e5 * rho / 1000.0 / dp_v100) ** 0.5


def circuit(authority: float, opening: float, characteristic: str, rho: float) -> dict:
    dp_netz = (1.0 - authority) * DP_TOTAL_KPA * 1000.0
    comps = {
        "zu": {"type": "inflow", "t_set_C": 70, "p_kPa": 150 + DP_TOTAL_KPA},
        "rv": {"type": "control_valve", "kvs_m3h": kvs_for(authority, rho),
               "opening": opening, "characteristic": characteristic},
        "ab": {"type": "outflow", "p_kPa": 150},
    }
    conns = [["zu.port", "rv.in"]]
    if dp_netz > 0.0:
        comps["netz"] = {"type": "flow_resistance", "c_Pa_m3h2": dp_netz / Q_100 ** 2}
        conns += [["rv.out", "netz.in"], ["netz.out", "ab.port"]]
    else:
        conns += [["rv.out", "ab.port"]]
    return {"components": comps, "connections": conns}


def sweep(characteristic: str, rho: float) -> dict[float, list[float]]:
    curves: dict[float, list[float]] = {}
    for a in AUTORITAETEN:
        q100 = h.load(circuit(a, 1.0, characteristic, rho)).solve(thermal=False)["rv"].q_m3h
        curves[a] = []
        for hub in HUB:
            r = h.load(circuit(a, hub, characteristic, rho)).solve(thermal=False)
            curves[a].append(r["rv"].q_m3h / q100)
        # Kontrolle: Autorität aus dem Solverergebnis = Auslegungswert
        r100 = h.load(circuit(a, 1.0, characteristic, rho)).solve(thermal=False)
        a_ist = abs(r100["rv"].dp_kPa) / DP_TOTAL_KPA
        assert abs(a_ist - a) < 1e-3, (a, a_ist)
    return curves


def main() -> None:
    rho = h.water_at(50.0).rho
    lin = sweep("linear", rho)
    glp = sweep("equal_percentage", rho)

    print("Installierte Kennlinie V̇/V̇₁₀₀ bei Hub H (lineares Ventil):")
    print("  H     " + "   ".join(f"a_V={a:.1f}" for a in AUTORITAETEN) + "   (inhärent: V̇/V̇₁₀₀ = H)")
    for k, hub in enumerate(HUB):
        if k % 4:
            continue
        print(f"  {hub:4.2f}  " + "   ".join(f"{lin[a][k]:6.3f}" for a in AUTORITAETEN))
    print("\nKenngröße der Verzerrung — Volumenstrom bei Halbhub (linear):")
    for a in AUTORITAETEN:
        print(f"  a_V = {a:.1f}:  V̇(H=0.5)/V̇₁₀₀ = {lin[a][10]:.3f}"
              f"   (ideal 0.500, Überströmung +{(lin[a][10] - 0.5) * 100:.0f} %-Punkte)")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("\nmatplotlib nicht installiert – Plot übersprungen.")
        return
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.6), sharey=True)
    for ax, curves, title, ideal in (
            (axes[0], lin, "lineares Ventil", HUB),
            (axes[1], glp, "gleichprozentiges Ventil (R = 100)",
             [100.0 ** (x - 1.0) if x > 0 else 0.0 for x in HUB])):
        for a in AUTORITAETEN:
            ax.plot(HUB, curves[a], marker=".", label=f"a_V = {a:.1f}")
        ax.plot(HUB, ideal, "k--", lw=1, label="inhärente Kennlinie")
        ax.set_title(title)
        ax.set_xlabel("Ventilhub H [-]")
        ax.grid(alpha=0.3)
    axes[0].set_ylabel("V̇/V̇₁₀₀ [-]")
    axes[0].legend()
    fig.suptitle("Installierte Kennlinien bei konstantem Kreis-Δp = 40 kPa "
                 f"(Auslegung {Q_100:.0f} m³/h)")
    fig.tight_layout()
    out = Path(__file__).parent / "ventilautoritaet_kennlinien.png"
    fig.savefig(out, dpi=150)
    print(f"\nPlot: {out}")


if __name__ == "__main__":
    main()
