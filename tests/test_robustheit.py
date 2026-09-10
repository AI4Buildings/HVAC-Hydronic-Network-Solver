"""Robustheit der Fehlerpfade: Modellfehler werden lesbar eingehüllt,
unplausible Austrittstemperaturen im Bericht gemeldet, Solver-Einstellungen
typ- und bereichsgeprüft (gesammelte Meldungen wie bei Komponenten)."""
from pathlib import Path

import pytest

import hydraulik as h

EXAMPLES = sorted((Path(__file__).parent.parent / "examples").glob("*.yaml"))


class _KaputtThermisch(h.Pipe):
    def thermal_outlet(self, t_in, m_dot, fluid):
        raise ZeroDivisionError("Testfehler im Modell")


class _KaputtHydraulisch(h.Pipe):
    def hydraulic_coefficients(self, q, fluid):
        raise ValueError("Koeffizient nicht berechenbar")


def _loop(pipe_cls):
    net = h.Network()
    net.add(h.IdealStorage("sp", t_set_C=60))
    net.add(h.Pump("pu", mode="constant_flow", q_m3h=1.0))
    net.add(pipe_cls("r1", length_m=5, d_inner_mm=20))
    net.connect("sp.out", "pu.in")
    net.connect("pu.out", "r1.in")
    net.connect("r1.out", "sp.in")
    return net


def test_thermischer_modellfehler_wird_eingehuellt():
    """Statt eines rohen Tracebacks: HydraulikError mit Komponente, Modell,
    Betriebspunkt und Ursache — CLI und Server geben ihn sauber aus."""
    with pytest.raises(h.ComponentModelError) as exc:
        _loop(_KaputtThermisch).solve()
    msg = str(exc.value)
    assert "'r1'" in msg and "'pipe'" in msg and "thermisches Modell" in msg
    # Betriebspunkt des fehlgeschlagenen Aufrufs: erster Sweep startet bei t_init
    assert "T_ein = 20.00 °C" in msg and "ṁ = 2.744e-01 kg/s" in msg and "(Sweep 1)" in msg
    assert "ZeroDivisionError: Testfehler im Modell" in msg
    assert isinstance(exc.value, h.HydraulikError)
    assert isinstance(exc.value.cause, ZeroDivisionError)


def test_hydraulischer_modellfehler_wird_eingehuellt():
    with pytest.raises(h.ComponentModelError) as exc:
        _loop(_KaputtHydraulisch).solve()
    msg = str(exc.value)
    assert "'r1'" in msg and "hydraulisches Modell" in msg and "m³/h" in msg
    assert "ValueError: Koeffizient nicht berechenbar" in msg


def test_hydraulik_fehler_bleiben_unveraendert():
    """Eigene Fehlerklassen (z.B. Bilanzfehler) werden nicht doppelt verpackt."""
    net = h.Network()
    net.add(h.Pump("p1", mode="constant_flow", q_m3h=1.0))
    net.add(h.Pump("p2", mode="constant_flow", q_m3h=2.0))
    net.connect("p1.out", "p2.in")
    net.connect("p2.out", "p1.in")
    with pytest.raises(h.SingularNetworkError):
        net.solve()


def _kleinstdurchfluss_mit_fester_leistung():
    net = h.Network()
    net.add(h.IdealStorage("sp", t_set_C=70))
    net.add(h.Pump("pu", mode="constant_dp", dp_kPa=20, q_nom_m3h=0.5))
    net.add(h.ControlValve("rv", kvs_m3h=0.63, opening=0.02, rangeability=1000))
    net.add(h.Radiator("hk", q_prescribed_kW=3, kv_m3h=1.0))
    net.connect("sp.out", "pu.in")
    net.connect("pu.out", "rv.in")
    net.connect("rv.out", "hk.in")
    net.connect("hk.out", "sp.in")
    return net


def test_unplausible_austrittstemperatur_wird_gemeldet():
    """Feste Leistung hinter einem fast geschlossenen Ventil: formal lösbar,
    physikalisch sinnlos — Bericht und to_dict() müssen das benennen."""
    net = _kleinstdurchfluss_mit_fester_leistung()
    r = net.solve()
    assert r.converged
    assert r["hk"].t_out_C < -50.0
    hits = [n for n in r.notices if "'hk'" in n and "plausiblen Bereichs" in n]
    assert len(hits) == 1
    assert "kg/s" in hits[0] and "-50 … 200 °C" in hits[0]
    assert "plausiblen Bereichs" in r.report()
    assert any("plausiblen Bereichs" in n for n in r.to_dict()["notices"])
    # Grenzen sind Solver-Einstellungen
    r2 = net.solve(h.SolverSettings(t_plausible_min=-1e6))
    assert not any("plausiblen Bereichs" in n for n in r2.notices)
    # rein hydraulisch: Temperaturen bleiben Startwert → kein Hinweis
    r3 = net.solve(thermal=False)
    assert not any("plausiblen Bereichs" in n for n in r3.notices)


@pytest.mark.parametrize("path", EXAMPLES, ids=[p.stem for p in EXAMPLES])
def test_beispiele_ohne_plausibilitaetshinweis(path):
    r = h.load(path).solve(h.load_settings(path))
    assert not any("plausiblen Bereichs" in n for n in r.notices)


def test_solver_einstellungen_typ_und_bereichsgeprueft():
    ok = h.load_settings({"settings": {"alpha_p": 0.6, "max_iter": 300, "t_init": -5,
                                       "t_plausible_max": 150}})
    assert ok.alpha_p == 0.6 and ok.max_iter == 300 and ok.t_init == -5
    assert ok.t_plausible_max == 150 and ok.t_plausible_min == -50.0
    assert h.load_settings({"settings": None}).max_iter == h.SolverSettings().max_iter
    with pytest.raises(h.NetworkValidationError) as exc:
        h.load_settings({"settings": {"max_iter": "400", "alpha_q": 1.5, "tol_t": 0,
                                      "max_iter_thermal": 2.5, "alpha_x": 0.5,
                                      "p_ref": True}})
    msg = str(exc.value)
    assert "(6 Fehler)" in msg                                  # alle gesammelt
    assert "'max_iter' = '400' muss eine Ganzzahl" in msg
    assert "'alpha_q' = 1.5 muss im Bereich 0 < α ≤ 1" in msg
    assert "'tol_t' = 0 muss größer als 0" in msg
    assert "'max_iter_thermal' = 2.5 muss eine Ganzzahl" in msg
    assert "Unbekannte Solver-Einstellung 'alpha_x'. Meinten Sie 'alpha_" in msg
    assert "'p_ref' = True muss eine Zahl" in msg
    with pytest.raises(h.NetworkValidationError) as exc:
        h.load_settings({"settings": [1, 2]})
    assert "muss ein Mapping sein" in str(exc.value)


def test_alle_registrierten_typen_sind_exportiert():
    """Jede registrierte Komponentenklasse ist über hydraulik.components und
    das Paket erreichbar (BallValve fehlte im __all__)."""
    from hydraulik.components import COMPONENT_REGISTRY, __all__ as exported
    for type_name, cls in COMPONENT_REGISTRY.items():
        assert cls.__name__ in exported, f"{type_name} → {cls.__name__} nicht exportiert"
        assert getattr(h, cls.__name__) is cls
