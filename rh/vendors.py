"""Vendor clients over plain HTTPS (no SDK version drift): Anthropic, OpenAI, xAI, and a fake vendor
for dry runs. Every call returns a normalised Response; sampling parameters are never sent."""
from __future__ import annotations

import hashlib
import io
import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field

from .util import LOGS, append_jsonl, env_flag, now_iso, retry

ENDPOINTS = {
    "anthropic": "https://api.anthropic.com/v1",
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "xai": "https://api.x.ai/v1",
}
KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "groq": "GROQ_API_KEY",
           "xai": "XAI_API_KEY"}
BATCH_PROVIDERS = {"anthropic", "openai", "fake"}
ANTHROPIC_VERSION = "2023-06-01"
RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504, 529}


class VendorError(Exception):
    def __init__(self, msg, status=None, retryable=False, retry_after=None):
        super().__init__(msg)
        self.status = status
        self.retryable = retryable
        self.retry_after = retry_after


class RetryableVendorError(VendorError):
    pass


@dataclass
class Response:
    text: str
    usage: dict            # input (uncached), cache_write, cache_read, output
    truncated: bool
    model: str
    latency_s: float
    headers: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- rate limiting
class TokenBucket:
    """Per-model budget per minute (tokens or requests); blocks the caller instead of tripping a 429.
    acquire() charges an upper-bound estimate up front; refund() returns what the response did not use."""

    def __init__(self, per_minute: int | None):
        self.capacity = float(per_minute or 0)
        self.tokens = self.capacity
        self.updated = time.monotonic()
        self.lock = threading.Lock()

    def refund(self, n: float) -> None:
        if self.capacity <= 0 or n <= 0:
            return
        with self.lock:
            self.tokens = min(self.capacity, self.tokens + float(n))

    def acquire(self, n: int) -> None:
        if self.capacity <= 0:
            return
        n = min(float(n), self.capacity)
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.capacity / 60.0)
                self.updated = now
                if self.tokens >= n:
                    self.tokens -= n
                    return
                wait = (n - self.tokens) * 60.0 / self.capacity
            time.sleep(min(wait, 5.0))


_BUCKETS: dict[str, TokenBucket] = {}
_RPM: dict[str, TokenBucket] = {}


def bucket(model: dict) -> TokenBucket:
    b = _BUCKETS.get(model["id"])
    if b is None:
        b = _BUCKETS[model["id"]] = TokenBucket(model.get("tpm_budget"))
    return b


def rpm_bucket(model: dict) -> TokenBucket:
    b = _RPM.get(model["id"])
    if b is None:
        b = _RPM[model["id"]] = TokenBucket(model.get("rpm_budget"))
    return b


# --------------------------------------------------------------------------- http
def _http(method: str, url: str, headers: dict, body: bytes | None = None, timeout: float = 600):
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
            hdrs = {k.lower(): v for k, v in r.headers.items()}
            return r.status, hdrs, data, time.monotonic() - t0
    except urllib.error.HTTPError as e:
        data = e.read() if hasattr(e, "read") else b""
        hdrs = {k.lower(): v for k, v in (e.headers or {}).items()}
        ra = hdrs.get("retry-after")
        msg = f"HTTP {e.code} {url}: {data[:500]!r}"
        if e.code in RETRYABLE:
            raise RetryableVendorError(msg, e.code, True, ra) from None
        raise VendorError(msg, e.code, False) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise RetryableVendorError(f"network error {url}: {e}", None, True) from None


def _key(provider: str) -> str:
    k = os.environ.get(KEY_ENV[provider], "")
    if not k:
        raise VendorError(f"{KEY_ENV[provider]} is not set")
    return k


USER_AGENT = "research-harness/1.0"   # some vendor edges (e.g. Groq) reject the default Python-urllib agent


def _headers(provider: str) -> dict:
    if provider == "anthropic":
        return {"x-api-key": _key(provider), "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json", "user-agent": USER_AGENT}
    return {"authorization": f"Bearer {_key(provider)}", "content-type": "application/json",
            "user-agent": USER_AGENT}


def _rl_headers(h: dict) -> dict:
    return {k: v for k, v in h.items() if "ratelimit" in k or k in ("retry-after", "request-id", "x-request-id")}


# --------------------------------------------------------------------------- request building
def build_anthropic(model: dict, prefix: str, item: str, max_tokens: int) -> dict:
    # The stable prefix is a system block marked for prompt caching; only the item varies.
    return {
        "model": model["id"],
        "max_tokens": int(max_tokens),
        "system": [{"type": "text", "text": prefix, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": item}],
    }


def build_openai(model: dict, prefix: str, item: str, max_tokens: int) -> dict:
    # Same prefix bytes first on every call so automatic prefix caching applies.
    body = {"model": model["id"],
            "messages": [{"role": "system", "content": prefix}, {"role": "user", "content": item}]}
    if model["provider"] == "openai":
        body["max_completion_tokens"] = int(max_tokens)
    else:                                   # groq, xai: OpenAI-compatible, classic parameter name
        body["max_tokens"] = int(max_tokens)
    return body


def parse_anthropic(d: dict) -> tuple[str, dict, bool]:
    text = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
    u = d.get("usage") or {}
    usage = {"input": u.get("input_tokens", 0) or 0,
             "cache_write": u.get("cache_creation_input_tokens", 0) or 0,
             "cache_read": u.get("cache_read_input_tokens", 0) or 0,
             "output": u.get("output_tokens", 0) or 0}
    return text, usage, d.get("stop_reason") == "max_tokens"


def parse_openai(d: dict) -> tuple[str, dict, bool]:
    ch = (d.get("choices") or [{}])[0]
    text = ((ch.get("message") or {}).get("content")) or ""
    u = d.get("usage") or {}
    cached = ((u.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0
    prompt = u.get("prompt_tokens", 0) or 0
    usage = {"input": max(prompt - cached, 0), "cache_write": 0, "cache_read": cached,
             "output": u.get("completion_tokens", 0) or 0}
    return text, usage, ch.get("finish_reason") == "length"


# --------------------------------------------------------------------------- fake vendor
FAKE_RESPONDER = None   # set by Context: fn(model_id, prefix, item) -> str
_FAKE_SEEN: set[str] = set()
_FAKE_LOCK = threading.Lock()


def _fake_call(model: dict, prefix: str, item: str, max_tokens: int) -> Response:
    text = FAKE_RESPONDER(model["id"], prefix, item) if FAKE_RESPONDER else json.dumps({"echo": item[:40]})
    if env_flag("RH_FAKE_FAIL_RATE_TEST") and hashlib.sha1(item.encode()).digest()[0] < 8:
        text = "not json"
    if env_flag("RH_FAKE_EMPTY_TEST") and hashlib.sha1(item.encode()).digest()[0] < 32:
        ek = "empty" + model["id"] + item
        with _FAKE_LOCK:
            if ek not in _FAKE_SEEN:          # first reply to this item is empty, as gpt-oss on Groq did
                _FAKE_SEEN.add(ek)
                text = ""
    key = model["id"] + hashlib.sha1(prefix.encode()).hexdigest()
    with _FAKE_LOCK:
        first = key not in _FAKE_SEEN
        _FAKE_SEEN.add(key)
    p, i = len(prefix) // 4, len(item) // 4
    usage = {"input": i, "cache_write": p if first else 0, "cache_read": 0 if first else p,
             "output": max(1, len(text) // 4)}
    time.sleep(float(os.environ.get("RH_FAKE_LATENCY", "0")))
    return Response(text, usage, False, model["id"], 0.0, {"x-ratelimit-remaining-tokens": "1000000"}, {})


def is_fake(model: dict) -> bool:
    return model["provider"] == "fake" or env_flag("RH_FAKE_VENDOR")


# --------------------------------------------------------------------------- sync call
def call(model: dict, prefix: str, item: str, max_tokens: int | None = None, *, log_stage: str = "") -> Response:
    max_tokens = int(max_tokens or model["max_output_tokens"])
    est = (len(prefix) + len(item)) // 3 + max_tokens
    rpm_bucket(model).acquire(1)
    bucket(model).acquire(est)
    if is_fake(model):
        r = _fake_call(model, prefix, item, max_tokens)
        _refund(model, est, r)
        _log(model, log_stage, prefix, item, r, None)
        return r
    prov = model["provider"]
    if prov == "anthropic":
        url, body = ENDPOINTS[prov] + "/messages", build_anthropic(model, prefix, item, max_tokens)
    else:
        url, body = ENDPOINTS[prov] + "/chat/completions", build_openai(model, prefix, item, max_tokens)
    payload = json.dumps(body).encode()

    def once():
        return _http("POST", url, _headers(prov), payload)

    try:
        status, hdrs, data, lat = retry(once, attempts=7, retry_on=(RetryableVendorError,))
    except VendorError as e:
        _log(model, log_stage, prefix, item, None, str(e))
        raise
    d = json.loads(data)
    text, usage, trunc = parse_anthropic(d) if prov == "anthropic" else parse_openai(d)
    r = Response(text, usage, trunc, model["id"], lat, _rl_headers(hdrs), d)
    _refund(model, est, r)
    _log(model, log_stage, prefix, item, r, None)
    return r


def _refund(model: dict, est: int, r: "Response") -> None:
    used = sum(r.usage.get(k, 0) for k in ("input", "cache_write", "cache_read", "output"))
    bucket(model).refund(est - used)


def _log(model, stage, prefix, item, r: Response | None, err: str | None):
    append_jsonl(LOGS / f"api_{model['id'].replace('/', '_')}.jsonl", {
        "ts": now_iso(), "model": model["id"], "stage": stage,
        "prefix_sha": hashlib.sha256(prefix.encode()).hexdigest()[:16], "item": item,
        "text": None if r is None else r.text, "usage": None if r is None else r.usage,
        "truncated": None if r is None else r.truncated, "headers": None if r is None else r.headers,
        "latency_s": None if r is None else round(r.latency_s, 3), "error": err})


def list_models(provider: str) -> list[str]:
    """$0 liveness check for a key."""
    if provider == "fake":
        return ["fake"]
    status, hdrs, data, _ = retry(lambda: _http("GET", ENDPOINTS[provider] + "/models", _headers(provider)),
                                  attempts=3, retry_on=(RetryableVendorError,))
    d = json.loads(data)
    return [m.get("id") for m in d.get("data", [])]


# --------------------------------------------------------------------------- batches
def custom_id(cell_id: str) -> str:
    return "c" + hashlib.sha1(cell_id.encode()).hexdigest()[:40]


class Batch:
    """Common interface: submit(list[(cell_id, prefix, item, max_tokens)]) -> batch_id;
    status(batch_id) -> dict(state, done, total); results(batch_id) -> list[(custom_id, Response|None, err)];
    cancel(batch_id)."""

    def __init__(self, model: dict):
        self.model = model
        self.prov = "fake" if is_fake(model) else model["provider"]

    # ---- submit
    def submit(self, items) -> str:
        if self.prov == "fake":
            bid = "fakebatch_" + uuid.uuid4().hex[:12]
            out = {}
            for cid, prefix, item, mt in items:
                out[custom_id(cid)] = _fake_call(self.model, prefix, item, mt).__dict__
            _fake_save(bid, {"submitted": time.time(), "results": out, "cancelled": False})
            return bid
        if self.prov == "anthropic":
            reqs = [{"custom_id": custom_id(cid), "params": build_anthropic(self.model, p, it, mt)}
                    for cid, p, it, mt in items]
            body = json.dumps({"requests": reqs}).encode()
            _, _, data, _ = retry(lambda: _http("POST", ENDPOINTS["anthropic"] + "/messages/batches",
                                                _headers("anthropic"), body),
                                  attempts=5, retry_on=(RetryableVendorError,))
            return json.loads(data)["id"]
        if self.prov == "openai":
            lines = [json.dumps({"custom_id": custom_id(cid), "method": "POST", "url": "/v1/chat/completions",
                                 "body": build_openai(self.model, p, it, mt)}) for cid, p, it, mt in items]
            file_id = _openai_upload("\n".join(lines).encode(), "batch.jsonl")
            body = json.dumps({"input_file_id": file_id, "endpoint": "/v1/chat/completions",
                               "completion_window": "24h"}).encode()
            _, _, data, _ = retry(lambda: _http("POST", ENDPOINTS["openai"] + "/batches", _headers("openai"), body),
                                  attempts=5, retry_on=(RetryableVendorError,))
            return json.loads(data)["id"]
        raise VendorError(f"batch not supported for provider {self.prov}")

    # ---- status
    def status(self, bid: str) -> dict:
        if self.prov == "fake":
            fb = _fake_load(bid)
            n = len(fb["results"]) if fb else 0
            wait = float(os.environ.get("RH_FAKE_BATCH_SECONDS", "0"))
            ended = (not fb) or fb.get("cancelled") or time.time() - fb["submitted"] >= wait
            return {"state": "ended" if ended else "running", "done": n if ended else 0, "total": n}
        if self.prov == "anthropic":
            _, _, data, _ = retry(lambda: _http("GET", f"{ENDPOINTS['anthropic']}/messages/batches/{bid}",
                                                _headers("anthropic")), attempts=5, retry_on=(RetryableVendorError,))
            d = json.loads(data)
            c = d.get("request_counts") or {}
            done = sum(c.get(k, 0) for k in ("succeeded", "errored", "canceled", "expired"))
            total = done + c.get("processing", 0)
            return {"state": "ended" if d.get("processing_status") == "ended" else "running",
                    "done": done, "total": total, "raw": d}
        _, _, data, _ = retry(lambda: _http("GET", f"{ENDPOINTS['openai']}/batches/{bid}", _headers("openai")),
                              attempts=5, retry_on=(RetryableVendorError,))
        d = json.loads(data)
        c = d.get("request_counts") or {}
        st = d.get("status")
        state = "ended" if st in ("completed", "failed", "expired", "cancelled") else "running"
        return {"state": state, "done": c.get("completed", 0) + c.get("failed", 0), "total": c.get("total", 0),
                "raw": d}

    # ---- results
    def results(self, bid: str):
        out = []
        if self.prov == "fake":
            fb = _fake_load(bid) or {"results": {}}
            keep = None
            if fb.get("cancelled_at") is not None:          # cancelled early: only the first part was processed
                wait = float(os.environ.get("RH_FAKE_BATCH_SECONDS", "0")) or 1.0
                frac = min(1.0, max(0.0, (fb["cancelled_at"] - fb["submitted"]) / wait))
                keep = int(len(fb["results"]) * frac)
            for i, (cid, rd) in enumerate(sorted(fb["results"].items())):
                if keep is not None and i >= keep:
                    out.append((cid, None, json.dumps({"type": "canceled"})))
                else:
                    out.append((cid, Response(**rd), None))
            return out
        if self.prov == "anthropic":
            st = self.status(bid)["raw"]
            url = st.get("results_url")
            _, _, data, _ = retry(lambda: _http("GET", url, _headers("anthropic")),
                                  attempts=5, retry_on=(RetryableVendorError,))
            for line in data.decode().splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                res = row.get("result") or {}
                if res.get("type") == "succeeded":
                    text, usage, trunc = parse_anthropic(res["message"])
                    out.append((row["custom_id"], Response(text, usage, trunc, self.model["id"], 0.0, {}, {}), None))
                else:
                    out.append((row["custom_id"], None, json.dumps(res)[:500]))
            return out
        st = self.status(bid)["raw"]
        for fid_key in ("output_file_id", "error_file_id"):
            fid = st.get(fid_key)
            if not fid:
                continue
            _, _, data, _ = retry(lambda fid=fid: _http("GET", f"{ENDPOINTS['openai']}/files/{fid}/content",
                                                        _headers("openai")),
                                  attempts=5, retry_on=(RetryableVendorError,))
            for line in data.decode().splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                resp = row.get("response") or {}
                if resp.get("status_code") == 200:
                    text, usage, trunc = parse_openai(resp.get("body") or {})
                    out.append((row["custom_id"], Response(text, usage, trunc, self.model["id"], 0.0, {}, {}), None))
                else:
                    out.append((row["custom_id"], None, json.dumps(row.get("error") or resp)[:500]))
        return out

    # ---- cancel
    def cancel(self, bid: str) -> None:
        if self.prov == "fake":
            fb = _fake_load(bid)
            if fb:
                fb["cancelled"] = True
                fb.setdefault("cancelled_at", time.time())
                _fake_save(bid, fb)
            return
        if self.prov == "anthropic":
            url = f"{ENDPOINTS['anthropic']}/messages/batches/{bid}/cancel"
        else:
            url = f"{ENDPOINTS['openai']}/batches/{bid}/cancel"
        try:
            _http("POST", url, _headers(self.prov), b"{}" if self.prov == "openai" else None)
        except VendorError as e:
            if e.status not in (400, 404, 409):   # already ended / not found: nothing to cancel
                raise


def _fake_dir():
    from .util import ROOT
    d = __import__("pathlib").Path(os.environ.get("RH_FAKE_AWS_DIR", str(ROOT / ".fake_aws"))) / "vendor_batches"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _fake_save(bid: str, d: dict) -> None:
    p = _fake_dir() / f"{bid}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d))
    os.replace(tmp, p)


def _fake_load(bid: str):
    p = _fake_dir() / f"{bid}.json"
    return json.loads(p.read_text()) if p.exists() else None


def _openai_upload(content: bytes, filename: str) -> str:
    boundary = "----rh" + uuid.uuid4().hex
    buf = io.BytesIO()
    for name, value in (("purpose", b"batch"),):
        buf.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n".encode())
        buf.write(value + b"\r\n")
    buf.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
              f"Content-Type: application/jsonl\r\n\r\n".encode())
    buf.write(content + b"\r\n")
    buf.write(f"--{boundary}--\r\n".encode())
    h = {"authorization": f"Bearer {_key('openai')}", "content-type": f"multipart/form-data; boundary={boundary}",
         "user-agent": USER_AGENT}
    body = buf.getvalue()
    _, _, data, _ = retry(lambda: _http("POST", ENDPOINTS["openai"] + "/files", h, body),
                          attempts=5, retry_on=(RetryableVendorError,))
    return json.loads(data)["id"]
