"""Load and validate every config file. Validation errors are collected, never raised one by one,
so the prep report can list all of them at once."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .util import ROOT

PLACEHOLDER = re.compile(r"REPLACE|TODO|TBD|XXX", re.I)
PROVIDERS = {"anthropic", "openai", "groq", "xai", "fake"}
BATCH_PROVIDERS = {"anthropic", "openai", "fake"}
CODENAME_RE = re.compile(r"^[a-z][a-z0-9-]{2,30}$")


def _load_yaml(p: Path):
    with open(p) as f:
        return yaml.safe_load(f) or {}


def _scan_placeholders(obj, path="", out=None):
    out = [] if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            _scan_placeholders(v, f"{path}.{k}" if path else str(k), out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _scan_placeholders(v, f"{path}[{i}]", out)
    elif isinstance(obj, str) and PLACEHOLDER.search(obj):
        out.append(path)
    return out


@dataclass
class Config:
    root: Path
    project: dict
    models: list
    prices: dict
    budget: dict
    pins: list
    gates: dict
    plan: dict
    compute: dict
    iam_required: dict
    errors: list = field(default_factory=list)

    @property
    def codename(self) -> str:
        return self.project.get("codename", "")

    def model(self, model_id: str) -> dict:
        for m in self.models:
            if m["id"] == model_id:
                return m
        raise KeyError(model_id)

    def ceiling(self) -> float:
        tier = self.budget.get("tier", "standard")
        return float(self.budget["ceilings"][tier])


def load(root: Path | None = None, pins_file: str | None = None) -> Config:
    root = Path(root or ROOT)
    c = root / "config"
    errors: list[str] = []

    def get(name, loader=_load_yaml, default=None):
        p = c / name
        if not p.exists():
            errors.append(f"missing config/{name}")
            return default
        try:
            return loader(p)
        except Exception as e:  # noqa: BLE001
            errors.append(f"config/{name} unreadable: {e}")
            return default

    project = get("project.yaml", default={}) or {}
    models = (get("models.yaml", default={}) or {}).get("models", [])
    prices = get("prices.yaml", default={}) or {}
    budget = get("budget.yaml", default={}) or {}
    pins_name = pins_file or "data_pins.json"
    pins = (get(pins_name, loader=lambda p: json.loads(p.read_text()), default={}) or {}).get("pins", [])
    gates = get("gates.yaml", default={}) or {}
    plan = get("plan.yaml", default={}) or {}
    compute = get("compute.yaml", default={}) or {}
    iam_required = get("iam-required.json", loader=lambda p: json.loads(p.read_text()), default={}) or {}

    cfg = Config(root, project, models, prices, budget, pins, gates, plan, compute, iam_required, errors)
    validate(cfg, strict_pins=(pins_file is None))
    return cfg


def validate(cfg: Config, strict_pins: bool = True) -> list[str]:
    e = cfg.errors
    # project
    cn = cfg.codename
    if not CODENAME_RE.match(cn or ""):
        e.append(f"project.codename '{cn}' must be lowercase, 3-31 chars, start with a letter")
    for k in ("codename", "bucket", "region", "expected_hours"):
        if k not in cfg.project:
            e.append(f"project.yaml missing '{k}'")
    # models
    if not cfg.models:
        e.append("models.yaml has no models")
    seen = set()
    for m in cfg.models:
        mid = m.get("id", "")
        if mid in seen:
            e.append(f"models.yaml duplicate id {mid}")
        seen.add(mid)
        if m.get("provider") not in PROVIDERS:
            e.append(f"model {mid}: provider must be one of {sorted(PROVIDERS)}")
        if "temperature" in m or "top_p" in m:
            e.append(f"model {mid}: sampling parameters are not allowed (vendor default only)")
        for k in ("max_output_tokens", "concurrency", "accessed"):
            if k not in m:
                e.append(f"model {mid}: missing '{k}'")
        if mid not in (cfg.prices.get("models") or {}):
            e.append(f"model {mid}: no price in prices.yaml")
        if m.get("batch") and m.get("provider") not in BATCH_PROVIDERS:
            e.append(f"model {mid}: batch is not supported for provider {m.get('provider')} (sync only)")
        for k in ("tpm_budget", "rpm_budget"):
            if k in m and m[k] is not None and (not isinstance(m[k], int) or m[k] < 0):
                e.append(f"model {mid}: {k} must be a non-negative integer")
    # prices
    for mid, p in (cfg.prices.get("models") or {}).items():
        for k in ("input_per_mtok", "output_per_mtok", "source"):
            if k not in p:
                e.append(f"prices.{mid}: missing '{k}'")
    if "as_of" not in cfg.prices:
        e.append("prices.yaml missing 'as_of'")
    comp = cfg.prices.get("compute") or {}
    itype = cfg.compute.get("instance_type")
    if not itype:
        e.append("compute.yaml missing 'instance_type'")
    elif itype not in comp or "per_hour" not in (comp.get(itype) or {}):
        e.append(f"prices.compute has no per_hour rate for instance type {itype}")
    if "gp3_per_gb_month" not in comp:
        e.append("prices.compute missing 'gp3_per_gb_month'")
    # budget
    b = cfg.budget
    tier = b.get("tier")
    if tier not in ("standard", "high"):
        e.append("budget.tier must be 'standard' or 'high'")
    ceilings = b.get("ceilings") or {}
    if ceilings.get("standard") != 150 or ceilings.get("high") != 300:
        e.append("budget.ceilings must be {standard: 150, high: 300}")
    if tier == "high" and not str(b.get("high_approval", "")).strip():
        e.append("budget.tier is 'high' but 'high_approval' is empty (needs the chat approval line)")
    for k in ("executor_ceiling", "pilot_ceiling"):
        if k not in b:
            e.append(f"budget.yaml missing '{k}'")
    # pins
    if strict_pins:
        for i, p in enumerate(cfg.pins):
            for k in ("name", "url", "record", "title", "file", "licence", "format", "min_rows", "columns"):
                if k not in p or p[k] in (None, ""):
                    e.append(f"data_pins[{i}] ({p.get('name', '?')}): missing '{k}'")
            fmt = p.get("format")
            if fmt not in ("csv", "tsv", "jsonl", "json", "git", "binary"):
                e.append(f"data_pins[{i}] format '{fmt}' not in csv, tsv, jsonl, json, git, binary")
            if fmt == "git":
                if not re.fullmatch(r"[0-9a-f]{40}", str(p.get("record", ""))):
                    e.append(f"data_pins[{i}] git record must be a full 40-hex commit")
                if p.get("sha256"):
                    e.append(f"data_pins[{i}] git pins carry no sha256 (the commit is the content address)")
                if not isinstance(p.get("columns"), list):
                    e.append(f"data_pins[{i}] git columns must be a list of required paths")
            if fmt == "binary" and int(p.get("min_rows", 1) or 1) != 1:
                e.append(f"data_pins[{i}] binary pins use min_rows: 1")
            if p.get("unpack") not in (None, "tar.gz", "zip"):
                e.append(f"data_pins[{i}] unpack must be tar.gz or zip")
            demo = bool(cfg.models) and all(m.get("provider") == "fake" for m in cfg.models)
            if str(p.get("url", "")).startswith("file://") and not demo:
                e.append(f"data_pins[{i}] uses a local file:// url (allowed only in the all-fake demo config)")
    # plan
    if "stages" not in cfg.plan:
        e.append("plan.yaml missing 'stages'")
    for k in ("pilot_per_cell", "canary_per_cell"):
        if k not in cfg.plan:
            e.append(f"plan.yaml missing '{k}'")
    # placeholders anywhere
    for name, obj in (("project", cfg.project), ("models", cfg.models), ("prices", cfg.prices),
                      ("budget", cfg.budget), ("pins", cfg.pins), ("compute", cfg.compute)):
        for path in _scan_placeholders(obj):
            if strict_pins or name != "pins":
                e.append(f"placeholder left in {name}: {path}")
    return e
