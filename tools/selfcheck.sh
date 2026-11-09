#!/usr/bin/env bash
# Run the unit tests and a dry run in a scratch copy of the repo (never touches runs/).
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-.venv/bin/python}"
[ -x "$PY" ] || PY=python3
"$PY" -m pytest tests -q -x -p no:cacheprovider
"$PY" - <<'PYEOF'
from rh import prep
code, tmp = prep.dry_run_copy()
print("SELFCHECK PASS" if code == 0 else f"SELFCHECK FAIL (dry-run exit {code}, log {tmp}/dryrun.log)")
raise SystemExit(0 if code == 0 else 1)
PYEOF
