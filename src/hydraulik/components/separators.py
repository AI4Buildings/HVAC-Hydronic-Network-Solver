"""Hydraulische Weiche, Verteiler, Abzweiger (T-Stück)."""
from __future__ import annotations

from ..fluids import Fluid
from ..params import Param
from .base import Component, EdgeCoefficients, NetworkBuilder
from .registry import register


@register("hydraulic_separator")
class HydraulicSeparator(Component):
    """Hydraulische Weiche als Zwei-Knoten-Modell.

    Ports: prim_in, sec_out (oben) – sec_in, prim_out (unten), verbunden durch
    eine vertikale Niederwiderstandskante. Damit entsteht das reale Verhalten
    automatisch aus der idealen Knotenmischung:
    Sekundärstrom > Primärstrom → Rücklaufwasser strömt nach oben und senkt
    die Sekundär-Vorlauftemperatur; umgekehrt kurzschließt Überschusswasser
    nach unten.
    """

    q_nom: float
    dp_nom: float
    ua: float
    t_amb: float

    PARAMS = (
        Param("q_nom", "flow", default=2.0 / 3600.0, minv=1e-6,
              help="Nennvolumenstrom zur Dimensionierung der vertikalen Kante"),
        Param("dp_nom", "pressure", default=100.0, minv=1.0,
              help="Druckverlust der vertikalen Strecke bei q_nom (Default 100 Pa)"),
        Param("ua", "ua", default=0.0, minv=0.0, help="Wärmeverlust an Aufstellraum"),
        Param("t_amb", "temperature", default=20.0),
    )

    def port_names(self) -> tuple[str, ...]:
        return ("prim_in", "prim_out", "sec_in", "sec_out")

    def result_notices(self, q: float, fluid: Fluid) -> list[str]:
        """Hinweis, wenn der Default-Nennpunkt (100 Pa bei 2 m³/h) bei großer
        Querströmung einen spürbaren Druckverlust ergibt — die Weiche
        entkoppelt dann Primär- und Sekundärkreis nicht mehr."""
        dp = self.dp_nom * (q / self.q_nom) ** 2
        if "q_nom" in self.given or dp <= 1e3:
            return []
        return [f"Weiche '{self.name}': Druckverlust der vertikalen Strecke {dp / 1e3:.1f} kPa "
                f"bei V̇ = {abs(q) * 3600:.2f} m³/h (Default-Nennpunkt {self.dp_nom:g} Pa bei "
                f"q_nom = {self.q_nom * 3600:g} m³/h) — die hydraulische Entkopplung ist "
                f"gestört. q_nom_m3h auf den Auslegungsvolumenstrom der Weiche setzen."]

    def _vertical_coeff(self, q: float, fluid: Fluid) -> EdgeCoefficients:
        return EdgeCoefficients(b=self.dp_nom / self.q_nom ** 2)

    def build(self, b: NetworkBuilder) -> None:
        top = b.port("prim_in")
        b.alias(top, b.port("sec_out"))
        bottom = b.port("sec_in")
        b.alias(bottom, b.port("prim_out"))
        b.edge(top, bottom, self._vertical_coeff, label="vertikal")
        if self.ua > 0.0:
            b.node_heat_loss(top, self.ua / 2.0, self.t_amb)
            b.node_heat_loss(bottom, self.ua / 2.0, self.t_amb)


@register("manifold")
class Manifold(Component):
    """Verteiler/Sammler: Hauptanschluss + n Strang-Anschlüsse, ein Mischknoten."""

    n_ports: int

    PARAMS = (
        Param("n_ports", "int", required=True, minv=1, maxv=24,
              help="Anzahl Strang-Anschlüsse s1…sN (zusätzlich zu 'main')"),
    )

    def port_names(self) -> tuple[str, ...]:
        return ("main",) + tuple(f"s{i+1}" for i in range(self.n_ports))

    def build(self, b: NetworkBuilder) -> None:
        main = b.port("main")
        for pn in self.port_names()[1:]:
            b.alias(main, b.port(pn))


@register("tee")
class Tee(Component):
    """Abzweiger (T-Stück 90°): a—b gerader Strang, c Abzweig.

    Ohne Durchmesserangabe: idealer Mischknoten (Default, wie bisher).
    Mit d_run + d_branch: Druckverlust nach Idelchik (Diagramme 7-10/7-21,
    Vereinigung UND Trennung, ζ = f(Q_s/Q_c, F_s/F_c), Totaldruckbezug auf
    den kombinierten Strang) — die Regime-Erkennung (welcher Strang führt
    den Gesamtstrom; Sammlung oder Verteilung) folgt in jeder Iteration den
    aktuellen Volumenströmen. Abszissen wie im Buch: Seitenpfad Q_s/Q_c,
    gerader Pfad bei Trennung Q_st/Q_c, bei Vereinigung Q_s/Q_c.

    Totaldruck im Netz: Je Pfad wirkt der Idelchik-TOTALDRUCKverlust
    ζ·ρw_c²/2 — wie bei allen anderen Bauteilen, die ihren Druckabfall als
    Totaldruckverlust rechnen und an Knoten keine Geschwindigkeit kennen. Die
    frühere Bernoulli-Umrechnung auf statische Knotendrücke war die einzige
    im Netz: der Zulaufschenkel bekam seine kinetische Energie geschenkt,
    die Kennlinie wurde mehrwertig (Solver-Prüfung 2026-10: 32 von 37
    mehrdeutigen Zufallsnetzen nur dadurch). Der statische Druck an jedem
    Anschluss (p_Knoten − ρw_Schenkel²/2, z.B. der Druckrückgewinn im
    geraden Auslauf) ist Ergebnis: extras "p_static_port_kPa", "v_m_s".
    Der kombinierte Strang selbst ist
    verlustfrei — die Pfadbeiwerte liegen auf Abzweig- und Durchgangskante.
    Führt der ABZWEIG den Gesamtstrom (Hosenrohr-Konfiguration), werden
    beide geraden Äste näherungsweise als Seitenpfade behandelt.

    Regimewechsel: Jeder Wechsel (Sammlung ↔ Trennung, kombinierter Strang
    wechselt) liegt bei Schenkelstrom 0, also unterhalb der Tabellengrenze
    x = 0.1 dieses Schenkels. Dort sind die Tabellenwerte beider Regime
    verschieden — die Kennlinie spränge, und zwischen den Sprungwerten gäbe
    es keine Lösung. Unterhalb von x = 0.1 wird daher linear im
    vorzeichenbehafteten Schenkelstrom zwischen den beiden angrenzenden
    Regimen (je an ihrer Tabellengrenze) interpoliert: die Kennlinie ist
    stetig, und die Tabellen werden nur im Buchbereich [0.1, 1] ausgewertet.

    Numerik: Jede Kante meldet ihre lokale Tangente (Linearterm a = dS/dQ,
    Eigenstrom gestört, Ausgleich über den größten anderen Schenkel; mind.
    ¼ der Sekante) und die nachgeführte Quelle a·Q − S — Newton bleibt auch
    bei steilem ζ(x), Druckgewinnen (Injektorwirkung, negatives ζ) und
    S(0) ≠ 0 im Überblendbereich konsistent, J > 0 hält die Druckkorrektur-
    Matrix SPD.
    """

    d_run: float | None
    d_branch: float | None

    PARAMS = (
        Param("d_run", "diameter", minv=0.003,
              help="Innendurchmesser gerader Strang a–b (mit d_branch: Idelchik-Druckverlust)"),
        Param("d_branch", "diameter", minv=0.003,
              help="Innendurchmesser Abzweig c (≤ d_run; Tabellenbereich F_s/F_c ≤ 1)"),
    )

    #: quasi-ideale Restkante (1 Pa bei 10 m³/h, link-Konvention)
    _B_IDLE = 1.0 / (10.0 / 3600.0) ** 2
    #: Tabellengrenze x = Q/Q_c; darunter Überblendung zwischen den Regimen
    _X_BLEND = 0.1

    def check_params(self):
        if (self.d_run is None) != (self.d_branch is None):
            return ["Idelchik-Druckverlust: 'd_run_mm' und 'd_branch_mm' gemeinsam angeben "
                    "(oder beide weglassen → idealer Knoten)."]
        if self.d_run is not None and self.d_branch > self.d_run:
            return ["Idelchik-Tabellenbereich: d_branch ≤ d_run (F_s/F_c ≤ 1)."]
        return None

    def port_names(self) -> tuple[str, ...]:
        return ("a", "b", "c")

    def nonmonotone_hydraulics(self) -> bool:
        """Idelchik-Kennlinie ist über den Regimewechsel nicht umkehrbar
        eindeutig (negative ζ der Vereinigung, U-förmige Durchgangstabelle):
        dieselben Portdrücke lassen verschiedene Strömungsbilder zu (Solver-
        Prüfung 2026-10: 59 von 310 Zufallsnetzen mit mehreren, jeweils
        dynamisch stabilen Lösungen)."""
        return self.d_run is not None

    def check_topology(self, port_nodes):
        """Mit Idelchik-Druckverlust dürfen keine zwei Schenkel am selben Knoten
        liegen: das T-Stück ist dann kurzgeschlossen, die Aufteilung über die
        beiden Schenkel bestimmt allein die Tabellenkennlinie (oft nicht
        eindeutig) — praktisch immer ein Zeichenfehler."""
        if self.d_run is None:
            return None
        errors = []
        names = [pn for pn in ("a", "b", "c") if pn in port_nodes]
        folge = ("die Aufteilung über die beiden Schenkel bestimmt dann allein die "
                 "Idelchik-Kennlinie (d_run/d_branch), meist mit Zirkulation durch das "
                 "T-Stück und oft nicht eindeutig; die Tabellen gelten für ungestörte "
                 "Leitungen hinter dem T-Stück, nicht für sofort wieder zusammengeführte "
                 "Abgänge")
        for i, p1 in enumerate(names):
            for p2 in names[i + 1:]:
                n1, lab1, pt1, via = port_nodes[p1][:4]
                n2, lab2, pt2, _ = port_nodes[p2][:4]
                if n1 == n2:
                    errors.append(
                        f"T-Stück '{self.name}': Schenkel '{p1}' und '{p2}' liegen am selben "
                        f"Knoten ('{lab1}') – das T-Stück ist kurzgeschlossen; {folge}. "
                        f"Abhilfe: Verbindungen von '{self.name}.{p1}' und "
                        f"'{self.name}.{p2}' prüfen oder d_run_mm/d_branch_mm weglassen "
                        f"(idealer Knoten).")
                elif pt1 == pt2:
                    ueber = ", ".join(f"'{v}'" for v in via[:4]) + (
                        f" u.a. ({len(via)} insgesamt)" if len(via) > 4 else "")
                    errors.append(
                        f"T-Stück '{self.name}': Schenkel '{p1}' ('{lab1}') und '{p2}' "
                        f"('{lab2}') sind nur über widerstandsfreie Verbindungen ({ueber}: "
                        f"ideale Verbindungsleitung, link, Kugelhahn ohne Kvs oder "
                        f"Volumenstromsensor) verbunden – das T-Stück ist kurzgeschlossen; "
                        f"{folge}. Abhilfe: Verbindungen prüfen, der Leitung dazwischen "
                        f"einen Widerstand geben (Rohrmodell, C-Wert) oder d_run_mm/"
                        f"d_branch_mm weglassen (idealer Knoten).")
        return errors

    def port_flow_area(self, port: str) -> float | None:
        if self.d_run is None or port not in ("a", "b", "c"):
            return None
        return self._areas()["abc".index(port)]

    def edge_result_extras(self, label: str, q: float, p_from: float, p_to: float,
                           fluid: Fluid) -> dict | None:
        """Ergebnis je Schenkel (Kante Anschluss → Knoten): Geschwindigkeit und
        statischer Druck am Anschluss p_Knoten − ρw²/2 (Netz rechnet mit
        Totaldruck)."""
        if self.d_run is None or label not in ("a", "b", "c"):
            return None
        w = abs(q) / self._areas()["abc".index(label)]
        return {"v_m_s": w, "p_static_port_kPa": (p_from - fluid.rho * w * w / 2.0) / 1e3}

    def pre_coefficients(self, q_edges: list[float], fluid: Fluid) -> None:
        """Solver-Hook: aktuelle Flüsse der eigenen Kanten (a, b, c → Knoten)."""
        self._q_legs = list(q_edges)

    def _leg_coeff(self, k: int):
        def fn(q_own: float, fluid: Fluid) -> EdgeCoefficients:
            return self._leg_coefficients(k, q_own, fluid)
        return fn

    def _areas(self) -> tuple[float, float, float]:
        import math as _m
        f_run = _m.pi * self.d_run ** 2 / 4.0
        return (f_run, f_run, _m.pi * self.d_branch ** 2 / 4.0)

    def _regime_pressures(self, q: list[float], rho: float) -> list[float]:
        """S_i = p_Port,i − p_Knoten nach Idelchik im Regime des Zustands q
        (Totaldruck; Knoten = Druck des kombinierten Strangs, dort S = 0)."""
        from . import idelchik
        absq = [abs(v) for v in q]
        q_c = max(absq)
        comb = absq.index(q_c)
        areas = self._areas()
        converging = q[comb] < 0.0                    # Gesamtstrom verlässt den Knoten
        S = [0.0, 0.0, 0.0]
        for k in range(3):
            if k == comb:
                continue
            if comb == 2:                             # Abzweig führt den Gesamtstrom
                zeta = idelchik.zeta_side(absq[k] / q_c, min(areas[k] / areas[2], 1.0),
                                          converging)
            else:
                x = absq[2] / q_c
                if k == 2:
                    zeta = idelchik.zeta_side(x, areas[2] / areas[0], converging)
                else:                                 # Abszisse Trennung: Q_st/Q_c
                    zeta = idelchik.zeta_straight(x if converging else absq[k] / q_c,
                                                  converging)
            w_c = q_c / areas[comb]
            # Totaldruckabfall entlang der Strömung (ζ auf den kombinierten Strang)
            drop = zeta * rho * w_c * w_c / 2.0
            S[k] = drop if converging else -drop
        return S

    def _pressures(self, q: list[float], rho: float) -> tuple[list[float], dict | None]:
        """S je Schenkel, stetig über die Regimewechsel; zweiter Wert: Daten
        der Überblendung (kleinster Schenkel k, Grenzstrom d, Steigung) oder None."""
        absq = [abs(v) for v in q]
        k = absq.index(min(absq))
        if absq[k] >= self._X_BLEND * max(absq):
            return self._regime_pressures(q, rho), None
        i, j = [m for m in range(3) if m != k]
        if q[i] < 0.0:
            i, j = j, i                               # i: Durchgang hinein, j: hinaus
        through = (q[i] - q[j]) / 2.0
        d = self._X_BLEND * through / (1.0 - self._X_BLEND / 2.0)   # x_k = 0.1 genau
        q_in, q_out = [0.0] * 3, [0.0] * 3            # Schenkel k zu- bzw. abströmend
        q_in[k], q_in[i], q_in[j] = d, through - d / 2.0, -through - d / 2.0
        q_out[k], q_out[i], q_out[j] = -d, through + d / 2.0, -through + d / 2.0
        s_in = self._regime_pressures(q_in, rho)
        s_out = self._regime_pressures(q_out, rho)
        w = (q[k] + d) / (2.0 * d)
        S = [(1.0 - w) * lo + w * hi for lo, hi in zip(s_out, s_in)]
        return S, {"k": k, "d": d, "slope": (s_in[k] - s_out[k]) / (2.0 * d)}

    def _leg_coefficients(self, k: int, q_own: float, fluid: Fluid) -> EdgeCoefficients:
        q = list(getattr(self, "_q_legs", None) or [0.0, 0.0, 0.0])
        if q_own != q[k]:
            # gestörter Eigenstrom (Differenzenquotient des Solvers): die
            # Kontinuität gleicht der größte andere Schenkel aus
            m = max((x for x in range(3) if x != k), key=lambda x: abs(q[x]))
            q[m] -= q_own - q[k]
            q[k] = q_own
        if max(abs(v) for v in q) < 1e-9:
            return EdgeCoefficients(b=self._B_IDLE)   # T-Stück in Ruhe
        rho = fluid.rho
        S, blend = self._pressures(q, rho)
        qk = q[k]
        if blend is not None and blend["k"] == k:
            # kleiner Schenkel im Überblendbereich: S(0) ≠ 0 — Linearform mit
            # der Steigung der Überblendung (q|q|-Formen hätten dort Steigung
            # 0 bzw. ∞) plus Mindeststeifigkeit eines Staudrucks bei x = 0.1
            b_ref = rho / (2.0 * min(self._areas()) ** 2)
            a = abs(blend["slope"]) + 2.0 * b_ref * blend["d"]
            return EdgeCoefficients(a=a, dp_source=a * qk - S[k])
        # Tangente dS/dQ (Ausgleich über den größten anderen Schenkel).
        # Schrittweite und Mindeststeifigkeit relativ zum größten Schenkelstrom:
        # während der Iteration können ZWEI Schenkel exakt 0 führen (Kontinuität
        # am T-Stück-Knoten erst bei Konvergenz erfüllt) — die Überblendung
        # erfasst nur einen, der zweite hätte sonst h = 0
        q_ref = max(abs(v) for v in q)
        h = 1e-6 * max(abs(qk), 1e-6 * q_ref)
        q_h = list(q)
        m = max((x for x in range(3) if x != k), key=lambda x: abs(q[x]))
        q_h[k] += h
        q_h[m] -= h
        slope = (self._pressures(q_h, rho)[0][k] - S[k]) / h
        sec = abs(S[k] / qk) if qk != 0.0 else 0.0
        a = max(slope, 0.25 * sec, 2.0 * self._B_IDLE * max(abs(qk), 1e-3 * q_ref))
        return EdgeCoefficients(a=a, dp_source=a * qk - S[k])

    def build(self, b: NetworkBuilder) -> None:
        if self.d_run is None:
            a = b.port("a")
            b.alias(a, b.port("b"))
            b.alias(a, b.port("c"))
            return
        j = b.internal("j")
        for k, port in enumerate(("a", "b", "c")):
            b.edge(b.port(port), j, self._leg_coeff(k), label=port)
