"""Pinned datasets: resumable download into a hash-addressed cache, then verify size, sha256,
format, row count, columns, encoding and duplicate keys. Any mismatch is a hard stop."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile
from pathlib import Path

from .exitcodes import DataFailed
from .util import ROOT, RUNS, journal, now_iso, read_json, retry, write_json

DATA_DIR = Path(os.environ.get("RH_DATA_DIR", str(ROOT / ".data")))
LOCK = RUNS / "pins.lock.json"
REPORT = RUNS / "datacheck.json"
csv.field_size_limit(1 << 28)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path) -> None:
    """Resumable: continues a partial file with an HTTP Range request."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    if url.startswith("file://"):
        src = Path(url[len("file://"):])
        if not src.is_absolute():
            src = ROOT / src
        shutil.copyfile(src, dest)
        return

    def once():
        have = part.stat().st_size if part.exists() else 0
        req = urllib.request.Request(url, headers={"User-Agent": "research-harness/1.0",
                                                   **({"Range": f"bytes={have}-"} if have else {})})
        with urllib.request.urlopen(req, timeout=600) as r:
            mode = "ab" if (have and r.status == 206) else "wb"
            with open(part, mode) as f:
                shutil.copyfileobj(r, f, 1 << 20)
        os.replace(part, dest)

    retry(once, attempts=6, base=5)


def _rows_and_columns(path: Path, fmt: str, encoding: str, member: str | None):
    raw = path.read_bytes()
    if member:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = z.namelist()
            if member not in names:
                raise DataFailed(f"zip member '{member}' not found (members: {names[:20]})")
            raw = z.read(member)
    text = raw.decode(encoding)   # raises on wrong encoding -> caught by caller
    if fmt == "csv" or fmt == "tsv":
        rdr = csv.reader(io.StringIO(text), delimiter="," if fmt == "csv" else "\t")
        rows = list(rdr)
        if not rows:
            return 0, [], []
        header = [h.strip() for h in rows[0]]
        body = [dict(zip(header, r)) for r in rows[1:] if any(c.strip() for c in r)]
        return len(body), header, body
    if fmt == "jsonl":
        body = [json.loads(line) for line in text.splitlines() if line.strip()]
        cols = sorted(body[0].keys()) if body else []
        return len(body), cols, body
    if fmt == "json":
        d = json.loads(text)
        body = d if isinstance(d, list) else d.get("data", [])
        cols = sorted(body[0].keys()) if body and isinstance(body[0], dict) else []
        return len(body), cols, body
    raise DataFailed(f"unsupported format '{fmt}' (supported: csv, tsv, jsonl, json)")


def _git(args, cwd, timeout=900):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise DataFailed(f"git {' '.join(args[:2])}: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def _link(out: Path, target: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.is_symlink() or out.is_file():
        out.unlink()
    elif out.is_dir():
        shutil.rmtree(out)
    out.symlink_to(target, target_is_directory=target.is_dir())


def verify_git(pin: dict, lock: dict, rep: dict) -> None:
    """format: git — url, record (40-hex commit), file (checkout dir name), min_rows (= files matching
    count_glob under count_roots), columns (paths that must exist). The commit is the content address."""
    name, sha = pin["name"], str(pin["record"]).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise DataFailed(f"{name}: record must be a full 40-hex commit, got '{sha}'")
    url = pin["url"]
    if url.startswith("file://") and not url.startswith("file:///"):
        url = str(ROOT / url[len("file://"):])          # repo-relative fixture path
    cache = DATA_DIR / "git" / hashlib.sha1(pin["url"].encode()).hexdigest()[:16] / pin["file"]
    reuse = (cache / ".git").exists() and _git(["rev-parse", "HEAD"], cache) == sha
    if not reuse:
        if cache.exists():
            shutil.rmtree(cache)
        cache.mkdir(parents=True)
        _git(["init", "-q"], cache)
        _git(["remote", "add", "origin", url], cache)
        retry(lambda: _git(["fetch", "-q", "--depth", "1", "origin", sha], cache), attempts=4, base=5,
              retry_on=(DataFailed,))
        _git(["checkout", "-q", "--detach", "FETCH_HEAD"], cache)
    rep["checks"]["downloaded"] = True
    rep["reused_cache"] = reuse
    head = _git(["rev-parse", "HEAD"], cache)
    if head != sha:
        raise DataFailed(f"{name}: checked-out commit {head[:12]} != pinned {sha[:12]}")
    rep["checks"]["sha256"] = True                     # commit id verified (content address)
    rep["sha256"] = head
    lock[name] = head
    pattern = pin.get("count_glob", "**/*.java")
    roots = pin.get("count_roots") or ["."]
    n = sum(1 for r in roots for p in (cache / r).glob(pattern) if p.is_file())
    rep["rows"] = n
    if n < int(pin["min_rows"]):
        raise DataFailed(f"{name}: {n} files matching {pattern} < min_rows {pin['min_rows']}")
    if pin.get("expected_rows") is not None and n != int(pin["expected_rows"]):
        raise DataFailed(f"{name}: {n} files matching {pattern} != expected_rows {pin['expected_rows']}")
    rep["checks"]["rows"] = True
    missing = [c for c in pin["columns"] if not (cache / c).exists()]
    rep["columns"] = list(pin["columns"])
    if missing:
        raise DataFailed(f"{name}: required paths missing from the tree: {missing}")
    rep["checks"]["columns"] = True
    rep["checks"]["duplicates"] = True
    rep["checks"]["size"] = True
    out = ROOT / "data" / name
    _link(out, cache)
    rep["path"] = str(out.relative_to(ROOT))


def _hash(path: Path, algo: str) -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_binary(pin: dict, lock: dict, rep: dict, cache: Path) -> None:
    """format: binary — size, sha256 (optional; locked on first verified download), optional
    checksum_url + checksum_algo (sha1|sha256|sha512) published by the host, optional unpack tar.gz|zip."""
    name = pin["name"]
    if not cache.exists():
        _download(pin["url"], cache)
    rep["checks"]["downloaded"] = True
    size = cache.stat().st_size
    rep["size"] = size
    if size == 0:
        raise DataFailed(f"{name}: downloaded file is empty")
    if pin.get("size") and int(pin["size"]) != size:
        raise DataFailed(f"{name}: size {size} != pinned {pin['size']}")
    rep["checks"]["size"] = True
    if pin.get("checksum_url"):
        algo = pin.get("checksum_algo", "sha256")
        if algo not in ("sha1", "sha256", "sha512"):
            raise DataFailed(f"{name}: checksum_algo must be sha1, sha256 or sha512")
        ck = cache.with_name(cache.name + f".{algo}.txt")
        if not ck.exists():
            _download(pin["checksum_url"], ck)
        want_len = {"sha1": 40, "sha256": 64, "sha512": 128}[algo]
        tokens = re.findall(r"\b[0-9a-fA-F]{%d}\b" % want_len, ck.read_text(errors="ignore"))
        if not tokens:
            raise DataFailed(f"{name}: no {algo} digest found at checksum_url")
        got = _hash(cache, algo)
        if got.lower() not in [t.lower() for t in tokens]:
            raise DataFailed(f"{name}: {algo} {got[:12]}… does not match the host's checksum file")
        rep["checks"]["checksum_url"] = True
    sha = _sha256(cache)
    rep["sha256"] = sha
    want = pin.get("sha256") or lock.get(name)
    if want and want != sha:
        raise DataFailed(f"{name}: sha256 {sha[:12]}… != pinned {want[:12]}…")
    if not pin.get("sha256"):
        lock[name] = sha
    rep["checks"]["sha256"] = True
    rep.update(rows=1, columns=[])
    if int(pin.get("min_rows", 1)) > 1:
        raise DataFailed(f"{name}: binary pins have one row; min_rows must be 1")
    rep["checks"]["rows"] = rep["checks"]["columns"] = rep["checks"]["duplicates"] = True
    unpack = pin.get("unpack")
    if unpack:
        out = ROOT / "data" / name
        if out.is_symlink() or out.is_file():
            out.unlink()
        elif out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        if unpack == "tar.gz":
            with tarfile.open(cache, "r:gz") as t:
                try:
                    t.extractall(out, filter="data")
                except TypeError:
                    t.extractall(out)
        elif unpack == "zip":
            with zipfile.ZipFile(cache) as z:
                for n in z.namelist():
                    if n.startswith("/") or ".." in Path(n).parts:
                        raise DataFailed(f"{name}: unsafe path in zip: {n}")
                z.extractall(out)
        else:
            raise DataFailed(f"{name}: unpack must be tar.gz or zip")
        rep["checks"]["unpacked"] = True
    else:
        out = ROOT / "data" / (name + Path(pin["file"]).suffix)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(cache, out)
    rep["path"] = str(out.relative_to(ROOT))


def verify_pin(pin: dict, lock: dict) -> dict:
    name = pin["name"]
    rep = {"name": name, "record": pin.get("record"), "format": pin.get("format"), "checks": {}, "ok": False}
    cache = DATA_DIR / hashlib.sha1(pin["url"].encode()).hexdigest()[:16] / pin["file"]
    try:
        if pin.get("format") == "git":
            verify_git(pin, lock, rep)
            rep["ok"] = True
            return rep
        if pin.get("format") == "binary":
            verify_binary(pin, lock, rep, cache)
            rep["ok"] = True
            return rep
        if not cache.exists():
            _download(pin["url"], cache)
        rep["checks"]["downloaded"] = True
        size = cache.stat().st_size
        rep["size"] = size
        if size == 0:
            raise DataFailed(f"{name}: downloaded file is empty")
        if pin.get("size") and int(pin["size"]) != size:
            raise DataFailed(f"{name}: size {size} != pinned {pin['size']}")
        rep["checks"]["size"] = True
        sha = _sha256(cache)
        rep["sha256"] = sha
        want = pin.get("sha256") or lock.get(name)
        if want and want != sha:
            raise DataFailed(f"{name}: sha256 {sha[:12]}… != pinned {want[:12]}…")
        if not pin.get("sha256"):
            lock[name] = sha     # first verified download pins it for all later rungs
        rep["checks"]["sha256"] = True
        n, cols, body = _rows_and_columns(cache, pin["format"], pin.get("encoding", "utf-8"), pin.get("member"))
        rep.update(rows=n, columns=cols)
        if n < int(pin["min_rows"]):
            raise DataFailed(f"{name}: {n} rows < min_rows {pin['min_rows']}")
        if pin.get("expected_rows") is not None and n != int(pin["expected_rows"]):
            raise DataFailed(f"{name}: {n} rows != expected_rows {pin['expected_rows']}")
        rep["checks"]["rows"] = True
        missing = [c for c in pin["columns"] if c not in cols]
        if missing:
            raise DataFailed(f"{name}: missing columns {missing}; found {cols[:30]}")
        rep["checks"]["columns"] = True
        key = pin.get("unique_key")
        if key:
            vals = [r.get(key) for r in body]
            dups = len(vals) - len(set(vals))
            rep["duplicates"] = dups
            if dups > int(pin.get("max_duplicates", 0)):
                raise DataFailed(f"{name}: {dups} duplicate values in key '{key}'")
        rep["checks"]["duplicates"] = True
        out = ROOT / "data" / (pin["name"] + Path(pin.get("member") or pin["file"]).suffix)
        out.parent.mkdir(parents=True, exist_ok=True)
        if pin.get("member"):
            with zipfile.ZipFile(cache) as z:
                out.write_bytes(z.read(pin["member"]))
        else:
            shutil.copyfile(cache, out)
        rep["path"] = str(out.relative_to(ROOT))
        rep["ok"] = True
    except DataFailed as e:
        rep["error"] = str(e)
    except UnicodeDecodeError as e:
        rep["error"] = f"{name}: not decodable as {pin.get('encoding', 'utf-8')}: {e}"
    except Exception as e:  # noqa: BLE001
        rep["error"] = f"{name}: {type(e).__name__}: {e}"
    return rep


def verify_all(pins: list[dict]) -> list[dict]:
    lock = read_json(LOCK, default={}) or {}
    reports = [verify_pin(p, lock) for p in pins]
    write_json(LOCK, lock)
    write_json(REPORT, {"ts": now_iso(), "pins": reports})
    bad = [r for r in reports if not r["ok"]]
    for r in reports:
        journal("DATA", f"{r['name']}: {'OK' if r['ok'] else 'FAIL'} rows={r.get('rows')} "
                        f"sha={str(r.get('sha256'))[:12]} {r.get('error', '')}")
    if bad:
        raise DataFailed("; ".join(r["error"] for r in bad))
    if not pins:
        raise DataFailed("no data pins configured")
    return reports


def present(name: str) -> bool:
    """True when the verified copy of this pin exists on this machine."""
    rep = read_json(REPORT, default={}) or {}
    for r in rep.get("pins", []):
        if r["name"] == name and r.get("ok") and r.get("path"):
            return (ROOT / r["path"]).exists()
    return False


def path(name: str) -> Path:
    rep = read_json(REPORT, default={}) or {}
    for r in rep.get("pins", []):
        if r["name"] == name and r.get("ok"):
            return ROOT / r["path"]
    raise DataFailed(f"dataset '{name}' has not passed verification")
