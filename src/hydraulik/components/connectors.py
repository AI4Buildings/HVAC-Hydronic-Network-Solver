"""Topologie-Hilfskomponenten."""
from __future__ import annotations

from ..fluids import Fluid
from ..params import Param
from .base import EdgeCoefficients, TwoPortComponent
from .registry import register


@register("link")
class Link(TwoPortComponent):
    """Widerstandsfreie Verbindung (Δp ≈ 0), die zwei Knoten thermisch trennt.

    Verwendung: mehrere Anschlüsse ENTLANG einer Leitung (Rücklaufsammler,
    Verteilerbalken, Äste, deren Widerstand bereits in konzentrierten
    C-Werten anderer Teilstrecken steckt). Eine gewöhnliche Verbindung
    verschmilzt Ports zu EINEM Mischknoten – jede Zapfstelle sähe die
    Mischtemperatur ALLER Zuströme, auch stromab eingemischter. Der link
    erzwingt getrennte Knoten mit gerichteter Kante, sodass der konvektive
    Transport der Reihenfolge stromab folgt.

    Hydraulisch quasi-ideal: interner Referenzwiderstand von 1 Pa beim
    Nennvolumenstrom q_nom. Exakt 0 ist nicht möglich, da die Kanten-
    impulsgleichung Δp = a·Q + b·Q·|Q| mit a = b = 0 entartet (Q wäre aus
    der Kante nicht bestimmbar).

    Hinweis: Oft ist der link vermeidbar — konzentrierte Teilstrecken-
    Widerstände hälftig auf Vor- und Rücklauf aufteilen (C/2 je Richtung),
    dann sind die Rücklaufäste ohnehin eigene Kanten (siehe README,
    Modellierungsrichtlinie).
    """

    q_nom: float

    PARAMS = (
        Param("q_nom", "flow", default=10.0 / 3600.0, minv=1e-7,
              help="Nennvolumenstrom; dort beträgt der interne Referenz-Druckverlust 1 Pa"),
    )

    def hydraulic_coefficients(self, q: float, fluid: Fluid) -> EdgeCoefficients:
        return EdgeCoefficients(b=1.0 / self.q_nom ** 2)
