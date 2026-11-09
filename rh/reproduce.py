"""Re-run the analysis from the pulled runs/ in a scratch copy and compare every results file.
  python -m rh.reproduce        -> 'REPRODUCE PASS' or 'REPRODUCE FAIL: <files>'
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .util import ROOT


def _digest(d: Path) -> dict:
    return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(d.rglob("*")) if p.is_file() and p.suffix in (".csv", ".json", ".txt", ".tsv")}


def main() -> int:
    if not (ROOT / "runs" / "full").exists() or not (ROOT / "results").exists():
        print("REPRODUCE FAIL: runs/full or results/ missing (run aws-pull.sh first)")
        return 1
    tmp = Path(tempfile.mkdtemp(prefix="rh-repro-")) / ROOT.name
    shutil.copytree(ROOT, tmp, ignore=shutil.ignore_patterns(".git", ".venv", "results", "__pycache__", ".fake_aws"))
    code = ("from rh.context import Context\n"
            "ctx = Context(mode='full')\n"
            "ctx.paper.analyze(ctx)\n")
    env = dict(os.environ, RH_ROOT=str(tmp))
    r = subprocess.run([sys.executable, "-c", code], cwd=tmp, env=env, capture_output=True, text=True)
    if r.returncode != 0:
        print("REPRODUCE FAIL: analysis raised\n" + r.stderr[-3000:])
        return 1
    a, b = _digest(ROOT / "results"), _digest(tmp / "results")
    diff = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
    if diff:
        print("REPRODUCE FAIL: differing files " + ", ".join(diff[:20]))
        return 1
    print(f"REPRODUCE PASS ({len(a)} result files identical)")
    shutil.rmtree(tmp.parent, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
