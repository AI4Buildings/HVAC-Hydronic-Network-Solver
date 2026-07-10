"""Fehlerklassen des Pakets."""
from __future__ import annotations


class HydraulikError(Exception):
    """Basisklasse aller Paketfehler."""


class ComponentParamError(HydraulikError):
    """Ungültige Parameter einer einzelnen Komponente."""

    def __init__(self, component: str, messages: list[str]):
        self.component = component
        self.messages = list(messages)
        body = "\n".join(f"  {i+1}. {m}" for i, m in enumerate(self.messages))
        super().__init__(f"Ungültige Parameter für Komponente '{component}':\n{body}")


class NetworkValidationError(HydraulikError):
    """Sammelt ALLE Validierungsfehler eines Netzes / einer YAML-Datei.

    Absichtlich als nummerierte Liste formatiert, damit ein LLM (oder Mensch)
    die gesamte Datei in einem Durchgang korrigieren kann.
    """

    def __init__(self, messages: list[str]):
        self.messages = list(messages)
        body = "\n".join(f"  {i+1}. {m}" for i, m in enumerate(self.messages))
        super().__init__(f"Netzwerk-Validierung fehlgeschlagen ({len(self.messages)} Fehler):\n{body}")


class SingularNetworkError(HydraulikError):
    """Hydraulisch unlösbares Netz (z.B. unvereinbare feste Volumenströme)."""


class ConvergenceError(HydraulikError):
    """Solver hat die Toleranzen nicht erreicht."""

    def __init__(self, message: str, residual_history=None):
        self.residual_history = residual_history or []
        super().__init__(message)
