"""Berechnet alle Beispielschaltungen und gibt die Berichte aus.

    python3 examples/run_examples.py
"""
from pathlib import Path

import hydraulik as h

for path in sorted(Path(__file__).parent.glob("*.yaml")):
    print(f"\n### {path.name}\n")
    net = h.load(path)
    result = net.solve(h.load_settings(path))
    print(result.report())
