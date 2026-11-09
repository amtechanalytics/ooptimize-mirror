"""Compute cost (instance + root volume) so the ceiling covers API and compute together."""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone

HOURS_PER_MONTH = 730.0


def hourly_rate(cfg) -> float:
    """Instance $/h + root volume $/h from prices.yaml compute section and config/compute.yaml."""
    comp = cfg.prices.get("compute") or {}
    itype = cfg.compute.get("instance_type")
    inst = float(comp[itype]["per_hour"])
    gp3 = float(comp.get("gp3_per_gb_month", 0.0))
    vol = float(cfg.compute.get("root_volume_gb", 0)) * gp3 / HOURS_PER_MONTH
    return inst + vol


def projected(cfg) -> float:
    return float(cfg.project.get("expected_hours", 0)) * hourly_rate(cfg)


def _boots_path():
    from .util import RUNS
    return RUNS / "compute.json"


def touch_boot() -> None:
    """Called every monitor tick: records how long this instance has been up, so a resumed run (new instance,
    new RH_BOOT_TS) still counts the compute of the instances before it (runs/compute.json is in the backup)."""
    boot = os.environ.get("RH_BOOT_TS")
    if not boot:
        return
    from .util import read_json, write_json
    d = read_json(_boots_path(), default={}) or {}
    d.setdefault("boots", {})[str(int(float(boot)))] = time.time()
    write_json(_boots_path(), d)


def _previous_hours(boot: str) -> float:
    from .util import read_json
    d = read_json(_boots_path(), default={}) or {}
    cur = str(int(float(boot)))
    return sum(max(0.0, last - float(b)) for b, last in (d.get("boots") or {}).items() if b != cur) / 3600.0


def elapsed_hours(started_iso: str | None = None) -> float:
    boot = os.environ.get("RH_BOOT_TS")
    if boot:
        return _previous_hours(boot) + max(0.0, (time.time() - float(boot)) / 3600.0)
    elif started_iso:
        t0 = datetime.strptime(started_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    else:
        return 0.0
    return max(0.0, (time.time() - t0) / 3600.0)


def so_far(cfg, started_iso: str | None = None) -> float:
    return elapsed_hours(started_iso) * hourly_rate(cfg)


def remaining(cfg, started_iso: str | None = None) -> float:
    left = max(0.0, float(cfg.project.get("expected_hours", 0)) - elapsed_hours(started_iso))
    return left * hourly_rate(cfg)
