"""A01: each provider key authenticates ($0 models-list call)."""
from rh import vendors


def test_keys(cfg):
    for prov in sorted({m["provider"] for m in cfg.models}):
        if vendors.is_fake({"provider": prov, "id": "x"}):
            prov = "fake"
        assert vendors.list_models(prov), f"{prov}: empty model list"
