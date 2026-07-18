"""VKA-Rechenkern (EN 16798-5-1) — übernommen aus dem Skill
vka-effizienz-en16798 (C. Heschl, FH Burgenland; 1:1 gegen MATLAB R2025a
verifiziert). Energieoptimale Regelung von Vollklima-Lüftungsanlagen mit
Wärmerückgewinnung: simulate() (Zuluft-Sollband) und simulate_room()
(raumgekoppelt mit Feuchtelast). Ein Zeitschritt oder Zeitreihe.
"""
from .simulate import simulate
from .simulate_room import simulate_room
