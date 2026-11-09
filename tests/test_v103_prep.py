"""1.0.3: a source file hidden by .gitignore would be missing on the instance (it clones the repo); L10 catches it."""
import shutil
import subprocess

from rh import prep
from rh.util import ROOT


def test_ignored_source_file_fails_l10(tmp_path, monkeypatch):
    dst = tmp_path / "repo"
    shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns("runs", "results", "logs", ".data", ".fake_aws",
                                                             "__pycache__", "data", ".venv"))
    subprocess.run(["git", "init", "-q"], cwd=dst, check=True)
    monkeypatch.setattr(prep, "ROOT", dst)
    assert prep.c_gitignore() == []
    (dst / "tests" / "demo" / "notes.md").write_text("x")
    errs = prep.c_gitignore()
    assert len(errs) == 1 and "tests/demo/notes.md" in errs[0]


def test_killpoll_pushes_status_files_every_minute():
    """1.0.3: status.sh lags at most a minute: the kill poll (every monitor tick) uploads the small status files."""
    from rh import aws, backup
    from rh.util import RUNS, write_json
    RUNS.mkdir(parents=True, exist_ok=True)
    write_json(RUNS / "heartbeat.json", {"ts": "t-now"})
    backup.killpoll()
    p = aws._fake_path(f"{backup.prefix()}/runs/heartbeat.json")
    assert p.exists() and "t-now" in p.read_text()


def test_compute_counts_earlier_instances_of_a_resumed_run(monkeypatch):
    """Day-0 brief said compute ~$0.00 after two resumes: only the last instance was counted."""
    import time as _t
    from rh import compute
    from rh.util import RUNS, write_json
    now = _t.time()
    write_json(RUNS / "compute.json", {"boots": {str(int(now - 7200)): now - 3600}})   # earlier instance: 1 h up
    monkeypatch.setenv("RH_BOOT_TS", str(now - 1800))                                 # this one: 0.5 h
    assert 1.45 < compute.elapsed_hours() < 1.55
    compute.touch_boot()
    assert len(__import__("json").loads((RUNS / "compute.json").read_text())["boots"]) == 2
    (RUNS / "compute.json").unlink()


def test_crlf_fixture_converted_by_git_fails_l10(tmp_path, monkeypatch):
    """Day-0: the fixture had CRLF lines; git on the Mac committed LF, so the instance's dry run failed the pin.
    `* -text` in .gitattributes prevents it, and L10 compares the committed blob with the pin."""
    import json
    import shutil
    import subprocess
    dst = tmp_path / "repo"
    shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns("runs", "results", "logs", ".data", ".fake_aws",
                                                             "__pycache__", "data", ".venv"))
    pf = dst / "config" / "data_pins.fixture.json"
    pins = json.loads(pf.read_text())
    rel = pins["pins"][0]["url"][len("file://"):]
    git = ["git", "-c", "user.email=a@b", "-c", "user.name=n", "-c", "core.autocrlf=input"]
    subprocess.run(["git", "init", "-q"], cwd=dst, check=True)
    monkeypatch.setattr(prep, "ROOT", dst)
    crlf = (dst / rel).read_bytes().replace(b"\n", b"\r\n")
    (dst / rel).write_bytes(crlf)
    import hashlib
    pins["pins"][0].update(sha256=hashlib.sha256(crlf).hexdigest(), size=len(crlf))
    pf.write_text(json.dumps(pins))
    (dst / ".gitattributes").unlink()
    subprocess.run(git + ["add", "-A"], cwd=dst, check=True, capture_output=True)
    subprocess.run(git + ["commit", "-qm", "x"], cwd=dst, check=True)
    errs = prep.c_gitignore()
    assert any(".gitattributes" in e for e in errs) and any("committed blob" in e for e in errs), errs


def test_repair_drill_fails_pilot_only_when_armed_and_asked(tmp_path):
    """aws-up.sh --inject-fault: the pilot rung fails on purpose; the repair (DISARMED_FOR = run id) lets it pass,
    and a drill with a new run id is armed again without touching the file."""
    import os
    import re
    import shutil
    import subprocess
    import sys
    dst = tmp_path / "repo"
    shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns("runs", "results", "logs", ".data", ".fake_aws",
                                                             "__pycache__", "data", ".venv"))
    d = dst / "rh" / "drill.py"
    d.write_text(re.sub(r'^DISARMED_FOR = .*$', 'DISARMED_FOR = ""', d.read_text(), flags=re.M))
    env = dict(os.environ, RH_ROOT=str(dst), RH_RUN_ID="drill-1", RH_FAKE_AWS_DIR=str(dst / ".fake_aws"),
               RH_FAULT_INJECT="1")
    run = [sys.executable, "-m", "rh.runner", "--dry-run"]
    r = subprocess.run(run, cwd=dst, env=env, capture_output=True, text=True, timeout=600)
    assert r.returncode == 1 and "drill-1" in (dst / "runs" / "exit.json").read_text()
    d.write_text(d.read_text().replace('DISARMED_FOR = ""', 'DISARMED_FOR = "drill-1"'))
    r = subprocess.run(run, cwd=dst, env=env, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-2000:]
    shutil.rmtree(dst / "runs")
    r = subprocess.run(run, cwd=dst, env=dict(env, RH_RUN_ID="drill-2"), capture_output=True, text=True, timeout=600)
    assert r.returncode == 1, "a new drill run must be armed again"


def test_stale_exit_is_cleared_from_status_when_runner_restarts():
    """Day-0: status.sh showed 'EXIT 1 crashed' while the repaired runner was already running again."""
    from rh import aws, backup
    from rh.util import RUNS, write_json
    RUNS.mkdir(parents=True, exist_ok=True)
    write_json(RUNS / "exit.json", {"code": 1})
    backup.push_status()
    remote = aws._fake_path(f"{backup.prefix()}/runs/exit.json")
    assert remote.exists()
    (RUNS / "exit.json").unlink()
    backup.push_status()
    assert not remote.exists()
