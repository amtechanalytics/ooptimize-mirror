"""E04: every pilot file recompiles under the scoring recipe; kept tests (project and Randoop) reproduce on a
third run with the baseline overlay (100 %), so a later failure is the patch, not flakiness."""
from pathlib import Path

from _oopt import rj, scope
from conftest import fake


def test_baseline_reproduces(runs, tmp_path):
    recs = [rj(p) for p in sorted((scope(runs) / "baseline").glob("*.json"))]
    assert recs and all(r["ok"] for r in recs), [r.get("errors") for r in recs if not r["ok"]]
    assert any(r["tests"]["keep"] for r in recs) or any(rr.get("keep") for r in recs for rr in r["randoop"].values())
    if fake():
        return
    from paper import env, javatools, oracles
    t = env.tools()
    for r in recs:
        run_cp = javatools.cp(r["overlay_base"], r["test_cp"])
        if r["tests"]["keep"]:
            res = javatools.run_tests(t, run_cp, r["tests"]["classes"], tmp_path / f"{r['key']}-t3.json")
            c = oracles.compare_tests(r["tests"]["keep"], res)
            assert c["failed"] == 0 and c["missing"] == 0, (r["file"], c["failures"][:3])
        for cls, rr in r["randoop"].items():
            if rr.get("keep"):
                res = javatools.run_tests(t, javatools.cp(rr["dir"], run_cp, t.randoop), rr["classes"],
                                          tmp_path / f"{r['key']}-r3.json", engine="junit4")
                c = oracles.compare_tests(rr["keep"], res)
                assert c["failed"] == 0 and c["missing"] == 0, (cls, c["failures"][:3])
