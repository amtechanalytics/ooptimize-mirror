"""Backups and kill markers.
  python -m rh.backup sync        # every 5 min (timer), on every gate, on SIGTERM, on abort
  python -m rh.backup killpoll    # every minute (timer): S3 kill marker -> local KILL flag
  python -m rh.backup final       # verified tarball of runs/ results/ logs/ to S3
  python -m rh.backup restore     # fresh instance: pull runs/ results/ logs/ back from S3 (resume)
"""
from __future__ import annotations

import hashlib
import json
import sys
import tarfile
import time
from pathlib import Path

import yaml

from . import aws
from .util import LOGS, RESULTS, ROOT, RUNS, journal, now_iso, read_json, run_id, write_json


def _project():
    with open(ROOT / "config" / "project.yaml") as f:
        return yaml.safe_load(f)


def prefix() -> str:
    p = _project()
    return f"s3://{p['bucket']}/{p['codename']}/{run_id()}"


def sync() -> bool:
    pre = prefix()
    try:
        for d, name in ((RUNS, "runs"), (RESULTS, "results"), (LOGS, "logs")):
            if d.exists():
                aws.s3_sync(d, f"{pre}/{name}")
        write_json(RUNS / "last_backup.json", {"ts": now_iso(), "epoch": time.time(), "prefix": pre})
        return True
    except Exception as e:  # noqa: BLE001
        journal("ERROR", f"backup failed: {e}")
        last_alert = read_json(RUNS / "backup_alert.json", default={}) or {}
        if time.time() - last_alert.get("epoch", 0) > 3600:      # at most one alert per hour
            from . import notify
            notify.send("BACKUP-FAIL", "S3 sync failing", f"Backup to {pre} failed:\n{e}")
            write_json(RUNS / "backup_alert.json", {"epoch": time.time()})
        return False


STATUS_FILES = ("heartbeat.json", "state.json", "spend.json", "exit.json")


def push_status() -> None:
    """Every minute (with the kill poll): the four small files status.sh reads, so the Mac view lags at most a
    minute instead of the five-minute full sync. Best effort; the full sync remains the backup."""
    pre = prefix()
    for n in STATUS_FILES:
        f = RUNS / n
        try:
            if f.exists():
                aws.s3_cp_file(f, f"{pre}/runs/{n}")
            elif n == "exit.json":
                aws.s3_rm(f"{pre}/runs/{n}")      # runner running again: a stale exit must not show in status.sh
        except Exception:  # noqa: BLE001
            pass


def killpoll() -> bool:
    p = _project()
    push_status()
    for marker in (f"s3://{p['bucket']}/_kill/ALL", f"s3://{p['bucket']}/_kill/{p['codename']}"):
        if aws.s3_exists(marker):
            if not (RUNS / "KILL").exists():
                (RUNS / "KILL").write_text(f"{now_iso()} {marker}\n")
                journal("KILL", f"kill marker seen: {marker}")
            return True
    return False


def final() -> str:
    pre = prefix()
    out = ROOT / f"final_{run_id()}.tar.gz"
    with tarfile.open(out, "w:gz") as tar:
        for d in (RUNS, RESULTS, LOGS):
            if d.exists():
                tar.add(d, arcname=d.name, filter=lambda ti: None if ti.name.endswith((".lock",)) else ti)
    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    aws.s3_cp_file(out, f"{pre}/_final/{out.name}")
    aws.s3_put_bytes(f"{pre}/_final/{out.name}.sha256", f"{sha}  {out.name}\n".encode())
    back = aws.s3_get_bytes(f"{pre}/_final/{out.name}.sha256")
    if not back or sha not in (back.decode() if isinstance(back, bytes) else back):
        raise RuntimeError("final tarball checksum did not round-trip from S3")
    journal("BACKUP", f"final tarball {out.name} sha256 {sha[:16]} verified in S3")
    out.unlink()
    return sha


def index_update(status: str, **extra) -> None:
    """Record run status in the shared index (s3://bucket/_index/<codename>/<run_id>.json)."""
    p = _project()
    uri = f"s3://{p['bucket']}/_index/{p['codename']}/{run_id()}.json"
    try:
        raw = aws.s3_get_bytes(uri)
        d = json.loads(raw) if raw else {}
        d.update(status=status, updated=now_iso(), **extra)
        if status in ("DONE", "DEAD", "KILLED", "BUDGET"):
            d.setdefault("ended", now_iso())
        aws.s3_put_json(uri, d)
    except Exception as e:  # noqa: BLE001
        journal("ERROR", f"index update failed: {e}")


def restore() -> None:
    pre = prefix()
    for name, d in (("runs", RUNS), ("results", RESULTS), ("logs", LOGS)):
        d.mkdir(parents=True, exist_ok=True)
        aws.s3_sync_down(f"{pre}/{name}", d)
    (RUNS / "KILL").unlink(missing_ok=True)
    for p in (RUNS / "exit.json", RUNS / "heartbeat.json"):
        p.unlink(missing_ok=True)
    journal("RESTORE", f"restored runs/ results/ logs/ from {pre}")


def main(argv):
    cmd = argv[0] if argv else "sync"
    if cmd == "sync":
        return 0 if sync() else 1
    if cmd == "killpoll":
        return 3 if killpoll() else 0
    if cmd == "final":
        final()
        return 0
    if cmd == "restore":
        restore()
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
