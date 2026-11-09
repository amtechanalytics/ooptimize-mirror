"""S07: the notification topic accepts a message with the '[codename] KIND | state' subject."""
import json

from rh import notify


def test_mail(runs):
    notify.send("TEST", "pilot mail check", "If you can read this, run notifications work.")
    last = [json.loads(x) for x in (runs / "mail.jsonl").read_text().splitlines()][-1]
    assert "error" not in last, last.get("error")
    assert last["subject"].endswith("TEST | pilot mail check")
