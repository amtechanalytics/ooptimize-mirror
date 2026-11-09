"""Demo study (replace per paper): classify requirement sentences into four quality categories
under two prompting arms (zero-shot, few-shot) for every model in config/models.yaml, then report
accuracy per arm x model. It exercises every harness path: pinned data, sync and batch calls,
prompt-cache-friendly prefixes, schema parsing, gates, analysis, audit sheet and brief."""
from __future__ import annotations

import csv
import hashlib
import json

from rh import datapins
from rh.context import Stage
from rh.util import RESULTS, RUNS, write_json

LABELS = ["functional", "performance", "security", "usability"]

_INSTRUCTIONS = (
    "You are a requirements analyst. Classify the requirement sentence given by the user into exactly one "
    "category.\n"
    "Categories:\n"
    "- functional: a behaviour or capability the system must provide.\n"
    "- performance: speed, latency, throughput, capacity or resource-use targets.\n"
    "- security: confidentiality, integrity, authentication, authorisation, auditing or privacy.\n"
    "- usability: ease of use, learnability, accessibility or user guidance.\n"
    "Answer with a single JSON object and nothing else, of the form "
    '{"label": "<one of functional|performance|security|usability>", "confidence": <number between 0 and 1>}.\n'
    "The confidence is your probability that the label is correct. Do not add explanations, markdown or code "
    "fences. If the sentence mixes categories, choose the category of its primary obligation.\n"
)
_SHOTS = (
    "\nExamples:\n"
    'Sentence: "Users shall be able to change their delivery address before dispatch." -> '
    '{"label": "functional", "confidence": 0.95}\n'
    'Sentence: "The checkout page shall render within 800 ms at the 95th percentile." -> '
    '{"label": "performance", "confidence": 0.95}\n'
    'Sentence: "Card numbers shall be tokenised and never stored in clear text." -> '
    '{"label": "security", "confidence": 0.95}\n'
    'Sentence: "First-time users shall find the search box without instructions." -> '
    '{"label": "usability", "confidence": 0.9}\n'
)
# Stable prefixes: byte-identical across every call of an arm, so vendor prompt caching applies.
PREFIX = {"zero": _INSTRUCTIONS, "few": _INSTRUCTIONS + _SHOTS}
ARMS = ("zero", "few")


def _rows():
    with open(datapins.path("requirements"), newline="") as f:
        return list(csv.DictReader(f))


def build_label(ctx) -> list[dict]:
    cells = []
    for m in ctx.cfg.models:
        for arm in ARMS:
            for r in _rows():
                cells.append({"cell_id": f"label|{arm}|{m['id']}|{r['id']}", "model": m["id"], "arm": arm,
                              "prefix": PREFIX[arm], "item": f'Sentence: "{r["text"]}"',
                              "meta": {"gold": r["label"], "id": r["id"]}})
    return cells


def parse_label(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`").split("\n", 1)[-1]
    d = json.loads(t)
    if d.get("label") not in LABELS:
        raise ValueError(f"label {d.get('label')!r} not in {LABELS}")
    c = float(d.get("confidence"))
    if not 0.0 <= c <= 1.0:
        raise ValueError(f"confidence {c} outside [0,1]")
    return {"label": d["label"], "confidence": c}


def aggregate(ctx) -> dict:
    acc = {}
    for r in ctx.stage_rows("label"):
        if r["status"] != "ok":
            continue
        k = f"{r['arm']}|{r['model']}"
        a = acc.setdefault(k, [0, 0])
        a[0] += int(r["parsed"]["label"] == r["meta"]["gold"])
        a[1] += 1
    table = {k: {"correct": c, "n": n, "accuracy": round(c / n, 4) if n else None} for k, (c, n) in acc.items()}
    write_json(RUNS / ctx.mode / "aggregate.json", table)
    return {"groups": len(table), "rows": sum(v["n"] for v in table.values())}


def _aggregate_check(ctx, metrics):
    return [] if metrics.get("rows", 0) > 0 else ["aggregate saw zero ok rows"]


STAGES = [
    Stage("label", "llm", build=build_label, schema=parse_label,
          gate={"min_rows": 150, "min_parse_rate": 0.9, "max_duplicate_cells": 0}),
    Stage("aggregate", "local", run=aggregate, gate={"check": _aggregate_check}),
]


def analyze(ctx) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    rows = ctx.stage_rows("label", "full")
    table = {}
    for r in rows:
        if r["status"] != "ok":
            continue
        k = (r["arm"], r["model"])
        t = table.setdefault(k, [0, 0])
        t[0] += int(r["parsed"]["label"] == r["meta"]["gold"])
        t[1] += 1
    with open(RESULTS / "t1_accuracy.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["arm", "model", "correct", "n", "accuracy"])
        for (arm, model), (c, n) in sorted(table.items()):
            w.writerow([arm, model, c, n, f"{c / n:.4f}"])
    # audit sheet: a deterministic sample of disagreements and agreements for a human to check
    sample = sorted(rows, key=lambda r: hashlib.sha1(r["cell_id"].encode()).hexdigest())[:20]
    with open(RESULTS / "audit_sample.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["cell_id", "text_id", "gold", "predicted", "status", "auditor_label", "note"])
        for r in sample:
            w.writerow([r["cell_id"], r["meta"]["id"], r["meta"]["gold"],
                        (r.get("parsed") or {}).get("label", ""), r["status"], "", ""])


def brief(ctx) -> list[str]:
    return ["Audit results/audit_sample.csv: fill auditor_label for 20 rows, then attach it in the chat.",
            "Check results/t1_accuracy.csv against the hypotheses in the approach doc."]


def fake_response(model_id: str, prefix: str, item: str) -> str:
    """Deterministic stand-in for a vendor reply (dry runs only)."""
    h = int(hashlib.sha1((model_id + item).encode()).hexdigest(), 16)
    text = item.lower()
    guess = ("performance" if any(w in text for w in ("second", "ms", "latency", "within", "concurrent", "mb"))
             else "security" if any(w in text for w in ("password", "encrypt", "auth", "log", "token", "malware"))
             else "usability" if any(w in text for w in ("user", "keyboard", "screen", "help", "click", "plain"))
             else "functional")
    if h % 10 == 0:
        guess = LABELS[h % 4]
    return json.dumps({"label": guess, "confidence": round(0.6 + (h % 40) / 100, 2)})
