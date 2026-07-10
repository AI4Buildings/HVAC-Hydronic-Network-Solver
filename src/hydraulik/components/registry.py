"""Typregister: YAML-`type`-Schlüssel → Komponentenklasse."""
from __future__ import annotations

from .base import Component

COMPONENT_REGISTRY: dict[str, type[Component]] = {}


def register(type_name: str):
    def deco(cls: type[Component]):
        cls.type_name = type_name
        COMPONENT_REGISTRY[type_name] = cls
        return cls
    return deco
