"""S2 generate (LLM stage) and the sizing rule.
Cells: method x rung (R1, R2, R3) x model x sample (k = 2). The arm of a cell is its rung, so the template's
pilot/canary subsampling and gates work per (rung, model).
Sizing (once, at the first canary build): token use per (rung, model) measured in the pilot, the actual
prompts of every eligible method rendered, and N = the largest number of methods (round-robin over libraries,
<= 50 per library, <= 250) whose full cost fits the ceiling after compute, spend so far, the canary, the
executor guard and a 15 % margin. N below the floor of 150 stops the run before any full-run spend."""
from __future__ import annotations

from rh import compute
from rh.exitcodes import GateFailed
from rh.ledger import cost_of
from rh.util import RUNS, read_jsonl

from .env import fake, note, read_json, scope_dir, write_json
from .prompts import PREFIX, parse_generate, render_item
from .subjects import CAP_TOTAL, FLOOR_TOTAL, RUNGS, SAMPLES, USE_PER_LIB, eligible, interleave

MARGIN = 0.15
OUT_SAFETY = 1.25
DEFAULT_CHARS_PER_TOKEN = 3.5
DEFAULT_OUT_TOKENS = 3000
FAKE_CAP, FAKE_FLOOR = 30, 15          # dry runs: same rule, smaller study


def calibration(cfg) -> dict:
    """Per model: tokens per prompt char; per (rung, model): mean output tokens. From the pilot's real calls."""
    rows = [r for r in read_jsonl(RUNS / "pilot" / "generate.jsonl") if r.get("usage")]
    cal = {"chars_per_token": {}, "out_tokens": {}, "pilot_rows": len(rows)}
    for m in cfg.models:
        mine = [r for r in rows if r["model"] == m["id"]]
        toks = sum(sum(r["usage"].get(k, 0) for k in ("input", "cache_read", "cache_write")) for r in mine)
        chars = sum(r["meta"].get("prompt_chars", 0) for r in mine)
        cal["chars_per_token"][m["id"]] = round(chars / toks, 4) if toks and chars else DEFAULT_CHARS_PER_TOKEN
        for rung in RUNGS:
            o = [r["usage"].get("output", 0) for r in mine if r.get("arm") == rung]
            cal["out_tokens"][f"{rung}|{m['id']}"] = round(sum(o) / len(o), 1) if o else DEFAULT_OUT_TOKENS
    return cal


def method_cost(cfg, cal: dict, m: dict) -> float:
    total = 0.0
    for model in cfg.models:
        price = cfg.prices["models"][model["id"]]
        cpt = cal["chars_per_token"][model["id"]] or DEFAULT_CHARS_PER_TOKEN
        for rung in RUNGS:
            item_tok = len(render_item(m, rung)) / cpt
            use = {"input": item_tok, "cache_read": len(PREFIX) / cpt,
                   "output": min(model["max_output_tokens"], cal["out_tokens"][f"{rung}|{model['id']}"] * OUT_SAFETY)}
            total += len(SAMPLES) * cost_of(price, use, batch=bool(model.get("batch")))
    return total


def sizing(ctx) -> dict:
    path = scope_dir("full") / "sizing.json"
    prev = read_json(path)
    if prev:
        return prev
    cfg = ctx.cfg
    cap, floor = (FAKE_CAP, FAKE_FLOOR) if fake() else (CAP_TOTAL, FLOOR_TOTAL)
    pool = interleave(eligible("full"), USE_PER_LIB)
    cal = calibration(cfg)
    costs = {m["id"]: method_cost(cfg, cal, m) for m in pool}
    avg = sum(costs.values()) / max(len(costs), 1)
    groups = len(RUNGS) * len(cfg.models)
    canary_est = int(cfg.plan["canary_per_cell"]) * groups * (avg / (len(RUNGS) * len(SAMPLES) * len(cfg.models)))
    spent = ctx.ledger.total()
    comp = compute.projected(cfg)
    executor = float(cfg.budget.get("executor_ceiling", 0))
    available = (cfg.ceiling() - spent - comp - executor) * (1 - MARGIN) - canary_est
    chosen, acc = [], 0.0
    for m in pool:
        if len(chosen) >= cap or acc + costs[m["id"]] > available:
            break
        chosen.append(m["id"])
        acc += costs[m["id"]]
    rec = {"n": len(chosen), "cap": cap, "floor": floor, "pool": len(pool), "methods": chosen,
           "projected_full_api": round(acc, 2), "available_api": round(available, 2), "spent_before": round(spent, 4),
           "compute_projected": round(comp, 2), "executor_guard": executor, "canary_est": round(canary_est, 2),
           "margin": MARGIN, "out_safety": OUT_SAFETY, "calibration": cal, "ceiling": cfg.ceiling(),
           "per_library": {lib: sum(1 for mid in chosen if mid.startswith(lib + ":")) for lib in
                           sorted({mid.split(":")[0] for mid in chosen})}}
    note("SIZING", f"N={rec['n']} of pool {len(pool)} (cap {cap}, floor {floor}); full API ${acc:.2f} of available "
                   f"${available:.2f}; compute ${comp:.2f}; spent ${spent:.2f}")
    if len(chosen) < floor:
        write_json(scope_dir("full") / "sizing_failed.json", rec)
        raise GateFailed(f"sizing: only {len(chosen)} methods fit the ceiling (floor {floor}); "
                         f"pool {len(pool)}, available ${available:.2f}")
    write_json(path, rec)
    return rec


def study_methods(ctx) -> list[dict]:
    if ctx.mode == "pilot":
        return eligible("pilot")
    ids = sizing(ctx)["methods"]
    by = {m["id"]: m for m in eligible("full")}
    return [by[i] for i in ids if i in by]


def build_generate(ctx) -> list[dict]:
    cells = []
    for m in study_methods(ctx):
        for rung in RUNGS:
            item = render_item(m, rung)
            for model in ctx.cfg.models:
                for k in SAMPLES:
                    cells.append({"cell_id": f"gen|{rung}|{model['id']}|{m['id']}|s{k}", "model": model["id"],
                                  "arm": rung, "prefix": PREFIX, "item": item,
                                  "meta": {"method": m["id"], "lib": m["lib"], "rung": rung, "sample": k,
                                           "prompt_chars": len(PREFIX) + len(item)}})
    return cells


def check_generate(ctx, metrics) -> list[str]:
    """Full run: at least half of the cells the floor implies came back parsed (min_rows by the sizing rule)."""
    if ctx.mode != "full":
        return []
    floor = FAKE_FLOOR if fake() else FLOOR_TOTAL
    need = floor * len(RUNGS) * len(SAMPLES) * len(ctx.cfg.models) // 2
    return [] if metrics.get("ok", 0) >= need else [f"full generate: {metrics.get('ok', 0)} ok rows < {need}"]


schema = parse_generate
