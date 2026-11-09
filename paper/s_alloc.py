"""S4 alloc: bytes allocated per operation (JMH -prof gc, gc.alloc.rate.norm) for every generation cell whose
patch compiles and passes the targeted tests, and once per method for the baseline overlay, using the
benchmark run (and parameters) in which the method was hottest. One lane per library, serial inside a lane
(benchmarks are not run concurrently within a library); lanes run in parallel."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rh.util import append_jsonl, read_jsonl

from . import fakes
from .env import bind, fake, note, tools, work_dir
from .javatools import jmh_gc
from .s_score import latest_rows
from .subjects import baseline, build_info, file_key, methods_by_id, short

MIN_MEASURED = 0.50                 # sanity floor (allocation is a secondary measure); the share is reported


def _jobs(ctx) -> list[dict]:
    rows = latest_rows(ctx.out("score"))
    by = methods_by_id(ctx.mode)
    jobs, base_done = [], set()
    for r in sorted(rows.values(), key=lambda r: r["cell_id"]):
        if r["kind"] != "gen" or r.get("status") != "scored":
            continue
        if not (r.get("classification") or {}).get("tests_pass") or not r.get("overlay"):
            continue
        m = by.get(r["method"])
        if not m or not m.get("bench"):
            continue
        if m["id"] not in base_done:
            b = baseline(ctx.mode, file_key(m)) or {}
            jobs.append({"cell_id": f"alloc|base|{m['id']}", "kind": "base", "method": m["id"], "lib": m["lib"],
                         "overlay": b.get("overlay_base"), "bench": m["bench"], "params": m.get("bench_params")})
            base_done.add(m["id"])
        jobs.append({"cell_id": "alloc|" + r["cell_id"], "kind": "patch", "score_cell": r["cell_id"],
                     "method": m["id"], "lib": m["lib"], "overlay": r["overlay"], "bench": m["bench"],
                     "params": m.get("bench_params"), "model": r["model"], "rung": r["rung"], "sample": r["sample"]})
    return jobs


def _ensure_overlay(ctx, t, j: dict) -> bool:
    """Overlays live in /opt/data; after a resume on a new instance a patched overlay is rebuilt from the reply
    by the same recipe (splice + javac) and the baseline overlay by the baseline stage that ran before."""
    if Path(j["overlay"]).exists():
        return True
    if j["kind"] != "patch" or fake():
        return False
    from .javatools import cp, javac
    from .pipeline import _write_src, splice
    m = methods_by_id(ctx.mode).get(j["method"])
    gen = {r["cell_id"]: r for r in ctx.stage_rows("generate") if r.get("status") == "ok"}.get(
        j["score_cell"][len("score|"):])
    base = baseline(ctx.mode, file_key(m)) if m else None
    b = build_info(ctx.mode, j["lib"]) or {}
    if not (m and gen and base and base.get("ok") and b.get("tree")):
        return False
    text = (Path(b["tree"]) / m["file"]).read_text(errors="ignore")
    work = work_dir(ctx.mode) / "rebuild" / short(j["cell_id"], 16)
    src = _write_src(work, m["file"], splice(text, m["start"], m["end"], gen["parsed"]["patched_method"]))
    ok, _ = javac(t, src, cp(base["test_cp"]), Path(j["overlay"]), base["release"])
    return ok


def _lane(ctx, t, lib: str, jobs: list[dict], out) -> None:
    jar = (build_info(ctx.mode, lib) or {}).get("jmh_jar")
    for j in jobs:
        row = {k: j.get(k) for k in ("cell_id", "kind", "score_cell", "method", "lib", "bench", "model", "rung",
                                     "sample")}
        if fake():
            row.update(fakes.alloc(j))
        elif not jar or not j.get("overlay") or not _ensure_overlay(ctx, t, j):
            row.update(status="unavailable", bytes_per_op=None)
        else:
            res = jmh_gc(t, Path(j["overlay"]), Path(jar), j["bench"], j.get("params"),
                         work_dir(ctx.mode) / "alloc" / f"{short(j['cell_id'], 16)}.json", hb=ctx.hb)
            row.update(status="measured" if res and res.get("bytes_per_op") is not None else "failed",
                       **(res or {"bytes_per_op": None}))
        append_jsonl(out, row)
        ctx.hb.progress(lane=f"alloc-{lib}")


def run_alloc(ctx) -> dict:
    bind(ctx)
    jobs = _jobs(ctx)
    out = ctx.out("alloc")
    # a failed measurement is retried on resume (the latest row per cell wins when the share is computed)
    done = {r["cell_id"] for r in read_jsonl(out) if r.get("status") != "failed"}
    pending = [j for j in jobs if j["cell_id"] not in done]
    lanes: dict[str, list] = {}
    for j in pending:
        lanes.setdefault(j["lib"], []).append(j)
    ctx.hb.set(stage="alloc", total=len(jobs), done=len(jobs) - len(pending), lanes={})
    note("ALLOC", f"{ctx.mode}: {len(jobs)} measurements, {len(pending)} pending in {len(lanes)} lanes")
    t = None if fake() else tools()
    if lanes:
        with ThreadPoolExecutor(max_workers=len(lanes), thread_name_prefix="alloc") as ex:
            for f in [ex.submit(_lane, ctx, t, lib, js, out) for lib, js in sorted(lanes.items())]:
                f.result()
    last = {}
    for r in read_jsonl(out):
        last[r["cell_id"]] = r
    patch = [r for r in last.values() if r["kind"] == "patch"]
    measured = [r for r in patch if r.get("status") == "measured"]
    return {"jobs": len(jobs), "patch_rows": len(patch), "measured": len(measured),
            "measured_share": round(len(measured) / len(patch), 4) if patch else None,
            "base_measured": sum(1 for r in last.values() if r["kind"] == "base" and r.get("status") == "measured")}


def check_alloc(ctx, m) -> list[str]:
    if m["patch_rows"] and m["measured_share"] < MIN_MEASURED:
        return [f"allocation measured for {m['measured_share']:.1%} of eligible cells < {MIN_MEASURED:.0%}"]
    return []


__all__ = ["run_alloc", "check_alloc", "methods_by_id"]
