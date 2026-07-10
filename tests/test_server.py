"""Editor-Server: GET / (GUI) und POST /solve (Rechen-Endpunkt)."""
import json
import threading
import urllib.request

import pytest

from hydraulik.server import make_server, solve_payload

YAML_OK = """\
fluid: {preset: water, t_C: 50}
components:
  qu1: {type: inflow, t_set_C: 60, p_kPa: 10}
  rv1: {type: control_valve, kvs_m3h: 10, opening: 1.0, characteristic: linear}
  ab1: {type: outflow, p_kPa: 0}
connections:
  - [qu1.port, rv1.in]
  - [rv1.out, ab1.port]
layout:
  components:
    qu1: {x: 100, y: 100}
"""


@pytest.fixture(scope="module")
def server_url():
    httpd = make_server(port=0)                      # freier Port
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _post(url, body: str) -> dict:
    req = urllib.request.Request(url + "/solve", data=body.encode("utf-8"), method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_get_serves_editor(server_url):
    with urllib.request.urlopen(server_url + "/", timeout=10) as resp:
        html = resp.read().decode("utf-8")
    assert "Schaltbild-Editor" in html and "btn-solve" in html


def test_solve_endpoint_ok(server_url):
    data = _post(server_url, YAML_OK)
    assert data["ok"] and data["converged"]
    rv = next(c for c in data["components"] if c["name"] == "rv1")
    # Kv 10 bei 10 kPa: V̇ = 10·sqrt(0.1·1000/ρ)
    rho = 988.0
    assert rv["q_m3h"] == pytest.approx(10 * (0.1 * 1000 / rho) ** 0.5, rel=1e-4)
    assert rv["t_in_C"] == pytest.approx(60.0, abs=1e-6)
    # port_map: Portreferenz → Knotenindex, Knoten trägt p (gauge) und T
    idx = data["port_map"]["rv1.in"]
    assert data["nodes"][idx]["p_kPa"] == pytest.approx(10.0, abs=1e-6)


def test_solve_endpoint_collects_errors(server_url):
    bad = YAML_OK.replace("opening: 1.0", "opening: 100")
    data = _post(server_url, bad)
    assert not data["ok"]
    assert "opening" in data["error"] and "Maximum" in data["error"]


def test_solve_payload_velocity_for_pipes():
    yaml_text = """\
components:
  qu1: {type: inflow, t_set_C: 60, q_m3h: 1.8}
  ro1: {type: pipe, length_m: 10, d_inner_mm: 20}
  ab1: {type: outflow, p_kPa: 0}
connections:
  - [qu1.port, ro1.in]
  - [ro1.out, ab1.port]
"""
    data = solve_payload(yaml_text)
    ro = next(c for c in data["components"] if c["name"] == "ro1")
    import math
    v_ref = (1.8 / 3600) / (math.pi * 0.02 ** 2 / 4)
    assert ro["extras"]["v_m_s"] == pytest.approx(v_ref, rel=1e-9)


def test_solve_payload_thermal_fallback():
    """Thermik unlösbar (isolierter Umlauf mit fester Leistung) → Hydraulik
    kommt trotzdem zurück, mit Hinweis."""
    yaml_text = """\
components:
  pu1: {type: pump, mode: constant_flow, q_m3h: 1.0}
  hk1: {type: radiator, q_prescribed_kW: 5, kv_m3h: 100}
connections:
  - [pu1.out, hk1.in]
  - [hk1.out, pu1.in]
"""
    data = solve_payload(yaml_text)
    assert data["ok"]
    assert any("Thermik nicht gelöst" in n for n in data["notices"])
    hk = next(c for c in data["components"] if c["name"] == "hk1")
    assert hk["q_m3h"] == pytest.approx(1.0, rel=1e-9)
