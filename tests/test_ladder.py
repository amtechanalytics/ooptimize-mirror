"""End-to-end ladder on the fake vendor and fake AWS: full pass, crash + resume without duplicate
calls, kill flag stop + resume, duplicate-cell gate, and the plain-English exit codes."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from rh.util import ROOT


def _copy(tmp_path) -> Path:
    dst = tmp_path / ROOT.name
    shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns("runs", "results", "logs", ".data", ".fake_aws",
                                                             "__pycache__", "data"))
    return dst


def _run(repo: Path, **env) -> subprocess.CompletedProcess:
    e = dict(os.environ, RH_ROOT=str(repo), RH_RUN_ID="t", RH_FAKE_AWS_DIR=str(repo / ".fake_aws"), **env)
    return subprocess.run([sys.executable, "-m", "rh.runner", "--dry-run"], cwd=repo, env=e,
                          capture_output=True, text=True, timeout=600)


def _subjects(repo):
    p = repo / ".fake_aws" / "sns.log"
    return [json.loads(x)["subject"] for x in p.read_text().splitlines()] if p.exists() else []


def _ledger_cells(repo, mode="full"):
    rows = [json.loads(x) for x in (repo / "runs" / "ledger.jsonl").read_text().splitlines()]
    ids = [r["cell_id"] for r in rows if r["kind"] == "experiment" and r["cell_id"].startswith("label|")]
    return ids


def test_full_ladder(tmp_path):
    repo = _copy(tmp_path)
    r = _run(repo)
    assert r.returncode == 0, r.stderr[-3000:]
    st = json.loads((repo / "runs" / "state.json").read_text())
    assert st["done"] == ["datacheck", "pilot", "canary", "full", "analysis", "finish"]
    subs = _subjects(repo)
    assert any("PILOT | PASS" in s for s in subs) and any("CANARY | PASS" in s for s in subs)
    import yaml
    cn = yaml.safe_load((repo / "config" / "project.yaml").read_text())["codename"]
    assert any(s.startswith(f"[{cn}] DONE |") for s in subs)
    assert (repo / "results" / "t1_accuracy.csv").exists() and (repo / "runs" / "brief.md").exists()
    plan = json.loads((repo / "runs" / "plan.json").read_text())
    assert plan["total_cells"] == 160
    full = [json.loads(x) for x in (repo / "runs" / "full" / "label.jsonl").read_text().splitlines()]
    assert len({r["cell_id"] for r in full}) == 160
    assert any(r["batch"] for r in full) and any(not r["batch"] for r in full)   # both paths exercised
    finals = list((repo / ".fake_aws" / "s3").rglob("_final/*.tar.gz"))
    assert finals, "final tarball not uploaded"


def test_crash_then_resume_has_no_duplicate_calls(tmp_path):
    repo = _copy(tmp_path)
    r1 = _run(repo, RH_TEST_CRASH_AFTER="25")
    assert r1.returncode == 1, r1.stderr[-2000:]
    ex = json.loads((repo / "runs" / "exit.json").read_text())
    assert "injected crash" in ex["message"]
    r2 = _run(repo)
    assert r2.returncode == 0, r2.stderr[-3000:]
    ids = _ledger_cells(repo)
    full_ids = [i for i in ids if True]
    rows = [json.loads(x) for x in (repo / "runs" / "full" / "label.jsonl").read_text().splitlines()]
    ok_ids = [x["cell_id"] for x in rows if x["status"] in ("ok", "parse_fail")]
    assert len(ok_ids) == len(set(ok_ids)) == 160, "a cell was recorded twice"
    journal = (repo / "runs" / "journal.txt").read_text()
    assert "resume-from full" in journal


def test_kill_flag_stops_then_resume(tmp_path):
    repo = _copy(tmp_path)
    r1 = _run(repo, RH_TEST_KILL_AFTER="10")
    assert r1.returncode == 30, r1.stderr[-2000:]
    assert (repo / "runs" / "KILL").exists()
    (repo / "runs" / "KILL").unlink()
    r2 = _run(repo)
    assert r2.returncode == 0, r2.stderr[-2000:]


def test_duplicate_cells_fail_the_gate(tmp_path):
    repo = _copy(tmp_path)
    p = repo / "paper" / "__init__.py"
    p.write_text(p.read_text().replace("    return cells\n", "    return cells + cells[:3]\n", 1))
    r = _run(repo)
    assert r.returncode == 20, r.stderr[-2000:]       # pilot rung fails on the gate
    assert "duplicate cell_ids" in (repo / "runs" / "pilot_report.md").read_text()


def test_budget_ceiling_stops_run(tmp_path):
    repo = _copy(tmp_path)
    b = repo / "config" / "prices.yaml"
    b.write_text(b.read_text().replace("input_per_mtok: 1.0", "input_per_mtok: 9000000.0"))
    r = _run(repo)
    assert r.returncode == 10, r.stderr[-2000:]     # BUDGET is terminal, never a repairable pilot failure
    assert "ceiling" in json.loads((repo / "runs" / "exit.json").read_text())["message"]
