"""Execute an LLM stage: dedupe cells, resume from the output file, run sync calls under per-model
concurrency or vendor batches (re-polled, never re-submitted, on resume), enforce the spend ledger
and the rolling parse floor, and keep the heartbeat moving."""
from __future__ import annotations

import collections
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import vendors
from .exitcodes import GateFailed, Killed, ParseFloor
from .util import RUNS, append_jsonl, journal, now_iso, read_json, read_jsonl, write_json

BATCHES = RUNS / "batches.json"
KILL_FLAG = RUNS / "KILL"
SMALL_FOR_SYNC = 10          # fewer pending cells than this never go to a batch


def _killed() -> bool:
    return KILL_FLAG.exists()


class ParseMonitor:
    def __init__(self, floor: float, window: int = 50, min_n: int = 20):
        self.floor, self.window, self.min_n = floor, window, min_n
        self.q: dict[str, collections.deque] = collections.defaultdict(lambda: collections.deque(maxlen=window))
        self.lock = threading.Lock()

    def add(self, model: str, ok: bool) -> None:
        with self.lock:
            self.q[model].append(1 if ok else 0)
            q = self.q[model]
            if len(q) >= self.min_n and sum(q) / len(q) < self.floor:
                raise ParseFloor(f"{model}: rolling parse rate {sum(q) / len(q):.2f} < floor {self.floor}")

    def rates(self) -> dict:
        with self.lock:
            return {m: round(sum(q) / len(q), 3) for m, q in self.q.items() if q}


def _row(cell, status, parsed=None, text=None, usage=None, cost=0.0, err=None, batch=False, truncated=False):
    return {"cell_id": cell["cell_id"], "model": cell["model"], "arm": cell.get("arm"), "status": status,
            "parsed": parsed, "text": text, "usage": usage, "cost": cost, "error": err, "batch": batch,
            "truncated": truncated, "meta": cell.get("meta"), "ts": now_iso()}


def run_llm_stage(ctx, stage) -> dict:
    cells_all = stage.build(ctx)
    # ---- dedupe (a twice-ingested dataset must be visible, not silently doubled)
    seen, cells, dups = set(), [], 0
    for c in cells_all:
        if c["cell_id"] in seen:
            dups += 1
            continue
        seen.add(c["cell_id"])
        cells.append(c)
    if dups:
        journal("DEDUPE", f"{stage.name}: {dups} duplicate cell_ids dropped ({len(cells)} unique)")
    max_dup = int(stage.gate.get("max_duplicate_cells", 0))
    if dups > max_dup:
        raise GateFailed(f"{stage.name}: {dups} duplicate cell_ids > allowed {max_dup}")
    unknown = {c["model"] for c in cells} - {m["id"] for m in ctx.cfg.models}
    if unknown:
        raise GateFailed(f"{stage.name}: cells reference models not in models.yaml: {sorted(unknown)}")
    cells = ctx.select(cells)
    out = ctx.out(stage.name)
    done = {}
    for r in read_jsonl(out):
        if r["status"] in ("ok", "parse_fail"):
            done[r["cell_id"]] = r
    pending = [c for c in cells if c["cell_id"] not in done]
    journal("STAGE", f"{ctx.mode}/{stage.name}: {len(cells)} cells, {len(done)} done, {len(pending)} pending")
    ctx.hb.set(stage=stage.name, total=len(cells), done=len(done), lanes={})

    floor = float(stage.gate.get("min_parse_rate", ctx.cfg.gates.get("parse_floor", 0.85)))
    pm = ParseMonitor(floor)
    by_model: dict[str, list] = collections.defaultdict(list)
    for c in pending:
        by_model[c["model"]].append(c)

    # ---- vendors run in parallel:
    #   1) every open batch of this stage is resumed (re-polled, never re-submitted) and every new batch chunk
    #      of every batch-capable model is submitted (recorded in batches.json before any polling);
    #   2) all sync lanes start at once in the background (per-model pools, token buckets);
    #   3) one loop polls all open batches round-robin and ingests each as it ends;
    #   4) the stage returns when every batch is ingested and every sync job is done.
    cellmap = {c["cell_id"]: c for c in cells}
    batches = read_json(BATCHES, default={}) or {}
    in_batch = {cid for b in batches.values() if b["stage"] == stage.name and b["mode"] == ctx.mode
                and not b.get("ingested") for cid in b["cells"].values()}
    open_bids = [bid for bid, b in batches.items()
                 if b["stage"] == stage.name and b["mode"] == ctx.mode and not b.get("ingested")]
    use_batch = ctx.mode in ("canary", "full")
    sync_jobs = []
    for mid, lst in by_model.items():
        model = ctx.cfg.model(mid)
        lst = [c for c in lst if c["cell_id"] not in in_batch]
        if use_batch and model.get("batch") and len(lst) >= SMALL_FOR_SYNC:
            size = int(model.get("batch_size", 1000))
            for i in range(0, len(lst), size):
                open_bids.append(_submit_batch(ctx, stage, model, lst[i:i + size], batches))
        else:
            sync_jobs.extend(lst)

    stop = threading.Event()
    sync_err: list[BaseException] = []

    def sync_lane():
        try:
            _run_sync(ctx, stage, sync_jobs, out, pm, stop)
        except BaseException as e:  # noqa: BLE001 — handed to the main thread
            sync_err.append(e)
            stop.set()

    lane = threading.Thread(target=sync_lane, name="sync-lanes", daemon=True)
    if sync_jobs:
        lane.start()
    try:
        _poll_batches(ctx, stage, open_bids, batches, out, pm, cellmap, stop, sync_err)
    except BaseException:
        stop.set()
        if lane.is_alive():
            lane.join()
        raise
    if lane.is_alive():
        lane.join()
    if sync_err:
        raise sync_err[0]

    rows = {r["cell_id"]: r for r in read_jsonl(out)}
    ok = sum(1 for r in rows.values() if r["status"] == "ok")
    pf = sum(1 for r in rows.values() if r["status"] == "parse_fail")
    ve = sum(1 for r in rows.values() if r["status"] == "vendor_error")   # last row per cell wins
    metrics = {"stage": stage.name, "mode": ctx.mode, "cells": len(cells), "ok": ok, "parse_fail": pf,
               "vendor_error": ve, "duplicates_dropped": dups, "parse_rates": pm.rates(),
               "groups": sorted({f"{c.get('arm', '-')}|{c['model']}" for c in cells})}
    return metrics


def _run_sync(ctx, stage, jobs, out, pm, stop=None):
    pools = {}
    futures = []
    # cache warm-up: the first call of every (model, prefix) runs alone and completes before the rest of that
    # model's calls go out. Vendors fill the prefix cache only when a request finishes, so a cold burst gets no
    # cache reads (Day-0: six concurrent Groq pilot calls all missed the cache).
    warm, rest, seen = [], [], set()
    for c in jobs:
        k = (c["model"], c["prefix"])
        (rest if k in seen else warm).append(c)
        seen.add(k)
    for c in warm + rest:
        mid = c["model"]
        if mid not in pools:
            pools[mid] = ThreadPoolExecutor(max_workers=int(ctx.cfg.model(mid)["concurrency"]),
                                            thread_name_prefix=f"lane-{mid}")
    try:
        first = [pools[c["model"]].submit(_one, ctx, stage, c, out, pm, stop) for c in warm]
        futures.extend(first)
        for f in as_completed(first):
            f.result()
        futures.extend(pools[c["model"]].submit(_one, ctx, stage, c, out, pm, stop) for c in rest)
        for f in as_completed(futures):
            f.result()          # re-raises BudgetExceeded / ParseFloor / Killed
    except BaseException:
        for f in futures:
            f.cancel()
        raise
    finally:
        for p in pools.values():
            p.shutdown(wait=True, cancel_futures=True)


_TEST_COUNT = {"n": 0}
_TEST_LOCK = threading.Lock()


def _test_hooks() -> None:
    """Fault injection used only by the resume/kill tests (env vars never set in real runs)."""
    import os
    crash = int(os.environ.get("RH_TEST_CRASH_AFTER", "0") or 0)
    kill = int(os.environ.get("RH_TEST_KILL_AFTER", "0") or 0)
    if not (crash or kill):
        return
    with _TEST_LOCK:
        _TEST_COUNT["n"] += 1
        n = _TEST_COUNT["n"]
    if crash and n > crash:
        raise RuntimeError(f"injected crash after {crash} calls")
    if kill and n > kill:
        KILL_FLAG.write_text("injected kill\n")


def _one(ctx, stage, c, out, pm, stop=None):
    if stop is not None and stop.is_set():
        return                     # another lane or the batch poller already stopped the stage
    if _killed():
        raise Killed("kill flag present")
    if ctx.mode == "full":
        _test_hooks()
    model = ctx.cfg.model(c["model"])
    mt = int(c.get("max_tokens") or model["max_output_tokens"])
    est = ctx.ledger.estimate(model["id"], (len(c["prefix"]) + len(c["item"])) // 3, mt)
    ctx.ledger.check(model["id"], est)
    cost = 0.0
    for attempt in range(1 + EMPTY_RETRIES):
        if attempt:
            ctx.ledger.check(model["id"], est)
        try:
            r = vendors.call(model, c["prefix"], c["item"], mt, log_stage=stage.name)
        except vendors.VendorError as e:
            append_jsonl(out, _row(c, "vendor_error", err=str(e)[:500]))
            journal("VENDOR", f"{stage.name} {c['cell_id']} {model['id']}: {str(e)[:200]}")
            ctx.hb.touch()
            return
        cost += ctx.ledger.record(model["id"], r.usage, stage=stage.name, cell_id=c["cell_id"])
        if r.text.strip() or r.truncated or attempt == EMPTY_RETRIES:
            break
        # a reply with no content that was not cut off (seen with open-weights reasoning models: the tokens went
        # to reasoning only) is a vendor anomaly, not an answer: ask again, every attempt is paid and recorded
        journal("VENDOR", f"{stage.name} {c['cell_id']} {model['id']}: empty reply "
                          f"({r.usage.get('output', 0)} output tokens), retry {attempt + 1}/{EMPTY_RETRIES}")
    _ingest(ctx, stage, c, r, out, pm, cost, batch=False)


EMPTY_RETRIES = 2


def _ingest(ctx, stage, c, r, out, pm, cost, batch):
    try:
        if r.truncated:
            raise ValueError("output truncated at max_tokens")
        parsed = stage.schema(r.text)
        append_jsonl(out, _row(c, "ok", parsed, r.text, r.usage, cost, batch=batch))
        okflag = True
    except Exception as e:  # noqa: BLE001  — any schema error is a parse failure
        append_jsonl(out, _row(c, "parse_fail", None, r.text, r.usage, cost, f"{type(e).__name__}: {e}"[:300],
                               batch=batch, truncated=r.truncated))
        okflag = False
    ctx.hb.progress(lane=c["model"], parse_rates=pm.rates(), spend=round(ctx.ledger.total(), 4))
    pm.add(c["model"], okflag)


def _submit_batch(ctx, stage, model, chunk, batches) -> str:
    mt_of = {c["cell_id"]: int(c.get("max_tokens") or model["max_output_tokens"]) for c in chunk}
    est = sum(ctx.ledger.estimate(model["id"], (len(c["prefix"]) + len(c["item"])) // 3, mt_of[c["cell_id"]],
                                  batch=True) for c in chunk)
    ctx.ledger.check(model["id"], est)
    if _killed():
        raise Killed("kill flag present")
    b = vendors.Batch(model)
    bid = b.submit([(c["cell_id"], c["prefix"], c["item"], mt_of[c["cell_id"]]) for c in chunk])
    rec = {"model": model["id"], "stage": stage.name, "mode": ctx.mode, "submitted": now_iso(),
           "cells": {vendors.custom_id(c["cell_id"]): c["cell_id"] for c in chunk}, "ingested": False}
    batches[bid] = rec
    write_json(BATCHES, batches)          # recorded before polling: kill.sh and resume both read this
    journal("BATCH", f"{stage.name} {model['id']}: submitted {bid} with {len(chunk)} requests")
    return bid


def _poll_batches(ctx, stage, open_bids, batches, out, pm, cellmap, stop, sync_err):
    """Round-robin over every open batch of the stage; ingest each as it ends. Also the place where the
    main thread notices a sync-lane failure or the kill flag while batches are still running."""
    poll = float(ctx.cfg.plan.get("batch_poll_seconds", 60))
    open_bids = list(dict.fromkeys(open_bids))
    last = {}
    while open_bids:
        if sync_err:
            raise sync_err[0]
        if _killed():
            raise Killed("kill flag present")
        for bid in list(open_bids):
            rec = batches[bid]
            st = vendors.Batch(ctx.cfg.model(rec["model"])).status(bid)
            if st["done"] != last.get(bid):
                last[bid] = st["done"]
            ctx.hb.touch(batch={"open": len(open_bids), "id": bid, "done": st["done"], "total": st["total"]},
                         batch_poll=now_iso())
            if st["state"] == "ended":
                redo = _ingest_batch(ctx, stage, bid, rec, batches, out, pm, cellmap)
                open_bids.remove(bid)
                if redo and not _killed():
                    # requests the vendor never processed (batch cancelled by kill.sh, or expired): submit them
                    # again as a new batch; nothing was paid for them, so no call is duplicated
                    model = ctx.cfg.model(rec["model"])
                    nb = _submit_batch(ctx, stage, model, redo, batches)
                    batches[nb]["attempt"] = int(rec.get("attempt", 1)) + 1
                    batches[nb]["requeued_from"] = bid
                    write_json(BATCHES, batches)
                    open_bids.append(nb)
        if open_bids:
            time.sleep(poll)


def _ingest_batch(ctx, stage, bid, rec, batches, out, pm, cellmap):
    model = ctx.cfg.model(rec["model"])
    b = vendors.Batch(model)
    already = {r["cell_id"] for r in read_jsonl(out) if r["status"] in ("ok", "parse_fail")}
    n_ok = 0
    requeue_ok = int(rec.get("attempt", 1)) < MAX_BATCH_ATTEMPTS
    redo, seen = [], set()
    for cid, resp, err in b.results(bid):
        cell_id = rec["cells"].get(cid)
        if cell_id is None:
            continue
        seen.add(cid)
        if cell_id in already:
            continue
        c = cellmap.get(cell_id) or {"cell_id": cell_id, "model": model["id"]}
        if resp is None:
            if requeue_ok and _unprocessed(err) and cell_id in cellmap:
                redo.append(c)
            else:
                append_jsonl(out, _row(c, "vendor_error", err=err, batch=True))
            continue
        cost = ctx.ledger.record(model["id"], resp.usage, batch=True, stage=stage.name, cell_id=cell_id)
        _ingest(ctx, stage, c, resp, out, pm, cost, batch=True)
        n_ok += 1
    # OpenAI leaves unprocessed requests of a cancelled batch out of the output files altogether
    for cid, cell_id in rec["cells"].items():
        if cid not in seen and cell_id not in already and cell_id in cellmap:
            if requeue_ok:
                redo.append(cellmap[cell_id])
            else:
                append_jsonl(out, _row(cellmap[cell_id], "vendor_error", err="no result returned", batch=True))
    rec["ingested"] = True
    rec["ended"] = now_iso()
    rec["requeued"] = len(redo)
    batches[bid] = rec
    write_json(BATCHES, batches)
    journal("BATCH", f"{stage.name} {model['id']}: {bid} ended, {n_ok} results ingested"
                     + (f", {len(redo)} unprocessed (cancelled/expired) to resubmit" if redo else ""))
    return redo


MAX_BATCH_ATTEMPTS = 3


def _unprocessed(err) -> bool:
    """True when the vendor never ran the request (cancelled or expired), so resubmitting costs nothing extra."""
    try:
        d = json.loads(err) if isinstance(err, str) else (err or {})
    except ValueError:
        return False
    return str(d.get("type", "")).lower() in ("canceled", "cancelled", "expired")
