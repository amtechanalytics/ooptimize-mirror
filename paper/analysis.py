"""Tables T1-T9, the audit sheet and the brief, from the full run's rows only (results/*.csv).
Primary analysis uses sample 1 of every (method, rung, model); sample 2 is the replicate (T5 stability and a
replication column). The headline counts a patch as a silent break only when it compiles, passes the targeted
project tests, at least one of those tests actually ran, and a contract oracle fired (R3a/b/c, R4, R5, R6)."""
from __future__ import annotations

import csv
import difflib
import hashlib
from collections import defaultdict

from rh.util import RESULTS, RUNS, read_jsonl

from .env import read_json, scope_dir
from .oracles import HEADLINE_ROWS, ROWS
from .stats import bootstrap_mean, cochran_q, holm, mcnemar_exact, wilson
from .subjects import RUNGS, candidates, eligible

AUDIT_N = 40


def _w(name: str, header: list[str], rows: list[list]) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    with open(RESULTS / name, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _f(x, d=4):
    return "" if x is None or (isinstance(x, float) and x != x) else (f"{x:.{d}f}" if isinstance(x, float) else x)


def load(mode: str = "full") -> dict:
    last = {}
    for r in read_jsonl(RUNS / mode / "score.jsonl"):
        last[r["cell_id"]] = r
    alloc = {}
    for r in read_jsonl(RUNS / mode / "alloc.jsonl"):
        alloc[r["cell_id"]] = r
    gen = {}
    for r in read_jsonl(RUNS / mode / "generate.jsonl"):
        gen[r["cell_id"]] = r
    return {"score": last, "alloc": alloc, "gen": gen}


def _cls(r):
    return r.get("classification") or {}


def analyze(ctx) -> None:
    d = load("full")
    rows = [r for r in d["score"].values() if r["kind"] == "gen"]
    models = [m["id"] for m in ctx.cfg.models]
    scored = [r for r in rows if r.get("status") == "scored"]
    s1 = [r for r in scored if r.get("sample") == 1]

    # T1 subjects
    sizing = read_json(scope_dir("full") / "sizing.json") or {}
    used = set(sizing.get("methods") or [])
    cands = candidates("full")
    elig = {m["id"] for m in eligible("full")}
    t1 = []
    for lib in sorted({m["lib"] for m in cands}):
        ms = [m for m in cands if m["lib"] == lib]
        u = [m for m in ms if m["id"] in used]
        tags = defaultdict(int)
        for m in u:
            for k, v in m["tags"].items():
                tags[k] += int(bool(v))
        t1.append([lib, len(ms), sum(1 for m in ms if m["id"] in elig), len(u)] +
                  [tags[k] for k in ("T_EQUALS", "T_COMPARABLE", "T_SYNC", "T_COPY", "T_THROWS", "T_ORDER", "T_STATE",
                                     "T_COLLECTION_RETURN")])
    _w("t1_subjects.csv", ["library", "candidates", "baseline_ok", "used", "T_EQUALS", "T_COMPARABLE", "T_SYNC",
                           "T_COPY", "T_THROWS", "T_ORDER", "T_STATE", "T_COLLECTION_RETURN"], t1)

    # T2 headline per model x rung (sample 1) and pooled over rungs
    t2 = []
    for mid in models:
        for rung in list(RUNGS) + ["all"]:
            g = [r for r in s1 if r["model"] == mid and (rung == "all" or r["rung"] == rung)]
            comp = sum(1 for r in g if r.get("compile_ok"))
            tp = sum(1 for r in g if _cls(r).get("tests_pass"))
            elig_n = sum(1 for r in g if _cls(r).get("headline_eligible"))
            sb = sum(1 for r in g if _cls(r).get("silent_break"))
            p, lo, hi = wilson(sb, elig_n)
            g2 = [r for r in scored if r.get("sample") == 2 and r["model"] == mid and (rung == "all" or r["rung"] == rung)]
            e2 = sum(1 for r in g2 if _cls(r).get("headline_eligible"))
            b2 = sum(1 for r in g2 if _cls(r).get("silent_break"))
            t2.append([mid, rung, len(g), comp, tp, elig_n, sb, _f(p), _f(lo), _f(hi), e2, b2,
                       _f(b2 / e2 if e2 else None)])
    _w("t2_headline.csv", ["model", "rung", "patches_s1", "compile", "tests_pass", "headline_eligible",
                           "silent_break", "rate", "wilson_lo", "wilson_hi", "eligible_s2", "silent_break_s2",
                           "rate_s2"], t2)

    # T3 taxonomy among tests-pass patches (sample 1), per model
    t3 = []
    for mid in models:
        g = [r for r in s1 if r["model"] == mid and _cls(r).get("tests_pass")]
        for row in ROWS:
            k = sum(1 for r in g if _cls(r).get("rows", {}).get(row))
            p, lo, hi = wilson(k, len(g))
            t3.append([mid, row, len(g), k, _f(p), _f(lo), _f(hi), int(row in HEADLINE_ROWS)])
    _w("t3_taxonomy.csv", ["model", "row", "tests_pass_n", "fired", "share", "wilson_lo", "wilson_hi",
                           "in_headline"], t3)

    # T4 context ladder: paired per method (sample 1); outcome = silent break (0/1) for every scored patch
    t4, pv = [], {}
    for mid in models:
        per = defaultdict(dict)
        for r in s1:
            if r["model"] == mid:
                per[r["method"]][r["rung"]] = int(bool(_cls(r).get("silent_break")))
        full = [[v[x] for x in RUNGS] for v in per.values() if all(x in v for x in RUNGS)]
        q, pq, df = cochran_q(full)
        rates = [sum(r[i] for r in full) / len(full) if full else None for i in range(3)]
        t4.append([mid, "cochran_q", len(full), _f(q), _f(pq), df] + [_f(x) for x in rates] + [""])
        for a, b in (("R1", "R2"), ("R1", "R3"), ("R2", "R3")):
            ia, ib = RUNGS.index(a), RUNGS.index(b)
            bb = sum(1 for r in full if r[ia] == 1 and r[ib] == 0)
            cc = sum(1 for r in full if r[ia] == 0 and r[ib] == 1)
            p = mcnemar_exact(bb, cc)
            pv[f"{mid}|{a}-{b}"] = p
            t4.append([mid, f"mcnemar {a} vs {b}", len(full), f"b={bb} c={cc}", _f(p), "", "", "", "", ""])
    adj = holm(pv)
    for row in t4:
        key = f"{row[0]}|{row[1].replace('mcnemar ', '').replace(' vs ', '-')}"
        if key in adj:
            row[-1] = _f(adj[key])
    _w("t4_ladder.csv", ["model", "test", "paired_methods", "statistic", "p", "df", "rate_R1", "rate_R2", "rate_R3",
                         "p_holm"], t4)

    # T5 sampling stability: flips of the silent-break outcome between sample 1 and sample 2
    t5 = []
    for mid in models:
        for rung in RUNGS:
            pair = defaultdict(dict)
            for r in scored:
                if r["model"] == mid and r["rung"] == rung:
                    pair[r["method"]][r["sample"]] = int(bool(_cls(r).get("silent_break")))
            both = [v for v in pair.values() if 1 in v and 2 in v]
            flips = sum(1 for v in both if v[1] != v[2])
            p, lo, hi = wilson(flips, len(both))
            t5.append([mid, rung, len(both), flips, _f(p), _f(lo), _f(hi)])
    _w("t5_stability.csv", ["model", "rung", "methods_with_two_samples", "flips", "flip_rate", "wilson_lo",
                            "wilson_hi"], t5)

    # T6 allocation change (B/op patched - baseline) for tests-pass patches, by outcome
    base = {r["method"]: r.get("bytes_per_op") for r in d["alloc"].values()
            if r["kind"] == "base" and r.get("status") == "measured"}
    deltas = defaultdict(list)
    for r in d["alloc"].values():
        if r["kind"] != "patch" or r.get("status") != "measured" or base.get(r["method"]) is None:
            continue
        sr = d["score"].get(r["score_cell"]) or {}
        outcome = "silent_break" if _cls(sr).get("silent_break") else "clean"
        delta = r["bytes_per_op"] - base[r["method"]]
        deltas[(r["model"], outcome)].append(delta)
        deltas[("all", outcome)].append(delta)
    t6 = []
    for (mid, outcome), xs in sorted(deltas.items()):
        mean, lo, hi = bootstrap_mean(xs)
        t6.append([mid, outcome, len(xs), _f(mean, 2), _f(lo, 2), _f(hi, 2),
                   sum(1 for x in xs if x < 0)])
    _w("t6_allocation.csv", ["model", "outcome", "n", "mean_delta_bytes_per_op", "boot_lo", "boot_hi",
                             "n_reduced"], t6)

    # T7 claimed change x fired contract rows (tests-pass, sample 1)
    t7 = defaultdict(lambda: defaultdict(int))
    for r in s1:
        if _cls(r).get("tests_pass"):
            cc = r.get("claimed_change") or "?"
            t7[cc]["n"] += 1
            for row in _cls(r).get("contract_rows") or []:
                t7[cc][row] += 1
    _w("t7_claimed_change.csv", ["claimed_change", "tests_pass_n"] + list(HEADLINE_ROWS),
       [[cc, v["n"]] + [v[row] for row in HEADLINE_ROWS] for cc, v in sorted(t7.items())])

    # T8 robustness: headline on subsets (all three dynamic/static oracles available; any-tests variant)
    t8 = []
    for mid in models:
        g = [r for r in s1 if r["model"] == mid]
        allo = [r for r in g if all((_cls(r).get("oracles") or {}).values()) and _cls(r).get("headline_eligible")]
        k = sum(1 for r in allo if _cls(r).get("silent_break"))
        p, lo, hi = wilson(k, len(allo))
        t8.append([mid, "all_oracles_available", len(allo), k, _f(p), _f(lo), _f(hi)])
        anyt = [r for r in g if _cls(r).get("tests_pass")]
        k2 = sum(1 for r in anyt if _cls(r).get("silent_break_any_tests"))
        p, lo, hi = wilson(k2, len(anyt))
        t8.append([mid, "tests_pass_including_no_tests_ran", len(anyt), k2, _f(p), _f(lo), _f(hi)])
    _w("t8_robustness.csv", ["model", "subset", "n", "silent_break", "rate", "wilson_lo", "wilson_hi"], t8)

    # T9 statuses, tokens and spend per model; oracle controls
    t9 = []
    led = list(read_jsonl(RUNS / "ledger.jsonl"))
    for mid in models:
        gr = [r for r in d["gen"].values() if r["model"] == mid]
        sr = [r for r in rows if r["model"] == mid]
        use = defaultdict(int)
        for r in gr:
            for k, v in (r.get("usage") or {}).items():
                use[k] += int(v or 0)
        spend = sum(x["cost"] for x in led if x.get("model") == mid and x.get("mode") == "full")
        t9.append([mid, len(gr), sum(1 for r in gr if r["status"] == "ok"), sum(1 for r in gr if r["status"] ==
                                                                                 "parse_fail"),
                   sum(1 for r in gr if r["status"] == "vendor_error"), sum(1 for r in sr if r.get("status") ==
                                                                            "wrong_method"),
                   use["input"], use["cache_read"], use["cache_write"], use["output"], _f(spend, 2)])
    _w("t9_cost_status.csv", ["model", "cells", "ok", "parse_fail", "vendor_error", "wrong_method", "input_tok",
                              "cache_read_tok", "cache_write_tok", "output_tok", "spend_full_usd"], t9)
    ctl = []
    for kind in ("ctl_identity", "ctl_throw"):
        g = [r for r in d["score"].values() if r["kind"] == kind and r.get("status") == "scored"]
        for row in ROWS:
            ctl.append([kind, row, len(g), sum(1 for r in g if _cls(r).get("rows", {}).get(row))])
    _w("t9b_controls.csv", ["control", "row", "n", "fired"], ctl)

    _audit(d, s1)


def _audit(d, s1) -> None:
    by_id = {}
    for m in candidates("full"):
        by_id[m["id"]] = m
    sb = [r for r in s1 if _cls(r).get("silent_break")]
    per_model = defaultdict(list)
    for r in sorted(sb, key=lambda r: hashlib.sha1(r["cell_id"].encode()).hexdigest()):
        per_model[r["model"]].append(r)
    pick, i = [], 0
    while len(pick) < AUDIT_N and any(i < len(v) for v in per_model.values()):
        for v in per_model.values():
            if i < len(v) and len(pick) < AUDIT_N:
                pick.append(v[i])
        i += 1
    out = []
    for r in pick:
        m = by_id.get(r["method"], {})
        gen = d["gen"].get(r.get("gen_cell") or "", {})
        patched = ((gen.get("parsed") or {}).get("patched_method")) or ""
        diff = "\n".join(difflib.unified_diff(m.get("src", "").splitlines(), patched.splitlines(), "original",
                                              "patched", lineterm="", n=1))
        sig = r.get("signals") or {}
        ev = []
        for f in ((sig.get("randoop") or {}).get("failures") or [])[:3]:
            ev.append("randoop: " + f.get("message", "")[:200])
        for b in ((sig.get("spot") or {}).get("new") or [])[:3]:
            ev.append(f"spotbugs: {b.get('type')} in {b.get('method')}")
        if (sig.get("sync") or {}).get("any_removed"):
            ev.append(f"sync removed: {(sig.get('sync') or {}).get('removed')}")
        if (sig.get("copy") or {}).get("removed"):
            ev.append(f"copy removed: {(sig.get('copy') or {}).get('removed')}")
        if sig.get("ev_patch") == "FAIL":
            ev.append("EqualsVerifier: PASS on original, FAIL on patched")
        out.append([r["cell_id"], r["model"], r["rung"], r["method"], ";".join(_cls(r).get("contract_rows") or []),
                    " | ".join(ev), diff[:4000], "", ""])
    _w("audit_silent_breaks.csv", ["cell_id", "model", "rung", "method", "rows_fired", "evidence", "diff",
                                   "auditor_verdict (real_break|oracle_artifact|unsure)", "note"], out)


def brief(ctx) -> list[str]:
    sizing = read_json(scope_dir("full") / "sizing.json") or {}
    return [f"Audit results/audit_silent_breaks.csv: for each of up to {AUDIT_N} silent-break cells, mark "
            "auditor_verdict (real_break | oracle_artifact | unsure) from the diff and the evidence, then attach it in "
            "the chat (about 1 hour).",
            f"Study size used: {sizing.get('n', '?')} methods (cap {sizing.get('cap', '?')}, floor "
            f"{sizing.get('floor', '?')}); check results/t1_subjects.csv for the per-library split.",
            "Check results/t9b_controls.csv: identity controls should be R0 almost everywhere (oracle false-positive "
            "rate) and throw controls caught wherever suites ran (oracle sensitivity).",
            "Compare results/t2_headline.csv and results/t4_ladder.csv with H1 and H3 in the approach doc."]
