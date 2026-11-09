"""Pilot tests run on the instance after the pilot stages (and in dry runs against the fakes).
Each file is named after its row in certificate/generation_certificate.md."""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("RH_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def cfg():
    from rh import config
    return config.load(ROOT, pins_file="data_pins.fixture.json" if os.environ.get("RH_FAKE_AWS") else None)


@pytest.fixture(scope="session")
def runs():
    return ROOT / "runs"


def fake() -> bool:
    return os.environ.get("RH_FAKE_VENDOR", "") == "1"


def read_jsonl(p):
    if not Path(p).exists():
        return []
    return [json.loads(x) for x in Path(p).read_text().splitlines() if x.strip()]
