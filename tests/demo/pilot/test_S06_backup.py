"""S06: runs/ results/ logs/ sync to S3 and the sync time is recorded."""
import json

from rh import backup


def test_backup(runs):
    assert backup.sync() is True
    assert json.loads((runs / "last_backup.json").read_text())["prefix"].startswith("s3://")
