"""1.0.2 C + D: compute in projections; Groq provider shapes, retries, config rules; token refund and rpm."""
import io
import json
import os
import shutil
import subprocess
import sys
import urllib.error

import pytest

from rh import config, prep, vendors
from rh.util import ROOT

M_G = {"id": "openai/gpt-oss-120b", "provider": "groq", "max_output_tokens": 100}
GROQ_OK = {"choices": [{"message": {"content": '{"label":"security","confidence":0.8}'}, "finish_reason": "stop"}],
           "usage": {"prompt_tokens": 1200, "completion_tokens": 30, "prompt_tokens_details": {"cached_tokens": 1024}}}
GROQ_LEN = {"choices": [{"message": {"content": '{"label":'}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 100}}


def test_groq_request_shape():
    b = vendors.build_openai(M_G, "PREFIX", "ITEM", 64)
    assert b["max_tokens"] == 64 and "max_completion_tokens" not in b
    assert "temperature" not in b and "top_p" not in b
    assert b["messages"][0] == {"role": "system", "content": "PREFIX"}
    assert vendors.ENDPOINTS["groq"] == "https://api.groq.com/openai/v1"
    assert vendors.KEY_ENV["groq"] == "GROQ_API_KEY"


def test_groq_parse_cached_and_length():
    _, u, trunc = vendors.parse_openai(GROQ_OK)
    assert u == {"input": 176, "cache_write": 0, "cache_read": 1024, "output": 30} and not trunc
    assert vendors.parse_openai(GROQ_LEN)[2] is True


class _Resp(io.BytesIO):
    def __init__(self, body):
        super().__init__(json.dumps(body).encode())
        self.status = 200
        self.headers = {"x-ratelimit-remaining-tokens": "240000", "x-ratelimit-remaining-requests": "990"}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_groq_429_retry_after_then_ok_and_400_error_body(monkeypatch):
    calls = {"n": 0}

    def ok_after_429(req, timeout=0):
        calls["n"] += 1
        assert req.full_url == "https://api.groq.com/openai/v1/chat/completions"
        assert req.get_header("User-agent") == vendors.USER_AGENT, "default Python-urllib agent is blocked by Groq"
        if calls["n"] == 1:
            raise urllib.error.HTTPError("u", 429, "rate", {"retry-after": "0"},
                                         io.BytesIO(b'{"error":{"message":"Rate limit reached"}}'))
        return _Resp(GROQ_OK)

    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("RH_FAKE_VENDOR", "0")
    monkeypatch.setattr(vendors.urllib.request, "urlopen", ok_after_429)
    import rh.util
    monkeypatch.setattr(rh.util.time, "sleep", lambda s: None)
    r = vendors.call(M_G, "P", "I", 50)
    assert calls["n"] == 2 and r.usage["cache_read"] == 1024
    assert r.headers["x-ratelimit-remaining-tokens"] == "240000"

    def bad(req, timeout=0):
        raise urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(
            b'{"error":{"message":"model_decommissioned","type":"invalid_request_error"}}'))
    monkeypatch.setattr(vendors.urllib.request, "urlopen", bad)
    with pytest.raises(vendors.VendorError, match="model_decommissioned"):
        vendors.call(M_G, "P", "I", 50)


def test_config_accepts_groq_rejects_groq_batch():
    c = config.load()
    base = dict(c.models[0], id="openai/gpt-oss-120b", provider="groq")
    c.prices = dict(c.prices, models=dict(c.prices["models"], **{"openai/gpt-oss-120b": {
        "input_per_mtok": 0.15, "output_per_mtok": 0.6, "source": "s"}}))
    c.models = [base]
    c.errors = []
    assert not [e for e in config.validate(c) if "gpt-oss" in e]
    c.models = [dict(base, batch=True)]
    c.errors = []
    assert any("batch is not supported" in e for e in config.validate(c))


class Clock:
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_refund_keeps_throughput_under_tpm(monkeypatch):
    clk = Clock()
    monkeypatch.setattr(vendors.time, "monotonic", clk.monotonic)
    monkeypatch.setattr(vendors.time, "sleep", clk.sleep)
    b = vendors.TokenBucket(250_000)          # Developer-plan limit
    for _ in range(100):                      # estimate 66K (long prompt + 64K max_tokens), actual 2.5K
        b.acquire(66_000)
        b.refund(66_000 - 2_500)
    assert clk.t < 60, f"refund should keep 100 calls inside a minute, took {clk.t:.0f}s"
    clk2 = Clock()
    monkeypatch.setattr(vendors.time, "monotonic", clk2.monotonic)
    monkeypatch.setattr(vendors.time, "sleep", clk2.sleep)
    nb = vendors.TokenBucket(250_000)
    for _ in range(10):
        nb.acquire(66_000)                    # without refund: throttled to ~4 calls/min
    assert clk2.t > 60


def test_rpm_budget_enforced(monkeypatch):
    clk = Clock()
    monkeypatch.setattr(vendors.time, "monotonic", clk.monotonic)
    monkeypatch.setattr(vendors.time, "sleep", clk.sleep)
    b = vendors.TokenBucket(60)               # 60 requests/min
    for _ in range(120):
        b.acquire(1)
    assert 55 <= clk.t <= 65, clk.t           # burst of 60, then 1/s


def test_projection_fails_only_because_of_compute(monkeypatch):
    p = prep.projection()
    assert p["total"] <= p["ceiling"] and p["compute"] > 0
    real = config.load

    def heavy(*a, **k):
        c = real(*a, **k)
        c.project = dict(c.project, expected_hours=5000)
        return c
    monkeypatch.setattr(config, "load", heavy)
    out = prep.c_projection()
    assert isinstance(out, list) and any("compute" in e for e in out)
    p2 = prep.projection()
    assert p2["with_overheads"] <= p2["ceiling"] < p2["total"], "API alone fits; compute pushes it over"


def test_missing_compute_price_is_config_error():
    c = config.load()
    c.compute = dict(c.compute, instance_type="z9.mega")
    c.errors = []
    assert any("z9.mega" in e for e in config.validate(c))


def test_canary_message_shows_api_and_compute(tmp_path):
    dst = tmp_path / ROOT.name
    shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns("runs", "results", "logs", ".data", ".fake_aws",
                                                             "__pycache__", "data"))
    e = dict(os.environ, RH_ROOT=str(dst), RH_RUN_ID="t", RH_FAKE_AWS_DIR=str(dst / ".fake_aws"))
    r = subprocess.run([sys.executable, "-m", "rh.runner", "--dry-run"], cwd=dst, env=e, capture_output=True,
                       text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-2000:]
    line = next(ln for ln in (dst / "runs" / "journal.txt").read_text().splitlines() if "| PROJECT" in ln)
    assert "API $" in line and "compute $" in line
    subs = [json.loads(x) for x in (dst / ".fake_aws" / "sns.log").read_text().splitlines()]
    canary = next(s for s in subs if "CANARY | PASS" in s["subject"])
    assert "compute $" in canary["body"]
