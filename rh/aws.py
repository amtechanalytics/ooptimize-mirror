"""Thin wrapper over the AWS CLI (S3, SNS, EC2 describe) with a local fake for dry runs
(RH_FAKE_AWS=1 writes to .fake_aws/ under the repo)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from .util import ROOT, env_flag, now_iso


def fake() -> bool:
    return env_flag("RH_FAKE_AWS")


FAKE_DIR = Path(os.environ.get("RH_FAKE_AWS_DIR", str(ROOT / ".fake_aws")))


def _aws(args: list[str], check=True, capture=True, timeout=600) -> subprocess.CompletedProcess:
    cmd = ["aws", *args]
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if region and "--region" not in args:
        cmd += ["--region", region]
    return subprocess.run(cmd, check=check, capture_output=capture, text=True, timeout=timeout)


def _fake_path(s3_uri: str) -> Path:
    assert s3_uri.startswith("s3://"), s3_uri
    return FAKE_DIR / "s3" / s3_uri[len("s3://"):]


# ---------------------------------------------------------------- S3
def s3_sync(local: Path, s3_uri: str) -> None:
    if fake():
        dst = _fake_path(s3_uri)
        if Path(local).exists():
            shutil.copytree(local, dst, dirs_exist_ok=True)
        return
    _aws(["s3", "sync", str(local), s3_uri, "--only-show-errors", "--exclude", "*.lock", "--exclude", "*.tmp*"])


def s3_sync_down(s3_uri: str, local: Path) -> None:
    if fake():
        src = _fake_path(s3_uri)
        if src.exists():
            shutil.copytree(src, local, dirs_exist_ok=True)
        return
    _aws(["s3", "sync", s3_uri, str(local), "--only-show-errors"])


def s3_put_bytes(s3_uri: str, data: bytes) -> None:
    if fake():
        p = _fake_path(s3_uri)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return
    tmp = Path(os.environ.get("TMPDIR", "/var/tmp")) / f"rh_put_{os.getpid()}"
    tmp.write_bytes(data)
    try:
        _aws(["s3", "cp", str(tmp), s3_uri, "--only-show-errors"])
    finally:
        tmp.unlink(missing_ok=True)


def s3_put_json(s3_uri: str, obj) -> None:
    s3_put_bytes(s3_uri, json.dumps(obj, indent=2, default=str).encode())


def s3_get_bytes(s3_uri: str) -> bytes | None:
    if fake():
        p = _fake_path(s3_uri)
        return p.read_bytes() if p.exists() else None
    r = _aws(["s3", "cp", s3_uri, "-", "--only-show-errors"], check=False)
    if r.returncode != 0:
        return None
    return r.stdout.encode()


def s3_exists(s3_uri: str) -> bool:
    if fake():
        return _fake_path(s3_uri).exists()
    r = _aws(["s3", "ls", s3_uri], check=False)
    return r.returncode == 0 and bool(r.stdout.strip())


def s3_rm(s3_uri: str) -> None:
    if fake():
        _fake_path(s3_uri).unlink(missing_ok=True)
        return
    _aws(["s3", "rm", s3_uri, "--only-show-errors"], check=False)


def s3_cp_file(local: Path, s3_uri: str) -> None:
    if fake():
        p = _fake_path(s3_uri)
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local, p)
        return
    _aws(["s3", "cp", str(local), s3_uri, "--only-show-errors"])


# ---------------------------------------------------------------- SNS
def sns_publish(topic_arn: str, subject: str, body: str) -> None:
    subject = subject[:100]   # SNS email subject limit
    if fake():
        FAKE_DIR.mkdir(parents=True, exist_ok=True)
        with open(FAKE_DIR / "sns.log", "a") as f:
            f.write(json.dumps({"ts": now_iso(), "subject": subject, "body": body}) + "\n")
        return
    _aws(["sns", "publish", "--topic-arn", topic_arn, "--subject", subject, "--message", body])


# ---------------------------------------------------------------- EC2 (read-only on the instance)
def tagged_instances(tag_key="mode", tag_value="auto-unattended") -> list[dict]:
    if fake():
        p = FAKE_DIR / "ec2.json"
        return json.loads(p.read_text()) if p.exists() else []
    r = _aws(["ec2", "describe-instances", "--filters", f"Name=tag:{tag_key},Values={tag_value}",
              "Name=instance-state-name,Values=pending,running,stopping,stopped",
              "--query", "Reservations[].Instances[].{id:InstanceId,launch:LaunchTime,state:State.Name,"
                         "type:InstanceType,tags:Tags}", "--output", "json"])
    return json.loads(r.stdout or "[]")
