"""1.0.2 B: vendors run in parallel inside an LLM stage — sync lanes finish while a slow batch is open,
batch models are polled together, and crash/kill during concurrent polling resume without duplicate calls."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from rh.util import ROOT


def _copy(tmp_path, extra_batch_model=False) -> Path:
    dst = tmp_path / ROOT.name
    shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns("runs", "results", "logs", ".data", ".fake_aws",
                                                             "__pycache__", "data"))
    if extra_batch_model:
        m = dst / "config" / "models.yaml"
        m.write_text(m.read_text() + """  - id: fake-large2
    provider: fake
    max_output_tokens: 200
    context_tokens: 32000
    concurrency: 4
    tpm_budget: 0
    batch: true
    batch_size: 1000
    accessed: "2026-11-06"
""")
        p = dst / "config" / "prices.yaml"
        p.write_text(p.read_text().replace("compute:", """  fake-large2:
    input_per_mtok: 3.0
    output_per_mtok: 6.0
    batch_discount: 0.5
    source: "fake vendor (dry run only)"
compute:""", 1))
    return dst


def _run(repo, **env):
    e = dict(os.environ, RH_ROOT=str(repo), RH_RUN_ID="t", RH_FAKE_AWS_DIR=str(repo / ".fake_aws"), **env)
    return subprocess.run([sys.executable, "-m", "rh.runner", "--dry-run"], cwd=repo, env=e, capture_output=True,
                          text=True, timeout=600)


def _rows(repo):
    return [json.loads(x) for x in (repo / "runs" / "full" / "label.jsonl").read_text().splitlines()]


def _full_journal(repo):
    lines = (repo / "runs" / "journal.txt").read_text().splitlines()
    start = max(i for i, ln in enumerate(lines) if "| RUNG      | full start" in ln)
    return lines[start:]


def test_sync_lane_finishes_before_slow_batch_ends(tmp_path):
    repo = _copy(tmp_path)
    r = _run(repo, RH_FAKE_BATCH_SECONDS="4", RH_FAKE_LATENCY="0.01")
    assert r.returncode == 0, r.stderr[-3000:]
    rows = _rows(repo)
    sync_last = max(x["ts"] for x in rows if not x["batch"])
    batch_first = min(x["ts"] for x in rows if x["batch"])
    assert sync_last < batch_first, (sync_last, batch_first)


def test_two_batch_models_polled_concurrently(tmp_path):
    repo = _copy(tmp_path, extra_batch_model=True)
    r = _run(repo, RH_FAKE_BATCH_SECONDS="2")
    assert r.returncode == 0, r.stderr[-3000:]
    j = _full_journal(repo)
    sub = [i for i, ln in enumerate(j) if "| BATCH" in ln and "submitted" in ln]
    end = [i for i, ln in enumerate(j) if "| BATCH" in ln and " ended," in ln]
    assert len(sub) == 2 and len(end) == 2
    assert max(sub) < min(end), "second batch was not submitted before the first one ended"
    assert len({x["cell_id"] for x in _rows(repo)}) == 240


def test_crash_during_concurrent_polling_resumes_without_duplicates(tmp_path):
    repo = _copy(tmp_path)
    r1 = _run(repo, RH_FAKE_BATCH_SECONDS="5", RH_TEST_CRASH_AFTER="10", RH_FAKE_LATENCY="0.01")
    assert r1.returncode == 1, r1.stderr[-2000:]
    b = json.loads((repo / "runs" / "batches.json").read_text())
    open_full = [k for k, v in b.items() if v["mode"] == "full" and not v.get("ingested")]
    assert len(open_full) == 1, "the open batch must be recorded for resume"
    r2 = _run(repo, RH_FAKE_BATCH_SECONDS="0")
    assert r2.returncode == 0, r2.stderr[-3000:]
    b2 = json.loads((repo / "runs" / "batches.json").read_text())
    assert [k for k, v in b2.items() if v["mode"] == "full"] == open_full, "batch was re-submitted on resume"
    rows = _rows(repo)
    done = [x["cell_id"] for x in rows if x["status"] in ("ok", "parse_fail")]
    assert len(done) == len(set(done)) == 160
    led = [json.loads(x) for x in (repo / "runs" / "ledger.jsonl").read_text().splitlines()]
    full_cells = [x["cell_id"] for x in led if x["stage"] == "label" and x.get("mode") == "full"]
    assert len(full_cells) == len(set(full_cells)) == 160, "a paid call was recorded twice"


def test_kill_during_concurrent_polling(tmp_path):
    repo = _copy(tmp_path)
    r = _run(repo, RH_FAKE_BATCH_SECONDS="8", RH_TEST_KILL_AFTER="5", RH_FAKE_LATENCY="0.01")
    assert r.returncode == 30, r.stderr[-2000:]
    b = json.loads((repo / "runs" / "batches.json").read_text())
    assert any(v["mode"] == "full" and not v.get("ingested") for v in b.values())


def test_kill_cancels_batch_then_resume_resubmits_only_unprocessed(tmp_path):
    """1.0.3: kill.sh cancels open batches at the vendor. On resume the cancelled batch is ingested (what the vendor
    finished is kept and paid once) and only the requests it never processed are submitted again."""
    repo = _copy(tmp_path)
    r1 = _run(repo, RH_FAKE_BATCH_SECONDS="8", RH_TEST_KILL_AFTER="5", RH_FAKE_LATENCY="0.01")
    assert r1.returncode == 30, r1.stderr[-2000:]
    b = json.loads((repo / "runs" / "batches.json").read_text())
    bid = next(k for k, v in b.items() if v["mode"] == "full" and not v.get("ingested"))
    env = dict(os.environ, RH_ROOT=str(repo), RH_FAKE_AWS_DIR=str(repo / ".fake_aws"), RH_FAKE_BATCH_SECONDS="8")
    code = ("import time; from rh import vendors; m={'id':'x','provider':'fake'}; b=vendors.Batch(m); "
            f"fb=vendors._fake_load('{bid}'); fb['submitted']=time.time()-4; vendors._fake_save('{bid}', fb); "
            f"b.cancel('{bid}')")                         # cancelled half-way through the vendor's processing
    subprocess.run([sys.executable, "-c", code], cwd=repo, env=env, check=True)
    (repo / "runs" / "KILL").unlink(missing_ok=True)
    r2 = _run(repo, RH_FAKE_BATCH_SECONDS="8")
    assert r2.returncode == 0, r2.stderr[-3000:]
    b2 = json.loads((repo / "runs" / "batches.json").read_text())
    full = {k: v for k, v in b2.items() if v["mode"] == "full"}
    assert len(full) == 2 and full[bid]["ingested"] and 0 < full[bid]["requeued"] < len(full[bid]["cells"])
    new = next(v for k, v in full.items() if k != bid)
    assert new["requeued_from"] == bid and len(new["cells"]) == full[bid]["requeued"]
    rows = _rows(repo)
    last = {}
    for x in rows:
        last[x["cell_id"]] = x["status"]
    assert len(last) == 160 and all(s in ("ok", "parse_fail") for s in last.values())
    led = [json.loads(x) for x in (repo / "runs" / "ledger.jsonl").read_text().splitlines()]
    full_cells = [x["cell_id"] for x in led if x["stage"] == "label" and x.get("mode") == "full"]
    assert len(full_cells) == len(set(full_cells)) == 160, "a paid call was recorded twice"


def test_resume_on_new_instance_downloads_data_again(tmp_path):
    """1.0.3 (Day-0): data/ and the download cache are not in the S3 backup, so a resume on a fresh instance must
    fetch and re-verify the pins (against the sha256 locked by the first run) before the remaining rungs."""
    repo = _copy(tmp_path)
    r1 = _run(repo, RH_TEST_KILL_AFTER="10", RH_FAKE_LATENCY="0.01")
    assert r1.returncode == 30, r1.stderr[-2000:]
    (repo / "runs" / "KILL").unlink()
    shutil.rmtree(repo / "data")
    shutil.rmtree(repo / ".data", ignore_errors=True)
    r2 = _run(repo)
    assert r2.returncode == 0, r2.stderr[-3000:]
    j = (repo / "runs" / "journal.txt").read_text()
    assert "resume: data/ missing on this instance" in j
    after = j[j.index("resume: data/ missing"):]
    assert "| DATA      | requirements: OK" in after, "pins were not verified again on resume"


def test_empty_reply_is_asked_again_and_paid(tmp_path):
    """Day-0: gpt-oss-120b on Groq returned empty content (reasoning only, not truncated) for 5 of 80 calls; they
    became parse failures. Now an empty, non-truncated reply is asked again (up to 2 more times), each attempt paid."""
    repo = _copy(tmp_path)
    r = _run(repo, RH_FAKE_EMPTY_TEST="1", RH_FAKE_LATENCY="0.0")
    assert r.returncode == 0, r.stderr[-3000:]
    j = (repo / "runs" / "journal.txt").read_text()
    assert "empty reply" in j
    full = [x for x in _rows(repo)]
    assert all(x["status"] == "ok" for x in full if not x["batch"]), "an empty reply was not retried"
