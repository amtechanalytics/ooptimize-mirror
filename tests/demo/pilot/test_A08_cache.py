"""A08: for stages that request caching, the second call on the same prefix reads from the cache."""
import json
import warnings

import yaml

BEST_EFFORT = {"groq", "xai"}     # these vendors document prompt caching as best effort, not guaranteed


def test_cache(cfg, runs):
    plan = cfg.plan.get("stages") or {}
    logs = runs.parent / "logs"
    for name, st in plan.items():
        if not st.get("cache"):
            continue          # exempt: prefix too short to cache (prep L13 enforces this)
        for mid in st["models"]:
            rows = [json.loads(x) for x in (logs / f"api_{mid.replace('/', '_')}.jsonl").read_text().splitlines()
                    if x.strip()]
            rows = [r for r in rows if r["stage"] == name and r.get("usage")]
            assert len(rows) >= 2, f"{name}/{mid}: need >= 2 calls to test caching"
            hit = any(r["usage"]["cache_read"] > 0 for r in rows[1:])
            if not hit and cfg.model(mid)["provider"] in BEST_EFFORT:
                warnings.warn(f"{name}/{mid}: no cache reads in the pilot (best-effort cache; spend may exceed the "
                              "prep projection, the canary re-projects from real token counts)")
                continue
            assert hit, f"{name}/{mid}: no cache reads"
