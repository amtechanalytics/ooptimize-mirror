"""The run ladder: datacheck -> pilot -> canary -> full -> analysis -> finish.
No rung can be skipped by configuration. Each completed rung is recorded in runs/state.json,
so a restart (RESUME) continues from the first unfinished rung and, inside a rung, from the
first unfinished cell.

  python -m rh.runner                 # full ladder (instance)
  python -m rh.runner --dry-run       # fake vendor + fake AWS + fixture pins, no shutdown
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import traceback

from . import backup, compute, datapins, gates, notify
from .cells import KILL_FLAG, run_llm_stage
from .context import Context
from .exitcodes import (BudgetExceeded, CanaryFailed, ConfigInvalid, HarnessStop, Killed, PilotFailed,
                        meaning)
from .heartbeat import Heartbeat
from .util import RESULTS, ROOT, RUNS, ensure_dirs, env_flag, journal, now_iso, read_json, read_jsonl, write_json

LADDER = ["datacheck", "pilot", "canary", "full", "analysis", "finish"]
STATE = RUNS / "state.json"


def _state() -> dict:
    return read_json(STATE, default=None) or {"done": [], "current": None, "started": now_iso()}


def _mark(state: dict, rung: str) -> None:
    state["done"] = sorted(set(state["done"]) | {rung}, key=LADDER.index)
    state["current"] = None
    write_json(STATE, state)
    backup.sync()


def _stages(ctx, mode):
    ctx.mode = mode
    ctx.ledger.set_mode(mode)
    results = []
    for st in ctx.paper.STAGES:
        if KILL_FLAG.exists():
            raise Killed("kill flag present")
        if st.kind == "llm":
            m = run_llm_stage(ctx, st)
        else:
            ctx.hb.set(stage=st.name)
            m = st.run(ctx) or {}
            m.setdefault("stage", st.name)
            m.setdefault("mode", mode)
        results.append(gates.evaluate(ctx, st, m))
        backup.sync()
    return results


def rung_datacheck(ctx):
    datapins.verify_all(ctx.cfg.pins)


def _ensure_data(ctx, state) -> None:
    """On a resumed run (new instance) data/ and the download cache are not part of the S3 backup, so the pins are
    downloaded and verified again before any later rung; the sha256 locked on the first download (runs/, restored)
    must still match, so a resumed run can never use different data."""
    if "datacheck" not in state["done"] or "finish" in state["done"]:
        return
    if all(datapins.present(p["name"]) for p in ctx.cfg.pins):
        return
    journal("DATA", "resume: data/ missing on this instance; downloading and verifying the pins again")
    datapins.verify_all(ctx.cfg.pins)


def rung_pilot(ctx):
    from . import drill
    drill.check()                  # no-op unless the launch asked for a repair drill (aws-up.sh --inject-fault)
    report = ["# Pilot report", "", f"run {ctx.run_id} · {now_iso()}", ""]
    fails = []
    try:
        res = _stages(ctx, "pilot")
        for r in res:
            report.append(f"- stage {r['stage']}: PASS ({r['metrics'].get('ok', '-')} ok)")
    except (BudgetExceeded, Killed):
        raise                      # terminal: never turned into a repairable pilot failure
    except HarnessStop as e:
        fails.append(f"pilot stages: {e}")
        report.append(f"- pilot stages: FAIL — {e}")
    # certificate-driven pilot tests (pilot/test_<ID>.py)
    junit = RUNS / "pilot_junit.xml"
    env = dict(os.environ, RH_MODE="pilot")
    r = subprocess.run([sys.executable, "-m", "pytest", "pilot", "-q", "-rA", f"--junitxml={junit}"],
                       cwd=ROOT, env=env, capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    (RUNS / "pilot_pytest.txt").write_text(out)
    report += ["", "## Pilot tests (ids from the generation certificate)", "```", out[-6000:], "```"]
    if r.returncode not in (0, 5):          # 5 = no tests collected
        fails.append("pilot tests failed (see pilot_pytest.txt)")
    verdict = "PILOT PASS" if not fails else "PILOT FAIL"
    report += ["", f"**{verdict}**"] + [f"- {f}" for f in fails]
    (RUNS / "pilot_report.md").write_text("\n".join(report) + "\n")
    notify.send("PILOT", "PASS" if not fails else "FAIL: " + fails[0][:60], "\n".join(report))
    if fails:
        raise PilotFailed("; ".join(fails))


def rung_canary(ctx):
    try:
        res = _stages(ctx, "canary")
    except (BudgetExceeded, Killed):
        raise
    except HarnessStop as e:
        notify.send("CANARY", "FAIL", f"Canary failed: {e}")
        raise CanaryFailed(str(e)) from e
    # re-project the full run from real canary token counts
    spend_canary = sum(r.get("cost", 0) for st in ctx.paper.STAGES if st.kind == "llm"
                       for r in read_jsonl(RUNS / "canary" / f"{st.name}.jsonl"))
    n_canary = sum(1 for st in ctx.paper.STAGES if st.kind == "llm"
                   for _ in read_jsonl(RUNS / "canary" / f"{st.name}.jsonl"))
    ctx.mode = "full"
    planned = _write_plan(ctx)
    per_cell = spend_canary / max(n_canary, 1)
    api = ctx.ledger.total() + per_cell * planned
    started = (ctx.hb.state if ctx.hb else {}).get("started")
    comp_so_far, comp_left = compute.so_far(ctx.cfg, started), compute.remaining(ctx.cfg, started)
    projected = api + comp_so_far + comp_left
    msg = (f"canary: {n_canary} cells, ${spend_canary:.4f}; full plan {planned} cells; projected total "
           f"${projected:.2f} of ${ctx.cfg.ceiling()} = API ${api:.2f} + compute ${comp_so_far + comp_left:.2f} "
           f"(${comp_so_far:.2f} so far + ${comp_left:.2f} remaining)")
    journal("PROJECT", msg)
    lines = [f"- {r['stage']}: PASS {r['metrics']}" for r in res] + ["", msg]
    if projected > ctx.cfg.ceiling():
        notify.send("CANARY", "FAIL: projection over ceiling", "\n".join(lines))
        raise CanaryFailed(msg)
    notify.send("CANARY", f"PASS · projected ${projected:.2f}/${ctx.cfg.ceiling()}", "\n".join(lines))


def _write_plan(ctx) -> int:
    """Original plan for progress reporting; written once, never overwritten on resume."""
    plan = read_json(RUNS / "plan.json", default=None)
    if plan:
        return plan["total_cells"]
    stages = {}
    for st in ctx.paper.STAGES:
        if st.kind == "llm":
            ids = {c["cell_id"] for c in st.build(ctx)}
            stages[st.name] = {"cells": len(ids)}
    total = sum(s["cells"] for s in stages.values())
    write_json(RUNS / "plan.json", {"created": now_iso(), "stages": stages, "total_cells": total})
    return total


def rung_full(ctx):
    ctx.mode = "full"
    _write_plan(ctx)
    _stages(ctx, "full")


def rung_analysis(ctx):
    ctx.mode = "full"
    ctx.hb.set(stage="analysis")
    RESULTS.mkdir(parents=True, exist_ok=True)
    ctx.paper.analyze(ctx)
    items = list(ctx.paper.brief(ctx) or [])
    spend = ctx.ledger.summary()
    brief = ["# Morning brief", "", f"run {ctx.run_id} finished {now_iso()}",
             f"spend ${spend['total']:.2f} of ${ctx.cfg.ceiling()} (executor "
             f"${spend['by_kind'].get('executor', 0):.2f}); compute ~${compute.so_far(ctx.cfg, ctx.hb.state.get('started')):.2f}",
             "", "## Needs you"]
    brief += [f"- {i}" for i in items] or ["- nothing"]
    fixes = [ln for ln in (RUNS / "journal.txt").read_text().splitlines() if "| FIX" in ln or "| RESTART" in ln]
    brief += ["", "## Automatic repairs during the run"] + ([f"- {f}" for f in fixes] or ["- none"])
    (RUNS / "brief.md").write_text("\n".join(brief) + "\n")


def rung_finish(ctx):
    backup.sync()
    sha = backup.final()
    state = _state()
    state["final_sha256"] = sha
    write_json(STATE, state)
    brief = (RUNS / "brief.md").read_text() if (RUNS / "brief.md").exists() else ""
    backup.index_update("DONE", final_sha256=sha, spend=round(ctx.ledger.total(), 4))
    notify.send("DONE", f"results in S3 · ${ctx.ledger.total():.2f}", brief + f"\nfinal sha256 {sha}")


RUNGS = {"datacheck": rung_datacheck, "pilot": rung_pilot, "canary": rung_canary, "full": rung_full,
         "analysis": rung_analysis, "finish": rung_finish}


def _exit(code: int, msg: str, tb: str = "") -> int:
    write_json(RUNS / "exit.json", {"code": code, "meaning": meaning(code), "message": msg[:2000],
                                    "traceback": tb[-8000:], "ts": now_iso(), "pid": os.getpid()})
    hbp = RUNS / "heartbeat.json"
    hb = read_json(hbp, default={}) or {}
    hb.update(status="ended", exit_code=code, updated=now_iso())
    write_json(hbp, hb)
    journal("EXIT", f"{code} {meaning(code)} {msg[:300]}")
    try:
        backup.sync()
    except Exception:  # noqa: BLE001
        pass
    return code


def main(argv: list[str]) -> int:
    dry = "--dry-run" in argv
    if dry:
        os.environ.setdefault("RH_FAKE_VENDOR", "1")
        os.environ.setdefault("RH_FAKE_AWS", "1")
    ensure_dirs()
    (RUNS / "exit.json").unlink(missing_ok=True)

    def on_term(signum, frame):
        raise Killed(f"signal {signum}")
    signal.signal(signal.SIGTERM, on_term)

    try:
        ctx = Context(pins_file="data_pins.fixture.json" if dry else None)
        if ctx.cfg.errors:
            raise ConfigInvalid("; ".join(ctx.cfg.errors[:20]))
        ctx.hb = Heartbeat()
        state = _state()
        state["run_id"] = ctx.run_id
        write_json(STATE, state)
        journal("START", f"runner pid {os.getpid()} run {ctx.run_id} resume-from "
                         f"{next((r for r in LADDER if r not in state['done']), 'done')}")
        _ensure_data(ctx, state)
        for rung in LADDER:
            if rung in state["done"]:
                continue
            if KILL_FLAG.exists():
                raise Killed("kill flag present")
            state["current"] = rung
            write_json(STATE, state)
            ctx.hb.set(rung=rung)
            journal("RUNG", f"{rung} start")
            RUNGS[rung](ctx)
            journal("RUNG", f"{rung} PASS")
            _mark(state, rung)
        return _exit(0, "all rungs passed")
    except HarnessStop as e:
        return _exit(e.code, str(e), traceback.format_exc())
    except Exception as e:  # noqa: BLE001
        return _exit(1, f"{type(e).__name__}: {e}", traceback.format_exc())


if __name__ == "__main__":
    code = main(sys.argv[1:])
    if code == 0 and env_flag("RH_ON_INSTANCE") and "--dry-run" not in sys.argv:
        subprocess.run(["sudo", "shutdown", "-h", "+1"], check=False)   # instance terminates on shutdown
    sys.exit(code)
