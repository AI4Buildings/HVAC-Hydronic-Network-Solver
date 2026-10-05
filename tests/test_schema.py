"""JSON Schema (Draft 2020-12) aus der Komponenten-Registry (AP4).

Das Schema entsteht ausschließlich aus Registry, Param-Deklarationen,
fluid-Deklarationen und SolverSettings — die Tests prüfen Gültigkeit,
Beispieldateien und vor allem die KONSISTENZ mit dem Loader: was das Schema
ablehnt, lehnt auch der Loader ab (und umgekehrt), Parameter für Parameter.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")
from jsonschema import Draft202012Validator  # noqa: E402

import hydraulik as h  # noqa: E402
from hydraulik.air.components import AIR_REGISTRY  # noqa: E402
from hydraulik.components.registry import COMPONENT_REGISTRY  # noqa: E402
from hydraulik.exceptions import ComponentParamError, NetworkValidationError  # noqa: E402
from hydraulik.params import UNIT_GROUPS, Param, parse_params  # noqa: E402
from hydraulik.schema import json_schema  # noqa: E402
from hydraulik.yamlio import load_document  # noqa: E402

ROOT = Path(__file__).parent.parent
FILES = sorted(ROOT.glob("examples/*.yaml")) + [ROOT / "schaltung.yaml"] + [
    ROOT / "tests/data/blockstil_kreis.yaml", ROOT / "tests/data/alt_editor_export.yaml"]

SCHEMA = json_schema()
AIR_SCHEMA = json_schema("air")
VALIDATOR = Draft202012Validator(SCHEMA)
AIR_VALIDATOR = Draft202012Validator(AIR_SCHEMA)


def _errors(validator, instance) -> list:
    return list(validator.iter_errors(instance))


def _cases(registry):
    for tname in sorted(registry):
        for p in registry[tname].PARAMS:
            for key in p.accepted_keys():
                yield tname, p, key


# --- Gültigkeit und Beispiele ---------------------------------------------------

@pytest.mark.parametrize("schema", [SCHEMA, AIR_SCHEMA], ids=["hydraulik", "lueftung"])
def test_schema_ist_gueltiges_draft_2020_12(schema):
    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    json.dumps(schema)                                   # serialisierbar


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_beispieldateien_validieren(path):
    doc = load_document(path)
    assert _errors(VALIDATOR, doc) == []
    h.load(doc)                                          # und der Loader akzeptiert sie


def test_luftbeispiel_validiert():
    doc = {"components": {
        "aul": {"type": "aussenluft", "t_C": -12.0, "rh": 80, "ts": "1",
                "bems": [{"id": "x'AI1", "key": "T_AUL"}]},
        "w": {"type": "wrg", "adiab_exhaust": False}, "fol": {"type": "fortluft"}},
        "connections": [["aul.out", "w.sup_in"]],
        "layout": {"components": {"aul": {"x": 1, "y": 2}}, "wires": []}}
    assert _errors(AIR_VALIDATOR, doc) == []
    bad = {**doc, "connections": [["aul.out", "w.sup_in", "fol.in"]]}   # Luft: nur Paare
    assert _errors(AIR_VALIDATOR, bad)


def test_alle_typen_und_schluessel_im_schema():
    for registry, schema in ((COMPONENT_REGISTRY, SCHEMA), (AIR_REGISTRY, AIR_SCHEMA)):
        defs = schema["$defs"]
        assert set(schema["$defs"]["component"]["properties"]["type"]["enum"]) == set(registry)
        for tname, p, key in _cases(registry):
            props = defs[f"type:{tname}"]["properties"]
            assert key in props, (tname, key)
            assert {"type", "ts", "bems", "description"} <= set(props)
            assert defs[f"type:{tname}"]["additionalProperties"] is False


def test_schema_entsteht_nur_aus_der_registry(monkeypatch):
    """Ein neu registrierter Typ erscheint ohne jede Schema-Pflege."""
    from hydraulik.components.base import TwoPortComponent

    class Probe(TwoPortComponent):
        """Probekomponente nur für den Test."""
        type_name = "probe_x"
        PARAMS = (Param("dp", "pressure", required=True, minv=0.0, maxv=1e5, help="Probe-Δp"),
                  Param("art", "str", default="a", choices=("a", "b")))

        def hydraulic_coefficients(self, q, fluid):          # pragma: no cover
            raise NotImplementedError

    monkeypatch.setitem(COMPONENT_REGISTRY, "probe_x", Probe)
    s = json_schema()
    d = s["$defs"]["type:probe_x"]
    assert set(UNIT_GROUPS["pressure"]) == {k[3:] for k in d["properties"] if k.startswith("dp_")}
    assert d["properties"]["dp_kPa"]["maximum"] == 100.0 and d["properties"]["dp_bar"]["maximum"] == 1.0
    assert d["properties"]["art"]["enum"] == ["a", "b"]
    v = Draft202012Validator(s)
    assert not _errors(v, {"components": {"p": {"type": "probe_x", "dp_kPa": 5}},
                           "connections": [["p.in", "p.out"]]})
    assert _errors(v, {"components": {"p": {"type": "probe_x"}}, "connections": [["p.in", "p.out"]]})


# --- Konsistenz Schema ↔ Loader, je Typ und Parameter ----------------------------

def _prop_schema(schema, tname, key):
    return schema["$defs"][f"type:{tname}"]["properties"][key]


def _loader_errors(tname, registry, key, value) -> list[str]:
    _, errs = parse_params(tname, registry[tname].PARAMS, {key: value})
    return [e for e in errs if f"'{key}'" in e]


def _factor(p: Param, key: str) -> float:
    return 1.0 if p.group in ("none", "int", "str", "bool") else UNIT_GROUPS[p.group][key[len(p.name) + 1:]]


def _bad_values(p: Param, key: str) -> list:
    """Werte, die BEIDE (Schema und Loader) ablehnen müssen."""
    if p.group == "str":
        return [1, True, None, [], {}] + (["gibt_es_nicht"] if p.choices else [])
    if p.group == "bool":
        return ["yes", "true", 1, 0, None]
    bad = ["abc", "1.5", True, None, [1]] + ([2.5] if p.group == "int" else [])
    f = _factor(p, key)
    if p.minv is not None:
        lo = p.minv / f
        bad.append(lo - max(abs(lo) * 1e-3, 1e-3))
    if p.maxv is not None:
        hi = p.maxv / f
        bad.append(hi + max(abs(hi) * 1e-3, 1e-3))
    return bad


def _good_value(p: Param, key: str):
    if p.group == "str":
        return p.choices[-1] if p.choices else "Text"
    if p.group == "bool":
        return True
    f = _factor(p, key)
    lo = None if p.minv is None else p.minv / f
    hi = None if p.maxv is None else p.maxv / f
    if lo is not None and hi is not None:
        v = (lo + hi) / 2
    elif lo is not None:
        v = lo + max(abs(lo), 1.0)
    elif hi is not None:
        v = hi - max(abs(hi), 1.0)
    else:
        v = 1.5
    return int(round(v)) if p.group == "int" else v


@pytest.mark.parametrize("registry, schema", [(COMPONENT_REGISTRY, SCHEMA), (AIR_REGISTRY, AIR_SCHEMA)],
                         ids=["hydraulik", "lueftung"])
def test_konsistenz_je_parameter(registry, schema):
    """Für JEDEN Typ, Parameter und Suffix-Schlüssel: falscher Typ bzw. Wert
    außerhalb des Bereichs → Fehler in Schema UND Loader; gültiger Wert → beide ok."""
    problems = []
    for tname, p, key in _cases(registry):
        validator = Draft202012Validator(_prop_schema(schema, tname, key))
        good = _good_value(p, key)
        if _errors(validator, good) or _loader_errors(tname, registry, key, good):
            problems.append(f"{tname}.{key}={good!r}: gültiger Wert abgelehnt")
        for bad in _bad_values(p, key):
            in_schema = bool(_errors(validator, bad))
            in_loader = bool(_loader_errors(tname, registry, key, bad))
            if not (in_schema and in_loader):
                problems.append(f"{tname}.{key}={bad!r}: Schema={in_schema} Loader={in_loader}")
    assert problems == []


@pytest.mark.parametrize("registry, schema", [(COMPONENT_REGISTRY, SCHEMA), (AIR_REGISTRY, AIR_SCHEMA)],
                         ids=["hydraulik", "lueftung"])
def test_konsistenz_pflicht_alternativen_unbekannte_schluessel(registry, schema):
    validator = Draft202012Validator({"$defs": schema["$defs"], "$ref": "#/$defs/component"})
    problems = []
    for tname, cls in sorted(registry.items()):
        # Pflichtparameter: Schema und Loader verlangen dieselben
        _, errs = parse_params(tname, cls.PARAMS, {})
        loader_req = {p.name for p in cls.PARAMS if any(f"'{p.display_key()}'" in e for e in errs)}
        missing = set()                                    # fehlende Schlüssel laut Schema
        for e in _errors(validator, {"type": tname}):
            if e.validator == "required":
                missing.update(k for k in e.validator_value if k not in {"type"})
            elif e.validator == "anyOf":
                missing.update(sub["required"][0] for sub in e.validator_value if "required" in sub)
        schema_req = {p.name for p in cls.PARAMS if missing & set(p.accepted_keys())}
        if loader_req != schema_req:
            problems.append(f"{tname}: Pflicht Loader={sorted(loader_req)} Schema={sorted(schema_req)}")
        for p in cls.PARAMS:
            keys = p.accepted_keys()
            if len(keys) > 1:                              # genau EINE Alternative
                spec = {"type": tname, keys[0]: _good_value(p, keys[0]), keys[1]: _good_value(p, keys[1])}
                if not _errors(validator, spec) or not any("mehrfach" in e for e in
                                                           parse_params(tname, cls.PARAMS, spec)[1]):
                    problems.append(f"{tname}: {keys[0]} + {keys[1]} nicht in beiden abgelehnt")
        spec = {"type": tname, "zz_unbekannt": 1}
        if not _errors(validator, spec) or not any("zz_unbekannt" in e for e in
                                                   parse_params(tname, cls.PARAMS, {"zz_unbekannt": 1})[1]):
            problems.append(f"{tname}: unbekannter Schlüssel nicht in beiden abgelehnt")
    assert problems == []


@pytest.mark.parametrize("value, ok", [("08", True), (4, True), ("TS 1", True), (1.5, False),
                                       (True, False), ([1], False), ({"a": 1}, False)])
def test_konsistenz_ts(value, ok):
    v = Draft202012Validator({"$defs": SCHEMA["$defs"], "$ref": "#/$defs/ts"})
    assert (not _errors(v, value)) is ok
    try:
        h.Cap("c", ts=value)
        loader_ok = True
    except ComponentParamError:
        loader_ok = False
    assert loader_ok is ok


@pytest.mark.parametrize("bems, ok", [
    ([{"id": "x'AI1", "key": "K", "description": "d"}], True), ({"id": "x"}, True),
    ([{"id": 123}], True), ([{"id": 1.5}], False), ([{"foo": "x"}], False), (["x"], False),
    ("x", False), ([{"id": True}], False)])
def test_konsistenz_bems(bems, ok):
    v = Draft202012Validator({"$defs": SCHEMA["$defs"], "$ref": "#/$defs/bems"})
    assert (not _errors(v, bems)) is ok
    try:
        h.Cap("c", bems=bems)
        loader_ok = True
    except ComponentParamError:
        loader_ok = False
    assert loader_ok is ok


@pytest.mark.parametrize("pipes, ok", [
    ([{"length_m": 5, "d_inner_mm": 20}], True), ({"length_m": 5}, True), ([], True),
    ([{"d_inner_mm": 20}], False), ([{"length_m": 5, "zz": 1}], False), ([{"length_m": -1}], False),
    ([{"length_m": 5, "length_cm": 500}], False), ("x", False), ([1], False)])
def test_konsistenz_pipes_liste(pipes, ok):
    v = Draft202012Validator(SCHEMA["$defs"]["type:conduit"]["properties"]["pipes"])
    assert (not _errors(v, pipes)) is ok
    try:
        h.Conduit("c", pipes=pipes)
        loader_ok = True
    except ComponentParamError:
        loader_ok = False
    assert loader_ok is ok


def _base(**top):
    return {"components": {"c": {"type": "cap"}}, "connections": [["c.port", "c.port"]], **top}


@pytest.mark.parametrize("fluid, ok", [
    ({"preset": "water", "t_C": 40}, True), ({"preset": "water"}, True),
    ({"rho": 1040, "mu": 3.5e-3, "cp": 3600, "name": "glykol"}, True),
    ({"preset": "oil"}, False), ({"preset": "water", "t_C": "warm"}, False),
    ({"preset": "water", "rho": 998}, False), ({"rho": 998, "mu": 1e-3}, False),
    ({"rho": -1, "mu": 1e-3, "cp": 4180}, False), ({"preset": "water", "t_c": 40}, False),
    ("water", False), ({}, False)])
def test_konsistenz_fluid(fluid, ok):
    doc = _base(fluid=fluid)
    assert (not _errors(VALIDATOR, doc)) is ok
    try:
        h.load(doc)
        loader_ok = True
    except NetworkValidationError:
        loader_ok = False
    assert loader_ok is ok


@pytest.mark.parametrize("settings, ok", [
    ({"alpha_p": 0.6, "max_iter": 400}, True), ({"max_iter": 400.0}, True), ({}, True),
    ({"alpha_p": 0}, False), ({"alpha_p": 1.5}, False), ({"max_iter": 0}, False),
    ({"max_iter": 10.5}, False), ({"tol_t": -1}, False), ({"alpha_pp": 0.5}, False),
    ({"p_ref": "hoch"}, False), ({"t_init": True}, False), ("schnell", False)])
def test_konsistenz_settings(settings, ok):
    doc = _base(settings=settings)
    assert (not _errors(VALIDATOR, doc)) is ok
    try:
        h.load_settings(doc)
        loader_ok = True
    except NetworkValidationError:
        loader_ok = False
    assert loader_ok is ok


@pytest.mark.parametrize("doc", [
    {"components": {}, "connections": [["a.b", "c.d"]]},                       # leer
    {"components": {"c": {"type": "cap"}}},                                    # connections fehlt
    {"components": {"c": {"type": "cap"}}, "connections": [["c.port"]]},       # < 2 Ports
    {"components": {"c": {"type": "gibts_nicht"}}, "connections": [["c.port", "c.port"]]},
    {"components": {"c": {"kvs_m3h": 1}}, "connections": [["c.port", "c.port"]]},   # type fehlt
    {**_base(), "extra": 1},                                                   # unbekannt oben
])
def test_konsistenz_struktur(doc):
    assert _errors(VALIDATOR, doc)
    with pytest.raises(NetworkValidationError):
        h.load(doc)


def test_beschreibungen_kennzeichnen_loader_pruefungen():
    """Nicht als Schema Ausdrückbares ist per description gekennzeichnet."""
    text = json.dumps(SCHEMA, ensure_ascii=False)
    assert "prüft der Loader" in text
    conn = SCHEMA["properties"]["connections"]
    assert "Loader" in conn["description"]
    assert "Ports:" in SCHEMA["$defs"]["type:manifold"]["description"]


# --- CLI ---------------------------------------------------------------------------

def test_cli_schema(tmp_path):
    out = tmp_path / "s.json"
    proc = subprocess.run([sys.executable, "-m", "hydraulik.cli", "schema", "--out", str(out)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(out.read_text(encoding="utf-8")) == json.loads(json.dumps(SCHEMA))
    proc = subprocess.run([sys.executable, "-m", "hydraulik.cli", "schema", "--luft"],
                          capture_output=True, text=True)
    assert proc.returncode == 0 and json.loads(proc.stdout) == json.loads(json.dumps(AIR_SCHEMA))
