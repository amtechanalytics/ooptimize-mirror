"""1.0.2 A: git and binary data pins — every failure class stops the run; resume reuses the cache."""
import hashlib
import io
import subprocess
import tarfile

import pytest

from rh import config, datapins
from rh.exitcodes import DataFailed


def _sh(*a, cwd=None):
    return subprocess.run(list(a), cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture(scope="module")
def bare(tmp_path_factory):
    work = tmp_path_factory.mktemp("srcrepo")
    _sh("git", "init", "-q", "-b", "main", cwd=work)
    for p in ("src/A.java", "src/B.java", "src/pkg/C.java", "README.md", "build.xml"):
        f = work / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"// {p}\n")
    _sh("git", "add", "-A", cwd=work)
    _sh("git", "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q", "-m", "c1", cwd=work)
    sha = _sh("git", "rev-parse", "HEAD", cwd=work)
    bare = tmp_path_factory.mktemp("bare") / "r.git"
    _sh("git", "clone", "-q", "--bare", str(work), str(bare))
    _sh("git", "config", "uploadpack.allowReachableSHA1InWant", "true", cwd=bare)
    return f"file://{bare}", sha


def _gitpin(url, sha, **kw):
    p = {"name": "subject", "format": "git", "url": url, "record": sha, "file": "subject", "title": "t",
         "licence": "MIT", "min_rows": 3, "columns": ["build.xml", "src"], "count_glob": "**/*.java"}
    p.update(kw)
    return p


def test_git_right_commit_then_resume_reuses_cache(bare):
    url, sha = bare
    r1 = datapins.verify_all([_gitpin(url, sha, expected_rows=3)])[0]
    assert r1["ok"] and r1["rows"] == 3 and r1["sha256"] == sha and not r1["reused_cache"]
    assert (datapins.path("subject") / "src" / "A.java").exists()
    r2 = datapins.verify_all([_gitpin(url, sha)])[0]
    assert r2["ok"] and r2["reused_cache"]


def test_git_wrong_commit(bare):
    url, _ = bare
    with pytest.raises(DataFailed):
        datapins.verify_all([_gitpin(url, "0" * 40, name="wrong", file="wrong")])


def test_git_missing_required_path(bare):
    url, sha = bare
    with pytest.raises(DataFailed, match="required paths missing"):
        datapins.verify_all([_gitpin(url, sha, columns=["pom.xml"], name="nopom", file="nopom")])


def test_git_file_count_mismatch(bare):
    url, sha = bare
    with pytest.raises(DataFailed, match="expected_rows"):
        datapins.verify_all([_gitpin(url, sha, expected_rows=7, name="count", file="count")])
    with pytest.raises(DataFailed, match="min_rows"):
        datapins.verify_all([_gitpin(url, sha, min_rows=10, name="count2", file="count2")])


def _binpin(path, **kw):
    p = {"name": "tool", "format": "binary", "url": f"file://{path}", "record": "release 1", "file": path.name,
         "title": "t", "licence": "Apache-2.0", "min_rows": 1, "columns": []}
    p.update(kw)
    return p


@pytest.fixture()
def archive(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        data = b"tool binary\n"
        ti = tarfile.TarInfo("tool/bin/run.sh")
        ti.size = len(data)
        t.addfile(ti, io.BytesIO(data))
    p = tmp_path / "tool-1.0.tar.gz"
    p.write_bytes(buf.getvalue())
    return p


def test_binary_ok_with_checksum_url_and_unpack(archive, tmp_path):
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    ck = tmp_path / "tool-1.0.tar.gz.sha512"
    ck.write_text(hashlib.sha512(archive.read_bytes()).hexdigest() + "  tool-1.0.tar.gz\n")
    r = datapins.verify_all([_binpin(archive, size=archive.stat().st_size, sha256=sha, checksum_url=f"file://{ck}",
                                     checksum_algo="sha512", unpack="tar.gz")])[0]
    assert r["ok"] and r["checks"]["checksum_url"] and r["checks"]["unpacked"]
    assert (datapins.path("tool") / "tool" / "bin" / "run.sh").exists()


def test_binary_size_sha_and_checksum_mismatch(archive, tmp_path):
    with pytest.raises(DataFailed, match="size"):
        datapins.verify_all([_binpin(archive, name="b1", size=1)])
    with pytest.raises(DataFailed, match="sha256"):
        datapins.verify_all([_binpin(archive, name="b2", sha256="0" * 64)])
    bad = tmp_path / "bad.sha256"
    bad.write_text("f" * 64 + "  tool-1.0.tar.gz\n")
    with pytest.raises(DataFailed, match="checksum"):
        datapins.verify_all([_binpin(archive, name="b3", checksum_url=f"file://{bad}")])


def test_binary_lock_on_first_download_then_mismatch(tmp_path):
    f = tmp_path / "blob.bin"
    f.write_bytes(b"version one")
    pin = _binpin(f, name="locked", file="blob.bin")
    assert datapins.verify_all([pin])[0]["ok"]               # sha locked
    cache = datapins.DATA_DIR / hashlib.sha1(pin["url"].encode()).hexdigest()[:16] / "blob.bin"
    cache.write_bytes(b"version two")                         # content changed under the same pin
    with pytest.raises(DataFailed, match="sha256"):
        datapins.verify_all([pin])


def test_config_accepts_binary_and_checks_git_record():
    c = config.load()
    c.pins = [dict(_binpin(__import__("pathlib").Path("/x/t.bin")), url="https://example.org/t.bin")]
    c.errors = []
    assert not [e for e in config.validate(c) if "data_pins" in e]
    c.pins = [_gitpin("https://example.org/r.git", "abc")]
    c.errors = []
    assert any("40-hex" in e for e in config.validate(c))
