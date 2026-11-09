"""A07: rate-limit headers are returned and the planned concurrency leaves >= 30% token headroom."""
import json
import os

from conftest import fake


def test_headroom(cfg, runs):
    logs = (runs.parent / "logs")
    for m in cfg.models:
        p = logs / f"api_{m['id'].replace('/', '_')}.jsonl"
        assert p.exists(), f"no api log for {m['id']}"
        rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
        hdrs = [r["headers"] for r in rows if r.get("headers")]
        assert hdrs, f"{m['id']}: no response headers logged"
        if fake() or m["provider"] == "xai":   # xAI does not return token headroom headers
            continue
        remaining = [int(v) for h in hdrs for k, v in h.items()
                     if k.endswith("tokens-remaining") or k == "x-ratelimit-remaining-tokens"]
        if remaining and m.get("tpm_budget"):
            assert max(remaining) >= 0.3 * int(m["tpm_budget"]), \
                f"{m['id']}: remaining tokens {max(remaining)} < 30% of planned {m['tpm_budget']}"
