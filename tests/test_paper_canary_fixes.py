"""Regressions from the first AWS run (7 Nov 2026, run ooptimize-20261107T033817Z): the pilot's three failures
(fixed on the instance by repair commits f13bcc4a and 3aed78a1, folded here) and the canary's two causes
(JMH machine-wide lock under parallel lanes; caffeine's build needing JDK 25/26 toolchains)."""
import _ooptpaper  # noqa: F401  (registers the study as package 'ooptpaper')
import json
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

from ooptpaper import env, javatools, libraries, s_build, s_select

LOCK_FAIL = ("# JMH version: 1.37\nERROR: org.openjdk.jmh.runner.RunnerException: ERROR: Another JMH instance might "
             "be running. Unable to acquire the JMH lock (/tmp/jmh.lock), exiting.\n")


class _T:
    java = Path("/jdk/bin/java")
    java_home = Path("/jdk")
    mvn = Path("/mvn/bin/mvn")

    def env(self):
        return {}


def _capture(monkeypatch, mod, stdout=""):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout, "")
    monkeypatch.setattr(mod, "run", fake_run)
    return calls


def test_every_jmh_call_ignores_the_machine_wide_lock(monkeypatch, tmp_path):
    calls = _capture(monkeypatch, javatools, "Benchmarks: \norg.a.B.x\n")
    javatools.jmh_list(_T(), tmp_path / "b.jar")
    javatools.jmh_stack(_T(), tmp_path / "b.jar", "org.a.B.x")
    out = tmp_path / "missing" / "dir" / "r.json"
    javatools.jmh_gc(_T(), tmp_path / "ov", tmp_path / "b.jar", "org.a.B.x", {"size": "8"}, out)
    assert len(calls) == 3 and all("-Djmh.ignoreLock=true" in c for c in calls)
    assert all(c.index("-Djmh.ignoreLock=true") == 1 for c in calls)          # a JVM option of the host JVM
    assert out.parent.is_dir()                                               # f13bcc4a: JMH needs the dir


def test_lock_failure_is_not_a_profile_and_is_redone(monkeypatch, tmp_path):
    from test_paper_oracles import JMH
    assert javatools.profiled(JMH) and not javatools.profiled(LOCK_FAIL)
    hb = SimpleNamespace(touch=lambda **k: None, set=lambda **k: None, progress=lambda **k: None)
    ctx = SimpleNamespace(mode="canary", hb=hb)
    scope = env.scope_dir("canary")
    tree = tmp_path / "tree"
    src = tree / "core/src/main/java/org/jctools/queues/MpscArrayQueue.java"
    src.parent.mkdir(parents=True)
    src.write_text("package org.jctools.queues;\npublic class MpscArrayQueue {\n  int n;\n"
                   "  public boolean offer(Object e) {\n    n++;\n    return true;\n  }\n}\n")
    (scope / "build").mkdir(parents=True, exist_ok=True)
    (scope / "build" / "toylock.json").write_text(json.dumps(
        {"lib": "toylock", "ok": True, "tree": str(tree), "jmh_jar": str(tmp_path / "b.jar"),
         "benchmarks": ["org.jctools.jmh.QueueBench.offerPoll"]}))
    # a record and a raw output left by the failed canary: empty selection, lock-failure text
    (scope / "select").mkdir(parents=True, exist_ok=True)
    (scope / "select" / "toylock.json").write_text(json.dumps({"lib": "toylock", "complete": True, "methods": [],
                                                               "stats": {}}))
    (scope / "select" / "toylock").mkdir(exist_ok=True)
    (scope / "select" / "toylock" / "000.txt").write_text(LOCK_FAIL)
    s_select.LIBS["toylock"] = {"roots": {"core/src/main/java": ("core", "core")}, "packages": ["org.jctools"],
                                "release": 8}
    ran = []
    monkeypatch.setattr(s_select, "jmh_stack", lambda t, jar, bench, **kw: ran.append(bench) or JMH)
    located = [{"file": str(src), "class": "org.jctools.queues.MpscArrayQueue", "simple": "MpscArrayQueue",
                "name": "offer", "params": "Object", "start": src.read_text().index("public boolean"),
                "end": src.read_text().index("  }\n}") + 3, "line_start": 4, "line_end": 7, "abstract": False,
                "static": False, "synchronized": False, "sig": "public boolean offer(Object e)"}]
    monkeypatch.setattr(s_select, "locate", lambda t, files: located)
    try:
        rec = s_select._select_lib(ctx, _T(), "toylock")
    finally:
        del s_select.LIBS["toylock"]
    assert ran == ["org.jctools.jmh.QueueBench.offerPoll"]                  # the lock-failure output was redone
    assert [m["name"] for m in rec["methods"]] == ["offer"]
    assert rec["stats"]["benchmarks_without_profile"] == 0


def test_caffeine_build_and_classpath_may_provision_toolchains(monkeypatch, tmp_path):
    spec = libraries.LIBS["caffeine"]
    assert "auto-download=true" in spec["build"][0] and "auto-download=true" in spec["gradle_flags"]
    for lib in ("RoaringBitmap",):                                          # the others keep the pinned JDK only
        assert "auto-download=false" in libraries.LIBS[lib]["build"][0]
    calls = _capture(monkeypatch, s_build)
    s_build._classpath_gradle(_T(), tmp_path, ["caffeine"], tmp_path / "l.log", spec["gradle_flags"])
    assert "auto-download=true" in calls[0] and "/jdk" in calls[0] and "{java_home}" not in calls[0]


def test_atomic_json_write_is_thread_safe(tmp_path):
    from rh.util import write_json                                          # 3aed78a1 (heartbeat rename race)
    p = tmp_path / "hb.json"
    errs = []

    def w(i):
        try:
            for k in range(200):
                write_json(p, {"i": i, "k": k})
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=w, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and json.loads(p.read_text())["k"] == 199


def test_ev_helper_never_suppresses_both_surrogate_warnings():
    src = (Path(_ooptpaper.REPO) / "paper" / "java" / "OoptEV.java").read_text()
    lenient = src[src.index("LENIENT = {"):src.index("};")]
    assert '"SURROGATE_OR_BUSINESS_KEY"' in lenient and '"SURROGATE_KEY"' not in lenient


def test_failed_allocation_rows_are_retried():
    src = (Path(_ooptpaper.REPO) / "paper" / "s_alloc.py").read_text()
    assert 'if r.get("status") != "failed"' in src
