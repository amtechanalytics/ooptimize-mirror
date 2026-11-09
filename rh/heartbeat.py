"""Liveness: runs/heartbeat.json is rewritten (throttled) on every unit of progress, with pid,
rung, stage, counts, per-lane counters and the last-progress timestamp the monitor uses."""
from __future__ import annotations

import os
import threading
import time

from .util import RUNS, now_iso, read_json, write_json

PATH = RUNS / "heartbeat.json"


class Heartbeat:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = read_json(PATH, default={}) or {}
        self.state.update(pid=os.getpid(), status="running", started=self.state.get("started") or now_iso())
        self._last_write = 0.0
        self.flush(force=True)

    def set(self, **kw) -> None:
        with self.lock:
            self.state.update(kw)
        self.flush(force=True)

    def progress(self, lane: str | None = None, done_delta: int = 1, **kw) -> None:
        with self.lock:
            self.state["done"] = self.state.get("done", 0) + done_delta
            self.state["last_progress"] = now_iso()
            self.state["last_progress_ts"] = time.time()
            if lane:
                ln = self.state.setdefault("lanes", {}).setdefault(lane, {"done": 0})
                ln["done"] = ln.get("done", 0) + done_delta
                ln["last"] = now_iso()
                ln["last_ts"] = time.time()
            self.state.update(kw)
        self.flush()

    def touch(self, **kw) -> None:
        """Progress that is not a completed cell (e.g. a batch poll that moved forward)."""
        with self.lock:
            self.state["last_progress"] = now_iso()
            self.state["last_progress_ts"] = time.time()
            self.state.update(kw)
        self.flush()

    def flush(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_write < 2.0:
            return
        with self.lock:
            self.state["updated"] = now_iso()
            self.state["updated_ts"] = now
            snap = dict(self.state)
        write_json(PATH, snap)
        self._last_write = now


def read() -> dict:
    return read_json(PATH, default={}) or {}
