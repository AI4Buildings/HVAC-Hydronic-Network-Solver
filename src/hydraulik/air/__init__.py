"""Luftseite: Lüftungsanlagen (VKA) — Komponentenregistry, Loader und
Adapter auf den integrierten Rechenkern (EN 16798-5-1)."""
from .adapter import solve_air
from .components import AIR_REGISTRY
from .loader import load_air
