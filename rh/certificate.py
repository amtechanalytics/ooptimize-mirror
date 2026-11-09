"""Generation certificate: certificate/generation_certificate.md holds the failure-mode register.
Row format (markdown table):  | ID | Group | Failure mode | Guarantee | Evidence / test |
Guarantee is one of G-SANDBOX, G-STATIC, PREP (Mac prep report, online), PILOT, CANARY.
Every PILOT row must have pilot/test_<ID>_*.py and every pilot test file must have a PILOT row.
  python -m rh.certificate check
"""
from __future__ import annotations

import re
import sys

from .util import ROOT

PATH = ROOT / "certificate" / "generation_certificate.md"
LEVELS = {"G-SANDBOX", "G-STATIC", "PREP", "PILOT", "CANARY"}
ROW = re.compile(r"^\|\s*([A-Z]\d{2,3})\s*\|([^|]*)\|([^|]*)\|\s*([A-Z-]+)\s*\|(.*)\|\s*$")


def rows() -> list[dict]:
    out = []
    if not PATH.exists():
        return out
    for line in PATH.read_text().splitlines():
        m = ROW.match(line)
        if m:
            out.append({"id": m.group(1), "group": m.group(2).strip(), "mode": m.group(3).strip(),
                        "level": m.group(4).strip(), "evidence": m.group(5).strip()})
    return out


def check() -> list[str]:
    errs = []
    rs = rows()
    if not rs:
        return [f"{PATH.relative_to(ROOT)} missing or has no register rows"]
    ids = [r["id"] for r in rs]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        errs.append(f"duplicate certificate ids {sorted(dup)}")
    for r in rs:
        if r["level"] not in LEVELS:
            errs.append(f"{r['id']}: guarantee '{r['level']}' not in {sorted(LEVELS)}")
        if r["level"] in ("G-SANDBOX", "G-STATIC") and len(r["evidence"]) < 8:
            errs.append(f"{r['id']}: {r['level']} row has no evidence")
    pilot_ids = {r["id"] for r in rs if r["level"] == "PILOT"}
    test_ids = set()
    for p in (ROOT / "pilot").glob("test_*.py"):
        m = re.match(r"test_([A-Z]\d{2,3})_", p.name)
        if not m:
            errs.append(f"pilot/{p.name}: name must be test_<ID>_<what>.py")
            continue
        test_ids.add(m.group(1))
    for i in sorted(pilot_ids - test_ids):
        errs.append(f"{i}: PILOT row has no pilot/test_{i}_*.py")
    for i in sorted(test_ids - pilot_ids):
        errs.append(f"pilot test for {i} has no PILOT row in the certificate")
    if "## Verdict" not in PATH.read_text():
        errs.append("certificate has no '## Verdict' section")
    return errs


if __name__ == "__main__":
    e = check()
    for x in e:
        print("FAIL", x)
    print("CERTIFICATE PASS" if not e else f"CERTIFICATE FAIL ({len(e)})")
    sys.exit(1 if e else 0)
