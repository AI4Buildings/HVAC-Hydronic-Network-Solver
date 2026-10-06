"""Abgleich des VKA-Kerns im Repo mit der Skill-Kopie vka-effizienz-en16798.

    python3 tools/vka_skill_abgleich.py [SKILL_SCRIPTS_DIR] [--streng]

Der Kern src/hydraulik/air/vka/ ist aus dem Skill übernommen und physikalisch
identisch mit ihm zu halten (Korrekturen L1–L12 der Solver-Prüfung 2026-10
stehen in beiden). Unterschiede, die erwartet sind und hier ausgeglichen
werden:
- Importe: im Paket relativ (`from .x import`), im Skill absolut, dazu in
  simulate.py `sys.path.insert(…)` für den Skriptaufruf;
- simulate.py im Repo liefert zusätzlich die Stationszustände für die
  Editor-Tooltips (Block „Stationszustände …“, ohne Einfluss auf die
  validierten Größen); --streng vergleicht auch diesen Block.

Rückgabe 0 = identisch, 1 = Abweichungen (Diff wird ausgegeben). Nach einer
Kernänderung die Skill-Kopie von Hand nachziehen und dort
`python3 validate_vka.py` laufen lassen (PDF-Referenz exakt).
"""
from __future__ import annotations

import difflib
import re
import sys
from pathlib import Path

REPO_VKA = Path(__file__).resolve().parents[1] / "src" / "hydraulik" / "air" / "vka"
SKILL_DEFAULT = Path.home() / ".claude" / "skills" / "vka-effizienz-en16798" / "scripts"
SYS_PATH = "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))"


def _als_skill(text: str, name: str, streng: bool) -> list[str]:
    text = re.sub(r"^(\s*)from \. import ", r"\1import ", text, flags=re.M)
    text = re.sub(r"^(\s*)from \.(\w)", r"\1from \2", text, flags=re.M)
    lines = text.splitlines()
    if name == "simulate.py":
        i = lines.index("import numpy as np")
        lines.insert(i + 1, SYS_PATH)
        if not streng:
            a = next(k for k, s in enumerate(lines) if "# Stationszustände" in s)
            b = next(k for k, s in enumerate(lines) if 'out["x_eta_wheel_gkg"]' in s)
            del lines[a:b + 1]
    return lines


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    streng = "--streng" in sys.argv
    skill = Path(args[0]) if args else SKILL_DEFAULT
    if not skill.is_dir():
        print(f"Skill-Verzeichnis nicht gefunden: {skill}")
        return 1
    abweichend = 0
    for f in sorted(REPO_VKA.glob("*.py")):
        if f.name == "__init__.py":
            continue
        s = skill / f.name
        if not s.exists():
            print(f"FEHLT im Skill: {f.name}")
            abweichend += 1
            continue
        soll = _als_skill(f.read_text(encoding="utf-8"), f.name, streng)
        ist = s.read_text(encoding="utf-8").splitlines()
        diff = list(difflib.unified_diff(ist, soll, f"skill/{f.name}", f"repo/{f.name}",
                                         lineterm="", n=1))
        if diff:
            abweichend += 1
            print("\n".join(diff))
        else:
            print(f"identisch: {f.name}")
    print("ERGEBNIS:", "Skill-Kopie identisch" if not abweichend
          else f"{abweichend} Datei(en) abweichend")
    return 1 if abweichend else 0


if __name__ == "__main__":
    sys.exit(main())
