"""Spend arithmetic, ceilings, cross-thread ledger integrity, and config validation rules."""
import copy
import threading

import pytest

from rh import config
from rh.exitcodes import BudgetExceeded
from rh.ledger import Ledger, cost_of

P = {"input_per_mtok": 3.0, "output_per_mtok": 15.0, "cache_write_per_mtok": 3.75, "cache_read_per_mtok": 0.3,
     "batch_discount": 0.5}


def test_cost_math():
    u = {"input": 1_000_000, "cache_write": 0, "cache_read": 1_000_000, "output": 100_000}
    assert cost_of(P, u) == pytest.approx(3.0 + 0.3 + 1.5)
    assert cost_of(P, u, batch=True) == pytest.approx((3.0 + 0.3 + 1.5) / 2)


def test_template_config_is_valid():
    assert config.load().errors == []


def _cfg():
    return config.load()


def test_sampling_params_rejected():
    c = _cfg()
    c.models = copy.deepcopy(c.models)
    c.models[0]["temperature"] = 0
    c.errors = []
    assert any("sampling" in e for e in config.validate(c))


def test_high_tier_needs_approval():
    c = _cfg()
    c.budget = dict(c.budget, tier="high", high_approval="")
    c.errors = []
    assert any("high_approval" in e for e in config.validate(c))
    c.budget = dict(c.budget, tier="high", high_approval="approved in chat 2026-11-06: 'go high'")
    c.errors = []
    assert not any("high_approval" in e for e in config.validate(c))
    assert c.ceiling() == 300


def test_placeholders_and_file_urls_caught_for_real_models():
    c = _cfg()
    c.models = [dict(c.models[0], provider="anthropic", id="REPLACE_ME")]
    c.prices = {"as_of": "x", "models": {"REPLACE_ME": dict(P, source="s")}}
    c.errors = []
    errs = config.validate(c)
    assert any("placeholder" in e for e in errs)
    assert any("file://" in e for e in errs)


def test_ceiling_enforced_and_lock_integrity():
    c = _cfg()
    led = Ledger(c)
    led.check("fake-small", 1.0)
    with pytest.raises(BudgetExceeded):
        led.check("fake-small", 151.0)
    with pytest.raises(BudgetExceeded):
        led.check("executor", 21.0, kind="executor")
    before = led.summary()["calls"]

    def work():
        for _ in range(50):
            led.record("fake-small", {"input": 1000, "output": 1000})
    ts = [threading.Thread(target=work) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    s = led.summary()
    assert s["calls"] - before == 400
    assert s["by_model"]["fake-small"] >= 400 * 0.003 - 1e-6
