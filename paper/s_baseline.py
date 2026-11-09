"""S1 baseline: per selected source file, the original recompiled by the scoring recipe, its targeted project
tests and its Randoop suite each run twice (only tests that pass both times are kept), EqualsVerifier and
SpotBugs on the baseline overlay. Files whose original does not recompile under the recipe are dropped."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import fakes
from .env import bind, fake, note, read_json, scope_dir, tools, work_dir, workers, write_json
from .libraries import LIBS
from .pipeline import baseline_file
from .subjects import build_info, candidates, file_key

FLAKY_MAX = 0.30
MIN_TESTED_SHARE = 0.40            # sanity floor; the headline counts only cells where project tests ran


def _groups(mode: str) -> dict[str, list[dict]]:
    g: dict[str, list[dict]] = {}
    for m in candidates(mode):
        g.setdefault(file_key(m), []).append(m)
    return g


def baseline_present(rec: dict) -> bool:
    """A dropped file stays dropped (deterministic); a kept one is reusable only while its baseline overlay and
    compiled Randoop suites exist on this machine (they live in /opt/data, which a new instance does not have)."""
    if not rec.get("ok"):
        return True
    if not Path(rec.get("overlay_base", "")).exists():
        return False
    return all(Path(rr["dir"]).exists() for rr in (rec.get("randoop") or {}).values() if rr.get("keep"))


def _one(ctx, t, key: str, ms: list[dict]) -> dict:
    out = scope_dir(ctx.mode) / "baseline" / f"{key}.json"
    prev = read_json(out)
    if prev is not None and "ok" in prev and baseline_present(prev):
        return prev
    m0 = ms[0]
    b = build_info(ctx.mode, m0["lib"]) or {}
    tree = Path(b["tree"])
    mod = b["modules"][m0["module"]]
    tmod = b["modules"][m0["test_module"]]
    rel_class = m0["top"].replace(".", "/") + ".class"
    classes = sorted({m["class"] for m in ms} | {m0["top"]})
    simple = sorted({m0["top"].split(".")[-1]} | {m["simple"] for m in ms})
    try:
        rec = baseline_file(t, file_abs=tree / m0["file"], file_rel=m0["file"], top=m0["top"], classes=classes,
                            simple_names=simple, orig_class_file=Path(mod["classes"]) / rel_class,
                            test_cp=tmod["test_cp"], test_src=Path(tmod["test_src"]),
                            work=work_dir(ctx.mode) / "base" / key, default_release=LIBS[m0["lib"]]["release"],
                            hb=ctx.hb)
    except Exception as e:  # noqa: BLE001 — recorded; the file is dropped, the gate counts it
        rec = {"file": m0["file"], "ok": False, "errors": [f"{type(e).__name__}: {e}"]}
    rec["key"] = key
    rec["lib"] = m0["lib"]
    rec["methods"] = [m["id"] for m in ms]
    write_json(out, rec)
    return rec


def run_baseline(ctx) -> dict:
    bind(ctx)
    groups = _groups(ctx.mode)
    (scope_dir(ctx.mode) / "baseline").mkdir(parents=True, exist_ok=True)
    recs = []
    if fake():
        for key, ms in sorted(groups.items()):
            r = fakes.baseline(key, ms)
            write_json(scope_dir(ctx.mode) / "baseline" / f"{key}.json", r)
            recs.append(r)
    else:
        t = tools()
        ctx.hb.set(total=len(groups), done=0)
        n = workers(28 if ctx.mode != "pilot" else 3)
        with ThreadPoolExecutor(max_workers=n, thread_name_prefix="base") as ex:
            futs = {ex.submit(_one, ctx, t, k, ms): k for k, ms in sorted(groups.items())}
            for f in as_completed(futs):
                recs.append(f.result())
                ctx.hb.progress(lane="baseline")
    ok = [r for r in recs if r.get("ok")]
    methods = sum(len(r["methods"]) for r in ok)
    tested = sum(len(r["methods"]) for r in ok if (r.get("tests") or {}).get("keep"))
    rnd = [rr for r in ok for rr in (r.get("randoop") or {}).values() if rr.get("compiled")]
    flaky = sum(rr.get("flaky", 0) for rr in rnd)
    seen = sum(rr.get("seen", 0) for rr in rnd)
    m = {"files": len(recs), "files_ok": len(ok), "methods_ok": methods, "methods_with_project_tests": tested,
         "randoop_suites": len(rnd), "randoop_kept": sum(len(rr.get("keep", [])) for rr in rnd),
         "randoop_flaky_share": round(flaky / seen, 4) if seen else 0.0,
         "ev_pass": sum(1 for r in ok for v in (r.get("ev") or {}).values() if v == "PASS"),
         "dropped": sorted(r["file"] for r in recs if not r.get("ok"))[:50]}
    note("BASELINE", f"{ctx.mode}: {m['files_ok']}/{m['files']} files ok, {methods} methods, {tested} with project "
                     f"tests, {m['randoop_suites']} Randoop suites")
    return m


def check_baseline(ctx, m) -> list[str]:
    errs = []
    if m["methods_ok"] == 0:
        errs.append("no method survived the baseline")
    if m["randoop_flaky_share"] > FLAKY_MAX:
        errs.append(f"Randoop flaky share {m['randoop_flaky_share']} > {FLAKY_MAX}")
    if ctx.mode == "pilot":
        if not m["randoop_suites"] and not m["methods_with_project_tests"]:
            errs.append("pilot: neither project tests nor a Randoop suite available for any method")
    elif m["methods_ok"] and m["methods_with_project_tests"] / m["methods_ok"] < MIN_TESTED_SHARE:
        errs.append(f"only {m['methods_with_project_tests']}/{m['methods_ok']} methods have a kept project test "
                    f"(< {MIN_TESTED_SHARE:.0%})")
    return errs
