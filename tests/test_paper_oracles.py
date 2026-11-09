"""Taxonomy, headline rule and the pure oracle helpers (certificate E10, E11)."""
import _ooptpaper  # noqa: F401  (registers the study as package 'ooptpaper')
from ooptpaper import oracles as o


def _clean(ran=3):
    return {"compile_ok": True, "tests": {"available": True, "ran": ran, "failed": 0, "failures": []},
            "randoop": {"available": True, "ran": 5, "failed": 0, "failures": []}, "ev_base": "NA", "ev_patch": "NA",
            "sync": {"any_removed": False}, "copy": {"removed": 0},
            "spot": {"available": True, "new": [], "new_exposure": 0, "new_sync": 0, "new_other": 0}}


def test_clean_patch_is_r0_and_not_a_break():
    c = o.classify(_clean())
    assert c["rows"]["R0_NONE"] and not c["silent_break"] and c["tests_pass"] and c["headline_eligible"]


def test_no_compile_is_r1_only():
    c = o.classify({"compile_ok": False})
    assert c["fired"] == ["R1_NO_COMPILE"] and not c["tests_pass"] and not c["silent_break"]


def test_tests_fail_is_not_silent_even_with_contract_rows():
    s = _clean()
    s["tests"]["failed"] = 1
    s["copy"]["removed"] = 1
    c = o.classify(s)
    assert c["rows"]["R2_TESTS_FAIL"] and c["rows"]["R6_EXPOSURE"] and not c["silent_break"]


def test_headline_needs_tests_that_ran():
    s = _clean(ran=0)
    s["sync"]["any_removed"] = True
    c = o.classify(s)
    assert c["rows"]["R5_SYNC_REMOVED"] and not c["silent_break"] and c["silent_break_any_tests"]
    assert not c["headline_eligible"]
    s2 = _clean(ran=2)
    s2["sync"]["any_removed"] = True
    assert o.classify(s2)["silent_break"]


def test_each_contract_row():
    s = _clean()
    s["randoop"].update(failed=1, failures=[{"message": "expected:<[1, 2, 3]> but was:<[3, 2, 1]>"}])
    assert o.classify(s)["rows"]["R3c_ORDER"]
    s = _clean()
    s["randoop"].update(failed=1, failures=[{"message": "java.lang.NullPointerException: x"}])
    assert o.classify(s)["rows"]["R3b_EXCEPTION"]
    s = _clean()
    s["randoop"].update(failed=1, failures=[{"message": "expected:<4> but was:<5>"}])
    assert o.classify(s)["rows"]["R3a_VALUE"]
    s = _clean()
    s.update(ev_base="PASS", ev_patch="FAIL")
    assert o.classify(s)["rows"]["R4_EQUALS_CONTRACT"]
    s = _clean()
    s["ev_base"], s["ev_patch"] = "NA", "FAIL"         # only a PASS -> FAIL transition counts
    assert not o.classify(s)["rows"]["R4_EQUALS_CONTRACT"]
    s = _clean()
    s["spot"]["new_exposure"] = 1
    assert o.classify(s)["rows"]["R6_EXPOSURE"]
    s = _clean()
    s["spot"]["new_other"] = 2
    c = o.classify(s)
    assert c["rows"]["R7_STATIC_OTHER"] and not c["silent_break"]       # R7 is reported, not in the headline


def test_randoop_expected_exception_message():
    assert o.classify_divergence("Expected exception of type java.lang.IllegalStateException; message: x") == \
        "R3b_EXCEPTION"
    assert o.classify_divergence("java.lang.AssertionError: expected:<1> but was:<2>") == "R3a_VALUE"


def test_sync_and_copy_deltas_ignore_comments_and_strings():
    a = "public synchronized int f() { /* synchronized */ return Arrays.copyOf(x, n).length; }"
    b = "public int f() { String s = \"synchronized\"; return x.length; }"
    d = o.sync_delta(a, b)
    assert d["removed"]["synchronized"] == 1 and d["any_removed"]
    assert o.copy_delta(a, b)["removed"] == 1
    assert not o.sync_delta(a, a)["any_removed"]


def test_spotbugs_parse_and_delta():
    xml = ('<BugCollection><BugInstance type="EI_EXPOSE_REP" priority="2" category="MALICIOUS_CODE">'
           '<Method name="view"/></BugInstance><BugInstance type="IS2_INCONSISTENT_SYNC" priority="2" '
           'category="MT_CORRECTNESS"><Method name="get"/></BugInstance></BugCollection>')
    bugs = o.parse_spotbugs(xml)
    assert {b["type"] for b in bugs} == {"EI_EXPOSE_REP", "IS2_INCONSISTENT_SYNC"}
    d = o.spotbugs_delta([], bugs)
    assert d["new_exposure"] == 1 and d["new_sync"] == 1 and d["new_other"] == 0
    assert o.spotbugs_delta(bugs, bugs)["new"] == []
    assert not o.spotbugs_delta(None, bugs)["available"]


def test_compare_tests_counts_only_kept_tests():
    res = {"tests": [{"id": "A#a", "status": "PASS"}, {"id": "A#b", "status": "FAIL", "message": "m"},
                     {"id": "A#c", "status": "FAIL", "message": "flaky, not kept"}]}
    c = o.compare_tests(["A#a", "A#b"], res)
    assert c["ran"] == 2 and c["failed"] == 1 and c["failures"][0]["id"] == "A#b"
    assert o.compare_tests(["A#a"], None)["available"] is False


JMH = """# JMH version: 1.37
# Benchmark: org.jctools.jmh.QueueBench.offerPoll
# Parameters: (qType = MpscArrayQueue, size = 128)

# Run progress: 0.00% complete
Secondary result "org.jctools.jmh.QueueBench.offerPoll:stack":
Stack profiler:

....[Thread state distributions]....................................................................
 98.0%         RUNNABLE

....[Thread state: RUNNABLE]........................................................................
 40.0%  40.8% org.jctools.queues.MpscArrayQueue.offer
              org.jctools.jmh.QueueBench.offerPoll
 20.0%  20.4% org.jctools.queues.MpscArrayQueue.poll
              org.jctools.jmh.QueueBench.offerPoll
  0.5%   0.5% org.jctools.queues.MpscArrayQueue$1.run

....[Thread state: TIMED_WAITING]...................................................................
  2.0% 100.0% java.lang.Object.wait
# Benchmark: org.jctools.jmh.QueueBench.offerPoll
# Parameters: (qType = SpscArrayQueue, size = 128)
....[Thread state: RUNNABLE]........................................................................
 55.0%  55.0% org.jctools.queues.SpscArrayQueue.offer
"""


def test_parse_jmh_runs_and_frames():
    runs = o.parse_jmh_runs(JMH)
    assert len(runs) == 2
    bench, params, groups = runs[0]
    assert bench.endswith("offerPoll") and params == {"qType": "MpscArrayQueue", "size": "128"}
    assert groups[0][0] == 40.0 and groups[0][1][1] == "org.jctools.jmh.QueueBench.offerPoll"
    assert all("Object.wait" not in f for g in groups for f in g[1])
    assert o.frame_class_method("org.a.B$C.m") == ("org.a.B$C", "m")
    assert o.frame_class_method("org.a.B$1.run") is None
    assert o.frame_class_method("org.a.B.lambda$x$0") is None


def test_list_benchmarks():
    assert o.list_benchmarks("Benchmarks: \norg.a.B.x\norg.a.B.y\n") == ["org.a.B.x", "org.a.B.y"]


def test_contract_tags():
    cls = "class Bag implements Comparable<Bag> { public boolean equals(Object o) {return true;} " \
          "public int hashCode(){return 1;} }"
    t = o.contract_tags(cls, "public List<Integer> view() { return Collections.unmodifiableList(items); }",
                        "public List<Integer> view()", False, False)
    assert t["T_EQUALS"] and t["T_COMPARABLE"] and t["T_COPY"] and t["T_COLLECTION_RETURN"] and not t["T_SYNC"]


def test_normalise_sig_ignores_names_and_final():
    assert o.normalise_sig("int,java.util.List<String>") == o.normalise_sig("int, java.util.List<String>")
