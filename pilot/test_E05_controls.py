"""E05: every parsed pilot reply was scored; identity controls compile and score R0 (no false positive on
unchanged code); throw controls are caught wherever the kept suites ran (the suites see the overlay)."""
from _oopt import rows
from conftest import read_jsonl


def test_controls(runs):
    gen = {r["cell_id"] for r in read_jsonl(runs / "pilot" / "generate.jsonl") if r["status"] == "ok"}
    sc = rows(runs / "pilot" / "score.jsonl")
    scored = {r["gen_cell"] for r in sc if r["kind"] == "gen" and r.get("status") in ("scored", "wrong_method")}
    assert gen and gen <= scored, f"unscored pilot replies: {sorted(gen - scored)[:5]}"
    ident = [r for r in sc if r["kind"] == "ctl_identity"]
    assert ident and all(r.get("status") == "scored" and r.get("compile_ok") for r in ident), \
        [r.get("compile_error") for r in ident if not r.get("compile_ok")][:2]
    assert all(r["classification"]["rows"]["R0_NONE"] for r in ident), \
        [r["classification"]["fired"] for r in ident]
    thr = [r for r in sc if r["kind"] == "ctl_throw" and r.get("status") == "scored"]
    reached = [r for r in thr if r["signals"]["tests"].get("ran") or r["signals"]["randoop"].get("ran")]
    caught = [r for r in reached if set(r["classification"]["fired"]) & {"R2_TESTS_FAIL", "R3a_VALUE",
                                                                          "R3b_EXCEPTION", "R3c_ORDER"}]
    assert reached and len(caught) >= max(1, len(reached) // 2), (len(caught), len(reached))
