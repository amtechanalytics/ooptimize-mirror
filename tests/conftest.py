"""Unit tests run against a throwaway copy of the repo so runs/ in the real tree is never touched."""
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_TMP = Path(tempfile.mkdtemp(prefix="rh-tests-"))
COPY = _TMP / REPO.name
shutil.copytree(REPO, COPY, ignore=shutil.ignore_patterns(".git", ".venv", "runs", "results", "logs", ".data",
                                                          ".fake_aws", "__pycache__", "data"))
# The harness tests always run against the frozen demo study in tests/demo (config, paper, certificate, pilot), whatever study
# the repository itself holds, so a paper's own config and code never break the harness tests.
for sub in ("config", "paper", "certificate", "pilot"):
    shutil.rmtree(COPY / sub, ignore_errors=True)
    shutil.copytree(REPO / "tests" / "demo" / sub, COPY / sub)
# start from a clean slate: an instance exports RH_BOOT_TS, RH_RUN_ID, RH_MAX_HOURS, ... which must not leak into tests
for _k in [k for k in os.environ if k.startswith("RH_")]:
    os.environ.pop(_k)
os.environ["RH_ROOT"] = str(COPY)
os.environ["RH_FAKE_AWS"] = "1"
os.environ["RH_FAKE_VENDOR"] = "1"
os.environ["RH_FAKE_AWS_DIR"] = str(COPY / ".fake_aws")
os.environ.pop("RH_ON_INSTANCE", None)
os.chdir(COPY)
sys.path.insert(0, str(COPY))

# tests/demo holds frozen study files (including pilot tests that only run on an instance); never collect them here.
collect_ignore = ["demo"]
