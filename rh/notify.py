"""Notifications. Every subject is '[<codename>] <KIND> | <state>'.
  python -m rh.notify status     # the 30-minute status mail (systemd timer)
  python -m rh.notify sweep      # RESOURCE-24H: tagged resources alive > 24 h (hourly timer)
  python -m rh.notify send KIND "state" "body"
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone

import yaml

from . import aws, heartbeat
from .exitcodes import meaning
from .util import ROOT, RUNS, append_jsonl, now_iso, read_json, read_jsonl, write_json

KINDS = {"STATUS-30MIN", "PILOT", "CANARY", "DONE", "DEAD", "STALLED", "BUDGET", "KILL", "RESOURCE-24H",
         "BACKUP-FAIL", "TEST"}
LADDER = ["datacheck", "pilot", "canary", "full", "analysis", "finish"]


def _project() -> dict:
    with open(ROOT / "config" / "project.yaml") as f:
        return yaml.safe_load(f)


def topic() -> str:
    return os.environ.get("RH_TOPIC_ARN") or _project().get("topic_arn", "")


def send(kind: str, state: str, body: str, codename: str | None = None) -> None:
    assert kind in KINDS, kind
    cn = codename or _project()["codename"]
    subject = f"[{cn}] {kind} | {state}"
    try:
        aws.sns_publish(topic(), subject, body)
        append_jsonl(RUNS / "mail.jsonl", {"ts": now_iso(), "subject": subject})
    except Exception as e:  # noqa: BLE001 — a failed mail must never crash the caller
        append_jsonl(RUNS / "mail.jsonl", {"ts": now_iso(), "subject": subject, "error": str(e)[:300]})


def _fmt_dur(sec: float | None) -> str:
    if sec is None or sec < 0:
        return "n/a"
    h, rem = divmod(int(sec), 3600)
    return f"{h}h{rem // 60:02d}m"


def _parse_ts(s: str | None) -> float | None:
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def full_progress() -> tuple[int, int, dict]:
    plan = read_json(RUNS / "plan.json", default=None)
    if not plan:
        return 0, 0, {}
    per, done_all = {}, 0
    for st, info in plan["stages"].items():
        rows = {r["cell_id"]: r for r in read_jsonl(RUNS / "full" / f"{st}.jsonl")}
        d = sum(1 for r in rows.values() if r["status"] in ("ok", "parse_fail"))
        per[st] = (d, info["cells"])
        done_all += d
    return done_all, plan["total_cells"], per


def status_body(consume: bool = True) -> tuple[str, str]:
    p = _project()
    hb = heartbeat.read()
    state = read_json(RUNS / "state.json", default={}) or {}
    spend = read_json(RUNS / "spend.json", default={}) or {}
    exitinfo = read_json(RUNS / "exit.json", default=None)
    with open(ROOT / "config" / "budget.yaml") as f:
        budget = yaml.safe_load(f)
    ceiling = budget["ceilings"][budget["tier"]]
    started = _parse_ts(hb.get("started"))
    elapsed = time.time() - started if started else None
    done, total, per = full_progress()
    hist = list(read_jsonl(RUNS / "progress_hist.jsonl"))
    if consume:
        append_jsonl(RUNS / "progress_hist.jsonl", {"ts": time.time(), "done": done})
    eta = None
    if hist and total and done > hist[-1]["done"]:
        rate = (done - hist[-1]["done"]) / max(time.time() - hist[-1]["ts"], 1)
        eta = (total - done) / rate
    rung = state.get("current") or "-"
    pct = f"{100 * done / total:.0f}%" if total else "-"
    total_spend = float(spend.get("total", 0.0))
    if exitinfo and exitinfo.get("code") == 0:
        headline = f"finished · ${total_spend:.2f}/${ceiling}"
    elif exitinfo and exitinfo.get("code") is not None:
        headline = f"{rung} stopped: {meaning(exitinfo['code'])}"
    else:
        headline = f"{rung}/{hb.get('stage', '-')} {pct} · ${total_spend:.2f}/${ceiling} · ETA {_fmt_dur(eta)}"

    ladder = " · ".join(f"{r}{' ✓' if r in state.get('done', []) else (' (running)' if r == rung else '')}"
                        for r in LADDER)
    lines = [
        f"Run {os.environ.get('RH_RUN_ID', state.get('run_id', '-'))} · instance "
        f"{os.environ.get('RH_INSTANCE_ID', 'local')} · tier {budget['tier']} (${ceiling})",
        f"Elapsed {_fmt_dur(elapsed)} · expected {p.get('expected_hours')}h · wall "
        f"{os.environ.get('RH_MAX_HOURS', p.get('max_hours', '-'))}h",
        f"Ladder: {ladder}",
        "",
        "PROGRESS AGAINST THE ORIGINAL PLAN (full run)",
    ]
    if per:
        for st, (d, t) in per.items():
            lines.append(f"  {st}: {d}/{t} ({100 * d / t if t else 0:.0f}%)")
        lines.append(f"  overall: {done}/{total} ({pct}); ETA {_fmt_dur(eta)}")
    else:
        lines.append("  full run not started yet (plan.json is written when the full rung begins)")
    try:
        from . import compute, config as _config
        _cfg = _config.load(ROOT)
        comp_now = compute.so_far(_cfg, hb.get("started"))
    except Exception:  # noqa: BLE001
        comp_now = 0.0
    lines += ["", "SPEND",
              f"  API ${total_spend:.2f} + compute ~${comp_now:.2f} = ${total_spend + comp_now:.2f} of ${ceiling}"
              + (f"; projected at completion ${total_spend * total / done:.2f}" if done and total else ""),
              f"  by provider: {spend.get('by_provider', {})}",
              f"  executor: ${spend.get('by_kind', {}).get('executor', 0.0):.2f} of ${budget['executor_ceiling']}",
              "", "LANES"]
    now = time.time()
    for lane, ln in (hb.get("lanes") or {}).items():
        lines.append(f"  {lane}: {ln.get('done', 0)} done, last progress {_fmt_dur(now - ln.get('last_ts', now))} ago")
    lines.append(f"  parse rates: {hb.get('parse_rates', {})}")
    lb = read_json(RUNS / "last_backup.json", default={}) or {}
    lines.append(f"  last backup: {lb.get('ts', 'never')}"
                 + (f" ({_fmt_dur(now - lb['epoch'])} ago)" if lb.get("epoch") else ""))

    # issues since the previous status mail, and whether they were auto-fixed
    marker = read_json(RUNS / "mail_marker.json", default={}) or {}
    since = marker.get("journal_offset", 0)
    jpath = RUNS / "journal.txt"
    jtext = jpath.read_text() if jpath.exists() else ""
    new = jtext[since:].splitlines()
    if consume:
        write_json(RUNS / "mail_marker.json", {"journal_offset": len(jtext), "ts": now_iso()})
    issue_kinds = ("ERROR", "RESTART", "STALL", "EXECUTOR", "FIX", "VENDOR", "BUDGET", "KILL", "DATA", "GATE")
    def _is_issue(ln: str) -> bool:
        parts = [p.strip() for p in ln.split("|", 2)]
        if len(parts) < 3 or parts[1] not in issue_kinds:
            return False
        return not (parts[2].endswith("PASS") or ": OK " in parts[2] + " ")
    issues = [ln for ln in new if _is_issue(ln)]
    lines += ["", f"ISSUES SINCE LAST MAIL ({len(issues)})"]
    lines += [f"  {ln}" for ln in issues[-25:]] or ["  none"]
    fixed = [ln for ln in new if "| FIX" in ln or ("| RESTART" in ln and "resumed" in ln)]
    lines += ["", "AUTO-FIXED"] + ([f"  {ln}" for ln in fixed[-10:]] or ["  none"])
    needs = read_json(RUNS / "needs_attention.json", default=[]) or []
    if exitinfo and exitinfo.get("code") not in (0, None):
        needs = needs + [f"run stopped: {meaning(exitinfo['code'])} — reply in the paper's chat with this mail"]
    lines += ["", "NEEDS YOUR ATTENTION"] + ([f"  - {n}" for n in needs] or ["  nothing"])
    return headline, "\n".join(lines)


def status() -> None:
    headline, body = status_body()
    send("STATUS-30MIN", headline, body)


def sweep() -> None:
    old = []
    now = time.time()
    for inst in aws.tagged_instances():
        launch = inst.get("launch")
        try:
            t = datetime.fromisoformat(launch.replace("Z", "+00:00")).timestamp()
        except Exception:  # noqa: BLE001
            continue
        if now - t > 24 * 3600:
            tags = {d["Key"]: d["Value"] for d in (inst.get("tags") or [])}
            old.append(f"{inst['id']} {inst.get('type')} {inst.get('state')} paper={tags.get('paper', '?')} "
                       f"up {_fmt_dur(now - t)}")
    if old:
        send("RESOURCE-24H", f"{len(old)} tagged instance(s) alive > 24h",
             "Resources tagged mode=auto-unattended older than 24 hours:\n" + "\n".join(old)
             + "\n\nFrom the Mac: bash _tools/sweep.sh   (or kill.sh <codename>)", codename="all")


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "status"
    if cmd == "status":
        status()
    elif cmd == "sweep":
        sweep()
    elif cmd == "send":
        send(argv[1], argv[2], argv[3] if len(argv) > 3 else "")
    elif cmd == "print-status":
        h, b = status_body(consume=False)
        print(h)
        print(b)
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
