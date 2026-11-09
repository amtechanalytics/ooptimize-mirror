"""E01: the pinned JDK 21, Maven, Randoop, SpotBugs and EqualsVerifier run on the instance; the helpers compile."""
from _oopt import rj, scope
from conftest import fake


def test_toolchain(runs):
    d = rj(scope(runs) / "toolchain.json")
    assert fake() or "21" in d["java"], d["java"]
    assert d["javac"] and d["mvn"], d
    assert d["randoop"] and d["spotbugs"] and d["equalsverifier"], d
    assert not d["helper_errors"], d["helper_errors"]
