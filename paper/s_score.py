"""S3 score: every parsed generation cell, plus two oracle controls per method (identity: the original method
returned unchanged, must score R0; throw: the body replaced by a throw, must be caught where tests or Randoop
reach the method), scored in a worker pool. Resumable by cell id; each row is appended as soon as it is done."""
from __future__ import annotations

import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from rh.util import append_jsonl, read_jsonl

from . import fakes
from .env import bind, WORK, fake, note, scope_dir, tools, work_dir, workers
from .pipeline import score_cell
from .subjects import baseline, build_info, file_key, methods_by_id, short

IDENTITY_MIN_R0 = 0.90
IDENTITY_MIN_COMPILE = 0.95
THROW_MIN_CAUGHT = 0.50            # private helpers may not be reached by the suites that ran; reported in full


def throw_variant(m: dict) -> str:
    """The method with its body replaced by a throw (signature text kept byte for byte)."""
    src = m["src"]
    j = re.search(r"\b" + re.escape(m["name"]) + r"\s*\(", src)
    i = j.end() - 1 if j else src.find("(")
    depth = 0
    while i < len(src):
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    b = src.find("{", i)
    return src[:b] + "{\n    throw new UnsupportedOperationException(\"oopt-control\");\n}"


def _cells(ctx) -> list[dict]:
    gen = [r for r in ctx.stage_rows("generate") if r["status"] == "ok"]
    last = {}
    for r in gen:
        last[r["cell_id"]] = r
    cells = [{"cell_id": "score|" + cid, "kind": "gen", "gen_cell": cid, "method": r["meta"]["method"],
              "model": r["model"], "rung": r["meta"]["rung"], "sample": r["meta"]["sample"],
              "patched": r["parsed"]["patched_method"], "claimed_change": r["parsed"]["claimed_change"],
              "confidence": r["parsed"]["confidence"]} for cid, r in sorted(last.items())]
    mids = sorted({c["method"] for c in cells})
    by = methods_by_id(ctx.mode)
    for mid in mids:
        m = by.get(mid)
        if not m:
            continue
        cells.append({"cell_id": f"score|ctl|identity|{mid}", "kind": "ctl_identity", "method": mid, "model": "-",
                      "rung": "-", "sample": 0, "patched": m["src"]})
        cells.append({"cell_id": f"score|ctl|throw|{mid}", "kind": "ctl_throw", "method": mid, "model": "-",
                      "rung": "-", "sample": 0, "patched": throw_variant(m)})
    return cells


def _score_one(ctx, t, c: dict, by: dict) -> dict:
    m = by[c["method"]]
    base = baseline(ctx.mode, file_key(m))
    row = {k: c[k] for k in ("cell_id", "kind", "method", "model", "rung", "sample")}
    row.update(lib=m["lib"], claimed_change=c.get("claimed_change"), confidence=c.get("confidence"),
               gen_cell=c.get("gen_cell"))
    if not base or not base.get("ok"):
        row.update(status="no_baseline")
        return row
    if fake():
        row.update(fakes.score(c, m, base))
        return row
    b = build_info(ctx.mode, m["lib"])
    work = work_dir(ctx.mode) / "cells" / short(c["cell_id"], 16)
    if work.exists():
        shutil.rmtree(work)
    try:
        res = score_cell(t, method=m, base=base, file_abs=Path(b["tree"]) / m["file"], patched=c["patched"],
                         work=work, hb=ctx.hb)
    except Exception as e:  # noqa: BLE001 — a scorer error is recorded and gated, never silent
        res = {"status": "error", "error": f"{type(e).__name__}: {e}"[:600]}
    # keep the overlay (small) for the allocation stage; drop the rest of the work dir
    ov = res.get("overlay")
    if ov:
        keep = work_dir(ctx.mode) / "overlays" / short(c["cell_id"], 16)
        if keep.exists():
            shutil.rmtree(keep)
        shutil.copytree(ov, keep)
        res["overlay"] = str(keep)
    shutil.rmtree(work, ignore_errors=True)
    row.update(res)
    return row


def run_score(ctx) -> dict:
    bind(ctx)
    cells = _cells(ctx)
    out = ctx.out("score")
    done = {r["cell_id"] for r in read_jsonl(out) if r.get("status") != "error"}
    pending = [c for c in cells if c["cell_id"] not in done]
    by = methods_by_id(ctx.mode)
    ctx.hb.set(stage="score", total=len(cells), done=len(cells) - len(pending), lanes={})
    note("SCORE", f"{ctx.mode}: {len(cells)} cells, {len(cells) - len(pending)} done, {len(pending)} pending")
    t = None if fake() else tools()
    n = 1 if fake() else workers(28 if ctx.mode != "pilot" else 4)
    with ThreadPoolExecutor(max_workers=n, thread_name_prefix="score") as ex:
        futs = [ex.submit(_score_one, ctx, t, c, by) for c in pending]
        for f in as_completed(futs):
            row = f.result()
            append_jsonl(out, row)
            ctx.hb.progress(lane="score")
    return summarize(ctx, cells)


def latest_rows(path) -> dict:
    last = {}
    for r in read_jsonl(path):
        last[r["cell_id"]] = r
    return last


def summarize(ctx, cells=None) -> dict:
    rows = latest_rows(ctx.out("score"))
    gen = [r for r in rows.values() if r["kind"] == "gen"]
    ident = [r for r in rows.values() if r["kind"] == "ctl_identity" and r.get("status") == "scored"]
    thr = [r for r in rows.values() if r["kind"] == "ctl_throw" and r.get("status") == "scored"]
    scored = [r for r in gen if r.get("status") in ("scored", "wrong_method")]
    by_model = {}
    for r in gen:
        d = by_model.setdefault(r["model"], {"n": 0, "compile": 0, "tests_pass": 0, "silent": 0})
        if r.get("status") != "scored":
            continue
        cl = r.get("classification") or {}
        d["n"] += 1
        d["compile"] += int(bool(r.get("compile_ok")))
        d["tests_pass"] += int(bool(cl.get("tests_pass")))
        d["silent"] += int(bool(cl.get("silent_break")))
    id_compile = sum(1 for r in ident if r.get("compile_ok")) / len(ident) if ident else 0.0
    id_r0 = sum(1 for r in ident if (r.get("classification") or {}).get("rows", {}).get("R0_NONE")) / len(ident) \
        if ident else 0.0
    reach = [r for r in thr if ((r.get("signals") or {}).get("tests") or {}).get("ran") or
             ((r.get("signals") or {}).get("randoop") or {}).get("ran")]
    caught = [r for r in reach if any((r.get("classification") or {}).get("rows", {}).get(k) for k in
                                      ("R2_TESTS_FAIL", "R3a_VALUE", "R3b_EXCEPTION", "R3c_ORDER"))]
    return {"cells": len(cells) if cells is not None else len(rows), "gen_rows": len(gen),
            "gen_scored_share": round(len(scored) / len(gen), 4) if gen else 0.0,
            "errors": sum(1 for r in rows.values() if r.get("status") == "error"),
            "wrong_method": sum(1 for r in gen if r.get("status") == "wrong_method"), "by_model": by_model,
            "identity_n": len(ident), "identity_compile": round(id_compile, 4), "identity_r0": round(id_r0, 4),
            "throw_reached": len(reach), "throw_caught": len(caught)}


def check_score(ctx, m) -> list[str]:
    errs = []
    if m["gen_rows"] == 0:
        errs.append("no generation rows to score")
    elif m["gen_scored_share"] < 0.95:
        errs.append(f"only {m['gen_scored_share']:.1%} of parsed cells scored (< 95 %); {m['errors']} scorer errors")
    if m["identity_n"] == 0:
        errs.append("no identity control scored")
    else:
        if m["identity_compile"] < IDENTITY_MIN_COMPILE:
            errs.append(f"identity controls compile {m['identity_compile']:.1%} < {IDENTITY_MIN_COMPILE:.0%} "
                        "(the scoring recipe, not the models, is broken)")
        if m["identity_r0"] < IDENTITY_MIN_R0:
            errs.append(f"identity controls clean (R0) {m['identity_r0']:.1%} < {IDENTITY_MIN_R0:.0%} "
                        "(an oracle fires on unchanged code)")
    if m["throw_reached"] and m["throw_caught"] / m["throw_reached"] < THROW_MIN_CAUGHT:
        errs.append(f"throw controls caught {m['throw_caught']}/{m['throw_reached']} where tests or Randoop ran "
                    f"(< {THROW_MIN_CAUGHT:.0%}: the suites are probably not running against the overlay)")
    return errs


__all__ = ["run_score", "check_score", "summarize", "latest_rows", "throw_variant", "WORK", "re"]
