# Neue Komponenten hinzufügen

Eine Komponente ist eine Klasse mit (1) Parameterdeklaration, (2) hydraulischem
Vertrag, (3) optional thermischem Vertrag. Solver bleiben unberührt.

## Zweitor-Komponente (Standardfall)

Beispiel: Rückschlagklappe mit festem ζ-Wert — Schema (eine Kv-basierte
Variante ist inzwischen als `check_valve` im Paket enthalten):

```python
# src/hydraulik/components/check_valve.py
from ..fluids import Fluid
from ..friction import zeta_to_b
from ..params import Param
from .base import EdgeCoefficients, ThermalResult, TwoPortComponent
from .registry import register

@register("check_valve")                       # YAML-Typname
class CheckValve(TwoPortComponent):
    d_inner: float                             # Annotation je Parameter (für IDE/Pyright)
    zeta: float

    PARAMS = (
        Param("d_inner", "length", required=True, minv=1e-3, help="Innendurchmesser"),
        Param("zeta", "none", default=2.5, minv=0.0, help="Widerstandsbeiwert offen"),
    )

    def hydraulic_coefficients(self, q: float, fluid: Fluid) -> EdgeCoefficients:
        # wird JEDE Iteration mit dem aktuellen q aufgerufen → darf von q abhängen
        b = zeta_to_b(self.zeta, self.d_inner, fluid.rho)
        if q < 0.0:
            b *= 1e6                           # Sperrrichtung: hoher Widerstand
        return EdgeCoefficients(a=0.0, b=b)

    # thermal_outlet weglassen → adiabat. Sonst:
    def thermal_outlet(self, t_in: float, m_dot: float, fluid: Fluid) -> ThermalResult:
        return ThermalResult(t_out=t_in, q_dot=0.0)
```

Dann in `components/__init__.py` importieren (füllt das Register) und in
`__all__` aufnehmen. Fertig — YAML-Typ `check_valve` existiert inklusive
Parametervalidierung.

## Regeln für die Verträge

- `hydraulic_coefficients` liefert `a` [Pa/(m³/s)] und `b` [Pa/(m³/s)²] der
  Impulsgleichung `Δp = a·Q + b·Q|Q| − dp_source`. Muss für beliebige q
  (auch 0 und negativ) endliche Werte liefern; nie b = a = 0 (sonst greift
  nur der globale R-Floor). Unstetige Widerstände (wie oben die
  Sperrrichtung) verkraftet der Solver dank Divergenz-Wächter, glatte
  Übergänge konvergieren aber schneller.
- `thermal_outlet` bekommt IMMER `m_dot = |ṁ| > 0` und die stromauf
  liegende Temperatur; Vorzeichen/Upwind übernimmt der Solver.
  `q_dot > 0` heißt Wärme ins Wasser. Wichtig für Kreiskonvergenz:
  |∂t_out/∂t_in| ≤ 1 einhalten (physikalische Modelle erfüllen das).
- `q_seed()` überschreiben, wenn ein Nennvolumenstrom bekannt ist
  (besserer Startwert, definiert auch den R-Floor-Maßstab).
- Neue Parameter-Einheiten: Gruppe in `params.UNIT_GROUPS` ergänzen.
- Reservierte Kwargs (Basisklasse, VOR der Parameterprüfung abgeräumt):
  `ts=<label>` (Teilstrecken-Gruppierung), `bems=[{id, key, description}, …]`
  (BEMS-Messpunktliste, base._parse_bems). Zusätzlich hängt der
  @register-Dekorator jedem Typ automatisch den Param `description` an.
- Editor: Betriebsarten-Felder deklariert PARAM_MODES in
  editor_template.html (nur relevante Felder je Modus sichtbar).
- Reserviertes Kwarg im Detail: `ts=<label>` wird von der Basisklasse VOR der
  Param-Auswertung abgegriffen (Teilstrecken-Gruppierung) — kein eigener
  Parameter darf `ts` heißen.

## Mehrtor-Komponente

Von `Component` erben, `port_names()` und `build(b)` implementieren.
Der Builder bietet:

```python
b.port("name")                      # Portelement dieser Komponente
b.internal("label")                 # zusätzlicher interner Knoten
b.alias(el1, el2)                   # Elemente zu einem Knoten verschmelzen
b.edge(el_i, el_j, coeff_fn, thermal_fn, fixed_q=None, q_seed=None, label="x")
b.node_heat_loss(el, ua, t_amb)     # UA-Verlust am Knoten
b.pressure_bc(el, p, t_supply)      # Druck-Randbedingung
b.flow_bc(el, q, t_supply)          # Fluss-Randbedingung (q > 0 = ins Netz)
```

Vorbilder: `valves.MixingValve3Way` (2 Kanten auf einen Mischknoten),
`separators.HydraulicSeparator` (2 Knoten + interne Kante),
`storage.BufferStorage` (Aliase + UA).

Kanten mit `label` erscheinen im Ergebnis als `"name:label"`
(z.B. `result["mv1:a"]`).

## Tests

Für jede neue Komponente mindestens: einen analytisch nachrechenbaren
Fall (Handformel) und einen Einbau in einen lösbaren Kreis mit
Energiebilanz-Check. Muster: tests/test_thermal.py.
