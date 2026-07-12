"""Typregister: YAML-`type`-Schlüssel → Komponentenklasse."""
from __future__ import annotations

from ..params import Param
from .base import Component

COMPONENT_REGISTRY: dict[str, type[Component]] = {}

#: Jede Komponente darf eine freie Semantikbeschreibung tragen — für die
#: BEMS-Betriebsdatenanalyse (Ort, Kreis, Bezug) und als LLM-Kontext.
_DESCRIPTION_PARAM = Param(
    "description", "str",
    help="Semantik für die Datenanalyse (Ort, Kreis, Bezug) — rein deklarativ")


def register(type_name: str):
    def deco(cls: type[Component]):
        cls.type_name = type_name
        if not any(p.name == "description" for p in cls.PARAMS):
            cls.PARAMS = tuple(cls.PARAMS) + (_DESCRIPTION_PARAM,)
        COMPONENT_REGISTRY[type_name] = cls
        return cls
    return deco
