"""VKA-Kern gegen die MATLAB/PDF-Referenzlösungen (2 Anlagen × 7 Betriebspunkte).

Portierung von validate_vka.py aus dem Skill vka-effizienz-en16798 — Schutz-
linie für Korrekturen am Kern: die validierten Betriebspunkte dürfen sich
nicht ändern (Toleranz 0.01 wie im Skript; PDF-Rundung).
"""
import pytest

from hydraulik.air.vka.vka_chain import Setpoints
from hydraulik.air.vka.vka_plant import (OperatingPoint, plant_anlage1, plant_anlage2,
                                        run_plant)

SP = Setpoints(22.0, 24.0, 0.40, 0.55, 1e5)
T_AUL = [-10, 0, 5, 15, 22, 29, 33]
PHI_AUL = [60, 55, 40, 40, 45, 50, 55]
T_ABL = [22, 22, 22, 22, 22, 24, 24]
PHI_ABL = [45, 45, 45, 50, 50, 55, 55]

REF1 = dict(
    T_sup=[22, 22, 22, 22, 22.38228, 24.0, 24.0],
    x_sup=[6.660895, 6.660895, 6.660895, 6.732937, 7.503552, 10.397677, 10.397677],
    eta_hr=[0.689980, 0.689980, 0.689980, 0.689980, 0.0, 0.689980, 0.689980],
    Q_KR=[0, 0, 0, 0, 0, 45.862299, 64.445554],
    Q_NHR=[41.956657, 29.607484, 25.086567, 0, 0, 31.970183, 31.970183],
)
V1 = [10000] * 7
REF2 = dict(
    T_sup=[22, 22, 22, 22, 22.63714, 24.0, 24.0],
    x_sup=[2.930895, 3.114628, 2.571303, 4.279559, 7.503552, 10.397677, 10.397677],
    eta_hr=[0.689980, 0.689980, 0.736565, 0.767621, 0.0, 0.736565, 0.689980],
    eta_xr=[0.299997, 0.188179, 0.073486, 0.0, 0.0, 0.0, 0.250801],
    Q_KR=[0, 0, 0, 0, 0, 29.418547, 78.142825],
    Q_NHR=[31.965171, 21.780486, 9.913258, 2.436514, 0, 22.379128, 31.970183],
)
V2 = [10000, 10000, 7000, 5000, 5000, 7000, 10000]


@pytest.mark.parametrize("cfg, V, ref", [(plant_anlage1(), V1, REF1), (plant_anlage2(), V2, REF2)],
                         ids=["anlage1_sorption", "anlage2_kondensation"])
def test_vka_kern_gegen_matlab_referenz(cfg, V, ref):
    ops = [OperatingPoint(T_AUL[i], PHI_AUL[i], T_ABL[i], PHI_ABL[i], V[i]) for i in range(7)]
    res = run_plant(cfg, SP, ops)
    for key, vals in ref.items():
        for i, rv in enumerate(vals):
            got = res[i][key] * (1000 if key == "x_sup" else 1)
            assert got == pytest.approx(rv, abs=0.01), f"{key}[{i}]"
