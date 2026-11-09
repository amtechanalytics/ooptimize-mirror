"""Watchdog decisions, certificate cross-check, status mail rendering."""
import json
import time

import pytest

from rh import certificate, monitor, notify
from rh.exitcodes import BUDGET, CRASH, DONE, PILOT_FAIL
from rh.util import ROOT, RUNS, write_json


class Harness:
    def __init__(self, monkeypatch, active=False):
        self.events = []
        self.active = active
        monkeypatch.setattr(monitor, "runner_active", lambda: self.active)
        monkeypatch.setattr(monitor, "start_runner", lambda: self.events.append("start"))
        monkeypatch.setattr(monitor, "stop_runner", lambda: self.events.append("stop"))
        monkeypatch.setattr(monitor, "run_executor", lambda cfg, st, ex: self.events.append("executor") or True)
        monkeypatch.setattr(monitor.notify, "send", lambda k, s, b, codename=None: self.events.append(f"mail:{k}"))

        def fake_shutdown(reason):
            self.events.append(f"shutdown:{reason}")
            raise SystemExit(0)
        monkeypatch.setattr(monitor, "shutdown", fake_shutdown)
        RUNS.mkdir(parents=True, exist_ok=True)
        for f in ("exit.json", "KILL", "heartbeat.json", "state.json"):
            (RUNS / f).unlink(missing_ok=True)


def _exit(code):
    write_json(RUNS / "exit.json", {"code": code, "message": "x"})


def test_crash_restarts_twice_then_executor(monkeypatch):
    h = Harness(monkeypatch)
    st = {"boot": time.time()}
    for i in range(3):
        write_json(RUNS / "exit.json", {"code": CRASH, "message": f"transient {i}"})
        st = monitor.tick(st)
    assert h.events == ["start", "start", "executor", "start"]


def test_same_crash_twice_goes_straight_to_executor(monkeypatch):
    """Day-0: a deterministic crash used both restarts (about two minutes) before the repair step started."""
    h = Harness(monkeypatch)
    _exit(CRASH)
    st = {"boot": time.time()}
    for _ in range(2):
        st = monitor.tick(st)
    assert h.events == ["start", "executor", "start"]


def test_pilot_fail_goes_straight_to_executor(monkeypatch):
    h = Harness(monkeypatch)
    _exit(PILOT_FAIL)
    monitor.tick({"boot": time.time()})
    assert h.events == ["executor", "start"]


def test_executor_exhausted_means_dead(monkeypatch):
    h = Harness(monkeypatch)
    _exit(PILOT_FAIL)
    with pytest.raises(SystemExit):
        monitor.tick({"boot": time.time(), "executor_used": 2})
    assert "mail:DEAD" in h.events and h.events[-1] == "shutdown:dead"


def test_budget_is_terminal(monkeypatch):
    h = Harness(monkeypatch)
    _exit(BUDGET)
    with pytest.raises(SystemExit):
        monitor.tick({"boot": time.time()})
    assert "mail:BUDGET" in h.events and "start" not in h.events


def test_done_shuts_down(monkeypatch):
    h = Harness(monkeypatch)
    _exit(DONE)
    with pytest.raises(SystemExit):
        monitor.tick({"boot": time.time()})
    assert h.events == ["shutdown:done"]


def test_stall_restarts(monkeypatch):
    h = Harness(monkeypatch, active=True)
    write_json(RUNS / "heartbeat.json", {"last_progress_ts": time.time() - 3600, "rung": "full", "stage": "label"})
    monitor.tick({"boot": time.time()})
    assert h.events == ["mail:STALLED", "start"]


def test_kill_flag_stops_and_shuts_down(monkeypatch):
    h = Harness(monkeypatch, active=True)
    (RUNS / "KILL").write_text("x")
    with pytest.raises(SystemExit):
        monitor.tick({"boot": time.time()})
    assert h.events[:2] == ["stop", "mail:KILL"]


def test_wall_limit(monkeypatch):
    h = Harness(monkeypatch, active=True)
    monkeypatch.setenv("RH_MAX_HOURS", "1")
    with pytest.raises(SystemExit):
        monitor.tick({"boot": time.time() - 7200})
    assert "mail:DEAD" in h.events


def test_template_certificate_consistent():
    assert certificate.check() == []


def test_status_mail_renders_without_a_run():
    head, body = notify.status_body(consume=False)
    assert "PROGRESS AGAINST THE ORIGINAL PLAN" in body and "NEEDS YOUR ATTENTION" in body


def test_needs_human_mails_and_shuts_down_without_restart(monkeypatch):
    h = Harness(monkeypatch)
    _exit(CRASH)
    with pytest.raises(SystemExit):
        monitor.tick({"boot": time.time(), "needs_human": True})
    assert "start" not in h.events and "mail:DEAD" in h.events and h.events[-1] == "shutdown:needs human"


def _exec_repo(tmp_path):
    import subprocess
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    repo = tmp_path / "r"
    (repo / "rh").mkdir(parents=True)
    (repo / "rh" / "x.py").write_text("A = 1\n")
    (repo / "runs").symlink_to(RUNS)        # live runs is the harness RUNS, as on the instance
    g = ["git", "-c", "user.email=a@b", "-c", "user.name=n"]
    for c in (["git", "init", "-q", "-b", "main"], ["git", "add", "rh/x.py"], g + ["commit", "-qm", "init"],
              ["git", "remote", "add", "origin", str(bare)], ["git", "push", "-q", "origin", "main"]):
        subprocess.run(c, cwd=repo, check=True)
    return repo, bare


def _run_fake_executor(monkeypatch, tmp_path, repo, body):
    import sys
    cfg = monitor._cfg()
    monkeypatch.setattr(monitor, "ROOT", repo)
    (RUNS / "needs_attention.json").unlink(missing_ok=True)
    script = tmp_path / "fake_exec.py"
    script.write_text("import json, os, pathlib, subprocess\nhere = pathlib.Path.cwd()\n" + body
                      + "print(json.dumps({'result': 'done', 'total_cost_usd': 0.1}))\n")
    rb = tmp_path / "runbook.md"
    rb.write_text("runbook")
    monkeypatch.setenv("RH_EXECUTOR_CMD", f"{sys.executable} {script}")
    monkeypatch.setenv("RH_EXECUTOR_RUNBOOK", str(rb))
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "t")
    st = {"rung": "full"}
    ok = monitor.run_executor(cfg, st, {"code": CRASH})
    (RUNS / "needs_attention.json").unlink(missing_ok=True)
    return ok, st


def test_uncommitted_executor_edits_never_reach_the_live_tree(monkeypatch, tmp_path):
    """Day-0: the repair step left an untested edit and asked for a human; the watchdog restarted the runner on
    it, and the half-made edit broke the backup timer meanwhile. Now the repair works in a separate worktree;
    uncommitted edits are discarded and a request for a human ends the run."""
    repo, _ = _exec_repo(tmp_path)
    ok, st = _run_fake_executor(monkeypatch, tmp_path, repo, (
        "assert here != pathlib.Path(%r)\n" % str(repo)
        + "(here / 'rh' / 'x.py').write_text('A = 2\\n')\n"
        + "(here / 'rh' / 'new.py').write_text('B = 1\\n')\n"
        + "p = here / 'runs' / 'needs_attention.json'; p.write_text(json.dumps(['decide X']))\n"))
    assert ok is False and st["needs_human"] is True
    assert (repo / "rh" / "x.py").read_text() == "A = 1\n" and not (repo / "rh" / "new.py").exists()
    assert not (repo.parent / ".r-repair").exists(), "worktree not removed"


def test_committed_repair_is_fast_forwarded_and_pushed(monkeypatch, tmp_path):
    import subprocess
    repo, bare = _exec_repo(tmp_path)
    ok, st = _run_fake_executor(monkeypatch, tmp_path, repo, (
        "(here / 'rh' / 'x.py').write_text('A = 3\\n')\n"
        "g = ['git', '-c', 'user.email=e@x', '-c', 'user.name=e']\n"
        "subprocess.run(g + ['add', 'rh/x.py'], check=True)\n"
        "subprocess.run(g + ['commit', '-qm', 'fix: x'], check=True)\n"))
    assert ok is True and not st.get("needs_human")
    assert (repo / "rh" / "x.py").read_text() == "A = 3\n", "fix not applied to the live tree"
    log = subprocess.run(["git", "--git-dir", str(bare), "log", "--format=%s", "main"], capture_output=True,
                         text=True).stdout.split("\n")[0]
    assert log == "fix: x", "fix not pushed"
