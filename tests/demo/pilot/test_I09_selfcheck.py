"""I09: the self-check the repair step must pass (unit tests + dry run in a scratch copy) passes on this instance,
from the GitHub clone, with the live run's environment. If it cannot pass here, no repair can ever be committed."""
import os
import subprocess
import sys

import pytest

from conftest import ROOT, fake


def test_selfcheck_passes_on_instance():
    if fake():
        pytest.skip("dry run: the self-check itself runs this dry run")
    env = dict(os.environ, PYTHON=sys.executable)
    r = subprocess.run(["bash", "tools/selfcheck.sh"], cwd=ROOT, env=env, capture_output=True, text=True,
                       timeout=1800)
    tail = (r.stdout + r.stderr)[-1500:]
    assert r.returncode == 0 and "SELFCHECK PASS" in r.stdout, tail
