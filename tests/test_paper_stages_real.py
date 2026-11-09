"""The real (non-fake) select -> baseline -> score -> alloc glue on the toy library, with only the two JMH calls
stubbed (no Maven, no JMH jar in the sandbox): file resolution, filters, tags, context cutting, per-file
baseline records, the worker pool with identity/throw controls, overlays kept for allocation, gates, resume."""
import _ooptpaper  # noqa: F401  (registers the study as package 'ooptpaper')
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ooptpaper import env, s_alloc, s_baseline, s_score, s_select, subjects
from test_paper_java_pipeline import BAG, BAG_TEST, _tools

JMH_TEXT = """# Benchmark: org.example.jmh.BagBench.run
# Parameters: (size = 64)
....[Thread state: RUNNABLE]........................................................................
 30.0%  30.0% org.example.Bag.view
              org.example.jmh.BagBench.run
 20.0%  20.0% org.example.Bag.sum
              org.example.jmh.BagBench.run
 10.0%  10.0% org.example.Bag.ops
  5.0%   5.0% org.example.Bag$Node.twice
  4.0%   4.0% org.example.Bag.add
  0.5%   0.5% org.example.Bag.hashCode
"""


class HB:
    def __init__(self):
        self.state = {}

    def set(self, **kw):
        self.state.update(kw)

    def touch(self, **kw):
        pass

    def progress(self, lane=None, **kw):
        pass


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    import subprocess
    t = _tools(tmp_path_factory.mktemp("tools"))
    tree = tmp_path_factory.mktemp("tree")
    src = tree / "core/src/main/java/org/example/Bag.java"
    src.parent.mkdir(parents=True)
    src.write_text(BAG)
    tsrc = tree / "core/src/test/java/org/example/BagTest.java"
    tsrc.parent.mkdir(parents=True)
    tsrc.write_text(BAG_TEST)
    classes, tclasses = tree / "core/target/classes", tree / "core/target/test-classes"
    classes.mkdir(parents=True)
    tclasses.mkdir(parents=True)
    subprocess.run([str(t.javac), "--release", "17", "-d", str(classes), str(src)], check=True, env=t.env())
    subprocess.run([str(t.javac), "--release", "17", "-cp", f"{classes}:{t.randoop}", "-d", str(tclasses),
                    str(tsrc)], check=True, env=t.env())
    subjects.LIBS["toy"] = {"roots": {"core/src/main/java": ("core", "core")}, "packages": ["org.example"],
                            "release": 17}
    env.set_tools(t)
    mode = "canary"
    bdir = env.scope_dir(mode) / "build"
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / "toy.json").write_text(json.dumps({
        "lib": "toy", "ok": True, "tree": str(tree), "jmh_jar": str(tree / "bench.jar"),
        "benchmarks": ["org.example.jmh.BagBench.run"],
        "modules": {"core": {"classes": str(classes), "test_classes": str(tclasses),
                             "test_src": str(tree / "core/src/test/java"),
                             "test_cp": [str(tclasses), str(classes), str(t.randoop)]}}}))
    yield SimpleNamespace(t=t, tree=tree, mode=mode)
    env.set_tools(None)
    subjects.LIBS.pop("toy", None)


def _ctx(world, tmp_rows):
    from rh.util import RUNS, read_jsonl
    return SimpleNamespace(mode=world.mode, hb=HB(), out=lambda st: RUNS / world.mode / f"{st}.jsonl",
                           stage_rows=lambda st, mode=None: list(read_jsonl(RUNS / (mode or world.mode) / f"{st}.jsonl")))


def test_select_baseline_score_alloc(world, monkeypatch, tmp_path):
    from rh.util import RUNS
    monkeypatch.setattr(s_select, "jmh_stack", lambda t, jar, bench, **kw: JMH_TEXT)
    monkeypatch.setattr(subjects, "libs_for", lambda mode: ["toy"])
    monkeypatch.setattr(s_baseline, "workers", lambda d: 2)
    monkeypatch.setattr(s_score, "workers", lambda d: 3)
    for mod in (s_select, s_baseline, s_score, s_alloc):
        monkeypatch.setattr(mod, "fake", lambda: False)        # the real path, although the suite runs with fakes on
    ctx = _ctx(world, tmp_path)

    rec = s_select._select_lib(ctx, world.t, "toy")
    names = {m["name"] for m in rec["methods"]}
    assert names == {"view", "sum", "ops", "add"}      # twice: 1 line (< 3); hashCode: < 1 % share; bench: not library source
    assert rec["stats"]["length"] == 1 and rec["stats"]["unresolved"] == 1
    view = next(m for m in rec["methods"] if m["name"] == "view")
    assert view["tags"]["T_COPY"] and view["tags"]["T_EQUALS"] and view["bench_params"] == {"size": "64"}
    assert next(m for m in rec["methods"] if m["name"] == "ops")["tags"]["T_SYNC"]
    assert view["ctx_r2"].startswith("package org.example;")

    mb = s_baseline.run_baseline(ctx)
    assert mb["files_ok"] == 1 and mb["methods_with_project_tests"] == len(rec["methods"])
    assert s_baseline.check_baseline(ctx, mb) == []

    gen = RUNS / world.mode / "generate.jsonl"
    gen.parent.mkdir(parents=True, exist_ok=True)
    rows = [("gen|R1|m|" + view["id"] + "|s1", view["id"], "public List<Integer> view() {\n  return items;\n}"),
            ("gen|R2|m|" + view["id"] + "|s1", view["id"], view["src"])]
    with open(gen, "w") as f:
        for cid, mid, pm in rows:
            f.write(json.dumps({"cell_id": cid, "model": "m", "arm": cid.split("|")[1], "status": "ok",
                                "parsed": {"patched_method": pm, "claimed_change": "COPY_ELISION",
                                           "confidence": "HIGH", "rationale": ""},
                                "meta": {"method": mid, "rung": cid.split("|")[1], "sample": 1}}) + "\n")
    ms = s_score.run_score(ctx)
    assert s_score.check_score(ctx, ms) == [], ms
    out = {r["cell_id"]: r for r in map(json.loads, (RUNS / world.mode / "score.jsonl").read_text().splitlines())}
    assert out["score|" + rows[0][0]]["classification"]["silent_break"]
    assert out["score|" + rows[1][0]]["classification"]["rows"]["R0_NONE"]
    assert ms["identity_n"] == 1 and ms["identity_r0"] == 1.0         # controls for every method with replies
    assert ms["throw_reached"] >= 1 and ms["throw_caught"] == ms["throw_reached"]
    assert Path(out["score|" + rows[0][0]]["overlay"]).exists()

    n_before = len((RUNS / world.mode / "score.jsonl").read_text().splitlines())
    s_score.run_score(ctx)                                             # resume: nothing re-scored
    assert len((RUNS / world.mode / "score.jsonl").read_text().splitlines()) == n_before

    seen = []
    monkeypatch.setattr(s_alloc, "jmh_gc", lambda t, overlay, jar, bench, params, out, **kw:
                        seen.append((str(overlay), params)) or {"bytes_per_op": 24.0, "score": 1.0})
    ma = s_alloc.run_alloc(ctx)
    assert ma["measured"] == 2 and ma["base_measured"] == 1 and s_alloc.check_alloc(ctx, ma) == []
    assert all(p == {"size": "64"} for _, p in seen)
    assert any("overlay_base" in o for o, _ in seen)

    # resume on a new instance: /opt/data (overlays, baseline suites) is empty, runs/ came back from the backup
    import shutil
    from ooptpaper import s_build
    shutil.rmtree(env.work_dir(world.mode) / "overlays")
    shutil.rmtree(env.work_dir(world.mode) / "base")
    assert not s_baseline.baseline_present(json.loads(next((env.scope_dir(world.mode) / "baseline").glob("*.json"))
                                                      .read_text()))
    mb2 = s_baseline.run_baseline(ctx)
    assert mb2["files_ok"] == 1 and (env.work_dir(world.mode) / "base").exists()
    (RUNS / world.mode / "alloc.jsonl").unlink()
    seen.clear()
    ma2 = s_alloc.run_alloc(ctx)
    assert ma2["measured"] == 2, ma2                               # patched overlays rebuilt from the replies
    assert all(Path(o).exists() for o, _ in seen)
    b = json.loads((env.scope_dir(world.mode) / "build" / "toy.json").read_text())
    assert not s_build.build_present(b)                            # toy record has no JMH jar: would be rebuilt

    # a failed measurement is retried (repair 3aed78a1), the latest row per cell wins
    rows_now = [json.loads(x) for x in (RUNS / world.mode / "alloc.jsonl").read_text().splitlines()]
    victim = next(r for r in rows_now if r["kind"] == "patch")         # as on the instance: the cell only failed
    with open(RUNS / world.mode / "alloc.jsonl", "w") as f:
        for r in rows_now:
            f.write(json.dumps(dict(r, status="failed", bytes_per_op=None) if r["cell_id"] == victim["cell_id"]
                               else r) + "\n")
    seen.clear()
    ma3 = s_alloc.run_alloc(ctx)
    assert len(seen) == 1 and ma3["measured"] == 2, (seen, ma3)
