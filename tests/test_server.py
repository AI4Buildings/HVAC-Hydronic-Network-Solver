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
        index = resp.read().decode("utf-8")
    assert "/hydraulik" in index and "/lueftung" in index      # Startseite
    with urllib.request.urlopen(server_url + "/hydraulik", timeout=10) as resp:
        html = resp.read().decode("utf-8")
    assert "Hydraulikschema-Editor" in html and "btn-solve" in html
    with urllib.request.urlopen(server_url + "/lueftung", timeout=10) as resp:
        luft = resp.read().decode("utf-8")
    assert "Lüftungsschema-Editor" in luft and "btn-solve" in luft


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


def _post_to(url, path, body: str) -> dict:
    req = urllib.request.Request(url + path, data=body.encode("utf-8"), method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


YAML_BLOCK = """\
# Blockstil, wie von Hand geschrieben — der lokale Editor-Parser kann das nicht
fluid:
  preset: water
  t_C: 60
components:
  qu1:
    type: inflow
    t_set_C: 60
    p_kPa: 10
    bems:
      - id: "A'B,C"
        key: T_VL
  rv1:
    type: control_valve
    kvs_m3h: 10
  ab1:
    type: outflow
    p_kPa: 0
connections:
  - [qu1.port, rv1.in]
  - - rv1.out
    - ab1.port
layout:
  wires:
    - a: qu1.port
      b: rv1.in
      pts: [[10, 20], [30, 40]]
"""


def test_normalize_endpoint_parst_blockstil(server_url):
    from hydraulik.server import normalize_payload
    data = _post_to(server_url, "/normalize", YAML_BLOCK)
    assert data["ok"] and data["issues"] == []
    doc = data["doc"]
    assert doc["fluid"] == {"preset": "water", "t_C": 60}
    assert doc["components"]["qu1"]["bems"][0]["id"] == "A'B,C"
    assert doc["connections"][1] == ["rv1.out", "ab1.port"]
    assert doc["layout"]["wires"][0]["pts"] == [[10, 20], [30, 40]]
    assert normalize_payload(YAML_BLOCK)["doc"] == doc
    # Pfad mit Editor-Präfix (Aufruf aus /hydraulik/) ebenso
    assert _post_to(server_url, "/hydraulik/normalize", YAML_OK)["ok"]


def test_normalize_endpoint_meldet_loader_hinweise_und_fehler(server_url):
    # unvollständige Datei: laden ja (im Editor ergänzbar), Hinweise mitliefern
    data = _post_to(server_url, "/normalize",
                    "components:\n  pu: {type: pump, mode: constant_flow}\nconnections: []\n")
    assert data["ok"] and "pu" in data["doc"]["components"]
    assert any("q_m3h" in m for m in data["issues"])
    # doppelter Komponentenname: kein stilles 'last wins'
    data = _post_to(server_url, "/normalize",
                    "components:\n  a: {type: cap}\n  a: {type: cap}\nconnections: []\n")
    assert not data["ok"] and "Doppelter Schlüssel 'a'" in data["error"]
    # YAML-Syntaxfehler mit Position
    data = _post_to(server_url, "/normalize", "components:\n  a: {type: cap\n")
    assert not data["ok"] and "YAML-Syntaxfehler" in data["error"] and "Zeile" in data["error"]
    # keine Mapping-Wurzel
    data = _post_to(server_url, "/normalize", "- nur eine Liste\n")
    assert not data["ok"] and "Mapping" in data["error"]


def test_normalize_air_endpoint(server_url):
    data = _post_to(server_url, "/normalize_air",
                    "components:\n  aul:\n    type: aussenluft\n    t_C: 5\n    rh: 80\n"
                    "connections: []\n")
    assert data["ok"] and data["doc"]["components"]["aul"]["type"] == "aussenluft"
    assert data["issues"]                          # Luft-Loader: Pflichtkomponenten fehlen


def test_editoren_nutzen_server_import_mit_fallback(server_url):
    """Beide Editoren rufen den Server-Parser und fallen ohne Server auf den
    lokalen YAML-1.2-Parser (yaml_core.js) zurück (Autosave-Restore bleibt
    lokal/synchron)."""
    html = urllib.request.urlopen(server_url + "/hydraulik").read().decode("utf-8")
    assert 'fetch("normalize"' in html and "YamlCore.parse(text)" in html
    assert "loadDoc(normalizeDoc(YamlCore.parse(saved)))" in html
    luft = urllib.request.urlopen(server_url + "/lueftung").read().decode("utf-8")
    assert 'fetch("normalize_air"' in luft and "loadDoc(normalizeDoc(YamlCore.parse(saved)))" in luft
    assert "const YamlCore" in html and "const YamlCore" in luft


# --- Robustheit des Endpunkts (Bugfix-Runde 2026-11) ------------------------------

import http.client
import socket


def _raw(server_url, method, path, body=b"", headers=None):
    """Anfrage mit frei gesetzten Kopfzeilen (http.client setzt Host selbst,
    solange keiner übergeben wird). Liefert (Status, Kopfzeilen, Body-Bytes)."""
    host, port = server_url.replace("http://", "").split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=10)
    try:
        conn.putrequest(method, path, skip_host=bool(headers and "Host" in headers),
                        skip_accept_encoding=True)
        hdrs = dict(headers or {})
        if body and "Content-Length" not in hdrs and "Transfer-Encoding" not in hdrs:
            hdrs["Content-Length"] = str(len(body))
        for k, v in hdrs.items():
            conn.putheader(k, v)
        conn.endheaders()
        if body:
            conn.send(body)
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read()
    finally:
        conn.close()


def _strict_loads(data: bytes):
    """json.loads, das NaN/Infinity ablehnt (wie JSON.parse im Browser)."""
    def no_const(name):
        raise AssertionError(f"kein striktes JSON: {name}")
    return json.loads(data.decode("utf-8"), parse_constant=no_const)


CLOSED_LOOP = """\
settings: {p_ref: %s}
components:
  pu1: {type: pump, mode: constant_flow, q_m3h: 1.0}
  r1: {type: flow_resistance, c_Pa_m3h2: 1000}
connections:
  - [pu1.out, r1.in]
  - [r1.out, pu1.in]
"""


def test_settings_p_ref_erreicht_das_netz(server_url):
    """settings.p_ref der Datei setzt den Referenzdruck des geschlossenen Kreises
    (compile(settings)) — vorher galt immer der Standardwert 150 kPa."""
    data = _post(server_url, CLOSED_LOOP % 300000)
    assert data["ok"], data
    assert max(n["p_kPa"] for n in data["nodes"]) == pytest.approx(300.0, abs=1e-6) or \
        any(n["p_kPa"] == pytest.approx(300.0, abs=1e-6) for n in data["nodes"])
    assert any("300 kPa" in n for n in data["notices"])
    std = solve_payload(CLOSED_LOOP.replace("settings: {p_ref: %s}\n", ""))
    assert any(n["p_kPa"] == pytest.approx(150.0, abs=1e-6) for n in std["nodes"])


@pytest.mark.parametrize("length", ["abc", "-1", "1e3", "+5", ""])
def test_ungueltiger_content_length_gibt_json_fehler(server_url, length):
    status, hdrs, data = _raw(server_url, "POST", "/solve", b"a: 1", {"Content-Length": length})
    assert status == 400 and "application/json" in hdrs["Content-Type"]
    out = _strict_loads(data)
    assert out["ok"] is False and "Content-Length" in out["error"]


def test_fehlender_content_length_und_chunked(server_url):
    status, _, data = _raw(server_url, "POST", "/solve")
    assert status == 411 and "Content-Length" in _strict_loads(data)["error"]
    status, _, data = _raw(server_url, "POST", "/solve", b"4\r\na: 1\r\n0\r\n\r\n",
                           {"Transfer-Encoding": "chunked"})
    assert status == 411 and _strict_loads(data)["ok"] is False


def test_zu_grosser_body_wird_abgelehnt_ohne_ihn_zu_lesen(server_url):
    from hydraulik.server import MAX_BODY_BYTES
    assert MAX_BODY_BYTES == 5 * 1024 * 1024
    status, _, data = _raw(server_url, "POST", "/normalize", b"",
                           {"Content-Length": str(MAX_BODY_BYTES + 1)})
    out = _strict_loads(data)
    assert status == 413 and out["ok"] is False and "5 MB" in out["error"]
    # genau an der Grenze wird gelesen (hier: Kommentarzeilen bis zur Grenze)
    body = (YAML_OK + "#" * (MAX_BODY_BYTES - len(YAML_OK.encode()))).encode()
    status, _, data = _raw(server_url, "POST", "/solve", body)
    assert status == 200 and _strict_loads(data)["ok"] is True


def test_unvollstaendiger_body_haengt_nicht(server_url, monkeypatch):
    from hydraulik import server as srv
    monkeypatch.setattr(srv._Handler, "timeout", 0.5)
    host, port = server_url.replace("http://", "").split(":")
    with socket.create_connection((host, int(port)), timeout=10) as s:
        s.sendall(f"POST /solve HTTP/1.1\r\nHost: {host}:{port}\r\nContent-Length: 500\r\n\r\na: 1".encode())
        s.settimeout(10)
        chunks = []
        while True:
            part = s.recv(65536)
            if not part:
                break
            chunks.append(part)
    reply = b"".join(chunks)
    assert reply.startswith(b"HTTP/1.0 408") or reply.startswith(b"HTTP/1.1 408"), reply[:80]
    assert _strict_loads(reply.split(b"\r\n\r\n", 1)[1])["ok"] is False


def test_kein_utf8_gibt_json_fehler(server_url):
    status, _, data = _raw(server_url, "POST", "/solve", b"a: \xff\xfe")
    out = _strict_loads(data)
    assert status == 400 and out["ok"] is False and "UTF-8" in out["error"]


def test_fremder_host_und_origin_werden_abgelehnt(server_url):
    port = server_url.rsplit(":", 1)[1]
    body = YAML_OK.encode()
    # DNS-Rebinding: fremder Host-Name zeigt auf 127.0.0.1
    for method, path in (("GET", "/hydraulik"), ("GET", "/"), ("POST", "/solve")):
        status, _, data = _raw(server_url, method, path, body if method == "POST" else b"",
                               {"Host": f"evil.example:{port}"})
        assert status == 403 and "evil.example" in _strict_loads(data)["error"]
    # eigener Host, aber falscher Port im Host-Kopf
    assert _raw(server_url, "POST", "/solve", body, {"Host": "127.0.0.1:1"})[0] == 403
    # Cross-Site-POST einer fremden Webseite (Browser schickt Origin mit)
    for origin in ("https://evil.example", "http://127.0.0.1:1", "null", f"https://127.0.0.1:{port}"):
        status, _, data = _raw(server_url, "POST", "/solve", body, {"Origin": origin})
        assert status == 403 and "Origin" in _strict_loads(data)["error"], origin
    # eigene Seite: 127.0.0.1 und localhost, mit und ohne Origin
    for host in (f"127.0.0.1:{port}", f"localhost:{port}", f"LOCALHOST:{port}"):
        for extra in ({}, {"Origin": f"http://{host.lower()}"}):
            status, _, data = _raw(server_url, "POST", "/solve", body, {"Host": host, **extra})
            assert status == 200 and _strict_loads(data)["ok"] is True, (host, extra)


def test_antwort_ist_striktes_json_bei_inf_und_nan(server_url):
    """/normalize mit .inf/.nan: vorher 'Infinity'/'NaN' im Body — der Browser
    konnte die Antwort nicht lesen und fiel still auf den lokalen Parser zurück."""
    text = "components:\n  a: {type: cap, x: .inf, y: -.inf, z: .nan}\nconnections: []\n"
    status, _, data = _raw(server_url, "POST", "/normalize", text.encode())
    assert status == 200 and b"Infinity" not in data and b"NaN" not in data
    out = _strict_loads(data)
    assert out["ok"] and out["doc"]["components"]["a"] == {"type": "cap", "x": None, "y": None, "z": None}
    assert "3 nicht endliche" in out["issues"][0] and "null" in out["issues"][0]


def test_encode_payload_meldet_nicht_endliche_werte():
    from hydraulik.server import encode_payload
    out = _strict_loads(encode_payload({"ok": True, "notices": ["a"],
                                        "nodes": [{"p_kPa": float("nan")}, {"p_kPa": 1.5}]}))
    assert out["nodes"] == [{"p_kPa": None}, {"p_kPa": 1.5}]
    assert out["notices"][1] == "a" and "nicht endliche" in out["notices"][0]
    out = _strict_loads(encode_payload({"ok": True, "zuluft": {"t_C": float("inf")}, "hinweise": []}))
    assert out["zuluft"]["t_C"] is None and "nicht endliche" in out["hinweise"][0]
    out = _strict_loads(encode_payload({"ok": True, "v": float("-inf")}))
    assert out["v"] is None and "nicht endliche" in out["hinweise"][0]
    # endliche Werte bleiben unverändert, ohne Hinweis
    assert _strict_loads(encode_payload({"ok": True, "v": 1e-300, "n": 3, "b": True, "s": "ä"})) == \
        {"ok": True, "v": 1e-300, "n": 3, "b": True, "s": "ä"}


def test_einzelnes_surrogat_reisst_die_antwort_nicht(server_url):
    """Ein einzelnes Surrogat ist kein gültiges UTF-8: die Antwort wird dann als
    ASCII-JSON mit Escape kodiert statt mit UnicodeEncodeError abzubrechen."""
    from hydraulik.server import encode_payload
    data = encode_payload({"ok": True, "error": "x\ud800y", "s": "ä"})
    assert _strict_loads(data) == {"ok": True, "error": "x\ud800y", "s": "ä"}
    # über den Endpunkt: immer eine lesbare JSON-Antwort (yamlio lehnt das Escape ab)
    text = 'components:\n  a: {type: cap, description: "x\\ud800y"}\nconnections: []\n'
    status, _, data = _raw(server_url, "POST", "/normalize", text.encode())
    assert status == 200 and isinstance(_strict_loads(data)["ok"], bool)


def test_normalize_lehnt_ganzzahlen_jenseits_2_hoch_53_ab(server_url):
    """Der Editor liest Zahlen als double — 12345678901234567890 käme als
    12345678901234567000 an und würde so wieder exportiert."""
    text = "components:\n  a: {type: cap, bems: [{id: 12345678901234567890}]}\nconnections: []\n"
    data = _post_to(server_url, "/normalize", text)
    assert not data["ok"] and "12345678901234567890" in data["error"] and "2^53" in data["error"]
    assert "components.a.bems[0].id" in data["error"]
    ok = text.replace("12345678901234567890", "9007199254740991")       # 2^53 − 1: exakt
    assert _post_to(server_url, "/normalize", ok)["ok"]
    assert _post_to(server_url, "/normalize", text.replace("12345678901234567890", '"12345678901234567890"'))["ok"]


def test_serve_meldet_belegten_port_auf_deutsch(capsys):
    from hydraulik.server import serve
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]
        with pytest.raises(SystemExit) as exc:
            serve(port=port, open_browser=False)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert f"Port {port}" in err and "--port" in err and "Traceback" not in err
