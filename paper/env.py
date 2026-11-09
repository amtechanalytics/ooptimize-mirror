"""Paths, modes, tool locations and a subprocess runner that keeps the heartbeat alive while a bounded
child process works. Every child process has its own timeout, so a hang ends as a timeout, never as a
silent stall."""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

from rh.util import ROOT, RUNS, env_flag, journal

DATA_DIR = Path(os.environ.get("RH_DATA_DIR", str(ROOT / ".data")))
WORK = DATA_DIR / "oopt"                     # large, regenerable artefacts (builds, Randoop tests, overlays)
TIMEOUT_DEFAULT = 900
BEAT_SECONDS = 60.0                           # heartbeat touch interval while a child runs
DEFAULT_HB = None                             # set by bind(ctx) at the start of every local stage


def fake() -> bool:
    """Dry runs (fake vendor) never touch Java, Maven, Gradle or the network."""
    return env_flag("RH_FAKE_VENDOR")


def scope_dir(mode: str) -> Path:
    """Pilot artefacts are kept apart; canary and full share one selection, baseline and sizing."""
    d = RUNS / ("pilot" if mode == "pilot" else "shared") / "oopt"
    d.mkdir(parents=True, exist_ok=True)
    return d


def work_dir(mode: str) -> Path:
    d = WORK / ("pilot" if mode == "pilot" else "shared")
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_json(p: Path, default=None):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return default


def write_json(p: Path, obj) -> None:
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, sort_keys=True))
    os.replace(tmp, p)


def read_jsonl(p: Path) -> list[dict]:
    out = []
    try:
        with open(p) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
    except OSError:
        pass
    return out


_APPEND_LOCK = threading.Lock()


def append_jsonl(p: Path, row: dict) -> None:
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    with _APPEND_LOCK, open(p, "a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


# ------------------------------------------------------------------ tools
def _pin_dir(name: str) -> Path:
    from rh import datapins
    return datapins.path(name)


def _first(base: Path, pattern: str) -> Path:
    hits = sorted(base.glob(pattern))
    if not hits:
        raise FileNotFoundError(f"{pattern} not found under {base}")
    return hits[0]


class Tools:
    """Resolved locations of the pinned toolchain (instance only)."""

    def __init__(self, java_home: Path, mvn: Path | None, randoop: Path, spotbugs: Path, ev: Path | None,
                 helpers: Path, m2: Path | None = None, gradle_home: Path | None = None):
        self.java_home = Path(java_home)
        self.java = self.java_home / "bin" / "java"
        self.javac = self.java_home / "bin" / "javac"
        self.mvn = Path(mvn) if mvn else None
        self.randoop = Path(randoop)
        self.spotbugs = Path(spotbugs)
        self.ev = Path(ev) if ev else None
        self.helpers = Path(helpers)
        self.m2 = Path(m2 or WORK / "m2")
        self.gradle_home = Path(gradle_home or WORK / "gradle")

    @classmethod
    def pinned(cls) -> "Tools":
        """The toolchain from the verified binary pins (instance)."""
        jdk = _first(_pin_dir("temurin_jdk21"), "jdk-21*").resolve()
        mvn = _first(_pin_dir("maven"), "apache-maven-*").resolve() / "bin" / "mvn"
        return cls(jdk, mvn, _pin_dir("randoop").resolve(),
                   _first(_pin_dir("spotbugs"), "spotbugs-*/lib/spotbugs.jar").resolve(),
                   _pin_dir("equalsverifier").resolve(), WORK / "helpers")

    def env(self) -> dict:
        e = dict(os.environ)
        for k in [k for k in e if k.endswith("_API_KEY") or k in ("CLAUDE_CODE_OAUTH_TOKEN", "GITHUB_TOKEN")]:
            e.pop(k, None)                      # build tools and tests never see credentials
        e["JAVA_HOME"] = str(self.java_home)
        e["PATH"] = f"{self.java_home / 'bin'}:{self.mvn.parent if self.mvn else ''}:{e.get('PATH', '')}"
        e["MAVEN_OPTS"] = f"-Xmx3g -Dmaven.repo.local={self.m2}"
        e["GRADLE_USER_HOME"] = str(self.gradle_home)
        e["GRADLE_OPTS"] = "-Dorg.gradle.daemon=false -Xmx3g"
        e.pop("JAVA_TOOL_OPTIONS", None)
        return e


_TOOLS = None


def tools() -> Tools:
    global _TOOLS
    if _TOOLS is None:
        _TOOLS = Tools.pinned()
    return _TOOLS


def set_tools(t: "Tools | None") -> None:
    """Tests inject a toolchain built from explicit paths."""
    global _TOOLS
    _TOOLS = t


def workers(default: int) -> int:
    try:
        return max(1, int(os.environ.get("OOPT_WORKERS", default)))
    except ValueError:
        return default


# ------------------------------------------------------------------ running children
def run(cmd: list[str] | str, *, cwd: Path | None = None, timeout: int = TIMEOUT_DEFAULT, env: dict | None = None,
        hb=None, log: Path | None = None, shell: bool = False) -> subprocess.CompletedProcess:
    """Run a child with a hard timeout. While it runs, touch the heartbeat every 60 s (liveness), so the
    monitor sees progress during long builds; the child's own timeout bounds any hang.
    Returns a CompletedProcess; on timeout returncode is 124 and stderr says so."""
    stop = threading.Event()
    hb = hb if hb is not None else DEFAULT_HB

    def beat():
        while not stop.wait(BEAT_SECONDS):
            if hb is not None:
                try:
                    hb.touch()
                except Exception:  # noqa: BLE001
                    pass

    t = threading.Thread(target=beat, daemon=True)
    t.start()
    t0 = time.time()
    try:
        r = subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env, capture_output=True, text=True,
                           timeout=timeout, shell=shell, errors="replace")
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        err = e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        r = subprocess.CompletedProcess(cmd, 124, out, err + f"\n[timeout after {timeout}s]")
    finally:
        stop.set()
    if log is not None:
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a") as f:
            f.write(f"\n$ {cmd if isinstance(cmd, str) else ' '.join(map(str, cmd))}\n[exit {r.returncode} in "
                    f"{time.time() - t0:.0f}s]\n{(r.stdout or '')[-20000:]}\n{(r.stderr or '')[-20000:]}\n")
    return r


def bind(ctx) -> None:
    """Every child process started by a local stage keeps this run's heartbeat alive, and the heartbeat no
    longer reports an open vendor batch (which would stretch the monitor's stall limit)."""
    global DEFAULT_HB
    DEFAULT_HB = getattr(ctx, "hb", None)
    if DEFAULT_HB is not None:
        DEFAULT_HB.set(batch=None)


def note(kind: str, msg: str) -> None:
    journal(kind, msg)
