"""Luftseitige Komponenten der Lüftungsanlage (VKA nach EN 16798-5-1).

Eigene Registry (AIR_REGISTRY), aber dieselbe deklarative Mechanik wie die
Hydraulik: Param-Deklarationen (Einheiten-Suffixe, Validierung, Katalog-
export) und die Component-Basisklasse (ts/bems/description) werden
wiederverwendet. Die Komponenten bilden 1:1 auf die simulate()-Konfiguration
des VKA-Rechenkerns (hydraulik.air.vka) ab; deklarative Elemente (Filter,
Schalldämpfer, Sensoren) tragen nur BEMS-Semantik und Position.

Zeichnungslogik: zwei lineare Stränge — Zuluft (aussenluft → … → zuluft)
und Abluft (abluft_raum → … → fortluft), verbunden über die WRG
(Ports zul_in/zul_out/abl_in/abl_out). Umluft sitzt als Zweitor im
Zuluftstrang (Token UML_Byp); die Beimischmenge ist Parameter.
"""
from __future__ import annotations

from ..components.base import Component
from ..components.registry import _DESCRIPTION_PARAM
from ..params import Param

AIR_REGISTRY: dict[str, type[Component]] = {}


def register_air(type_name: str):
    def deco(cls: type[Component]):
        cls.type_name = type_name
        if not any(p.name == "description" for p in cls.PARAMS):
            cls.PARAMS = tuple(cls.PARAMS) + (_DESCRIPTION_PARAM,)
        AIR_REGISTRY[type_name] = cls
        return cls
    return deco


class AirComponent(Component):
    """Basis: Luftkomponente — kein hydraulischer Netzaufbau."""

    def build(self, b) -> None:                     # pragma: no cover - nie gerufen
        raise NotImplementedError("Luftkomponenten werden nicht im Hydrauliknetz verbaut.")

    def port_names(self) -> tuple[str, ...]:
        return ("in", "out")


# ---------------------------------------------------------------- Randzustände

@register_air("aussenluft")
class Aussenluft(AirComponent):
    """Außenluft (AUL): Eintrittszustand des Zuluftstrangs."""

    PARAMS = (
        Param("t", "temperature", required=True, help="Außenlufttemperatur"),
        Param("rh", "none", required=True, minv=0.0, maxv=100.0,
              help="relative Feuchte Außenluft [%]"),
    )

    def port_names(self):
        return ("out",)


@register_air("abluft_raum")
class AbluftRaum(AirComponent):
    """Abluft/Raumzustand (ABL): WRG-Quelle; bei Raumkopplung die Raumtemperatur."""

    PARAMS = (
        Param("t", "temperature", required=True, help="Abluft-/Raumtemperatur"),
        Param("rh", "none", minv=0.0, maxv=100.0,
              help="relative Feuchte Abluft [%] (Pflicht außer bei Regelung 'raum')"),
    )

    def port_names(self):
        return ("out",)


@register_air("zuluft")
class Zuluft(AirComponent):
    """Zuluft (ZUL): Volumenstrom + Regelungsart.

    regelung = fest  → Zuluftzustand gepinnt (t, rh)
    regelung = band  → Zuluft-Sollband (t_min…t_max, rh_min…rh_max)
    regelung = raum  → raumgekoppelt (simulate_room): Zuluft-T-Band plus
                       Raumfeuchteband und interne Feuchtelast; die
                       Raumtemperatur kommt aus abluft_raum.t
    """

    PARAMS = (
        Param("v", "flow", required=True, minv=1e-6, help="Zuluft-Volumenstrom"),
        Param("regelung", "str", default="band", choices=("fest", "band", "raum"),
              help="Regelungsart der Zuluft"),
        Param("t", "temperature", help="Zulufttemperatur (regelung=fest)"),
        Param("rh", "none", minv=0.0, maxv=100.0, help="Zuluftfeuchte [%] (regelung=fest)"),
        Param("t_min", "temperature", help="Sollband untere Zulufttemperatur"),
        Param("t_max", "temperature", help="Sollband obere Zulufttemperatur"),
        Param("rh_min", "none", minv=0.0, maxv=100.0, help="Sollband untere Feuchte [%]"),
        Param("rh_max", "none", minv=0.0, maxv=100.0, help="Sollband obere Feuchte [%]"),
        Param("raum_rh_min", "none", minv=0.0, maxv=100.0,
              help="Raumfeuchteband unten [%] (regelung=raum)"),
        Param("raum_rh_max", "none", minv=0.0, maxv=100.0,
              help="Raumfeuchteband oben [%] (regelung=raum)"),
        Param("feuchtelast", "none", minv=0.0,
              help="interne Feuchtelast [g/h] (regelung=raum, z.B. Personen ≈ 60 g/h)"),
    )

    def check_params(self):
        errs = []
        need = {"fest": ("t", "rh"),
                "band": ("t_min", "t_max", "rh_min", "rh_max"),
                "raum": ("t_min", "t_max", "raum_rh_min", "raum_rh_max", "feuchtelast")}
        for f in need[self.regelung]:
            if getattr(self, f) is None:
                errs.append(f"Regelung '{self.regelung}': Parameter '{f}' fehlt.")
        return errs or None

    def port_names(self):
        return ("in",)


@register_air("fortluft")
class Fortluft(AirComponent):
    """Fortluft (FOL): Austritt des Abluftstrangs; Volumenstrom optional
    (ohne Angabe = Zuluftvolumenstrom, balancierte Anlage)."""

    PARAMS = (
        Param("v", "flow", minv=0.0, help="Abluft-/Fortluft-Volumenstrom (optional)"),
    )

    def port_names(self):
        return ("in",)


# --------------------------------------------------------- aktive Komponenten

@register_air("wrg")
class Waermerueckgewinnung(AirComponent):
    """Wärmerückgewinnung: Rotor (Sorption/Enthalpie/Kondensation), KVS oder
    Plattentauscher. Verbindet Zuluft- und Abluftstrang."""

    PARAMS = (
        Param("typ", "str", default="ROT_SORP",
              choices=("ROT_SORP", "ROT_HYG", "ROT_NH", "KVS", "PLATE"),
              help="WRG-Bauart"),
        Param("eta_hr_n", "none", default=0.0, minv=0.0, maxv=1.0,
              help="Referenz-Temperaturübertragungsgrad (0 = Normwert)"),
        Param("eta_xr_n", "none", default=0.0, minv=0.0, maxv=1.0,
              help="Referenz-Feuchteübertragungsgrad (0 = Normwert)"),
        Param("v_nom", "flow", default=4500.0 / 3600.0, minv=1e-6,
              help="Auslegungsvolumenstrom der WRG"),
        Param("adiab_exhaust", "bool", default=False,
              help="adiabate Abluftkühlung (nur ROT_HYG/ROT_NH)"),
        Param("rwz_n", "none", minv=0.0,
              help="Auslegungs-Rückwärmzahl (KVS/Plattentauscher; leer = Normwert)"),
        Param("v_m_kvs", "flow", default=2.5 / 3600.0, minv=1e-9,
              help="KVS: Nennvolumenstrom des Sole-Zwischenkreises"),
    )

    def port_names(self):
        return ("zul_in", "zul_out", "abl_in", "abl_out")


@register_air("frostschutz")
class Frostschutz(AirComponent):
    """Frostschutz der WRG: Vorwärmung oder Bypass (Bypass setzt Vorheizer voraus)."""

    PARAMS = (
        Param("modus", "str", default="preheater", choices=("preheater", "bypass"),
              help="Frostschutzart"),
        Param("t_fs", "temperature", default=-3.0,
              help="Frostschutz-Grenztemperatur (Sorptionsrotor: niedrig ansetzen)"),
    )


@register_air("vorheizer")
class Vorheizer(AirComponent):
    """Vorheizer (VHR): ideales Heizregister; die Leistung ist Ergebnis."""

    PARAMS = ()


@register_air("kuehler")
class Kuehler(AirComponent):
    """Kühler (KR) mit Entfeuchtung; die Leistung ist Ergebnis."""

    PARAMS = ()


@register_air("nachheizer")
class Nachheizer(AirComponent):
    """Nachheizer (NHR); die Leistung ist Ergebnis."""

    PARAMS = ()


@register_air("befeuchter")
class Befeuchter(AirComponent):
    """Befeuchter: Dampf (vor dem Kühler) oder Sprüh/adiabat (nach dem Kühler)."""

    PARAMS = (
        Param("typ", "str", default="steam", choices=("steam", "spray"),
              help="Befeuchterart"),
    )


@register_air("ventilator_luft")
class VentilatorLuft(AirComponent):
    """Ventilator: spezifische Ventilatorleistung SFP (Anteil dieses
    Ventilators; die Anteile beider Stränge werden summiert)."""

    PARAMS = (
        Param("sfp", "none", default=1250.0, minv=0.0,
              help="spezifische Ventilatorleistung [W/(m³/s)]"),
    )


@register_air("umluft")
class Umluft(AirComponent):
    """Umluft-Bypass (UML_Byp): Beimischung von Abluft in den Zuluftstrang."""

    PARAMS = (
        Param("v", "flow", required=True, minv=0.0, help="Umluft-Volumenstrom"),
    )


# ------------------------------------------------ deklarative Elemente/Sensoren

def _declarative(type_name: str, doc: str, ports: tuple = ("in", "out")):
    @register_air(type_name)
    class _D(AirComponent):
        PARAMS = ()

        def port_names(self):
            return ports
    _D.__doc__ = doc
    _D.__name__ = type_name.title().replace("_", "")
    return _D


Filter = _declarative("filter_luft", "Filter (im Strang; BEMS z.B. Δp-Überwachung).")
Schalldaempfer = _declarative("schalldaempfer", "Schalldämpfer (im Strang).")

#: Sensortypen sind ANZAPFUNGEN: ein Messanschluss (Δp: zwei), verbunden über
#: eine Messleitung mit einem beliebigen Kanal-Anschluss — sie liegen NICHT im
#: Strang und beeinflussen die Kette nicht (Adapter/Loader behandeln
#: Messleitungen separat).
Kombisensor = _declarative("kombisensor_luft",
                           "Kombifühler Temperatur + Feuchte (Messstelle im Kanal).", ("port",))
TempSensorLuft = _declarative("temperatursensor_luft", "Temperaturfühler im Kanal.", ("port",))
FeuchteSensorLuft = _declarative("feuchtesensor_luft", "Feuchtefühler im Kanal.", ("port",))
DpSensorLuft = _declarative("differenzdrucksensor_luft",
                            "Differenzdrucksensor (z.B. über Filter/Ventilator).",
                            ("plus", "minus"))
DruckSensorLuft = _declarative("drucksensor_luft", "Statischer Drucksensor im Kanal.", ("port",))
VdotSensorLuft = _declarative("volumenstromsensor_luft", "Volumenstromsensor im Kanal.", ("port",))
EnergiezaehlerLuft = _declarative("energiezaehler_luft", "Energiezähler (luftseitig).", ("port",))
Stromzaehler = _declarative("stromzaehler", "Elektrischer Stromzähler (z.B. Ventilator).", ("port",))

#: Namen der Anzapf-Sensortypen (Messleitungs-Semantik)
AIR_SENSOR_TYPES = frozenset({
    "kombisensor_luft", "temperatursensor_luft", "feuchtesensor_luft",
    "differenzdrucksensor_luft", "drucksensor_luft", "volumenstromsensor_luft",
    "energiezaehler_luft", "stromzaehler"})
