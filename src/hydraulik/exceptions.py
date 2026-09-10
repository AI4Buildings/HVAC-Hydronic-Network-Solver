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


class ComponentModelError(HydraulikError):
    """Ein Komponentenmodell (hydraulische Koeffizienten oder thermischer
    Austritt) hat während der Lösung eine Ausnahme geworfen. Die Meldung nennt
    Komponente, Modell und Betriebspunkt, damit der Fall ohne Traceback
    reproduzierbar ist (lesbar für Nutzer und LLM statt 'Interner Fehler')."""

    def __init__(self, component: str, type_name: str, model: str,
                 operating_point: str, cause: BaseException):
        self.component = component
        self.type_name = type_name
        self.model = model
        self.operating_point = operating_point
        self.cause = cause
        super().__init__(
            f"Komponente '{component}' (Typ '{type_name}'): {model} Modell fehlgeschlagen "
            f"bei {operating_point} — {type(cause).__name__}: {cause}. Der Betriebspunkt "
            f"liegt vermutlich außerhalb des Modellbereichs; Parameter und Betriebsfall prüfen.")


class ConvergenceError(HydraulikError):
    """Solver hat die Toleranzen nicht erreicht."""

    def __init__(self, message: str, residual_history=None):
        self.residual_history = residual_history or []
        super().__init__(message)
