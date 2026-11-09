"""The Java side on a toy library, with a real JDK, Randoop and SpotBugs (certificate E08, E09, E14):
method locator, test runner, baseline and the scoring recipe for an identity patch, a silent break
(defensive copy removed: tests pass, SpotBugs and the copy diff fire), a test-visible break, a patch that
does not compile and a reply that rewrote the wrong method. Skips when the tools are not present.
Tool locations: OOPT_TEST_JDK, OOPT_TEST_RANDOOP, OOPT_TEST_SPOTBUGS (optional OOPT_TEST_EV)."""
import _ooptpaper  # noqa: F401  (registers the study as package 'ooptpaper')
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ooptpaper import env, javatools, oracles, pipeline, s_score


def _tools(tmp):
    jdk = os.environ.get("OOPT_TEST_JDK")
    if not jdk and shutil.which("javac"):
        jdk = str(Path(os.path.realpath(shutil.which("javac"))).parents[1])
    rnd, sb = os.environ.get("OOPT_TEST_RANDOOP"), os.environ.get("OOPT_TEST_SPOTBUGS")
    if not (jdk and rnd and sb and Path(rnd).exists() and Path(sb).exists() and (Path(jdk) / "bin" / "javac").exists()):
        pytest.skip("JDK / Randoop / SpotBugs not available (set OOPT_TEST_JDK, OOPT_TEST_RANDOOP, OOPT_TEST_SPOTBUGS)")
    ev = os.environ.get("OOPT_TEST_EV")
    t = env.Tools(Path(jdk), None, Path(rnd), Path(sb), Path(ev) if ev else None, tmp / "helpers")
    assert javatools.compile_helpers(t, t.helpers) == []
    return t


@pytest.fixture(scope="module")
def tools(tmp_path_factory):
    return _tools(tmp_path_factory.mktemp("tools"))


BAG = """package org.example;

import java.util.ArrayList;
import java.util.List;

public class Bag {
    private final List<Integer> items = new ArrayList<>();
    private int ops;

    public void add(int x) {
        items.add(x);
        ops++;
    }

    public List<Integer> view() {
        return new ArrayList<>(items);
    }

    public int sum() {
        int s = 0;
        for (int i : items) {
            s += i;
        }
        return s;
    }

    public synchronized int ops() {
        return ops;
    }

    @Override
    public boolean equals(Object o) {
        return o instanceof Bag && ((Bag) o).items.equals(items);
    }

    @Override
    public int hashCode() {
        return items.hashCode();
    }

    static class Node {
        int v;
        int twice() { return v * 2; }
    }
}
"""

BAG_TEST = """package org.example;

import org.junit.Test;
import static org.junit.Assert.assertEquals;

public class BagTest {
    @Test public void addAndSum() { Bag b = new Bag(); b.add(1); b.add(2); assertEquals(3, b.sum()); }
    @Test public void viewSize() { Bag b = new Bag(); b.add(5); assertEquals(1, b.view().size()); }
}
"""


@pytest.fixture(scope="module")
def toy(tools, tmp_path_factory):
    root = tmp_path_factory.mktemp("toy")
    src = root / "src/main/java/org/example/Bag.java"
    src.parent.mkdir(parents=True)
    src.write_text(BAG)
    tsrc = root / "src/test/java/org/example/BagTest.java"
    tsrc.parent.mkdir(parents=True)
    tsrc.write_text(BAG_TEST)
    (root / "classes").mkdir()
    (root / "test-classes").mkdir()
    subprocess.run([str(tools.javac), "--release", "17", "-d", str(root / "classes"), str(src)], check=True,
                   env=tools.env())
    subprocess.run([str(tools.javac), "--release", "17", "-cp", f"{root / 'classes'}:{tools.randoop}", "-d",
                    str(root / "test-classes"), str(tsrc)], check=True, env=tools.env())
    test_cp = [str(root / "test-classes"), str(root / "classes"), str(tools.randoop)]
    base = pipeline.baseline_file(tools, file_abs=src, file_rel="src/main/java/org/example/Bag.java",
                                  top="org.example.Bag", classes=["org.example.Bag"], simple_names=["Bag"],
                                  orig_class_file=root / "classes/org/example/Bag.class", test_cp=test_cp,
                                  test_src=root / "src/test/java", work=root / "base", default_release=17,
                                  randoop_limit=300)
    ms = {m["name"]: m for m in javatools.locate(tools, [src]) if "error" not in m}
    for m in ms.values():
        m.update(file="src/main/java/org/example/Bag.java", top="org.example.Bag",
                 src=BAG[m["start"]:m["end"]])
    return root, src, base, ms


def test_locator_on_tricky_java(tools, tmp_path):
    f = tmp_path / "T.java"
    f.write_text('package q;\npublic class T {\n  record R(int a) { int twice() { return a * 2; } }\n'
                 '  static abstract class A { abstract void z(); }\n'
                 '  String s() { return """\n    text {block}\n    """; }\n'
                 '  Runnable r() { return new Runnable() { public void run() { } }; }\n}\n')
    rows = javatools.locate(tools, [f])
    names = {(r["class"], r["name"]) for r in rows}
    assert ("q.T$R", "twice") in names and ("q.T$A", "z") in names and ("q.T", "s") in names
    assert not any(r["name"] == "run" for r in rows)             # anonymous classes are skipped
    assert next(r for r in rows if r["name"] == "z")["abstract"]
    text = f.read_text()
    s = next(r for r in rows if r["name"] == "s")
    assert text[s["start"]:s["end"]].startswith("String s()") and text[s["start"]:s["end"]].endswith("}")
    assert "error" in javatools.locate_snippet(tools, "int f( { return 1; }", tmp_path / "sn")
    ok = javatools.locate_snippet(tools, "public int f(int x) { return x; }", tmp_path / "sn2")
    assert ok["methods"][0]["name"] == "f" and ok["methods"][0]["params"] == "int"


def test_baseline(toy):
    root, src, base, ms = toy
    assert base["ok"] and base["compile_ok"] and base["release"] == 17
    assert base["tests"]["classes"] == ["org.example.BagTest"] and len(base["tests"]["keep"]) == 2
    rd = base["randoop"]["org.example.Bag"]
    assert rd["compiled"] and rd["keep"], rd
    assert base["spot"] is not None
    assert {"view", "sum", "ops", "equals", "hashCode", "twice"} <= set(ms)
    assert ms["twice"]["class"] == "org.example.Bag$Node"


def _score(tools, toy, name, patched, tmp_path):
    root, src, base, ms = toy
    return pipeline.score_cell(tools, method=ms[name], base=base, file_abs=src, patched=patched, work=tmp_path / name)


def test_identity_is_clean(tools, toy, tmp_path):
    r = _score(tools, toy, "view", toy[3]["view"]["src"], tmp_path)
    c = r["classification"]
    assert r["compile_ok"] and c["rows"]["R0_NONE"] and not c["silent_break"], r


def test_defensive_copy_removed_is_a_silent_break(tools, toy, tmp_path):
    r = _score(tools, toy, "view", "public List<Integer> view() {\n        return items;\n    }", tmp_path)
    c = r["classification"]
    assert r["compile_ok"] and c["tests_pass"] and c["tests_ran"] == 2
    assert c["rows"]["R6_EXPOSURE"] and c["silent_break"], r
    assert r["signals"]["copy"]["removed"] == 1


def test_sync_removed_is_flagged(tools, toy, tmp_path):
    r = _score(tools, toy, "ops", "public int ops() {\n        return ops;\n    }", tmp_path)
    assert r["classification"]["rows"]["R5_SYNC_REMOVED"], r


def test_throw_control_is_caught(tools, toy, tmp_path):
    r = _score(tools, toy, "sum", s_score.throw_variant(toy[3]["sum"]), tmp_path)
    c = r["classification"]
    assert r["compile_ok"] and c["rows"]["R2_TESTS_FAIL"] and not c["silent_break"], r


def test_value_change_seen_by_randoop(tools, toy, tmp_path):
    r = _score(tools, toy, "sum", "public int sum() {\n        int s = 1;\n        for (int i : items) {\n"
                                  "            s += i;\n        }\n        return s;\n    }", tmp_path)
    rows = r["classification"]["rows"]
    assert rows["R2_TESTS_FAIL"] or rows["R3a_VALUE"], r


def test_no_compile_and_wrong_method(tools, toy, tmp_path):
    r = _score(tools, toy, "sum", "public int sum() {\n        return undefinedName;\n    }", tmp_path)
    assert not r["compile_ok"] and r["classification"]["fired"] == ["R1_NO_COMPILE"]
    r = _score(tools, toy, "sum", "public int total() {\n        return 0;\n    }", tmp_path)
    assert r["status"] == "wrong_method"
    r = _score(tools, toy, "sum", "public int sum( {", tmp_path)
    assert not r["compile_ok"]


def test_nested_class_method(tools, toy, tmp_path):
    r = _score(tools, toy, "twice", "int twice() { return v + v; }", tmp_path)
    assert r["compile_ok"] and r["classification"]["rows"]["R0_NONE"], r


def test_runner_reports_per_test(tools, toy, tmp_path):
    root = toy[0]
    res = javatools.run_tests(tools, javatools.cp(root / "test-classes", root / "classes", tools.randoop),
                              ["org.example.BagTest"], tmp_path / "r.json")
    assert res["run"] == 2 and res["failed"] == 0 and {t["status"] for t in res["tests"]} == {"PASS"}
    assert oracles.compare_tests([t["id"] for t in res["tests"]], res)["ran"] == 2
