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
    aktuellen Volumenströmen. Die statische Druckdifferenz je Pfad enthält
    die Bernoulli-Umrechnung (p = p_t − ρw²/2 je Strang). Druck-GEWINNE
    (negative ζ_c.s der Vereinigung, Injektorwirkung) werden explizit als
    nachgeführte Druckquelle behandelt, damit die Druckkorrektur-Matrix
    SPD bleibt. Der kombinierte Strang selbst ist verlustfrei — die beiden
    Pfadbeiwerte liegen vollständig auf Abzweig- und Durchgangskante.
    Führt der ABZWEIG den Gesamtstrom (Hosenrohr-Konfiguration), werden
    beide geraden Äste näherungsweise als Seitenpfade behandelt.
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

    def check_params(self):
        if (self.d_run is None) != (self.d_branch is None):
            return ["Idelchik-Druckverlust: 'd_run_mm' und 'd_branch_mm' gemeinsam angeben "
                    "(oder beide weglassen → idealer Knoten)."]
        if self.d_run is not None and self.d_branch > self.d_run:
            return ["Idelchik-Tabellenbereich: d_branch ≤ d_run (F_s/F_c ≤ 1)."]
        return None

    def port_names(self) -> tuple[str, ...]:
        return ("a", "b", "c")

    def pre_coefficients(self, q_edges: list[float], fluid: Fluid) -> None:
        """Solver-Hook: aktuelle Flüsse der eigenen Kanten (a, b, c → Knoten)."""
        self._q_legs = list(q_edges)

    def _leg_coeff(self, k: int):
        def fn(q_own: float, fluid: Fluid) -> EdgeCoefficients:
            return self._leg_coefficients(k, q_own, fluid)
        return fn

    def _leg_coefficients(self, k: int, q_own: float, fluid: Fluid) -> EdgeCoefficients:
        import math as _m

        from . import idelchik
        q_legs = getattr(self, "_q_legs", None) or [0.0, 0.0, 0.0]
        absq = [abs(v) for v in q_legs]
        q_c = max(absq)
        comb = absq.index(q_c)
        if q_c < 1e-9 or k == comb or absq[k] < 1e-12:
            return EdgeCoefficients(b=self._B_IDLE)   # Ruhe / kombinierter Strang
        f_run = _m.pi * self.d_run ** 2 / 4.0
        f_branch = _m.pi * self.d_branch ** 2 / 4.0
        areas = (f_run, f_run, f_branch)
        converging = q_legs[comb] < 0.0               # Gesamtstrom verlässt den Knoten
        if comb == 2:                                 # Abzweig führt den Gesamtstrom
            x = absq[k] / q_c
            zeta = idelchik.zeta_side(x, min(areas[k] / areas[2], 1.0), converging)
        else:
            x = absq[2] / q_c
            r_a = f_branch / f_run
            zeta = (idelchik.zeta_side(x, r_a, converging) if k == 2
                    else idelchik.zeta_straight(x, converging))
        rho = fluid.rho
        w_c = q_c / areas[comb]
        w_k = absq[k] / areas[k]
        dp_total = zeta * rho * w_c * w_c / 2.0       # Totaldruckverlust des Pfads
        w_in, w_out = (w_k, w_c) if converging else (w_c, w_k)
        # statischer Abfall entlang der Strömung: p_in − p_out = Δp_t + ρ(w_out² − w_in²)/2
        drop = dp_total + rho * (w_out * w_out - w_in * w_in) / 2.0
        target = drop if converging else -drop        # S = p_Port − p_Knoten am Arbeitspunkt
        if target * q_legs[k] > 0.0:                  # widerstandsartig → quadratischer Koeffizient
            return EdgeCoefficients(b=target / (q_legs[k] * absq[k]))
        # Druckgewinn entlang der Strömung: explizit nachgeführt (SPD bleibt)
        return EdgeCoefficients(b=self._B_IDLE,
                                dp_source=self._B_IDLE * q_legs[k] * absq[k] - target)

    def build(self, b: NetworkBuilder) -> None:
        if self.d_run is None:
            a = b.port("a")
            b.alias(a, b.port("b"))
            b.alias(a, b.port("c"))
            return
        j = b.internal("j")
        for k, port in enumerate(("a", "b", "c")):
            b.edge(b.port(port), j, self._leg_coeff(k), label=port)
