"""E09: the scoring recipe and the real stage code on the toy library (tests/test_paper_java_pipeline.py,
tests/test_paper_stages_real.py) pass with the pinned JDK, Randoop, SpotBugs and EqualsVerifier on the instance
(in the sandbox they ran with the sandbox JDK and with the pinned Temurin)."""
import os
import subprocess
import sys

import pytest

from conftest import ROOT, fake


def test_toy_pipeline():
    if fake():
        pytest.skip("dry run: no JDK")
    from paper import env
    t = env.tools()
    e = dict(os.environ, OOPT_TEST_JDK=str(t.java_home), OOPT_TEST_RANDOOP=str(t.randoop),
             OOPT_TEST_SPOTBUGS=str(t.spotbugs), OOPT_TEST_EV=str(t.ev))
    for k in ("RH_ROOT", "RH_MODE"):
        e.pop(k, None)
    r = subprocess.run([sys.executable, "-m", "pytest", "tests/test_paper_java_pipeline.py",
                        "tests/test_paper_stages_real.py", "-q", "-p", "no:cacheprovider"], cwd=ROOT, env=e, capture_output=True, text=True, timeout=1800)
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    assert " skipped" not in r.stdout.splitlines()[-1], r.stdout[-500:]
