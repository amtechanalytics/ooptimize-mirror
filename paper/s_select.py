"""S1 select: hot methods from each library's own JMH benchmarks (stack profiler, depth 4), resolved to source
with the JDK's parser, filtered (non-abstract, 3-120 lines, hand-written source, one overload), tagged by static
contract surface and chosen round-robin over tags. Context for rungs R2 and R3 is cut here, from the same tree."""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import fakes
from .env import bind, fake, note, read_json, scope_dir, tools, write_json
from .javatools import jmh_stack, locate, profiled
from .libraries import LIBS
from .oracles import contract_tags, frame_class_method, parse_jmh_runs
from .subjects import CANDIDATES_PER_LIB, FLOOR_PER_LIB, FLOOR_TOTAL, PILOT_METHODS, libs_for, short

MIN_SHARE = 1.0
MIN_LINES, MAX_LINES = 3, 120
MAX_BENCH = 60
PILOT_BENCH = 8
TIME_BUDGET_S = {"pilot": 900, "canary": 3000, "full": 3000}
TAG_ORDER = ["T_EQUALS", "T_SYNC", "T_COPY", "T_COMPARABLE", "T_ORDER", "T_THROWS", "T_COLLECTION_RETURN", "T_STATE",
             "T_NONE"]
R2_CHARS, R3_CHARS, MAX_CALL_SITES = 24000, 12000, 20


# ------------------------------------------------------------------ profiling
def hot_frames(texts: list[str]) -> dict:
    """(class, method) -> {"share": max % over runs, "bench": best run, "params": its parameters}."""
    best: dict[tuple, dict] = {}
    for text in texts:
        for bench, params, groups in parse_jmh_runs(text):
            acc: dict[tuple, float] = {}
            for pct, frames in groups:
                seen = set()
                for fr in frames[:4]:
                    cm = frame_class_method(fr)
                    if cm and cm not in seen:
                        seen.add(cm)
                        acc[cm] = acc.get(cm, 0.0) + pct
            for cm, s in acc.items():
                if s > best.get(cm, {}).get("share", -1):
                    best[cm] = {"share": round(s, 2), "bench": bench, "params": params}
    return best


def source_file(tree: Path, lib: str, cls: str) -> tuple[str, str] | None:
    top = cls.split("$")[0]
    rel = top.replace(".", "/") + ".java"
    for root in LIBS[lib]["roots"]:
        p = tree / root / rel
        if p.exists():
            return root, f"{root}/{rel}"
    return None


# ------------------------------------------------------------------ context for R2 / R3
def class_window(text: str, start: int, end: int, cap: int = R2_CHARS) -> str:
    if len(text) <= cap:
        return text
    half = max(0, (cap - (end - start)) // 2)
    a, b = max(0, start - half), min(len(text), end + half)
    head = text[:min(a, 3000)]                           # package, imports and the class header
    return head + "\n/* ... */\n" + text[max(a, len(head)):b] + "\n/* ... */\n"


_DECL = re.compile(r"^\s*(public|protected|private|static|final|abstract|default|synchronized|native|<)[^;{=]*\([^;{]*\)"
                   r"[^;{]*[;{]", re.M)
_HEADER = re.compile(r"\b(class|interface|enum|record)\s+(\w+)(\s*<[^{]*?>)?\s*(extends\s+([^{]+?))?"
                     r"(\s+implements\s+([^{]+))?\{", re.S)


def type_summary(text: str, max_lines: int = 40) -> str:
    out = []
    h = _HEADER.search(text)
    if h:
        out.append(re.sub(r"\s+", " ", text[h.start():h.end() - 1]).strip() + " {")
    for m in _DECL.finditer(text):
        line = re.sub(r"\s+", " ", m.group(0)).strip().rstrip("{").strip()
        out.append("  " + line + ";")
        if len(out) >= max_lines:
            break
    return "\n".join(out) + "\n}"


def _names(s: str | None) -> list[str]:
    if not s:
        return []
    s = re.sub(r"<[^<>]*>", "", re.sub(r"<[^<>]*>", "", s))
    return [x.strip().split(".")[-1] for x in s.split(",") if x.strip()]


def hierarchy_and_calls(tree: Path, lib: str, file_rel: str, simple: str, method: str, index: dict) -> str:
    """Supertypes and subtypes found in the library's own source (signatures only) and up to 20 call sites."""
    text = (tree / file_rel).read_text(errors="ignore")
    h = _HEADER.search(text)
    supers = (_names(h.group(5)) + _names(h.group(7))) if h else []
    parts = []
    for s in supers:
        p = index["by_simple"].get(s)
        if p:
            parts.append(f"// supertype {s} ({p})\n" + type_summary((tree / p).read_text(errors="ignore")))
    sub_re = re.compile(r"\b(extends|implements)\b[^{]*\b" + re.escape(simple) + r"\b")
    subs = [p for p in index["files"] if p != file_rel and sub_re.search(index["text"][p])][:6]
    for p in subs:
        parts.append(f"// subtype ({p})\n" + type_summary(index["text"][p], 20))
    call_re = re.compile(r"[.\s(]" + re.escape(method) + r"\s*\(")
    sites = []
    for p in index["files"]:
        if p == file_rel:
            continue
        lines = index["text"][p].splitlines()
        for i, ln in enumerate(lines):
            if call_re.search(ln) and not ln.strip().startswith(("*", "//", "/*")):
                lo, hi = max(0, i - 2), min(len(lines), i + 3)
                sites.append(f"// {p}:{i + 1}\n" + "\n".join(lines[lo:hi]))
                break
        if len(sites) >= MAX_CALL_SITES:
            break
    body = "\n\n".join(parts) + ("\n\n// call sites\n" + "\n\n".join(sites) if sites else "")
    return body[:R3_CHARS]


def _index(tree: Path, lib: str) -> dict:
    files, text, by_simple = [], {}, {}
    for root in LIBS[lib]["roots"]:
        for p in sorted((tree / root).rglob("*.java")):
            rel = str(p.relative_to(tree))
            files.append(rel)
            text[rel] = p.read_text(errors="ignore")
            by_simple.setdefault(p.stem, rel)
    return {"files": files, "text": text, "by_simple": by_simple}


# ------------------------------------------------------------------ choose
def round_robin(cands: list[dict], k: int) -> list[dict]:
    queues = {t: [c for c in cands if (c["tags"].get(t) if t != "T_NONE" else not any(c["tags"].values()))]
              for t in TAG_ORDER}
    for q in queues.values():
        q.sort(key=lambda c: (-c["share"], c["id"]))
    chosen, ids = [], set()
    while len(chosen) < k and any(queues.values()):
        for t in TAG_ORDER:
            q = queues[t]
            while q and q[0]["id"] in ids:
                q.pop(0)
            if q and len(chosen) < k:
                c = q.pop(0)
                chosen.append(c)
                ids.add(c["id"])
    return chosen


def _select_lib(ctx, t, lib: str) -> dict:
    out = scope_dir(ctx.mode) / "select" / f"{lib}.json"
    prev = read_json(out)
    if prev and prev.get("complete") and prev.get("methods"):
        return prev                       # empty or skipped selections are recomputed (7 Nov canaries)
    b = read_json(scope_dir(ctx.mode) / "build" / f"{lib}.json") or {}
    if not b.get("ok"):
        rec = {"lib": lib, "complete": True, "methods": [], "skipped": "build failed"}
        write_json(out, rec)
        return rec
    tree, jar = Path(b["tree"]), Path(b["jmh_jar"])
    benches = sorted(b["benchmarks"], key=lambda x: short(x))[:PILOT_BENCH if ctx.mode == "pilot" else MAX_BENCH]
    rawdir = scope_dir(ctx.mode) / "select" / lib
    rawdir.mkdir(parents=True, exist_ok=True)
    texts, t0, failed = [], time.time(), 0
    for i, bench in enumerate(benches):
        f = rawdir / f"{i:03d}.txt"
        if f.exists() and profiled(f.read_text()):
            texts.append(f.read_text())
            continue
        if time.time() - t0 > TIME_BUDGET_S[ctx.mode]:
            note("SELECT", f"{lib}: profiling time budget reached after {i} benchmarks")
            break
        txt = jmh_stack(t, jar, bench, hb=ctx.hb)
        f.write_text(f"# OOPT benchmark {bench}\n" + txt)
        texts.append(txt)
        if not profiled(txt):
            failed += 1
        ctx.hb.touch()
    hot = hot_frames(texts)
    pkgs = tuple(LIBS[lib]["packages"])
    hot = {cm: v for cm, v in hot.items() if v["share"] >= MIN_SHARE and cm[0].startswith(pkgs)}
    files = {}
    for (cls, meth) in hot:
        sf = source_file(tree, lib, cls)
        if sf:
            files.setdefault(sf[1], sf[0])
    located = locate(t, [tree / f for f in sorted(files)])
    by_key: dict[tuple, list] = {}
    for m in located:
        if "error" in m:
            continue
        rel = str(Path(m["file"]).relative_to(tree))
        by_key.setdefault((m["class"], m["name"]), []).append((rel, m))
    stats = {"hot_frames": len(hot), "files": len(files), "overloaded": 0, "abstract": 0, "length": 0,
             "offset": 0, "unresolved": 0}
    cands = []
    texts_cache: dict[str, str] = {}
    for (cls, meth), v in sorted(hot.items()):
        ms = by_key.get((cls, meth), [])
        if not ms:
            stats["unresolved"] += 1
            continue
        if len(ms) > 1:
            stats["overloaded"] += 1
            continue
        rel, m = ms[0]
        if m["abstract"]:
            stats["abstract"] += 1
            continue
        n_lines = m["line_end"] - m["line_start"] + 1
        if not MIN_LINES <= n_lines <= MAX_LINES:
            stats["length"] += 1
            continue
        text = texts_cache.setdefault(rel, (tree / rel).read_text(errors="ignore"))
        src = text[m["start"]:m["end"]]
        if meth not in src or not src.rstrip().endswith("}"):
            stats["offset"] += 1            # non-BMP characters shift UTF-16 offsets; skip rather than guess
            continue
        root = files[rel]
        mod, tmod = LIBS[lib]["roots"][root]
        mid = f"{lib}:{cls}#{meth}({m['params']})"
        cands.append({"id": mid, "lib": lib, "file": rel, "root": root, "module": mod, "test_module": tmod,
                      "class": cls, "simple": m["simple"], "top": cls.split("$")[0], "name": meth,
                      "params": m["params"], "sig": m["sig"], "start": m["start"], "end": m["end"],
                      "line_start": m["line_start"], "line_end": m["line_end"], "static": m["static"],
                      "synchronized": m["synchronized"], "src": src, "share": v["share"], "bench": v["bench"],
                      "bench_params": v["params"],
                      "tags": contract_tags(text, src, m["sig"], m["static"], m["synchronized"])})
    k = PILOT_METHODS if ctx.mode == "pilot" else CANDIDATES_PER_LIB
    chosen = round_robin(cands, k)
    index = _index(tree, lib)
    for c in chosen:
        text = texts_cache[c["file"]]
        c["ctx_r2"] = class_window(text, c["start"], c["end"])
        c["ctx_r3"] = hierarchy_and_calls(tree, lib, c["file"], c["simple"], c["name"], index)
    stats["benchmarks_without_profile"] = failed
    rec = {"lib": lib, "complete": True, "methods": chosen, "eligible_before_choice": len(cands), "stats": stats,
           "benchmarks_profiled": len(texts)}
    write_json(out, rec)
    note("SELECT", f"{lib}: {len(chosen)} chosen of {len(cands)} resolvable hot methods; {stats}")
    return rec


def run_select(ctx) -> dict:
    bind(ctx)
    libs = libs_for(ctx.mode)
    (scope_dir(ctx.mode) / "select").mkdir(parents=True, exist_ok=True)
    if fake():
        recs = [fakes.select(lib, ctx.mode) for lib in libs]
        for r in recs:
            write_json(scope_dir(ctx.mode) / "select" / f"{r['lib']}.json", r)
    else:
        t = tools()
        with ThreadPoolExecutor(max_workers=len(libs), thread_name_prefix="select") as ex:
            recs = list(ex.map(lambda lib: _select_lib(ctx, t, lib), libs))
    per = {r["lib"]: len(r["methods"]) for r in recs}
    tags = {}
    for r in recs:
        for m in r["methods"]:
            for k, v in m["tags"].items():
                tags[k] = tags.get(k, 0) + int(bool(v))
    return {"per_library": per, "total": sum(per.values()), "tags": tags}


def check_select(ctx, m) -> list[str]:
    if ctx.mode == "pilot":
        return [] if m["total"] >= PILOT_METHODS else [f"pilot selected {m['total']} methods < {PILOT_METHODS}"]
    big = [lib for lib, n in m["per_library"].items() if n >= FLOOR_PER_LIB]
    errs = []
    if len(big) < 3:
        errs.append(f"only {len(big)} libraries with >= {FLOOR_PER_LIB} methods: {m['per_library']}")
    if m["total"] < FLOOR_TOTAL:
        errs.append(f"{m['total']} candidate methods < floor {FLOOR_TOTAL}")
    return errs
