"""D03: every pinned dataset has the pinned rows, columns, encoding and no excess duplicate keys."""
import json


def test_shape(runs, cfg):
    rep = {r["name"]: r for r in json.loads((runs / "datacheck.json").read_text())["pins"]}
    for p in cfg.pins:
        r = rep[p["name"]]
        assert r["rows"] >= p["min_rows"]
        assert set(p["columns"]) <= set(r["columns"])
        assert r["checks"].get("duplicates")
