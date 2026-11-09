"""Small shared helpers: paths, time, atomic JSON, locked JSONL appends, journal, retry."""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import random
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("RH_ROOT", os.getcwd())).resolve()
RUNS = ROOT / "runs"
RESULTS = ROOT / "results"
LOGS = ROOT / "logs"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_ts() -> float:
    return time.time()


def ensure_dirs() -> None:
    for d in (RUNS, RESULTS, LOGS):
        d.mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        return default


def write_json(path: Path, obj) -> None:
    """Atomic write: tmp file + rename, so readers never see a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}.{threading.get_ident()}")
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2, sort_keys=True, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


@contextlib.contextmanager
def file_lock(path: Path):
    """Cross-process exclusive lock (fcntl). Used for the spend ledger and JSONL appends."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def append_jsonl(path: Path, row: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, sort_keys=True, default=str) + "\n"
    with file_lock(path.with_suffix(path.suffix + ".lock")):
        with open(path, "a") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())


def read_jsonl(path: Path):
    """Yield rows; a torn last line (crash mid-write) is skipped, never fatal."""
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        return


def journal(kind: str, msg: str) -> None:
    """One line per event; the 30-minute status mail diffs this file."""
    RUNS.mkdir(parents=True, exist_ok=True)
    line = f"{now_iso()} | {kind:<9} | {msg}\n"
    with file_lock(RUNS / "journal.lock"):
        with open(RUNS / "journal.txt", "a") as f:
            f.write(line)
    print(line, end="", file=sys.stderr, flush=True)


def retry(fn, *, attempts: int = 6, base: float = 2.0, cap: float = 120.0,
          retry_on=(Exception,), on_retry=None):
    """Exponential backoff with full jitter."""
    last = None
    for i in range(attempts):
        try:
            return fn()
        except retry_on as e:  # noqa: PERF203
            last = e
            if i == attempts - 1:
                break
            delay = random.uniform(0, min(cap, base * (2 ** i)))
            hinted = getattr(e, "retry_after", None)
            if hinted:
                delay = max(delay, float(hinted))
            if on_retry:
                on_retry(i + 1, e, delay)
            time.sleep(delay)
    raise last


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def codename() -> str:
    import yaml  # local import keeps util importable without config
    p = ROOT / "config" / "project.yaml"
    with open(p) as f:
        return yaml.safe_load(f)["codename"]


def run_id() -> str:
    rid = os.environ.get("RH_RUN_ID")
    if rid:
        return rid
    p = RUNS / "run_id.txt"
    if p.exists():
        return p.read_text().strip()
    rid = "local-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    RUNS.mkdir(parents=True, exist_ok=True)
    p.write_text(rid)
    return rid
