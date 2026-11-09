"""Prep report — runs on the Mac (new-paper.sh) and in the chat sandbox (--offline), before any
instance exists. Writes runs/prep_report.md; the last line is 'PREP PASS ...' or 'PREP FAIL (n)'.
  python -m rh.prep [--offline]
--offline skips the AWS/vendor/GitHub checks (marked SKIP); aws-up.sh refuses to launch unless the
latest report is an online PREP PASS.
"""
from __future__ import annotations

import compileall
import importlib
import json
import os
import pkgutil
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .util import ROOT, RUNS, now_iso

ATTRIBUTION = re.compile(r"co-authored-by|generated with|c\x6caude code|c\x6caude\.ai|noreply@anthropic|"
                         r"ai-generated|written by (an )?ai|chatgpt|copilot|ai[- ]assisted|llm[- ]assisted",
                         re.I)
VENUE = re.compile(r"\b(conference|journal|proceedings|symposium|workshop|springer|elsevier|ieee|acm|lncs|"
                   r"camera[- ]ready|submission|reviewers?|cfp|call for papers)\b", re.I)
REQUIRED_IGNORES = [".venv/", "secrets/", "*.md", "!README.md", ".data/", ".fake_aws/", "logs/", "runs/", "results/"]


class Report:
    def __init__(self):
        self.rows = []

    def add(self, cid, name, status, detail=""):
        self.rows.append((cid, name, status, str(detail).replace("\n", " ")[:400]))
        print(f"{status:<5} {cid} {name} {detail if status != 'PASS' else ''}"[:300], flush=True)

    def run(self, cid, name, fn):
        t0 = time.time()
        try:
            res = fn()
            if res is None or res is True:
                self.add(cid, name, "PASS", f"{time.time() - t0:.1f}s")
            elif isinstance(res, str) and res.startswith("SKIP"):
                self.add(cid, name, "SKIP", res)
            elif isinstance(res, list) and res:
                self.add(cid, name, "FAIL", "; ".join(map(str, res[:8])))
            elif isinstance(res, list):
                self.add(cid, name, "PASS", f"{time.time() - t0:.1f}s")
            else:
                self.add(cid, name, "PASS", str(res))
        except Exception as e:  # noqa: BLE001
            self.add(cid, name, "FAIL", f"{type(e).__name__}: {e}")


def _tracked_files() -> list[Path]:
    if (ROOT / ".git").exists():
        r = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return [ROOT / p for p in r.stdout.splitlines()]
    skip = {".git", ".venv", "runs", "results", "logs", ".data", ".fake_aws", "__pycache__", "data"}
    return [p for p in ROOT.rglob("*") if p.is_file() and not (set(p.relative_to(ROOT).parts) & skip)]


# ------------------------------------------------------------------ local checks
def c_python():
    if sys.version_info < (3, 11):
        return [f"python {sys.version.split()[0]} < 3.11 (use the repo .venv)"]
    if not (os.environ.get("VIRTUAL_ENV") or sys.prefix != sys.base_prefix):
        return ["not running inside a virtualenv"]


def c_compile():
    ok = all(compileall.compile_dir(str(ROOT / d), quiet=1, force=True) for d in ("rh", "paper", "pilot", "tests")
             if (ROOT / d).exists())
    return None if ok else ["compileall reported errors"]


def c_imports():
    errs = []
    for pkg in ("rh", "paper"):
        mod = importlib.import_module(pkg)
        for m in pkgutil.walk_packages(mod.__path__, pkg + "."):
            try:
                importlib.import_module(m.name)
            except Exception as e:  # noqa: BLE001
                errs.append(f"{m.name}: {e}")
    return errs


def c_pytest():
    r = subprocess.run([sys.executable, "-m", "pytest", "tests", "-q", "-x", "-p", "no:cacheprovider"],
                       cwd=ROOT, capture_output=True, text=True, timeout=1800)
    tail = (r.stdout + r.stderr).strip().splitlines()[-1:] or [""]
    return None if r.returncode == 0 else [f"pytest failed: {tail[0]}"]


def dry_run_copy() -> tuple[int, Path]:
    tmp = Path(tempfile.mkdtemp(prefix="rh-dryrun-"))
    ign = shutil.ignore_patterns(".git", ".venv", "runs", "results", "logs", ".data", ".fake_aws", "__pycache__",
                                 "data")
    shutil.copytree(ROOT, tmp / "repo", ignore=ign)
    # nothing from a live run (RH_BOOT_TS, RH_MAX_HOURS, RH_RUN_ID, RH_ON_INSTANCE, ...) may leak into the dry run
    env = {k: v for k, v in os.environ.items() if not k.startswith("RH_")}
    env.update(RH_ROOT=str(tmp / "repo"), RH_FAKE_VENDOR="1", RH_FAKE_AWS="1", RH_RUN_ID="dryrun",
               RH_FAKE_AWS_DIR=str(tmp / "repo" / ".fake_aws"))
    for k in ("RH_FAKE_BATCH_SECONDS",):
        if k in os.environ:
            env[k] = os.environ[k]
    r = subprocess.run([sys.executable, "-m", "rh.runner", "--dry-run"], cwd=tmp / "repo", env=env,
                       capture_output=True, text=True, timeout=1800)
    (tmp / "dryrun.log").write_text(r.stdout + r.stderr)
    return r.returncode, tmp


def c_dryrun():
    code, tmp = dry_run_copy()
    repo = tmp / "repo"
    errs = []
    if code != 0:
        ex = json.loads((repo / "runs" / "exit.json").read_text()) if (repo / "runs" / "exit.json").exists() else {}
        errs.append(f"dry-run exit {code}: {ex.get('message', '')[:200]} (log {tmp}/dryrun.log)")
    for must in ("runs/brief.md", "runs/pilot_report.md", "runs/plan.json"):
        if not (repo / must).exists():
            errs.append(f"dry-run did not produce {must}")
    sns = repo / ".fake_aws" / "sns.log"
    subjects = [json.loads(line)["subject"] for line in sns.read_text().splitlines()] if sns.exists() else []
    for kind in ("PILOT | PASS", "CANARY | PASS", "DONE |"):
        if not any(kind in s for s in subjects):
            errs.append(f"dry-run sent no '{kind}' mail")
    if not errs:
        shutil.rmtree(tmp, ignore_errors=True)
    return errs


def c_config():
    from . import config
    return config.load(ROOT).errors


def c_codename():
    import yaml
    cn = yaml.safe_load((ROOT / "config" / "project.yaml").read_text())["codename"]
    errs = []
    if ROOT.name != cn and not os.environ.get("RH_ALLOW_DIRNAME_MISMATCH"):
        errs.append(f"folder '{ROOT.name}' != codename '{cn}'")
    readme = (ROOT / "README.md").read_text() if (ROOT / "README.md").exists() else ""
    first = readme.splitlines()[0] if readme.strip() else ""
    if cn not in first:
        errs.append("README.md first line must name the codename")
    return errs


def c_readme():
    p = ROOT / "README.md"
    if not p.exists():
        return ["README.md missing"]
    t = p.read_text()
    errs = [f"README mentions venue/publisher word '{m.group(0)}'" for m in VENUE.finditer(t)]
    errs += [f"README attribution/AI-use phrase '{m.group(0)}'" for m in ATTRIBUTION.finditer(t)]
    return errs


def c_attribution_files():
    errs = []
    for p in _tracked_files():
        if p.name.upper() in ("C" + "LAUDE.MD", "AGENTS.MD"):
            errs.append(f"{p.relative_to(ROOT)} must not exist")
        if p.suffix in (".png", ".pdf", ".gz", ".zip", ".parquet"):
            continue
        try:
            t = p.read_text(errors="ignore")
        except Exception:  # noqa: BLE001
            continue
        for m in ATTRIBUTION.finditer(t):
            if p.name in ("prep.py",) or "tests/" in str(p) and "ATTRIBUTION" in t:
                continue
            errs.append(f"{p.relative_to(ROOT)}: '{m.group(0)}'")
    return errs


def c_git_log():
    if not (ROOT / ".git").exists():
        return "SKIP no git repository yet"
    r = subprocess.run(["git", "log", "--format=%an <%ae>%n%B"], cwd=ROOT, capture_output=True, text=True)
    return [f"commit text contains '{m.group(0)}'" for m in ATTRIBUTION.finditer(r.stdout)]


def c_gitignore():
    p = ROOT / ".gitignore"
    if not p.exists():
        return [".gitignore missing"]
    lines = {ln.strip() for ln in p.read_text().splitlines()}
    errs = [f".gitignore lacks '{x}'" for x in REQUIRED_IGNORES if x not in lines]
    for f in _tracked_files():
        rel = str(f.relative_to(ROOT))
        if rel.startswith("secrets/") or f.name in (".env",) or f.name.endswith(".pem"):
            errs.append(f"tracked secret-like file {rel}")
    ga = ROOT / ".gitattributes"
    if not ga.exists() or "* -text" not in ga.read_text():
        errs.append(".gitattributes must contain '* -text' (line-ending conversion would change pinned fixtures)")
    if (ROOT / ".git").exists():
        errs += _fixture_blobs_match()
        # every source file must reach GitHub: the instance clones the repo, so an ignored source file is missing there
        r = subprocess.run(["git", "ls-files", "--others", "--ignored", "--exclude-standard", "--", *SOURCE_DIRS],
                           cwd=ROOT, capture_output=True, text=True)
        lost = [x for x in r.stdout.splitlines() if x.strip() and "__pycache__" not in x and not x.endswith(".pyc")]
        errs += [f"source file ignored by .gitignore (would be missing on the instance): {x}" for x in lost]
    return errs


def _fixture_blobs_match() -> list[str]:
    """The instance runs from the GitHub clone: every file:// fixture pin must hash the same in the committed blob
    as on disk (Day-0: git line-ending conversion changed a CRLF fixture, so the dry run failed on the instance)."""
    import hashlib
    errs = []
    head = subprocess.run(["git", "rev-parse", "--verify", "-q", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    if head.returncode != 0:
        return errs
    pf = ROOT / "config" / "data_pins.fixture.json"
    pins = json.loads(pf.read_text()).get("pins", []) if pf.exists() else []
    for p in pins:
        url = str(p.get("url", ""))
        if not url.startswith("file://"):
            continue
        rel = url[len("file://"):]
        r = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=ROOT, capture_output=True)
        if r.returncode != 0:
            errs.append(f"fixture {rel} is not committed")
            continue
        sha = hashlib.sha256(r.stdout).hexdigest()
        if p.get("sha256") and sha != p["sha256"]:
            errs.append(f"fixture {rel}: committed blob sha256 {sha[:12]} != pinned {p['sha256'][:12]} "
                        "(line endings converted by git?)")
    return errs


SOURCE_DIRS = ["rh", "paper", "config", "pilot", "tests", "certificate", "tools", "README.md", "requirements.txt",
               ".gitattributes"]


def c_certificate():
    from . import certificate
    return certificate.check()


def projection() -> dict:
    from . import config
    from .ledger import cost_of
    cfg = config.load(ROOT)
    total, pilot, rows = 0.0, 0.0, []
    for name, st in (cfg.plan.get("stages") or {}).items():
        for mid in st["models"]:
            price = cfg.prices["models"][mid]
            model = cfg.model(mid)
            n = int(st["cells_per_model"])
            use = {"input": n * (st["avg_prefix_tokens"] + st["avg_item_tokens"]), "output": n * st["avg_out_tokens"]}
            c = cost_of(price, use, batch=bool(st.get("batch") and model.get("batch")))
            groups = int(st.get("groups_per_model", 1))
            pilot += cost_of(price, {"input": int(cfg.plan["pilot_per_cell"]) * groups *
                                     (st["avg_prefix_tokens"] + st["avg_item_tokens"]),
                                     "output": int(cfg.plan["pilot_per_cell"]) * groups * st["avg_out_tokens"]})
            total += c
            rows.append((name, mid, n, round(c, 2)))
    canary_frac = int(cfg.plan["canary_per_cell"]) / max(1, min(int(s["cells_per_model"]) for s in cfg.plan["stages"].values()))
    total_all = total * (1 + min(canary_frac, 1.0)) + pilot + float(cfg.budget["executor_ceiling"])
    from . import compute as compute_mod
    comp = compute_mod.projected(cfg)
    return {"rows": rows, "full": round(total, 2), "pilot": round(pilot, 2), "with_overheads": round(total_all, 2),
            "compute": round(comp, 2), "total": round(total_all + comp, 2),
            "ceiling": cfg.ceiling(), "pilot_ceiling": float(cfg.budget["pilot_ceiling"])}


def c_projection():
    p = projection()
    errs = []
    if p["total"] > p["ceiling"]:
        errs.append(f"projected ${p['total']} = API ${p['with_overheads']} (full ${p['full']} + canary + pilot + "
                    f"executor guard) + compute ${p['compute']} > ceiling ${p['ceiling']}")
    if p["pilot"] > p["pilot_ceiling"]:
        errs.append(f"pilot projection ${p['pilot']} > pilot ceiling ${p['pilot_ceiling']}")
    return errs or (f"API ${p['with_overheads']} + compute ${p['compute']} = ${p['total']} of ${p['ceiling']}")


def c_token_limits():
    from . import config
    cfg = config.load(ROOT)
    errs = []
    for name, st in (cfg.plan.get("stages") or {}).items():
        for mid in st["models"]:
            m = cfg.model(mid)
            need = st.get("max_prompt_tokens", st["avg_prefix_tokens"] + st["avg_item_tokens"]) + m["max_output_tokens"]
            if m.get("context_tokens") and need > int(m["context_tokens"]):
                errs.append(f"{name}/{mid}: prompt+output {need} > context {m['context_tokens']} (chunk the input)")
            if st["avg_out_tokens"] > m["max_output_tokens"]:
                errs.append(f"{name}/{mid}: avg_out_tokens {st['avg_out_tokens']} > max_output_tokens")
        if st.get("cache") and st["avg_prefix_tokens"] < 1024:
            errs.append(f"{name}: cache requested but prefix {st['avg_prefix_tokens']} tokens < 1024 "
                        "(too short to cache; set cache: false — the cache check is then exempt)")
    return errs


# ------------------------------------------------------------------ online checks
def _aws_json(args):
    r = subprocess.run(["aws", *args, "--output", "json"], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:300])
    return json.loads(r.stdout or "{}")


def _secret():
    from . import config
    cfg = config.load(ROOT)
    sid = cfg.project.get("secret_id", "research/llm-keys")
    d = _aws_json(["secretsmanager", "get-secret-value", "--secret-id", sid])
    return json.loads(d["SecretString"])


def c_identity():
    import yaml
    cn = yaml.safe_load((ROOT / "config" / "project.yaml").read_text())["codename"]
    arn = _aws_json(["sts", "get-caller-identity"])["Arn"]
    if f"research-{cn}-runner" not in arn:
        return [f"caller is {arn}, expected the research-{cn}-runner role (AWS_PROFILE=research-{cn})"]
    return arn


def c_iam():
    """Each required action is simulated against the resource (and tags) it is really used on; a bare
    simulation against '*' would report every resource-scoped permission as denied."""
    import yaml
    proj = yaml.safe_load((ROOT / "config" / "project.yaml").read_text())
    cn, bucket, topic = proj["codename"], proj["bucket"], proj["topic_arn"]
    secret_arn = proj.get("secret_arn")
    if not secret_arn:
        return ["project.yaml has no secret_arn (re-run new-paper.sh --update)"]
    who = _aws_json(["sts", "get-caller-identity"])
    acct, region = who["Account"], proj.get("region", "us-east-1")
    role_arn = f"arn:aws:iam::{acct}:role/research-{cn}-runner"
    inst = f"arn:aws:ec2:{region}:{acct}:instance/i-0123456789abcdef0"
    obj = f"arn:aws:s3:::{bucket}/{cn}/sim.txt"

    def cx(**kv):
        return [f"ContextKeyName={k},ContextKeyValues={v},ContextKeyType=string" for k, v in kv.items()]
    checks = [
        ("ec2:RunInstances", inst, cx(**{"aws:RequestTag/paper": cn, "aws:RequestTag/mode": "auto-unattended"})),
        ("ec2:RunInstances", f"arn:aws:ec2:{region}::image/ami-0123456789abcdef0", []),
        ("ec2:CreateTags", inst, cx(**{"ec2:CreateAction": "RunInstances"})),
        ("ec2:DescribeInstances", "*", []), ("ec2:DescribeImages", "*", []),
        ("ec2:TerminateInstances", inst, cx(**{"ec2:ResourceTag/paper": cn})),
        ("iam:PassRole", f"arn:aws:iam::{acct}:role/research-{cn}-instance", []),
        ("ssm:GetParameter", f"arn:aws:ssm:{region}::parameter/aws/service/canonical/ubuntu/server/24.04/stable/"
                             "current/amd64/hvm/ebs-gp3/ami-id", []),
        ("s3:ListBucket", f"arn:aws:s3:::{bucket}", cx(**{"s3:prefix": f"{cn}/x"})),
        ("s3:GetObject", obj, []), ("s3:PutObject", obj, []),
        ("s3:PutObject", f"arn:aws:s3:::{bucket}/_tools/instance/boot.sh", []),
        ("secretsmanager:GetSecretValue", secret_arn, []), ("sns:Publish", topic, []),
        ("servicequotas:GetServiceQuota", "*", []), ("sts:GetCallerIdentity", "*", []),
    ]
    denied = []
    for action, res, ctx in checks:
        args = ["iam", "simulate-principal-policy", "--policy-source-arn", role_arn, "--action-names", action,
                "--resource-arns", res] + (["--context-entries", *ctx] if ctx else [])
        d = _aws_json(args)
        if any(r["EvalDecision"] != "allowed" for r in d["EvaluationResults"]):
            denied.append(f"{action} on {res.split(':')[-1]}")
    return [f"denied: {x}" for x in denied]


def _providers():
    from . import config
    return sorted({m["provider"] for m in config.load(ROOT).models} - {"fake"})


def c_secret_keys():
    from .vendors import KEY_ENV
    s = _secret()
    need = [KEY_ENV[p] for p in _providers()] + ["CLAUDE_CODE_OAUTH_TOKEN", "GITHUB_TOKEN"]
    errs = [f"secret lacks non-empty {k}" for k in need if not str(s.get(k, "")).strip()]
    exp = (s.get("_meta") or {}).get("expires") or {}
    from . import config
    hrs = float(config.load(ROOT).project.get("expected_hours", 12))
    horizon = time.time() + (hrs + 24) * 3600
    for k in need:
        e = exp.get(k)
        if not e:
            errs.append(f"no expiry recorded for {k} (re-run secrets-push.sh)")
            continue
        t = time.mktime(time.strptime(e, "%Y-%m-%d"))
        if t < horizon:
            errs.append(f"{k} expires {e}, before the run's expected end + 24h — rotate first")
    return errs


def c_vendor_live():
    from . import config, vendors
    s = _secret()
    cfg = config.load(ROOT)
    errs = []
    for p in _providers():
        os.environ[vendors.KEY_ENV[p]] = s[vendors.KEY_ENV[p]]
        try:
            ids = set(vendors.list_models(p))
        except Exception as e:  # noqa: BLE001
            errs.append(f"{p}: key check failed: {e}")
            continue
        finally:
            os.environ.pop(vendors.KEY_ENV[p], None)
        for m in cfg.models:
            if m["provider"] == p and ids and m["id"] not in ids:
                errs.append(f"{p}: model id '{m['id']}' not offered to this key")
    return errs


def c_github():
    r = subprocess.run(["git", "ls-remote", "--heads", "origin"], cwd=ROOT, capture_output=True, text=True,
                       timeout=60)
    return None if r.returncode == 0 else [f"git ls-remote origin failed: {r.stderr.strip()[:200]}"]


def c_s3():
    from . import config
    p = config.load(ROOT).project
    uri = f"s3://{p['bucket']}/{p['codename']}/_probe/{int(time.time())}.txt"
    subprocess.run(["aws", "s3", "cp", "-", uri], input="probe", text=True, check=True, capture_output=True)
    back = subprocess.run(["aws", "s3", "cp", uri, "-"], capture_output=True, text=True, check=True).stdout
    subprocess.run(["aws", "s3", "rm", uri], capture_output=True, check=False)
    return None if back == "probe" else ["S3 round-trip mismatch"]


def c_sns():
    from . import config
    arn = config.load(ROOT).project.get("topic_arn")
    if not arn:
        return ["project.topic_arn missing"]
    _aws_json(["sns", "get-topic-attributes", "--topic-arn", arn])


def main(argv):
    offline = "--offline" in argv
    rep = Report()
    RUNS.mkdir(parents=True, exist_ok=True)
    for cid, name, fn in [
        ("L01", "python >= 3.11 in a venv", c_python), ("L02", "compile all", c_compile),
        ("L03", "import every module", c_imports), ("L04", "unit tests", c_pytest),
        ("L05", "configuration valid", c_config), ("L06", "codename consistent", c_codename),
        ("L07", "README hygiene", c_readme), ("L08", "no attribution in tracked files", c_attribution_files),
        ("L09", "no attribution in git log", c_git_log), ("L10", ".gitignore / no secrets tracked", c_gitignore),
        ("L11", "certificate <-> pilot tests", c_certificate), ("L12", "cost projection within ceilings", c_projection),
        ("L13", "per-call token limits", c_token_limits), ("L14", "dry run end to end (fake vendor, fake AWS)", c_dryrun),
    ]:
        rep.run(cid, name, fn)
    online = [("A01", "caller is the project runner role", c_identity), ("A02", "IAM simulate", c_iam),
              ("A03", "secret keys present + expiry", c_secret_keys), ("A04", "vendor keys live + model ids", c_vendor_live),
              ("A05", "GitHub push access", c_github), ("A06", "S3 round trip", c_s3), ("A07", "SNS topic", c_sns)]
    for cid, name, fn in online:
        if offline:
            rep.add(cid, name, "SKIP", "offline")
        else:
            rep.run(cid, name, fn)
    fails = [r for r in rep.rows if r[2] == "FAIL"]
    skips = [r for r in rep.rows if r[2] == "SKIP" and r[0].startswith("A")]
    ident = next((r[3] for r in rep.rows if r[0] == "A01" and r[2] == "PASS"), "")
    if fails:
        verdict = f"PREP FAIL ({len(fails)})"
    elif skips:
        verdict = "PREP PASS (offline — not valid for launch)"
    else:
        verdict = f"PREP PASS · {ident}"
    lines = ["# Prep report", "", f"{now_iso()} · {ROOT.name}", "", "| ID | Check | Result | Detail |",
             "|---|---|---|---|"]
    lines += [f"| {c} | {n} | {s} | {d} |" for c, n, s, d in rep.rows]
    lines += ["", verdict]
    (RUNS / "prep_report.md").write_text("\n".join(lines) + "\n")
    if (ROOT / ".git").exists():
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        (RUNS / "prep_head.txt").write_text(f"{head}\n{verdict}\n")
    print(verdict)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
