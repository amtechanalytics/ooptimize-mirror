"""Request building and response parsing for each vendor, error/retry behaviour (no network)."""
import io
import json
import urllib.error

import pytest

from rh import vendors

ANTHROPIC_OK = {"content": [{"type": "text", "text": '{"label":"security","confidence":0.9}'}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 12, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 900,
                          "output_tokens": 15}}
ANTHROPIC_TRUNC = dict(ANTHROPIC_OK, stop_reason="max_tokens")
OPENAI_OK = {"choices": [{"message": {"content": '{"label":"usability","confidence":0.7}'},
                          "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 1000, "completion_tokens": 20,
                       "prompt_tokens_details": {"cached_tokens": 896}}}
OPENAI_EMPTY = {"choices": [{"message": {"content": None}, "finish_reason": "length"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 0}}
M_A = {"id": "model-a", "provider": "anthropic", "max_output_tokens": 100}
M_O = {"id": "gpt-x", "provider": "openai", "max_output_tokens": 100}
M_X = {"id": "grok-x", "provider": "xai", "max_output_tokens": 100}


def test_anthropic_request_has_cached_prefix_and_no_sampling():
    b = vendors.build_anthropic(M_A, "PREFIX", "ITEM", 50)
    assert b["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert b["system"][0]["text"] == "PREFIX" and b["messages"][0]["content"] == "ITEM"
    assert "temperature" not in b and "top_p" not in b


def test_openai_and_xai_token_param_names():
    assert "max_completion_tokens" in vendors.build_openai(M_O, "P", "I", 5)
    assert "max_tokens" in vendors.build_openai(M_X, "P", "I", 5)
    for b in (vendors.build_openai(M_O, "P", "I", 5), vendors.build_openai(M_X, "P", "I", 5)):
        assert b["messages"][0] == {"role": "system", "content": "P"}
        assert "temperature" not in b


def test_parse_anthropic_usage_and_truncation():
    text, usage, trunc = vendors.parse_anthropic(ANTHROPIC_OK)
    assert json.loads(text)["label"] == "security"
    assert usage == {"input": 12, "cache_write": 0, "cache_read": 900, "output": 15}
    assert not trunc
    assert vendors.parse_anthropic(ANTHROPIC_TRUNC)[2] is True


def test_parse_openai_cached_split_and_empty():
    text, usage, trunc = vendors.parse_openai(OPENAI_OK)
    assert usage == {"input": 104, "cache_write": 0, "cache_read": 896, "output": 20}
    text, usage, trunc = vendors.parse_openai(OPENAI_EMPTY)
    assert text == "" and trunc is True


class _Resp(io.BytesIO):
    def __init__(self, body, status=200, headers=None):
        super().__init__(json.dumps(body).encode())
        self.status = status
        self.headers = headers or {"anthropic-ratelimit-tokens-remaining": "50000"}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(code, body=b"{}", headers=None):
    return urllib.error.HTTPError("u", code, "err", headers or {}, io.BytesIO(body))


def test_retry_on_429_then_success(monkeypatch):
    calls = {"n": 0}

    def fake_open(req, timeout=0):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _http_error(429, headers={"retry-after": "0"})
        return _Resp(ANTHROPIC_OK)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("RH_FAKE_VENDOR", "0")
    monkeypatch.setattr(vendors.urllib.request, "urlopen", fake_open)
    monkeypatch.setattr(vendors.time, "sleep", lambda s: None)
    import rh.util
    monkeypatch.setattr(rh.util.time, "sleep", lambda s: None)
    r = vendors.call(M_A, "P", "I", 20)
    assert calls["n"] == 3 and r.usage["cache_read"] == 900
    assert "anthropic-ratelimit-tokens-remaining" in r.headers


def test_400_is_not_retried(monkeypatch):
    calls = {"n": 0}

    def fake_open(req, timeout=0):
        calls["n"] += 1
        raise _http_error(400, b'{"error":{"type":"invalid_request_error"}}')

    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("RH_FAKE_VENDOR", "0")
    monkeypatch.setattr(vendors.urllib.request, "urlopen", fake_open)
    with pytest.raises(vendors.VendorError) as e:
        vendors.call(M_O, "P", "I", 20)
    assert calls["n"] == 1 and e.value.status == 400


def test_missing_key_is_clear(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setenv("RH_FAKE_VENDOR", "0")
    with pytest.raises(vendors.VendorError, match="XAI_API_KEY"):
        vendors.call(M_X, "P", "I", 20)


def test_custom_id_is_vendor_safe():
    cid = vendors.custom_id("label|few|model-a-long-identifier|r01" * 5)
    assert len(cid) <= 64 and cid.replace("c", "", 1).isalnum()
