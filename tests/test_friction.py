"""Reibungs- und Widerstandskorrelationen gegen analytische Referenzen."""
import math

import pytest

from hydraulik.friction import churchill, kv_to_b, pipe_coefficients, swamee_jain


def test_churchill_laminar_limit():
    # Im laminaren Bereich muss Churchill f = 64/Re reproduzieren
    for re in (100.0, 500.0, 1500.0):
        assert churchill(re, 0.0) == pytest.approx(64.0 / re, rel=0.02)


def test_churchill_vs_swamee_jain_turbulent():
    for re in (1e4, 1e5, 1e6):
        for rr in (0.0001, 0.001, 0.01):
            assert churchill(re, rr) == pytest.approx(swamee_jain(re, rr), rel=0.05)


def test_pipe_laminar_is_hagen_poiseuille():
    # Sehr kleiner Volumenstrom → Δp = 128·μ·L/(π·d⁴)·Q exakt
    mu, rho, L, d = 1e-3, 1000.0, 10.0, 0.02
    q = 1e-6  # Re ≈ 64 → laminar
    a, b = pipe_coefficients(q, L, d, 0.0, 0.0, rho, mu)
    dp = a * q + b * q * abs(q)
    dp_ref = 128.0 * mu * L / (math.pi * d**4) * q
    assert dp == pytest.approx(dp_ref, rel=1e-3)


def test_pipe_turbulent_darcy_weisbach():
    # Voll turbulent: Δp = f·L/d·ρ/2·(Q/A)²
    mu, rho, L, d, k = 1e-3, 1000.0, 10.0, 0.02, 0.05e-3
    q = 1.0 / 3600.0  # 1 m³/h, Re ≈ 17700
    area = math.pi * d**2 / 4
    re = rho * q * d / (area * mu)
    f = churchill(re, k / d)
    dp_ref = f * L / d * rho * (q / area) ** 2 / 2
    a, b = pipe_coefficients(q, L, d, k, 0.0, rho, mu)
    assert a * q + b * q**2 == pytest.approx(dp_ref, rel=1e-6)


def test_kv_definition():
    # Kv = 1 m³/h muss bei 1 m³/h und ρ = 1000 genau 1 bar Druckverlust ergeben
    b = kv_to_b(1.0, 1000.0)
    q = 1.0 / 3600.0
    assert b * q**2 == pytest.approx(1e5, rel=1e-9)


def test_kv_density_correction():
    # Bei halber Dichte halbiert sich der Druckverlust
    assert kv_to_b(2.5, 500.0) == pytest.approx(kv_to_b(2.5, 1000.0) / 2)
