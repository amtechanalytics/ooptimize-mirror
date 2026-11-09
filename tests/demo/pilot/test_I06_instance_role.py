"""I06: the instance runs under its own instance role (not a user key)."""
import json
import subprocess

import pytest

from conftest import fake


def test_role(cfg):
    if fake():
        pytest.skip("dry run")
    out = subprocess.run(["aws", "sts", "get-caller-identity", "--output", "json"], capture_output=True,
                         text=True, check=True).stdout
    arn = json.loads(out)["Arn"]
    assert f"research-{cfg.codename}-instance" in arn, arn
