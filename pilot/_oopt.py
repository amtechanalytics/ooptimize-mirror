"""Shared helpers for the study's pilot tests (not a test file)."""
import json
from pathlib import Path


def scope(runs: Path) -> Path:
    return runs / "pilot" / "oopt"


def rj(p):
    return json.loads(Path(p).read_text())


def rows(p):
    p = Path(p)
    if not p.exists():
        return []
    out = {}
    for x in p.read_text().splitlines():
        if x.strip():
            r = json.loads(x)
            out[r["cell_id"]] = r
    return list(out.values())
