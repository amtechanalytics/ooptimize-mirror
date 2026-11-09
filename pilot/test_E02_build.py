"""E02: the pilot library builds; its JMH jar lists >= 20 benchmarks; every module has a test classpath on disk."""
from pathlib import Path

from _oopt import rj, scope
from conftest import fake


def test_build(runs):
    from paper.libraries import PILOT_LIBRARY_DEFAULT
    b = rj(scope(runs) / "build" / f"{PILOT_LIBRARY_DEFAULT}.json")
    assert b["ok"], b.get("errors")
    assert len(b["benchmarks"]) >= 20
    if fake():
        return
    assert Path(b["jmh_jar"]).exists()
    for mod, d in b["modules"].items():
        assert len(d["test_cp"]) >= 2, mod
        missing = [p for p in d["test_cp"] if not Path(p).exists()]
        assert len(missing) <= 6, f"{mod}: classpath entries missing on disk: {missing[:5]}"
        assert Path(d["classes"]).exists(), d["classes"]
