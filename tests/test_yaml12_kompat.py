"""YAML-1.1-Altlasten und Grenzfälle der Werteprüfung (AP2).

YAML 1.2 deutet yes/no/on/off nicht mehr als Wahrheitswerte. Statt solche
Werte still als String durchzureichen, meldet die Werteprüfung sie mit
klarem Hinweis. Außerdem: ts-/BEMS-Labels sind Zeichenketten, Ganzzahl-
Parameter akzeptieren ganzzahlige Floats (wie JSON Schema 'integer'),
nicht endliche Zahlen sind unzulässig, der fluid-Block wird wie jeder
Komponentenparameter typ-, bereichs- und schlüsselgeprüft.
"""
from __future__ import annotations

import pytest

import hydraulik as h
from hydraulik.air import load_air
from hydraulik.exceptions import ComponentParamError, NetworkValidationError

BASE = """\
components:
  qu:  {{type: inflow, t_set_C: 50, q_m3h: 1}}
  kh:  {{type: ball_valve, kvs_m3h: 10{extra}}}
  ab:  {{type: outflow, p_kPa: 0}}
connections:
  - [qu.port, kh.in]
  - [kh.out, ab.port]
{tail}"""


def _messages(text: str) -> list[str]:
    with pytest.raises(NetworkValidationError) as ei:
        h.load(text)
        h.load_settings(text)
    return ei.value.messages


# --- Bool-Parameter: YAML-1.1-Wahrheitswörter mit Hinweis -----------------------

@pytest.mark.parametrize("word", ["yes", "no", "on", "off", "Yes", "NO", "y", "n", '"yes"'])
def test_bool_yaml11_wort_mit_hinweis(word):
    msgs = _messages(BASE.format(extra=f", closed: {word}", tail=""))
    assert any("'closed'" in m and "true/false" in m and "YAML 1.2" in m for m in msgs), msgs


@pytest.mark.parametrize("word, expected", [("true", True), ("True", True), ("false", False),
                                            ("FALSE", False)])
def test_bool_core_werte(word, expected):
    net = h.load(BASE.format(extra=f", closed: {word}", tail=""))
    assert net.components["kh"].closed is expected


def test_bool_sonstiger_string_ohne_yaml11_hinweis():
    msgs = _messages(BASE.format(extra=", closed: zu", tail=""))
    assert any("'closed' muss true/false sein" in m for m in msgs)
    assert not any("YAML 1.2" in m for m in msgs)


def test_bool_hinweis_auch_in_der_luft():
    doc = {"components": {"w": {"type": "wrg", "adiab_exhaust": "on"}}, "connections": []}
    with pytest.raises(NetworkValidationError) as ei:
        load_air(doc)
    assert any("adiab_exhaust" in m and "true/false" in m for m in ei.value.messages)


# --- Ganzzahlen und nicht endliche Zahlen --------------------------------------

def test_ganzzahl_parameter_akzeptiert_ganzzahligen_float():
    v = h.Manifold("vt", n_ports=2.0)
    assert v.n_ports == 2 and type(v.n_ports) is int
    with pytest.raises(ComponentParamError, match="Ganzzahl"):
        h.Manifold("vt", n_ports=2.5)
    with pytest.raises(ComponentParamError, match="Ganzzahl"):
        h.Manifold("vt", n_ports=True)


def test_settings_ganzzahl_akzeptiert_ganzzahligen_float():
    s = h.load_settings({"settings": {"max_iter": 1e3, "max_iter_thermal": 400.0}})
    assert s.max_iter == 1000 and type(s.max_iter) is int
    assert type(s.max_iter_thermal) is int
    with pytest.raises(NetworkValidationError, match="Ganzzahl"):
        h.load_settings({"settings": {"max_iter": 10.5}})


@pytest.mark.parametrize("value", [".inf", "-.inf", ".nan", "1e400"])
def test_nicht_endliche_zahlen_unzulaessig(value):
    msgs = _messages(BASE.format(extra=f", kvs_m3h: {value}", tail="").replace(
        "kvs_m3h: 10, ", ""))
    assert any("'kvs_m3h'" in m and "endliche Zahl" in m for m in msgs), msgs


@pytest.mark.parametrize("key", ["tol_t", "alpha_p", "p_ref", "t_plausible_max"])
def test_settings_nicht_endlich_unzulaessig(key):
    with pytest.raises(NetworkValidationError, match="endliche Zahl"):
        h.load_settings({"settings": {key: float("inf")}})
    with pytest.raises(NetworkValidationError, match="endliche Zahl"):
        h.load_settings({"settings": {key: float("nan")}})


def test_param_ohne_grenzen_nicht_endlich_unzulaessig():
    # 'n' (Heizkörperexponent) hat Grenzen, 'zeta' eines Rohrs nur minv:
    # unabhängig davon ist inf/nan immer ein Fehler
    with pytest.raises(ComponentParamError, match="endliche Zahl"):
        h.Pipe("r", length_m=1, d_inner_mm=20, zeta=float("inf"))
    with pytest.raises(ComponentParamError, match="endliche Zahl"):
        h.Pipe("r", length_m=float("nan"), d_inner_mm=20)


# --- ts-Labels ------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [('"08"', "08"), ("'4a'", "4a"), ("4", "4"),
                                           ("08", "8"), ("0x10", "16"), ("TS1", "TS1")])
def test_ts_label_wird_zeichenkette(raw, expected):
    net = h.load(BASE.format(extra=f", ts: {raw}", tail=""))
    assert net.components["kh"].ts == expected


@pytest.mark.parametrize("raw", ["1.5", "1.10", "true", "[1, 2]", "{a: 1}"])
def test_ts_label_float_bool_liste_mit_quoting_hinweis(raw):
    msgs = _messages(BASE.format(extra=f", ts: {raw}", tail=""))
    assert any("ts" in m and "Anführungszeichen" in m for m in msgs), msgs


def test_ts_label_python_api_int_weiterhin():
    assert h.FlowResistance("r", c_Pa_m3h2=10, ts=4).ts == "4"
    with pytest.raises(ComponentParamError, match="Anführungszeichen"):
        h.FlowResistance("r", c_Pa_m3h2=10, ts=4.5)


# --- BEMS-Felder: gleiche Label-Regel wie ts ------------------------------------

def test_bems_felder_als_zeichenkette():
    c = h.FlowResistance("r", c_Pa_m3h2=10, bems=[{"id": 12345, "key": "K"}])
    assert c.bems == [{"id": "12345", "key": "K"}]
    with pytest.raises(ComponentParamError, match="Anführungszeichen"):
        h.FlowResistance("r", c_Pa_m3h2=10, bems=[{"id": 1.50}])
    with pytest.raises(ComponentParamError, match="Anführungszeichen"):
        h.FlowResistance("r", c_Pa_m3h2=10, bems=[{"id": "x", "key": True}])


# --- fluid-Block ------------------------------------------------------------------

def _fluid_msgs(fluid: str) -> list[str]:
    return _messages(BASE.format(extra="", tail="").replace(
        "components:", f"fluid: {fluid}\ncomponents:", 1))


def test_fluid_preset_ok_und_custom_ok():
    net = h.load(BASE.format(extra="", tail="").replace(
        "components:", "fluid: {preset: water, t_C: 4.0e1}\ncomponents:", 1))
    assert net.fluid.rho == pytest.approx(h.water_at(40.0).rho)
    net = h.load(BASE.format(extra="", tail="").replace(
        "components:", "fluid: {name: glykol, rho: 1040, mu: 3.5e-3, cp: 3.6e3}\ncomponents:", 1))
    assert (net.fluid.name, net.fluid.rho, net.fluid.mu, net.fluid.cp) == \
        ("glykol", 1040.0, 3.5e-3, 3600.0)


@pytest.mark.parametrize("fluid, fragment", [
    ("{preset: water, t_C: abc}", "'t_C' muss eine Zahl sein"),
    ("{preset: water, t_C: true}", "'t_C' muss eine Zahl sein"),
    ("{preset: water, t_c: 40}", "Meinten Sie 't_C'"),
    ("{preset: water, rho: 998}", "'rho'"),
    ("{preset: oil}", "'preset'"),
    ("{rho: 998, mu: 1e-3}", "Pflichtparameter fehlt: 'cp'"),
    ("{rho: -1, mu: 1e-3, cp: 4180}", "'rho'"),
    ("{rho: 998, mu: .nan, cp: 4180}", "endliche Zahl"),
    ("[water]", "'fluid' muss ein Mapping sein"),
])
def test_fluid_fehler_gesammelt_statt_absturz(fluid, fragment):
    msgs = _fluid_msgs(fluid)
    assert any(fragment in m for m in msgs), msgs
    assert any(m.startswith("'fluid'") or "fluid" in m for m in msgs)


def test_fluid_fehler_und_komponentenfehler_zusammen():
    text = BASE.format(extra=", closed: yes", tail="").replace(
        "components:", "fluid: {preset: water, t_c: 40}\ncomponents:", 1)
    msgs = _messages(text)
    assert any("t_c" in m for m in msgs) and any("closed" in m for m in msgs)
