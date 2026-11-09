"""Dataset verification catches every failure class before any paid call."""
import json
import zipfile

import pytest

from rh import datapins
from rh.exitcodes import DataFailed
from rh.util import ROOT

BASE = json.loads((ROOT / "config" / "data_pins.fixture.json").read_text())["pins"][0]


def _pin(**kw):
    p = dict(BASE)
    p.update(kw)
    return p


def test_fixture_verifies():
    reps = datapins.verify_all([_pin()])
    assert reps[0]["ok"] and reps[0]["rows"] == 40
    assert datapins.path("requirements").exists()


@pytest.mark.parametrize("kw,needle", [
    ({"sha256": "0" * 64}, "sha256"),
    ({"size": 1}, "size"),
    ({"columns": ["id", "nope"]}, "missing columns"),
    ({"min_rows": 1000}, "min_rows"),
    ({"expected_rows": 39}, "expected_rows"),
    ({"encoding": "utf-32"}, "decodable"),
    ({"url": "file://tests/fixtures/does-not-exist.csv", "file": "x.csv", "size": None}, "FileNotFoundError"),
])
def test_failures(kw, needle):
    with pytest.raises(DataFailed, match=needle):
        datapins.verify_all([_pin(**kw)])


def test_duplicate_keys_and_zip_member(tmp_path):
    csvp = tmp_path / "d.csv"
    csvp.write_text("id,text,label\na,x,y\na,x,y\n")
    with pytest.raises(DataFailed, match="duplicate"):
        datapins.verify_all([_pin(url=f"file://{csvp}", file="d.csv", size=None, sha256=None, min_rows=2,
                                  expected_rows=2, name="dups")])
    z = tmp_path / "d.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("inner/data.csv", "id,text,label\na,x,y\nb,x,y\n")
    with pytest.raises(DataFailed, match="zip member"):
        datapins.verify_all([_pin(url=f"file://{z}", file="d.zip", size=None, sha256=None, member="wrong.csv",
                                  min_rows=2, expected_rows=2, name="zipped")])
    ok = datapins.verify_all([_pin(url=f"file://{z}", file="d.zip", size=None, sha256=None,
                                   member="inner/data.csv", min_rows=2, expected_rows=2, name="zipped2")])
    assert ok[0]["ok"]


def test_empty_pin_list_fails():
    with pytest.raises(DataFailed):
        datapins.verify_all([])
