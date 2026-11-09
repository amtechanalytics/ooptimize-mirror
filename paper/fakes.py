"""Deterministic stand-ins for every local stage and for the vendor reply, used only by dry runs
(RH_FAKE_VENDOR=1). They never start Java, Maven, Gradle or the network; the shapes match the real outputs,
and the fake scorer goes through the real taxonomy (oracles.classify), so analysis is exercised end to end."""
from __future__ import annotations

import hashlib
import json
import re

from . import oracles
from .libraries import ORDER
from .prompts import CHANGES
from .subjects import PILOT_METHODS, file_key

FAKE_PER_LIB = 42


def _h(s: str) -> int:
    return int(hashlib.sha1(s.encode()).hexdigest(), 16)


def toolchain() -> dict:
    return {"java": "openjdk version \"21.0.13\" (fake)", "javac": "javac 21.0.13 (fake)",
            "mvn": "Apache Maven 3.9.11 (fake)", "randoop": True, "spotbugs": True, "equalsverifier": True,
            "helper_errors": []}


def build(lib: str, scope) -> dict:
    return {"lib": lib, "ok": True, "tree": str(scope / "fake-src" / lib), "tool": "fake", "errors": [],
            "modules": {"core": {"classes": "", "test_classes": "", "test_src": "", "test_cp": ["a", "b"]}},
            "jmh_jar": "fake.jar", "benchmarks": [f"org.fake.{lib}.Bench.b{i}" for i in range(30)]}


_KINDS = [
    ("view", "public java.util.List<Integer> view{i}() {{\n    return java.util.Collections.unmodifiableList(new java.util.ArrayList<>(items));\n}}"),
    ("sum", "public synchronized int sum{i}() {{\n    int s = 0;\n    for (int x : items) {{\n        s += x;\n    }}\n    return s;\n}}"),
    ("eq", "public boolean sameAs{i}(Object o) {{\n    if (o == null) {{\n        throw new IllegalArgumentException(\"null\");\n    }}\n    return o.equals(items);\n}}"),
    ("arr", "public int[] toArray{i}() {{\n    int[] a = new int[items.size()];\n    for (int j = 0; j < a.length; j++) {{\n        a[j] = items.get(j);\n    }}\n    return a.clone();\n}}"),
]


def select(lib: str, mode: str) -> dict:
    n = PILOT_METHODS if mode == "pilot" else FAKE_PER_LIB
    methods = []
    for i in range(n):
        kind, tmpl = _KINDS[i % len(_KINDS)]
        src = tmpl.format(i=i)
        cls = f"org.fake.{lib.lower().replace('-', '')}.Fake{i // 4}"
        name = re.search(r"\s(\w+)\(", src).group(1)
        cls_src = f"package {cls.rsplit('.', 1)[0]};\npublic class {cls.rsplit('.', 1)[1]} {{\n" \
                  f"    private final java.util.List<Integer> items = new java.util.ArrayList<>();\n{src}\n" \
                  "    @Override public boolean equals(Object o) { return o == this; }\n" \
                  "    @Override public int hashCode() { return 1; }\n}\n"
        sig = src.split("{")[0].strip()
        methods.append({"id": f"{lib}:{cls}#{name}()", "lib": lib, "file": cls.replace(".", "/") + ".java",
                        "root": "src/main/java", "module": "core", "test_module": "core", "class": cls,
                        "simple": cls.rsplit(".", 1)[1], "top": cls, "name": name, "params": "", "sig": sig,
                        "start": 0, "end": len(src), "line_start": 3, "line_end": 3 + src.count("\n"),
                        "static": False, "synchronized": "synchronized" in sig, "src": src,
                        "share": round(1.0 + (_h(src + lib) % 300) / 10, 1), "bench": f"org.fake.{lib}.Bench.b{i % 30}",
                        "bench_params": {"size": "16"},
                        "tags": oracles.contract_tags(cls_src, src, sig, False, "synchronized" in sig),
                        "ctx_r2": cls_src, "ctx_r3": f"// supertype Object\n// call sites\n// Use.java:1\nx.{name}();"})
    return {"lib": lib, "complete": True, "methods": methods, "eligible_before_choice": n, "stats": {"fake": True},
            "benchmarks_profiled": 8}


def baseline(key: str, ms: list[dict]) -> dict:
    h = _h(key)
    tested = h % 5 != 0
    keep = [f"org.fake.T{h % 97}#t{i}" for i in range(3)] if tested else []
    classes = sorted({m["class"] for m in ms})
    return {"key": key, "lib": ms[0]["lib"], "file": ms[0]["file"], "ok": True, "release": 17, "compile_ok": True,
            "methods": [m["id"] for m in ms], "errors": [],
            "tests": {"classes": ["org.fake.T"], "keep": keep, "engine": "platform", "flaky": 0, "seen": len(keep)},
            "randoop": {c: {"generated_files": 1, "compiled": True, "keep": [f"R#{i}" for i in range(5)],
                            "classes": ["R"], "flaky": 0, "seen": 5, "dir": "fake"} for c in classes},
            "ev": {c: ("PASS" if h % 3 else "NA") for c in classes}, "spot": [], "overlay_base": "fake",
            "test_cp": ["a", "b"]}


def _tests(ran: int, failed: int, msg: str = "expected:<1> but was:<2>") -> dict:
    return {"available": ran > 0, "kept": ran, "ran": ran, "failed": failed, "missing": 0,
            "failures": [{"id": f"t{i}", "message": msg} for i in range(failed)], "error": None}


def score(c: dict, m: dict, base: dict) -> dict:
    h = _h(c["cell_id"])
    ran = len(base["tests"]["keep"])
    nil = {"available": True, "new": [], "new_exposure": 0, "new_sync": 0, "new_other": 0}
    sig = {"compile_ok": True, "tests": _tests(ran, 0), "randoop": _tests(5, 0), "ev_base": "NA", "ev_patch": "NA",
           "sync": {"any_removed": False}, "copy": {"removed": 0}, "spot": dict(nil)}
    if c["kind"] == "ctl_throw":
        sig["tests"] = _tests(ran, ran, "java.lang.UnsupportedOperationException: oopt-control")
        sig["randoop"] = _tests(5, 5, "java.lang.UnsupportedOperationException: oopt-control")
    elif c["kind"] == "gen":
        k = h % 10
        if k == 0:
            sig = {"compile_ok": False}
        elif k == 1:
            sig["tests"] = _tests(ran, min(1, ran))
        elif k == 2:
            sig["randoop"] = _tests(5, 1, "expected:<[1, 2]> but was:<[2, 1]>")
        elif k == 3:
            sig["spot"] = dict(nil, new_exposure=1)
        elif k == 4:
            sig["sync"] = {"any_removed": True}
        elif k == 5:
            sig["randoop"] = _tests(5, 1, "Expected exception of type java.lang.IllegalArgumentException")
    row = {"status": "scored", "compile_ok": sig["compile_ok"], "signals": sig,
           "classification": oracles.classify(sig), "overlay": "fake" if sig["compile_ok"] else None}
    return row


def alloc(j: dict) -> dict:
    h = _h(j["cell_id"])
    b = 64.0 + (_h(j["method"]) % 64)
    val = b if j["kind"] == "base" else max(0.0, b - (h % 48))
    return {"status": "measured", "bytes_per_op": val, "score": 1000.0 + h % 100, "unit": "ops/us", "mode": "thrpt"}


def response(model_id: str, prefix: str, item: str) -> str:
    m = re.search(r"### Target method \(rewrite this one method\)\n```java\n(.*)\n```\s*$", item, re.S)
    src = m.group(1) if m else "void f() {}"
    h = _h(model_id + item)
    cc = CHANGES[h % len(CHANGES)]
    patched = src if cc == "NONE" else src.replace("{", "{\n    // rewritten", 1)
    return json.dumps({"patched_method": patched, "rationale": "fake rewrite", "claimed_change": cc,
                       "confidence": ["LOW", "MEDIUM", "HIGH"][h % 3]})


__all__ = ["toolchain", "build", "select", "baseline", "score", "alloc", "response", "ORDER", "file_key"]
