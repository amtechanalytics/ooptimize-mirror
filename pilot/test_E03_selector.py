"""E03: the selector found >= 3 hot methods in the pilot library, each resolved to exactly its source text."""
from pathlib import Path

from _oopt import rj, scope
from conftest import fake


def test_selection(runs):
    from paper.libraries import PILOT_LIBRARY_DEFAULT
    s = rj(scope(runs) / "select" / f"{PILOT_LIBRARY_DEFAULT}.json")
    ms = s["methods"]
    assert len(ms) >= 3, s.get("stats")
    for m in ms:
        assert m["share"] >= 1.0 and m["bench"] and isinstance(m["tags"], dict)
        assert m["src"].rstrip().endswith("}") and m["name"] in m["src"]
        assert 3 <= m["line_end"] - m["line_start"] + 1 <= 120
        assert m["ctx_r2"] and m["ctx_r3"] is not None
        if not fake():
            b = rj(scope(runs) / "build" / f"{PILOT_LIBRARY_DEFAULT}.json")
            text = (Path(b["tree"]) / m["file"]).read_text(errors="ignore")
            assert text[m["start"]:m["end"]] == m["src"]
