"""Stage gates: counts, coverage of every (arm, model) group, parse rate, vendor-error rate,
and the paper's own check. A failed gate stops the ladder; it is never a warning."""
from __future__ import annotations

from .exitcodes import GateFailed
from .util import RUNS, append_jsonl, journal, now_iso, read_jsonl


def evaluate(ctx, stage, metrics: dict) -> dict:
    g = stage.gate or {}
    fails: list[str] = []
    if stage.kind == "llm":
        ok_groups = set()
        for r in read_jsonl(ctx.out(stage.name)):
            if r["status"] == "ok":
                ok_groups.add(f"{r.get('arm') or '-'}|{r['model']}")
        missing = [grp for grp in metrics["groups"] if grp not in ok_groups]
        if missing:
            fails.append(f"no successful row for groups {missing}")
        attempted = metrics["ok"] + metrics["parse_fail"]
        if metrics["cells"] == 0:
            fails.append("stage built zero cells")
        if attempted:
            pr = metrics["ok"] / attempted
            floor = float(g.get("min_parse_rate", ctx.cfg.gates.get("parse_floor", 0.85)))
            if pr < floor:
                fails.append(f"parse rate {pr:.3f} < {floor}")
        ver = metrics["vendor_error"] / max(metrics["cells"], 1)
        max_ver = float(g.get("max_vendor_error_rate", ctx.cfg.gates.get("max_vendor_error_rate", 0.01)))
        if ver > max_ver:
            fails.append(f"vendor-error rate {ver:.3f} > {max_ver} (resume retries these cells)")
        if ctx.mode == "full" and g.get("min_rows") is not None and metrics["ok"] < int(g["min_rows"]):
            fails.append(f"ok rows {metrics['ok']} < min_rows {g['min_rows']}")
    check = g.get("check")
    if check:
        try:
            extra = check(ctx, metrics) or []
        except Exception as e:  # noqa: BLE001
            extra = [f"gate check raised {type(e).__name__}: {e}"]
        fails.extend(extra)
    rec = {"ts": now_iso(), "mode": ctx.mode, "stage": stage.name, "metrics": metrics, "fails": fails,
           "pass": not fails}
    append_jsonl(RUNS / "gates.jsonl", rec)
    journal("GATE", f"{ctx.mode}/{stage.name}: {'PASS' if not fails else 'FAIL ' + '; '.join(fails)}")
    if fails:
        raise GateFailed(f"{ctx.mode}/{stage.name}: " + "; ".join(fails))
    return rec
