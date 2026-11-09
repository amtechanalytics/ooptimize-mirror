"""Pure functions behind selection and scoring (no I/O beyond the strings passed in), so every one is unit
tested on fixtures: JMH stack-profiler parsing, contract-surface tags, synchronisation/copy counts, SpotBugs
XML parsing and deltas, test-outcome comparison, and the pre-registered breakage taxonomy."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

# ------------------------------------------------------------------ JMH -prof stack
_HEAD = re.compile(r"^\s*([\d.]+)%\s+([\d.]+)%\s+(\S+)\s*$")
_CONT = re.compile(r"^\s{6,}(\S+)\s*$")


def parse_jmh_stack(text: str) -> list[tuple[float, list[str]]]:
    """Groups (percent, [frames]) from every '[Thread state: RUNNABLE]' section of JMH stack-profiler
    output (one section per benchmark parameter combination)."""
    groups: list[tuple[float, list[str]]] = []
    in_runnable = False
    cur = None
    for line in text.splitlines():
        if "[Thread state: RUNNABLE]" in line:
            in_runnable, cur = True, None
            continue
        if line.startswith("....[") or line.startswith("Secondary result") or line.startswith("# "):
            in_runnable, cur = False, None
            continue
        if not in_runnable:
            continue
        m = _HEAD.match(line)
        if m:
            cur = (float(m.group(1)), [m.group(3)])
            groups.append(cur)
            continue
        m2 = _CONT.match(line)
        if m2 and cur is not None:
            cur[1].append(m2.group(1))
    return groups


_PARAMS = re.compile(r"^# Parameters:\s*\((.*)\)\s*$")


def parse_jmh_runs(text: str) -> list[tuple[str, dict, list[tuple[float, list[str]]]]]:
    """Split JMH output into runs ('# Benchmark: ...' headers, optional '# Parameters: (a = 1, b = x)') and
    parse the stack-profiler groups of each run."""
    runs = []
    cur_name, cur_params, buf = None, {}, []

    def flush():
        if cur_name is not None:
            runs.append((cur_name, dict(cur_params), parse_jmh_stack("\n".join(buf))))

    for line in text.splitlines():
        if line.startswith("# Benchmark: "):
            flush()
            cur_name, cur_params, buf = line[len("# Benchmark: "):].strip(), {}, []
            continue
        m = _PARAMS.match(line)
        if m and cur_name is not None and not buf:
            for kv in m.group(1).split(","):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    cur_params[k.strip()] = v.strip()
            continue
        if cur_name is not None:
            buf.append(line)
    flush()
    return runs


def frame_class_method(frame: str) -> tuple[str, str] | None:
    """'org.pkg.Outer$Inner.method' -> ('org.pkg.Outer$Inner', 'method'); None for lambdas/synthetic."""
    if "." not in frame or "(" in frame:
        frame = frame.split("(")[0]
    cls, _, meth = frame.rpartition(".")
    if not cls or not meth or meth.startswith(("<", "lambda$", "access$")) or "$$" in cls:
        return None
    if re.search(r"\$\d+$", cls):          # anonymous class
        return None
    return cls, meth


def list_benchmarks(text: str) -> list[str]:
    out = []
    started = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("Benchmarks:"):
            started = True
            continue
        if started and s and " " not in s and "." in s:
            out.append(s)
    return out


# ------------------------------------------------------------------ contract surface tags
SYNC_RE = re.compile(r"\b(synchronized|volatile)\b")
JUC_RE = re.compile(r"\bjava\.util\.concurrent\b|\b(Atomic\w+|ReentrantLock|ReadWriteLock|StampedLock|Semaphore|"
                    r"CountDownLatch|ConcurrentHashMap|CopyOnWrite\w+|LockSupport|VarHandle|Unsafe|"
                    r"compareAndSet\w*|getAndAdd\w*|lazySet|soElement|svElement|lvElement|lpElement|spElement|"
                    r"UNSAFE|AtomicFieldUpdater\w*)\b")
COPY_RE = re.compile(r"\.clone\(\)|Arrays\.copyOf\w*|System\.arraycopy|Collections\.unmodifiable\w+|"
                     r"\b(List|Set|Map)\.copyOf|\.toArray\(|new\s+(ArrayList|HashMap|HashSet|LinkedList|LinkedHashMap|"
                     r"LinkedHashSet|TreeMap|TreeSet|ArrayDeque)\s*(<[^>]*>)?\s*\(\s*[A-Za-z_]\w*\s*\)")
THROW_RE = re.compile(r"\bthrow\s+new\s+(\w+)")
ORDER_RE = re.compile(r"\b(LinkedHashMap|LinkedHashSet|TreeMap|TreeSet|SortedMap|SortedSet|NavigableMap|NavigableSet|"
                      r"iterator\(\)|Comparator|Comparable|forEach)\b")


def contract_tags(class_src: str, method_src: str, method_sig: str, is_static: bool, is_sync: bool) -> dict:
    has_equals = re.search(r"\bboolean\s+equals\s*\(\s*(final\s+)?(@\w+\s+)?Object\b", class_src) is not None
    has_hash = re.search(r"\bint\s+hashCode\s*\(\s*\)", class_src) is not None
    comparable = re.search(r"implements\b[^{]*\bComparable\b", class_src) is not None or \
        re.search(r"\bint\s+compareTo\s*\(", class_src) is not None
    head = method_sig
    return {
        "T_EQUALS": has_equals or has_hash,
        "T_COMPARABLE": comparable,
        "T_SYNC": bool(SYNC_RE.search(method_src) or JUC_RE.search(method_src)) or is_sync,
        "T_COPY": bool(COPY_RE.search(method_src)),
        "T_THROWS": bool(THROW_RE.search(method_src)) or " throws " in f" {head} ",
        "T_ORDER": bool(ORDER_RE.search(method_src)),
        "T_STATE": (not is_static) and bool(re.search(r"\bthis\.\w+\s*=[^=]|\b\w+\s*(\+\+|--)|\b\w+\s*[-+*/|&^]=",
                                                     method_src)),
        "T_COLLECTION_RETURN": bool(re.search(r"\b(List|Set|Map|Collection|Iterable|Iterator|Queue|Deque)\b|\[\]",
                                              head.split("(")[0])),
    }


def _strip_comments_strings(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    src = re.sub(r"//[^\n]*", " ", src)
    src = re.sub(r'"""(.|\n)*?"""', '""', src)
    src = re.sub(r'"(\\.|[^"\\])*"', '""', src)
    src = re.sub(r"'(\\.|[^'\\])'", "''", src)
    return src


def sync_counts(src: str) -> dict:
    s = _strip_comments_strings(src)
    return {"synchronized": len(re.findall(r"\bsynchronized\b", s)), "volatile": len(re.findall(r"\bvolatile\b", s)),
            "juc": len(JUC_RE.findall(s))}


def sync_delta(orig: str, patched: str) -> dict:
    a, b = sync_counts(orig), sync_counts(patched)
    removed = {k: max(0, a[k] - b[k]) for k in a}
    return {"before": a, "after": b, "removed": removed, "any_removed": any(removed.values())}


def copy_delta(orig: str, patched: str) -> dict:
    a = len(COPY_RE.findall(_strip_comments_strings(orig)))
    b = len(COPY_RE.findall(_strip_comments_strings(patched)))
    return {"before": a, "after": b, "removed": max(0, a - b)}


def normalise_sig(sig: str) -> str:
    s = re.sub(r"@\w+(\([^)]*\))?", "", sig)
    s = re.sub(r"\b(final)\s+", "", s)                      # 'final' on parameters does not change the signature
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s*([(),<>\[\]])\s*", r"\1", s)
    # parameter names do not matter: keep types only
    m = re.match(r"(.*?)\((.*)\)(.*)$", s)
    if not m:
        return s
    pre, params, post = m.groups()
    types = []
    depth, cur = 0, ""
    for ch in params:
        if ch in "<":
            depth += 1
        elif ch in ">":
            depth -= 1
        if ch == "," and depth == 0:
            types.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        types.append(cur)
    types = [t.strip().rsplit(" ", 1)[0] if " " in t.strip() else t.strip() for t in types]
    return f"{pre}({','.join(types)}){post}"


# ------------------------------------------------------------------ SpotBugs
EXPOSURE = ("EI_EXPOSE_REP", "EI_EXPOSE_REP2", "EI_EXPOSE_STATIC_REP2", "MS_EXPOSE_REP", "MS_MUTABLE_ARRAY",
            "MS_MUTABLE_COLLECTION")
SYNC_BUGS = ("IS2_INCONSISTENT_SYNC", "IS_FIELD_NOT_GUARDED", "UG_SYNC_SET_UNSYNC_GET", "VO_VOLATILE_", "DC_DOUBLECHECK",
             "DL_SYNCHRONIZATION", "ML_SYNC", "NN_NAKED_NOTIFY", "UL_UNRELEASED_LOCK", "UW_UNCOND_WAIT",
             "AT_", "STCAL_", "JLM_", "LI_LAZY_INIT", "SWL_SLEEP", "RV_RETURN_VALUE_OF_PUTIFABSENT")


def parse_spotbugs(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    bugs = []
    for b in root.iter("BugInstance"):
        meth = None
        for m in b.iter("Method"):
            meth = m.get("name")
            break
        bugs.append({"type": b.get("type"), "category": b.get("category"), "method": meth,
                     "priority": b.get("priority")})
    return bugs


def spotbugs_delta(base: list[dict] | None, patched: list[dict] | None) -> dict:
    if base is None or patched is None:
        return {"available": False, "new": [], "new_exposure": 0, "new_sync": 0, "new_other": 0}
    seen = {(b["type"], b["method"]) for b in base}
    new = [b for b in patched if (b["type"], b["method"]) not in seen]
    exp = [b for b in new if b["type"].startswith(EXPOSURE)]
    syn = [b for b in new if b["type"].startswith(SYNC_BUGS)]
    oth = [b for b in new if b not in exp and b not in syn]
    return {"available": True, "new": new[:50], "new_exposure": len(exp), "new_sync": len(syn), "new_other": len(oth)}


# ------------------------------------------------------------------ test outcomes
def compare_tests(keep: list[str], result: dict | None) -> dict:
    """keep: test ids that passed twice on the original. result: OoptTestRunner JSON on the patched build."""
    if result is None:
        return {"available": False, "ran": 0, "failed": 0, "failures": [], "error": "no result"}
    by_id = {t["id"]: t for t in result.get("tests", [])}
    kept = [k for k in keep if k in by_id]
    fails = [{"id": k, "message": by_id[k].get("message", "")} for k in kept if by_id[k]["status"] == "FAIL"]
    missing = [k for k in keep if k not in by_id]
    return {"available": bool(keep), "kept": len(keep), "ran": sum(1 for k in kept if by_id[k]["status"] != "SKIP"),
            "failed": len(fails), "missing": len(missing), "failures": fails[:50], "error": result.get("error")}


_LIST = re.compile(r"\[(.*?)\]")


def classify_divergence(msg: str) -> str:
    """R3b exception / R3c same elements in another order / R3a other value divergence."""
    low = (msg or "").lower()
    if re.search(r"(expected|unexpected|threw|thrown|should have thrown)\b.*exception|exception\b.*(expected|thrown)",
                 low) or low.startswith(("java.lang.", "java.util.")) and "assertionerror" not in low:
        return "R3b_EXCEPTION"
    pairs = re.findall(r"expected:<(.*?)> but was:<(.*?)>", msg or "", re.S) or \
        re.findall(r"'([^']*)' != '([^']*)'", msg or "")
    for a, b in pairs:
        ma, mb = _LIST.search(a), _LIST.search(b)
        if ma and mb:
            ea = sorted(x.strip() for x in ma.group(1).split(",") if x.strip())
            eb = sorted(x.strip() for x in mb.group(1).split(",") if x.strip())
            if ea == eb and a != b:
                return "R3c_ORDER"
    return "R3a_VALUE"


ROWS = ["R1_NO_COMPILE", "R2_TESTS_FAIL", "R3a_VALUE", "R3b_EXCEPTION", "R3c_ORDER", "R4_EQUALS_CONTRACT",
        "R5_SYNC_REMOVED", "R6_EXPOSURE", "R7_STATIC_OTHER", "R0_NONE"]
HEADLINE_ROWS = ("R3a_VALUE", "R3b_EXCEPTION", "R3c_ORDER", "R4_EQUALS_CONTRACT", "R5_SYNC_REMOVED", "R6_EXPOSURE")


def classify(sig: dict) -> dict:
    """Pre-registered taxonomy. sig keys: compile_ok, tests (compare_tests), randoop (compare_tests),
    ev_base, ev_patch (PASS|FAIL|NA), sync (sync_delta), copy (copy_delta), spot (spotbugs_delta)."""
    rows = {r: False for r in ROWS}
    tests = sig.get("tests") or {}
    tests_ran = int(tests.get("ran") or 0)
    if not sig.get("compile_ok"):
        rows["R1_NO_COMPILE"] = True
    else:
        if tests.get("failed"):
            rows["R2_TESTS_FAIL"] = True
        rd = sig.get("randoop") or {}
        if rd.get("failed"):
            for f in rd.get("failures", []):
                rows[classify_divergence(f.get("message", ""))] = True
        if sig.get("ev_base") == "PASS" and sig.get("ev_patch") == "FAIL":
            rows["R4_EQUALS_CONTRACT"] = True
        sp = sig.get("spot") or {}
        if (sig.get("sync") or {}).get("any_removed") or sp.get("new_sync", 0) > 0:
            rows["R5_SYNC_REMOVED"] = True
        if sp.get("new_exposure", 0) > 0 or (sig.get("copy") or {}).get("removed", 0) > 0:
            rows["R6_EXPOSURE"] = True
        if sp.get("new_other", 0) > 0:
            rows["R7_STATIC_OTHER"] = True
    fired = [r for r in ROWS if rows[r] and r != "R0_NONE"]
    rows["R0_NONE"] = not fired
    tests_pass = bool(sig.get("compile_ok")) and not rows["R2_TESTS_FAIL"]
    contract = [r for r in fired if r in HEADLINE_ROWS]
    return {"rows": rows, "fired": fired, "tests_pass": tests_pass, "tests_ran": tests_ran,
            # headline counts only cells where at least one targeted project test actually ran
            "headline_eligible": tests_pass and tests_ran > 0,
            "silent_break": tests_pass and tests_ran > 0 and bool(contract),
            "silent_break_any_tests": tests_pass and bool(contract),
            "contract_rows": contract,
            "oracles": {"randoop": bool((sig.get("randoop") or {}).get("available")),
                        "equals_verifier": sig.get("ev_base") == "PASS",
                        "spotbugs": bool((sig.get("spot") or {}).get("available"))}}
