"""A02: every configured model id is offered to the key (catches typos and retired ids)."""
from rh import vendors


def test_ids(cfg):
    for m in cfg.models:
        if vendors.is_fake(m):
            continue
        ids = set(vendors.list_models(m["provider"]))
        assert m["id"] in ids, f"{m['id']} not in {m['provider']} model list"
