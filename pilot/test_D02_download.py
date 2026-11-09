"""D02 (+D05, D06): every pinned dataset downloaded and verified in the datacheck rung — tables: size + sha256;
git pins: exact commit checked out; binary pins: size + sha256 (+ host checksum when pinned)."""
import json


def test_datacheck_all_ok(runs, cfg):
    rep = json.loads((runs / "datacheck.json").read_text())
    names = {p["name"] for p in cfg.pins}
    got = {r["name"]: r for r in rep["pins"]}
    assert names <= set(got), f"pins not checked: {names - set(got)}"
    for n in names:
        r = got[n]
        assert r["ok"], r.get("error")
        assert r["checks"].get("downloaded") and r["checks"].get("size") and r["checks"].get("sha256")
        if r.get("format") != "git":        # git pins are verified by exact commit, they have no size
            assert r["size"] > 0
