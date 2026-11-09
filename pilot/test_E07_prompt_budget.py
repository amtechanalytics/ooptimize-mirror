"""E07: pilot prompts fit the per-call limit and no reply was truncated; the token calibration the sizing rule
will use is plausible (2-6 characters per token per model)."""
from conftest import fake, read_jsonl


def test_prompt_budget(runs, cfg):
    from paper import s_generate
    rows = [r for r in read_jsonl(runs / "pilot" / "generate.jsonl") if r.get("usage")]
    assert rows and not [r for r in rows if r.get("truncated")]
    limit = int(cfg.plan["stages"]["generate"]["max_prompt_tokens"])
    for r in rows:
        u = r["usage"]
        assert u["input"] + u["cache_read"] + u["cache_write"] <= limit, (r["cell_id"], u)
    cal = s_generate.calibration(cfg)
    if not fake():
        for mid, cpt in cal["chars_per_token"].items():
            assert 2.0 <= cpt <= 6.0, (mid, cpt)
