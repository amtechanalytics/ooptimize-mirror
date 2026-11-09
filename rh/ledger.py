"""Spend ledger: every paid call is checked against the ceilings before it is made and recorded
after it returns. A cross-process file lock guards read-modify-write of runs/spend.json."""
from __future__ import annotations

from .exitcodes import BudgetExceeded
from .util import RUNS, append_jsonl, file_lock, now_iso, read_json, write_json

SPEND = RUNS / "spend.json"
LEDGER = RUNS / "ledger.jsonl"
LOCK = RUNS / "spend.lock"


def cost_of(price: dict, usage: dict, batch: bool = False) -> float:
    """usage keys (normalised by vendors.py): input, cache_write, cache_read, output (tokens)."""
    m = 1_000_000.0
    inp = usage.get("input", 0) * price["input_per_mtok"] / m
    cw = usage.get("cache_write", 0) * price.get("cache_write_per_mtok", price["input_per_mtok"]) / m
    cr = usage.get("cache_read", 0) * price.get("cache_read_per_mtok", price["input_per_mtok"]) / m
    out = usage.get("output", 0) * price["output_per_mtok"] / m
    total = inp + cw + cr + out
    if batch:
        total *= price.get("batch_discount", 1.0)
    return round(total, 8)


class Ledger:
    def __init__(self, cfg):
        self.cfg = cfg
        self.prices = (cfg.prices.get("models") or {})

    # ---- reads
    def summary(self) -> dict:
        return read_json(SPEND, default=None) or {
            "total": 0.0, "by_provider": {}, "by_model": {}, "by_kind": {}, "calls": 0, "updated": None}

    def total(self) -> float:
        return float(self.summary()["total"])

    # ---- guards
    def estimate(self, model_id: str, in_tokens: int, out_tokens: int, batch: bool = False) -> float:
        return cost_of(self.prices[model_id], {"input": in_tokens, "output": out_tokens}, batch)

    def check(self, model_id: str, est: float, kind: str = "experiment") -> None:
        b = self.cfg.budget
        s = self.summary()
        if kind == "executor":
            if s["by_kind"].get("executor", 0.0) + est > float(b["executor_ceiling"]):
                raise BudgetExceeded(f"executor ceiling {b['executor_ceiling']} would be exceeded")
            return
        ceiling = self.cfg.ceiling()
        if s["total"] + est > ceiling:
            raise BudgetExceeded(f"run ceiling ${ceiling} would be exceeded "
                                 f"(spent ${s['total']:.2f}, next call est ${est:.4f})")
        mode = (s.get("mode") or "")
        if mode == "pilot" and s["by_kind"].get("experiment", 0.0) + est > float(b["pilot_ceiling"]):
            raise BudgetExceeded(f"pilot ceiling ${b['pilot_ceiling']} would be exceeded")
        prov = self.cfg.model(model_id)["provider"]
        pp = (b.get("per_provider") or {}).get(prov)
        if pp is not None and s["by_provider"].get(prov, 0.0) + est > float(pp):
            raise BudgetExceeded(f"provider {prov} ceiling ${pp} would be exceeded")
        pm = (b.get("per_model") or {}).get(model_id)
        if pm is not None and s["by_model"].get(model_id, 0.0) + est > float(pm):
            raise BudgetExceeded(f"model {model_id} ceiling ${pm} would be exceeded")

    # ---- writes
    def set_mode(self, mode: str) -> None:
        with file_lock(LOCK):
            s = self.summary()
            s["mode"] = mode
            write_json(SPEND, s)

    def record(self, model_id: str, usage: dict, *, batch: bool = False, kind: str = "experiment",
               stage: str = "", cell_id: str = "", cost: float | None = None, provider: str | None = None) -> float:
        if cost is None:
            cost = cost_of(self.prices[model_id], usage, batch)
        prov = provider or (self.cfg.model(model_id)["provider"] if kind != "executor" else "executor")
        with file_lock(LOCK):
            s = self.summary()
            s["total"] = round(s["total"] + cost, 8)
            s["by_provider"][prov] = round(s["by_provider"].get(prov, 0.0) + cost, 8)
            s["by_model"][model_id] = round(s["by_model"].get(model_id, 0.0) + cost, 8)
            s["by_kind"][kind] = round(s["by_kind"].get(kind, 0.0) + cost, 8)
            s["calls"] = s.get("calls", 0) + 1
            s["updated"] = now_iso()
            mode = s.get("mode")
            write_json(SPEND, s)
        append_jsonl(LEDGER, {"ts": now_iso(), "mode": mode, "model": model_id, "provider": prov, "kind": kind,
                              "stage": stage, "cell_id": cell_id, "batch": batch, "usage": usage, "cost": cost})
        return cost
