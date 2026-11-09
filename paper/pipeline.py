"""The per-file baseline and the per-cell scoring pipeline. Both use the same recipe, so a difference
between original and patched is never a difference in how they were built or run:
  javac the one source file (original or patched) into an overlay directory placed first on the module's
  test-runtime classpath -> targeted project tests -> Randoop regression replay -> EqualsVerifier -> SpotBugs
  on the overlay classes -> synchronisation / defensive-copy diffs of the method text -> taxonomy.
Functions take explicit paths and a Tools object (tests drive them on a toy library in the sandbox)."""
from __future__ import annotations

import re
import shutil
import threading
from pathlib import Path

from . import oracles
from .javatools import (class_major, classes_in, compile_tests, cp, equals_verifier, javac, locate_snippet,
                        randoop_generate, release_of, run_tests, spotbugs)

MAX_TEST_CLASSES = 12
TEST_TIMEOUT = 900

_DISCOVERY: dict[str, list] = {}
_DLOCK = threading.Lock()


# ------------------------------------------------------------------ test discovery
def _test_index(test_src: Path) -> list[tuple[str, str, str]]:
    key = str(test_src)
    with _DLOCK:
        if key in _DISCOVERY:
            return _DISCOVERY[key]
    rows = []
    if test_src.exists():
        for p in sorted(test_src.rglob("*.java")):
            txt = p.read_text(errors="ignore")
            if re.search(r"\babstract\s+class\s+" + re.escape(p.stem) + r"\b", txt):
                continue
            if not re.search(r"@(Test|ParameterizedTest|RepeatedTest|TestFactory|Property)\b|extends\s+TestCase\b", txt):
                continue
            pkg = re.search(r"^\s*package\s+([\w.]+)\s*;", txt, re.M)
            rows.append(((pkg.group(1) + "." if pkg else "") + p.stem, p.stem, txt))
    with _DLOCK:
        _DISCOVERY[key] = rows
    return rows


def discover_tests(test_src: Path, simple_names: list[str], cap: int = MAX_TEST_CLASSES) -> list[str]:
    """Targeted project tests: test classes named after the class (FooTest, TestFoo, FooXyzTest) first, then
    test classes whose source mentions the class by name; abstract test bases excluded; capped."""
    idx = _test_index(test_src)
    named, mentioned = [], []
    for fq, stem, txt in idx:
        if any(stem.startswith(s) and stem != s or stem == f"Test{s}" for s in simple_names):
            named.append(fq)
        elif any(re.search(r"\b" + re.escape(s) + r"\b", txt) for s in simple_names):
            mentioned.append(fq)
    return (sorted(named) + sorted(mentioned))[:cap]


# ------------------------------------------------------------------ helpers
def overlay_classes(overlay: Path) -> list[str]:
    return sorted(str(p.relative_to(overlay))[:-6].replace("/", ".") for p in overlay.rglob("*.class"))


def keep_set(r1: dict | None, r2: dict | None) -> list[str]:
    if not r1 or not r2:
        return []
    p1 = {t["id"] for t in r1.get("tests", []) if t["status"] == "PASS"}
    p2 = {t["id"] for t in r2.get("tests", []) if t["status"] == "PASS"}
    return sorted(p1 & p2)


def flaky_count(r1: dict | None, r2: dict | None) -> tuple[int, int]:
    if not r1 or not r2:
        return 0, 0
    p1 = {t["id"] for t in r1.get("tests", []) if t["status"] == "PASS"}
    p2 = {t["id"] for t in r2.get("tests", []) if t["status"] == "PASS"}
    return len(p1 ^ p2), len(p1 | p2)


def _write_src(work: Path, file_rel: str, text: str) -> Path:
    p = work / "src" / Path(file_rel).name
    if p.parent.exists():
        shutil.rmtree(p.parent)
    p.parent.mkdir(parents=True)
    p.write_text(text)
    return p


# ------------------------------------------------------------------ baseline (one source file)
def baseline_file(t, *, file_abs: Path, file_rel: str, top: str, classes: list[str], simple_names: list[str],
                  orig_class_file: Path, test_cp: list[str], test_src: Path, work: Path, default_release: int,
                  randoop_limit: int = 1500, hb=None) -> dict:
    """Recompile the original file into the baseline overlay, discover targeted tests, run them twice,
    generate + compile + run Randoop twice, EqualsVerifier and SpotBugs, all with the overlay first."""
    rec = {"file": file_rel, "ok": False, "errors": []}
    work.mkdir(parents=True, exist_ok=True)
    release = release_of(class_major(orig_class_file), default_release)
    rec["release"] = release
    text = file_abs.read_text(errors="ignore")
    src = _write_src(work, file_rel, text)
    overlay = work / "overlay_base"
    ok, err = javac(t, src, cp(test_cp), overlay, release, log=work / "javac.log")
    rec["compile_ok"] = ok
    if not ok:
        rec["errors"].append(f"original does not recompile in the pipeline: {err[-600:]}")
        return rec
    run_cp = cp(overlay, test_cp)
    # project tests
    tcls = discover_tests(test_src, simple_names)
    r1 = run_tests(t, run_cp, tcls, work / "tests1.json", tmp=work / "tmp", timeout=TEST_TIMEOUT, hb=hb)
    r2 = run_tests(t, run_cp, tcls, work / "tests2.json", tmp=work / "tmp", timeout=TEST_TIMEOUT, hb=hb)
    keep = keep_set(r1, r2)
    fl, tot = flaky_count(r1, r2)
    rec["tests"] = {"classes": tcls, "keep": keep, "engine": (r1 or {}).get("engine"), "flaky": fl, "seen": tot,
                    "failed_run1": (r1 or {}).get("failed"), "error": (r1 or {}).get("error")}
    # Randoop, one suite per class of interest
    rec["randoop"] = {}
    for cls in classes:
        name = "OoptR" + re.sub(r"\W", "", cls.split(".")[-1])[:40]
        rdir = work / "randoop" / re.sub(r"\W", "_", cls)
        files = randoop_generate(t, cls, run_cp, rdir / "src", name, limit=randoop_limit, hb=hb,
                                 log=work / "randoop.log")
        compiled = compile_tests(t, files, run_cp, rdir / "classes", log=work / "randoop.log") if files else False
        rr = {"generated_files": len(files), "compiled": compiled, "keep": [], "classes": []}
        if compiled:
            rcls = classes_in(files)
            rcp = cp(rdir / "classes", run_cp, t.randoop)
            a = run_tests(t, rcp, rcls, rdir / "run1.json", engine="junit4", tmp=work / "tmp", hb=hb)
            b = run_tests(t, rcp, rcls, rdir / "run2.json", engine="junit4", tmp=work / "tmp", hb=hb)
            rr.update(classes=rcls, keep=keep_set(a, b), dir=str(rdir / "classes"))
            rr["flaky"], rr["seen"] = flaky_count(a, b)
        rec["randoop"][cls] = rr
    # EqualsVerifier under the fixed configuration
    rec["ev"] = {cls: equals_verifier(t, cls, run_cp)[0] for cls in classes}
    # SpotBugs on the baseline overlay (the same recipe as the patched overlay)
    xml = spotbugs(t, overlay, overlay_classes(overlay), cp(test_cp), work / "spotbugs_base.xml")
    rec["spot"] = oracles.parse_spotbugs(xml) if xml else None
    rec["overlay_base"] = str(overlay)
    rec["test_cp"] = list(test_cp)
    rec["ok"] = True
    return rec


# ------------------------------------------------------------------ one cell
def splice(text: str, start: int, end: int, patched: str) -> str:
    return text[:start] + patched.strip("\n") + text[end:]


def score_cell(t, *, method: dict, base: dict, file_abs: Path, patched: str, work: Path, hb=None) -> dict:
    """Score one patched method against its file's baseline. Returns signals + taxonomy."""
    out = {"status": "scored"}
    work.mkdir(parents=True, exist_ok=True)
    sn = locate_snippet(t, patched, work / "snippet")
    if "error" in sn:
        out.update(compile_ok=False, compile_error=f"snippet: {sn['error']}"[:600], grounded=None)
        out["classification"] = oracles.classify({"compile_ok": False})
        return out
    names = [m["name"] for m in sn["methods"]]
    if method["name"] not in names:
        out.update(status="wrong_method", returned=names[:5])
        return out
    tgt = next(m for m in sn["methods"] if m["name"] == method["name"])
    out["signature_changed"] = oracles.normalise_sig(tgt.get("params", "")) != oracles.normalise_sig(method["params"])
    text = file_abs.read_text(errors="ignore")
    new_text = splice(text, method["start"], method["end"], patched)
    src = _write_src(work, method["file"], new_text)
    overlay = work / "overlay"
    ok, err = javac(t, src, cp(base["test_cp"]), overlay, base["release"], log=work / "javac.log")
    out["compile_ok"] = ok
    sig = {"compile_ok": ok}
    if not ok:
        out["compile_error"] = err[-600:]
        out["classification"] = oracles.classify(sig)
        return out
    out["overlay"] = str(overlay)
    run_cp = cp(overlay, base["test_cp"])
    keep = base["tests"]["keep"]
    if keep:
        r = run_tests(t, run_cp, base["tests"]["classes"], work / "tests.json", tmp=work / "tmp",
                      timeout=TEST_TIMEOUT, hb=hb)
        sig["tests"] = oracles.compare_tests(keep, r)
    else:
        sig["tests"] = {"available": False, "ran": 0, "failed": 0, "failures": []}
    rd = (base.get("randoop") or {}).get(method["class"]) or (base.get("randoop") or {}).get(method["top"]) or {}
    if rd.get("keep"):
        r = run_tests(t, cp(rd["dir"], run_cp, t.randoop), rd["classes"], work / "randoop.json", engine="junit4",
                      tmp=work / "tmp", hb=hb)
        sig["randoop"] = oracles.compare_tests(rd["keep"], r)
    else:
        sig["randoop"] = {"available": False, "ran": 0, "failed": 0, "failures": []}
    ev_cls = method["class"] if method["class"] in (base.get("ev") or {}) else method["top"]
    sig["ev_base"] = (base.get("ev") or {}).get(ev_cls, "NA")
    sig["ev_patch"] = equals_verifier(t, ev_cls, run_cp)[0] if sig["ev_base"] == "PASS" else "NA"
    xml = spotbugs(t, overlay, overlay_classes(overlay), cp(base["test_cp"]), work / "spotbugs.xml")
    sig["spot"] = oracles.spotbugs_delta(base.get("spot"), oracles.parse_spotbugs(xml) if xml else None)
    sig["sync"] = oracles.sync_delta(method["src"], patched)
    sig["copy"] = oracles.copy_delta(method["src"], patched)
    out["signals"] = sig
    out["classification"] = oracles.classify(sig)
    return out
