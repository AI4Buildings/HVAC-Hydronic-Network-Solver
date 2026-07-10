"""YAML-Loader (inkl. Fehlermeldungsqualität) und Beispielschaltungen."""
from pathlib import Path

import pytest

import hydraulik as h

EXAMPLES = sorted((Path(__file__).parent.parent / "examples").glob("*.yaml"))


@pytest.mark.parametrize("path", EXAMPLES, ids=[p.stem for p in EXAMPLES])
def test_examples_solve_and_balance(path):
    net = h.load(path)
    r = net.solve(h.load_settings(path))
    assert r.converged
    assert abs(r.energy_imbalance_W) < 1.0
    assert r.mass_residual < 1e-7


def test_example_01_plausibility():
    r = h.load(EXAMPLES[0]).solve()
    # WP liefert exakt 45 °C Vorlauf (unter der 12-kW-Grenze), HK-Rücklauf < Vorlauf
    assert r["wp1"].t_out_C == pytest.approx(45.0, abs=0.01)
    assert r["hk1"].t_out_C < r["hk1"].t_in_C
    assert 0 < -r["hk1"].q_dot_kW < 12


def test_example_04_separator_transfer():
    r = h.load([p for p in EXAMPLES if "separator" in p.stem][0]).solve()
    q_prim = r["pu_p"].q_m3h
    # Sekundärentnahme aus der Weiche: Heizkörperkreis (voll) + Injektion in den
    # Beimischkreis (nur der mv1.a-Strom; der Rest zirkuliert über den Bypass)
    q_sec = abs(r["pu_a"].q_m3h) + abs(r["mv1:a"].q_m3h)
    q_vert = r["weiche:vertikal"].q_m3h
    # Kontinuität an der Weiche: Transferstrom = Primär − Sekundär
    assert q_vert == pytest.approx(q_prim - q_sec, abs=1e-6)
    # Beimischkreis: Injektion + Bypass = Pumpenvolumenstrom
    assert abs(r["mv1:a"].q_m3h) + abs(r["mv1:b"].q_m3h) == pytest.approx(
        abs(r["pu_b"].q_m3h), abs=1e-6)


def test_yaml_roundtrip_dict():
    doc = {
        "components": {
            "pu": {"type": "pump", "mode": "constant_dp", "dp_kPa": 30, "q_nom_m3h": 1.0},
            "v1": {"type": "control_valve", "kvs_m3h": 2.5},
        },
        "connections": [["pu.out", "v1.in"], ["v1.out", "pu.in"]],
    }
    r = h.load(doc).solve()
    assert r.converged


def test_yaml_errors_are_collected():
    """Alle Fehler in EINER Meldung, mit Korrekturvorschlägen."""
    doc = {
        "components": {
            "pu": {"type": "pumpe", "mode": "constant_dp"},          # Typ-Tippfehler
            "v1": {"type": "control_valve", "kvs_m3h": 2.5, "öffnung": 0.5},  # Param-Tippfehler
            "hk": {"type": "radiator"},                              # Pflichtparameter fehlt
        },
        "connections": [["pu.out", "v1.in"]],
    }
    with pytest.raises(h.NetworkValidationError) as exc:
        h.load(doc)
    msg = str(exc.value)
    assert "pump" in msg                 # Vorschlag für 'pumpe'
    assert "opening" in msg              # Vorschlag für 'öffnung'
    assert "q_nom_kW" in msg             # fehlender Pflichtparameter benannt


def test_yaml_unknown_unit_suffix():
    doc = {
        "components": {
            "pu": {"type": "pump", "mode": "constant_dp", "dp_mmWS": 300},
        },
        "connections": [["pu.out", "pu.in"]],
    }
    with pytest.raises(h.NetworkValidationError) as exc:
        h.load(doc)
    assert "dp_Pa" in str(exc.value) or "dp_kPa" in str(exc.value)


def test_yaml_unconnected_port():
    doc = {
        "components": {
            "pu": {"type": "pump", "mode": "constant_dp", "dp_kPa": 30},
            "v1": {"type": "control_valve", "kvs_m3h": 2.5},
        },
        "connections": [["pu.out", "v1.in"]],
    }
    net = h.load(doc)
    with pytest.raises(h.NetworkValidationError) as exc:
        net.solve()
    msg = str(exc.value)
    assert "pu.in" in msg and "v1.out" in msg


def test_cli_runs(tmp_path, capsys):
    from hydraulik.cli import main
    rc = main(["run", str(EXAMPLES[0]), "--csv", str(tmp_path / "out.csv")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "konvergiert: ja" in out
    assert (tmp_path / "out.csv").exists()
