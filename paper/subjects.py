"""Where each stage keeps its outputs, and the loaders the later stages use. Pilot outputs live under
runs/pilot/oopt; canary and full share runs/shared/oopt (one selection, one baseline, one sizing)."""
from __future__ import annotations

import hashlib

from .env import read_json, scope_dir
from .libraries import LIBS, ORDER, PILOT_LIBRARY_DEFAULT

RUNGS = ("R1", "R2", "R3")
SAMPLES = (1, 2)                     # k = 2 samples per (method, rung, model)
CANDIDATES_PER_LIB = 60              # 50 used + spares for baseline drops
USE_PER_LIB = 50
CAP_TOTAL = 250
FLOOR_TOTAL = 150
FLOOR_PER_LIB = 40
PILOT_METHODS = 3


def libs_for(mode: str) -> list[str]:
    return [PILOT_LIBRARY_DEFAULT] if mode == "pilot" else list(ORDER)


def short(s: str, n: int = 12) -> str:
    return hashlib.sha1(s.encode()).hexdigest()[:n]


def build_info(mode: str, lib: str) -> dict | None:
    return read_json(scope_dir(mode) / "build" / f"{lib}.json")


def candidates(mode: str) -> list[dict]:
    """All selected candidate methods of the mode's scope, library order then round-robin order."""
    out = []
    for lib in libs_for(mode):
        d = read_json(scope_dir(mode) / "select" / f"{lib}.json") or {}
        out.extend(d.get("methods", []))
    return out


def baseline(mode: str, file_key: str) -> dict | None:
    return read_json(scope_dir(mode) / "baseline" / f"{file_key}.json")


def file_key(m: dict) -> str:
    return f"{m['lib']}-{short(m['lib'] + ':' + m['file'])}"


def eligible(mode: str) -> list[dict]:
    """Candidates whose source file passed the baseline (recompiles under the scoring pipeline)."""
    out = []
    for m in candidates(mode):
        b = baseline(mode, file_key(m))
        if b and b.get("ok"):
            out.append(m)
    return out


def interleave(methods: list[dict], per_lib: int) -> list[dict]:
    """Round-robin over libraries in ORDER, at most per_lib from each, keeping each library's own order."""
    by = {lib: [m for m in methods if m["lib"] == lib] for lib in ORDER}
    out, i = [], 0
    while True:
        took = False
        for lib in ORDER:
            q = by.get(lib) or []
            if i < len(q) and i < per_lib:
                out.append(q[i])
                took = True
        if not took:
            return out
        i += 1


def methods_by_id(mode: str) -> dict:
    return {m["id"]: m for m in candidates(mode)}


__all__ = ["LIBS", "RUNGS", "SAMPLES", "libs_for", "candidates", "eligible", "baseline", "file_key", "interleave",
           "methods_by_id", "build_info", "short"]
