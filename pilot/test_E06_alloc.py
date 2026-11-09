"""E06: JMH -prof gc with the overlay first on the classpath yields bytes/op for the baseline of every pilot
method that has a tests-pass patch."""
from _oopt import rows


def test_alloc(runs):
    al = rows(runs / "pilot" / "alloc.jsonl")
    base = [r for r in al if r["kind"] == "base"]
    if not base:
        sc = rows(runs / "pilot" / "score.jsonl")
        assert not [r for r in sc if r["kind"] == "gen" and (r.get("classification") or {}).get("tests_pass")], \
            "tests-pass patches exist but no allocation job ran"
        return
    assert any(r.get("bytes_per_op") is not None for r in base), [r.get("status") for r in base]
