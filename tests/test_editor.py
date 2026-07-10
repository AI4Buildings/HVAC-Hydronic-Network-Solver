"""Schaltbild-Editor: Katalogexport, HTML-Generierung, layout-Toleranz des Loaders."""
import json

import pytest

import hydraulik as h
from hydraulik.components.registry import COMPONENT_REGISTRY
from hydraulik.editor import build_editor, component_catalog


def test_catalog_covers_all_registered_types():
    cat = component_catalog()
    types = {t["type"] for t in cat["types"]}
    assert types == set(COMPONENT_REGISTRY)


def test_catalog_ports_and_params():
    cat = component_catalog()
    by_type = {t["type"]: t for t in cat["types"]}
    assert by_type["pump"]["ports"]["base"] == ["in", "out"]
    assert by_type["mixing_valve_3way"]["ports"]["base"] == ["a", "b", "ab"]
    # dynamische Ports als Regel, nicht als Liste
    assert by_type["manifold"]["ports"]["count_param"] == "n_ports"
    assert by_type["buffer_storage"]["ports"]["template"] == "p{}"
    # Parameter mit Einheiten-Schlüsseln (Single Source of Truth)
    dp = next(p for p in by_type["pump"]["params"] if p["name"] == "dp")
    assert "dp_kPa" in dp["keys"] and "dp_Pa" in dp["keys"]
    mode = next(p for p in by_type["pump"]["params"] if p["name"] == "mode")
    assert mode["choices"] == ["constant_dp", "constant_flow"]
    assert "pressure" in cat["units"] and cat["units"]["pressure"]["kPa"] == 1000.0


def test_catalog_is_json_serializable():
    json.dumps(component_catalog())


def test_build_editor_writes_selfcontained_html(tmp_path):
    out = build_editor(tmp_path / "editor.html")
    html = out.read_text(encoding="utf-8")
    assert "__CATALOG_JSON__" not in html          # Platzhalter ersetzt
    assert "flow_resistance" in html and "hydraulic_separator" in html
    assert "<script" in html and "</html>" in html
    assert "http://" not in html and "https://" not in html   # keine externen Abhängigkeiten


def test_loader_ignores_layout_block():
    doc = {
        "components": {
            "sp": {"type": "ideal_storage", "t_set_C": 60},
            "pu": {"type": "pump", "mode": "constant_flow", "q_m3h": 1.0},
            "v1": {"type": "flow_resistance", "c_Pa_m3h2": 500},
        },
        "connections": [["sp.out", "pu.in"], ["pu.out", "v1.in"], ["v1.out", "sp.in"]],
        "layout": {"components": {"sp": {"x": 100, "y": 200}},
                   "wires": [{"a": "sp.out", "b": "pu.in", "color": "vl"}]},
    }
    r = h.load(doc).solve()
    assert r.converged


def test_editor_yaml_roundtrip_solves(tmp_path):
    """Ein YAML im Editor-Exportformat (inkl. layout) ist direkt rechenbar."""
    yaml_text = """\
fluid: {preset: water, t_C: 50}

components:
  sp1: {type: ideal_storage, t_set_C: 70, ts: "1"}
  pu1: {type: pump, mode: constant_flow, q_m3h: 2}
  hk1: {type: radiator, q_prescribed_kW: 8, kv_m3h: 100}

connections:
  - [sp1.out, pu1.in]
  - [pu1.out, hk1.in]
  - [hk1.out, sp1.in]

layout:
  components:
    sp1: {x: 120, y: 240}
    pu1: {x: 280, y: 240}
    hk1: {x: 440, y: 240}
  wires:
    - {a: sp1.out, b: pu1.in, color: vl}
"""
    f = tmp_path / "zeichnung.yaml"
    f.write_text(yaml_text, encoding="utf-8")
    r = h.load(f).solve()
    assert r.converged
    assert r["hk1"].t_in_C == pytest.approx(70.0, abs=1e-9)
    assert r.teilstrecken and r.teilstrecken[0].ts == "1"
