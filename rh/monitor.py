"""Instance watchdog (systemd: rh-monitor.service, Restart=always). Every minute:
  kill flag -> stop runner, back up, mail KILL, shut down (instance terminates on shutdown)
  wall-clock limit -> stop, back up, mail DEAD, shut down
  runner ended with a terminal code -> mail and shut down (DONE shuts itself down)
  runner ended with a repairable code / vanished -> restart (transient) or hand to the executor
  runner alive but no progress for the stall limit -> mail STALLED, restart
The executor (an agentic coding CLI configured by the launch script through RH_EXECUTOR_CMD) is
invoked only here, only on failure, with a turn cap and its own spend ceiling, and never sees the
experiment API keys.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from . import backup, heartbeat, notify
from .exitcodes import BUDGET, CONFIG_INVALID, CRASH, DONE, KILLED, meaning
from .ledger import Ledger
from .util import ROOT, RUNS, journal, now_iso, read_json, write_json

STATE = RUNS / "monitor.json"
SERVICE = os.environ.get("RH_RUNNER_SERVICE", "rh-runner")
EXPERIMENT_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "GROQ_API_KEY", "XAI_API_KEY")


def _sh(*args, check=False, timeout=120):
    return subprocess.run(list(args), capture_output=True, text=True, check=check, timeout=timeout)


def runner_active() -> bool:
    return _sh("systemctl", "is-active", "--quiet", SERVICE).returncode == 0


def start_runner():
    (RUNS / "exit.json").unlink(missing_ok=True)
    _sh("sudo", "systemctl", "restart", SERVICE)


def stop_runner():
    _sh("sudo", "systemctl", "stop", SERVICE, timeout=300)


def shutdown(reason: str):
    journal("SHUTDOWN", reason)
    status = {"done": "DONE", "kill marker": "KILLED"}.get(reason, "BUDGET" if "spend" in reason else "DEAD")
    backup.index_update(status, reason=reason)
    try:
        backup.sync()
    except Exception:  # noqa: BLE001
        pass
    _sh("sudo", "systemctl", "stop", "rh-monitor-timers.target")
    _sh("sudo", "shutdown", "-h", "+1")
    sys.exit(0)


def spot_interrupted() -> bool:
    if os.environ.get("RH_ON_INSTANCE") != "1":
        return False
    import urllib.request
    try:
        tok = urllib.request.urlopen(urllib.request.Request(
            "http://169.254.169.254/latest/api/token", method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"}), timeout=2).read().decode()
        urllib.request.urlopen(urllib.request.Request(
            "http://169.254.169.254/latest/meta-data/spot/instance-action",
            headers={"X-aws-ec2-metadata-token": tok}), timeout=2).read()
        return True            # 200 only when an interruption is scheduled
    except Exception:  # noqa: BLE001 — 404 (no notice) or not a spot instance
        return False


def _cfg():
    from . import config
    return config.load(ROOT)


def limits(cfg) -> dict:
    lv = (cfg.gates.get("liveness") or {})
    return {"stall_min": float(lv.get("stall_minutes", 20)),
            "batch_stall_min": float(lv.get("batch_stall_minutes", 240)),
            "max_restarts": int(lv.get("max_restarts_per_rung", 2)),
            "executor_attempts": int(lv.get("executor_attempts", 2)),
            "executor_timeout_min": float(lv.get("executor_timeout_minutes", 40)),
            "executor_est_usd": float(lv.get("executor_estimate_usd", 3.0))}


def run_executor(cfg, st: dict, exitinfo: dict | None) -> bool:
    cmd = os.environ.get("RH_EXECUTOR_CMD")
    runbook = os.environ.get("RH_EXECUTOR_RUNBOOK")
    if not cmd or not runbook or not Path(runbook).exists():
        journal("EXECUTOR", "not configured (RH_EXECUTOR_CMD / RH_EXECUTOR_RUNBOOK); cannot repair")
        return False
    lim = limits(cfg)
    led = Ledger(cfg)
    try:
        led.check("executor", lim["executor_est_usd"], kind="executor")
    except Exception as e:  # noqa: BLE001
        journal("EXECUTOR", f"skipped: {e}")
        return False
    jtail = "\n".join((RUNS / "journal.txt").read_text().splitlines()[-150:]) if (RUNS / "journal.txt").exists() else ""
    extra = []
    for f in ("pilot_report.md", "datacheck.json"):
        p = RUNS / f
        if p.exists():
            extra.append(f"--- runs/{f} (tail) ---\n{p.read_text()[-4000:]}")
    gl = RUNS / "gates.jsonl"
    if gl.exists():
        extra.append("--- runs/gates.jsonl (last 5) ---\n" + "\n".join(gl.read_text().splitlines()[-5:]))
    prompt = (Path(runbook).read_text()
              + f"\n\n## Failure context ({now_iso()})\n"
              + f"exit: {json.dumps(exitinfo or {'code': None, 'meaning': 'runner vanished without exit.json'})[:6000]}\n"
              + f"rung: {st.get('rung')} · attempt {st.get('executor_used', 0) + 1}\n\n"
              + f"--- runs/journal.txt (tail) ---\n{jtail}\n\n" + "\n\n".join(extra))
    # subscription token only: any API key in the environment would take precedence over it and bill the API
    env = {k: v for k, v in os.environ.items() if k not in EXPERIMENT_KEYS}
    if not env.get("CLAUDE_CODE_OAUTH_TOKEN"):
        journal("EXECUTOR", "CLAUDE_CODE_OAUTH_TOKEN missing; cannot repair")
        return False
    journal("EXECUTOR", f"invoked for rung {st.get('rung')}: {meaning((exitinfo or {}).get('code', CRASH))}")
    needs_before = len(_needs())
    wt = _make_worktree()
    t0 = time.time()
    try:
        r = subprocess.run(shlex.split(cmd), input=prompt, capture_output=True, text=True, cwd=wt or ROOT, env=env,
                           timeout=lim["executor_timeout_min"] * 60)
        out = r.stdout or ""
    except subprocess.TimeoutExpired:
        journal("EXECUTOR", "timed out")
        _drop_worktree(wt)
        return False
    (RUNS / f"executor_{int(t0)}.json").write_text(out[-200000:])
    notional, ok, summary = 0.0, r.returncode == 0, ""
    try:
        d = json.loads(out)
        notional = float(d.get("total_cost_usd", 0.0))     # informational: billed to the subscription, not the API
        ok = ok and not d.get("is_error", False)
        summary = str(d.get("result", ""))[:600]
    except Exception:  # noqa: BLE001
        summary = out[-600:]
    led.record("executor", {}, kind="executor", cost=0.0, provider="subscription")
    asked_human = len(_needs()) > needs_before
    # The repair step works in its own git worktree, so its edits never touch the code the backup and mail timers
    # import (Day-0: a half-made edit broke the backup). Only a clean, committed fix is fast-forwarded into the live
    # tree and pushed; anything uncommitted is discarded with the worktree; a request for a human ends the run.
    dirty = _dirty_code(wt) if wt else _dirty_code()
    landed = None
    if wt and not asked_human and not dirty:
        landed = _land(wt)
    if not wt and dirty:
        _revert_code(dirty)
    _drop_worktree(wt)
    live_dirty = _dirty_code()
    if live_dirty:                       # edits made to the live tree by absolute path: never run them
        _revert_code(live_dirty)
        dirty = dirty + live_dirty
    if wt:
        ok = ok and bool(landed) and not asked_human
    elif asked_human or dirty:
        ok = False
    if asked_human:
        st["needs_human"] = True
    journal("FIX" if ok else "EXECUTOR", f"executor {'fixed and committed ' + (landed or '') if ok else 'did not fix'} in "
                                         f"{(time.time() - t0) / 60:.1f} min (subscription; notional "
                                         f"${notional:.2f})"
                                         + (f"; discarded uncommitted edits {dirty[:8]}" if dirty else "")
                                         + ("; no commit" if wt and not landed and not dirty and not asked_human else "")
                                         + ("; asked for a human" if asked_human else "") + f": {summary}")
    return ok


LINKED = ("runs", "results", "logs", ".venv", "data", ".data")


def _git(*args, cwd=None, check=False):
    return subprocess.run(["git", *args], cwd=cwd or ROOT, capture_output=True, text=True, check=check)


def _make_worktree():
    """A detached worktree of HEAD next to the repo, with runs/, data/, .venv/ ... linked to the live ones."""
    if not (ROOT / ".git").exists():
        return None
    wt = ROOT.parent / f".{ROOT.name}-repair"
    _git("worktree", "remove", "--force", str(wt))
    if wt.exists():
        import shutil
        shutil.rmtree(wt, ignore_errors=True)
    _git("worktree", "prune")
    r = _git("worktree", "add", "--detach", str(wt), "HEAD")
    if r.returncode != 0:
        journal("EXECUTOR", f"worktree failed ({r.stderr.strip()[:200]}); repairing in place")
        return None
    excl = ROOT / ".git" / "info" / "exclude"
    excl.parent.mkdir(parents=True, exist_ok=True)
    have = excl.read_text().splitlines() if excl.exists() else []
    excl.write_text("\n".join(have + [n for n in LINKED if n not in have]) + "\n")
    for n in LINKED:
        if (ROOT / n).exists() and not (wt / n).exists():
            (wt / n).symlink_to(ROOT / n)
    return wt


def _land(wt) -> str | None:
    """Fast-forward the live tree to the worktree's commit(s) and push them. Returns the short sha or None."""
    head = _git("rev-parse", "HEAD", cwd=wt).stdout.strip()
    live = _git("rev-parse", "HEAD").stdout.strip()
    if not head or head == live:
        return None
    if _git("merge", "--ff-only", head).returncode != 0:
        journal("EXECUTOR", "repair commit does not fast-forward the live tree; not applied")
        return None
    p = _git("push", "-q", "origin", "HEAD:main")
    if p.returncode != 0:
        journal("ERROR", f"repair committed but push failed: {p.stderr.strip()[:200]}")
    return head[:8]


def _drop_worktree(wt) -> None:
    if wt:
        _git("worktree", "remove", "--force", str(wt))
        _git("worktree", "prune")


CODE_DIRS = ["rh", "paper", "pilot", "tests", "tools", "requirements.txt"]


def _needs() -> list:
    return read_json(RUNS / "needs_attention.json", default=[]) or []


def _dirty_code(where=None) -> list[str]:
    """Uncommitted changes the executor left in code (runs/, results/, logs/ are ignored by git)."""
    where = where or ROOT
    if not (where / ".git").exists():
        return []
    r = subprocess.run(["git", "status", "--porcelain", "--", *CODE_DIRS], cwd=where, capture_output=True, text=True)
    return [ln[3:].strip() for ln in r.stdout.splitlines() if ln.strip()]


def _revert_code(paths: list[str]) -> None:
    if not paths or not (ROOT / ".git").exists():
        return
    dirs = [d for d in CODE_DIRS if (ROOT / d).exists()]    # git rejects the whole command on a missing pathspec
    subprocess.run(["git", "checkout", "--", *dirs], cwd=ROOT, capture_output=True, text=True)
    subprocess.run(["git", "clean", "-fdq", "--", *dirs], cwd=ROOT, capture_output=True, text=True)
    left = _dirty_code()
    journal("EXECUTOR", f"reverted uncommitted edits; still dirty: {left}" if left else "reverted uncommitted edits")


def tick(st: dict) -> dict:
    cfg = _cfg()
    lim = limits(cfg)
    # 1 kill (the poll also uploads the status files); compute bookkeeping for resumed runs
    backup.killpoll()
    try:
        from . import compute
        compute.touch_boot()
    except Exception:  # noqa: BLE001
        pass
    if (RUNS / "KILL").exists():
        stop_runner()
        notify.send("KILL", "stopped by kill marker", (RUNS / "KILL").read_text())
        shutdown("kill marker")
    # 1b spot interruption notice (two-minute warning from instance metadata)
    if spot_interrupted():
        stop_runner()
        notify.send("DEAD", "spot instance interrupted", "Capacity reclaimed by AWS. Data is in S3 (last 5-min "
                    "backup + final sync). Resume: bash _tools/aws-up.sh <codename> --resume <run-id>")
        shutdown("spot interruption")
    # 2 wall clock
    max_h = float(os.environ.get("RH_MAX_HOURS", cfg.project.get("max_hours", 24)))
    boot = float(os.environ.get("RH_BOOT_TS", st.setdefault("boot", time.time())))
    if time.time() - boot > max_h * 3600:
        stop_runner()
        notify.send("DEAD", f"wall limit {max_h}h reached", "Stopped at the wall-clock limit; resume from S3.")
        shutdown("wall limit")
    hb = heartbeat.read()
    exitinfo = read_json(RUNS / "exit.json", default=None)
    rung = (read_json(RUNS / "state.json", default={}) or {}).get("current") or hb.get("rung")
    if rung != st.get("rung"):
        st.update(rung=rung, restarts=0)
    st["rung"] = rung
    if runner_active():
        last = hb.get("last_progress_ts") or hb.get("updated_ts") or time.time()
        in_batch = bool(hb.get("batch"))
        limit = (lim["batch_stall_min"] if in_batch else lim["stall_min"]) * 60
        if time.time() - last > limit:
            journal("STALL", f"no progress for {(time.time() - last) / 60:.0f} min in {rung}/{hb.get('stage')}; restarting")
            notify.send("STALLED", f"{rung}/{hb.get('stage')} no progress {int((time.time() - last) / 60)}m",
                        "Restarting the runner with resume.")
            st["restarts"] = st.get("restarts", 0) + 1
            start_runner()
        return st
    # runner not active
    code = (exitinfo or {}).get("code")
    if code == DONE:
        journal("MONITOR", "runner finished; monitor exiting")
        shutdown("done")
    if code in (BUDGET, KILLED, CONFIG_INVALID):
        kind = "BUDGET" if code == BUDGET else "DEAD"
        notify.send(kind, meaning(code), (exitinfo or {}).get("message", ""))
        shutdown(meaning(code))
    if st.get("needs_human"):
        items = "\n".join(f"- {x}" for x in _needs()) or "- (see runs/journal.txt)"
        notify.send("DEAD", f"{rung}: needs you", f"The repair step needs a decision from you:\n{items}\n\n"
                    f"{json.dumps(exitinfo or {}, indent=2)[:4000]}\n\nReply in the paper's chat with this mail; "
                    "resume later with aws-up.sh <codename> --resume <run-id>.")
        shutdown("needs human")
    transient = code in (None, CRASH)
    msg = (exitinfo or {}).get("message")
    if transient and msg and msg == st.get("last_crash"):
        # the same exception twice in a row is a code bug, not a transient fault: go straight to the repair step
        st["restarts"] = lim["max_restarts"]
        journal("MONITOR", "same crash twice; skipping further restarts")
    st["last_crash"] = msg if transient else None
    if transient and st.get("restarts", 0) < lim["max_restarts"]:
        st["restarts"] = st.get("restarts", 0) + 1
        journal("RESTART", f"runner not running ({meaning(code) if code is not None else 'vanished'}); "
                           f"restart {st['restarts']}/{lim['max_restarts']}, resumed from {rung}")
        start_runner()
        return st
    if st.get("executor_used", 0) < lim["executor_attempts"]:
        ok = run_executor(cfg, st, exitinfo)
        st["executor_used"] = st.get("executor_used", 0) + 1
        st["restarts"] = 0
        if ok:
            journal("RESTART", f"after executor repair; resumed from {rung}")
            start_runner()
            return st
        if st.get("needs_human"):
            return tick(st)                  # -> the needs-human branch above: mail and shut down
    notify.send("DEAD", f"{rung}: {meaning(code) if code is not None else 'runner vanished'}",
                f"Restarts and executor attempts exhausted.\n\n{json.dumps(exitinfo or {}, indent=2)[:6000]}\n\n"
                "Reply in the paper's chat with this mail; resume later with aws-up.sh <codename> --resume <run-id>.")
    shutdown("dead")
    return st


def main():
    journal("MONITOR", f"monitor pid {os.getpid()} started")
    st = read_json(STATE, default={}) or {}
    while True:
        try:
            st = tick(st)
            write_json(STATE, st)
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001 — the watchdog itself must not die
            journal("ERROR", f"monitor tick: {type(e).__name__}: {e}")
        time.sleep(float(os.environ.get("RH_MONITOR_INTERVAL", 60)))


if __name__ == "__main__":
    main()
