"""Thin, bounded wrappers around the JDK tools, the Java helpers, Randoop, EqualsVerifier, SpotBugs and JMH.
Every call has its own timeout and returns data (never raises on a tool failure), so a stage decides what a
failure means. All functions take a Tools object, so the tests can point them at any JDK and jars."""
from __future__ import annotations

import json
import re
import shutil
import struct
import tempfile
from pathlib import Path

from .env import run

HELPERS = ("MethodLocator", "OoptTestRunner", "OoptEV")
# JMH takes an exclusive lock (/tmp/jmh.lock) so that only one benchmark runs per machine; profiling and allocation run
# one lane per library in parallel, so the lock is ignored (stack shares and bytes/op are not timing measurements).
# Without this every concurrent run fails at once with "Unable to acquire the JMH lock" (canary, 7 Nov).
JMH_HOST = ["-Djmh.ignoreLock=true"]
JAVA_SRC = Path(__file__).resolve().parent / "java"


def cp(*parts) -> str:
    out = []
    for p in parts:
        if p is None:
            continue
        if isinstance(p, (list, tuple)):
            out.extend(str(x) for x in p if x)
        else:
            out.append(str(p))
    return ":".join(dict.fromkeys(out))


def compile_helpers(t, out_dir: Path, log: Path | None = None) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    srcs = [str(JAVA_SRC / f"{h}.java") for h in HELPERS]
    r = run([str(t.javac), "--release", "17", "-proc:none", "-nowarn", "-encoding", "UTF-8", "-d", str(out_dir), *srcs],
            env=t.env(), timeout=300, log=log)
    if r.returncode != 0:
        return [f"helpers do not compile: {(r.stderr or r.stdout)[-800:]}"]
    missing = [h for h in HELPERS if not (out_dir / f"{h}.class").exists()]
    return [f"helper class missing after compile: {missing}"] if missing else []


def version(t, which: str) -> str:
    cmd = {"java": [str(t.java), "-version"], "javac": [str(t.javac), "-version"], "mvn": [str(t.mvn), "-v"]}[which]
    r = run(cmd, env=t.env(), timeout=120)
    return ((r.stdout or "") + (r.stderr or "")).strip().splitlines()[0] if r.returncode == 0 else ""


# ------------------------------------------------------------------ method locator
def locate(t, files: list[Path], timeout: int = 900) -> list[dict]:
    out = []
    files = [str(f) for f in files]
    for i in range(0, len(files), 150):
        r = run([str(t.java), "-cp", str(t.helpers), "MethodLocator", *files[i:i + 150]], env=t.env(),
                timeout=timeout)
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    return out


def locate_snippet(t, text: str, work: Path) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    f = work / "snippet.java.txt"
    f.write_text(text)
    r = run([str(t.java), "-cp", str(t.helpers), "MethodLocator", "--snippet", str(f)], env=t.env(), timeout=120)
    rows = [json.loads(x) for x in (r.stdout or "").splitlines() if x.strip().startswith("{")]
    if not rows:
        return {"error": f"locator produced nothing (exit {r.returncode})"}
    errs = [x for x in rows if "error" in x]
    if errs:
        return {"error": errs[0]["error"]}
    return {"methods": rows}


# ------------------------------------------------------------------ javac into an overlay
def class_major(path: Path) -> int | None:
    try:
        b = Path(path).read_bytes()[:8]
        if b[:4] != b"\xca\xfe\xba\xbe":
            return None
        return struct.unpack(">H", b[6:8])[0]
    except OSError:
        return None


def release_of(major: int | None, default: int) -> int:
    return (major - 44) if major and major >= 52 else default


def javac(t, src_file: Path, classpath: str, out_dir: Path, release: int, timeout: int = 300,
          log: Path | None = None) -> tuple[bool, str]:
    """Compile one source file against the original build (-implicit:none: only this file's classes are
    emitted). -source/-target (not --release) mirrors how the libraries build on a newer JDK, including
    their use of JDK-internal APIs."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    rel = str(release)
    r = run([str(t.javac), "-source", rel, "-target", rel, "-Xlint:-options", "-proc:none", "-nowarn", "-g",
             "-implicit:none", "-encoding", "UTF-8", "-cp", classpath, "-d", str(out_dir), str(src_file)],
            env=t.env(), timeout=timeout, log=log)
    ok = r.returncode == 0 and any(out_dir.rglob("*.class"))
    return ok, (r.stderr or r.stdout or "")[-3000:]


# ------------------------------------------------------------------ tests (project tests and Randoop replays)
def run_tests(t, classpath: str, classes: list[str], out_json: Path, *, engine: str = "auto", timeout: int = 900,
              tmp: Path | None = None, xmx: str = "2g", log: Path | None = None, hb=None) -> dict | None:
    if not classes:
        return {"engine": engine, "run": 0, "failed": 0, "skipped": 0, "tests": [], "error": None}
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.unlink(missing_ok=True)
    tmp = tmp or Path(tempfile.mkdtemp(prefix="oopt-t-"))
    tmp.mkdir(parents=True, exist_ok=True)
    r = run([str(t.java), f"-Xmx{xmx}", f"-Djava.io.tmpdir={tmp}", "-Djava.awt.headless=true", "-cp",
             cp(classpath, t.helpers), "OoptTestRunner", "--engine", engine, "--out", str(out_json), *classes],
            env=t.env(), timeout=timeout, log=log, hb=hb)
    try:
        d = json.loads(out_json.read_text())
    except (OSError, ValueError):
        return None if r.returncode == 124 else {"engine": engine, "run": 0, "failed": 0, "skipped": 0, "tests": [],
                                                 "error": f"runner exit {r.returncode}: {(r.stderr or '')[-400:]}"}
    if r.returncode == 124:
        d["error"] = "timeout"
    return d


def junit4_jar(t) -> str:
    return str(t.randoop)          # randoop-all bundles JUnit 4.13 and hamcrest


# ------------------------------------------------------------------ Randoop
def randoop_generate(t, cls: str, classpath: str, out_dir: Path, basename: str, *, limit: int = 1500,
                     output_limit: int = 300, timeout: int = 900, log: Path | None = None, hb=None) -> list[Path]:
    """Deterministic generation (fixed seed, generated-test limit, no time limit)."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    pkg = cls.rsplit(".", 1)[0] if "." in cls else ""
    args = [str(t.java), "-Xmx3g", "-cp", cp(classpath, t.randoop), "randoop.main.Main", "gentests",
            f"--testclass={cls}", f"--generated-limit={limit}", f"--output-limit={output_limit}", "--time-limit=0",
            "--deterministic=true", "--randomseed=0", "--no-error-revealing-tests=true",
            "--flaky-test-behavior=DISCARD", f"--junit-output-dir={out_dir}", f"--regression-test-basename={basename}",
            "--testsperfile=100"]
    if pkg:
        args.append(f"--junit-package-name={pkg}")
    run(args, env=t.env(), timeout=timeout, log=log, hb=hb)
    return sorted(p for p in out_dir.rglob(f"{basename}*.java") if not p.name.endswith("Driver.java"))


def compile_tests(t, sources: list[Path], classpath: str, out_dir: Path, timeout: int = 600,
                  log: Path | None = None) -> bool:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    if not sources:
        return False
    r = run([str(t.javac), "-proc:none", "-nowarn", "-Xlint:-options", "-encoding", "UTF-8", "-cp",
             cp(classpath, t.randoop), "-d", str(out_dir), *map(str, sources)], env=t.env(), timeout=timeout, log=log)
    return r.returncode == 0


def classes_in(src_files: list[Path]) -> list[str]:
    out = []
    for p in src_files:
        txt = p.read_text(errors="ignore")
        m = re.search(r"^\s*package\s+([\w.]+)\s*;", txt, re.M)
        out.append((m.group(1) + "." if m else "") + p.stem)
    return out


# ------------------------------------------------------------------ EqualsVerifier
def equals_verifier(t, cls: str, classpath: str, timeout: int = 300) -> tuple[str, str]:
    if t.ev is None or not Path(t.ev).exists():
        return "NA", "ev_missing"
    r = run([str(t.java), "-Xmx1g", "-cp", cp(classpath, t.ev, t.helpers), "OoptEV", cls, "lenient"], env=t.env(),
            timeout=timeout)
    for line in (r.stdout or "").splitlines():
        if line.startswith("EV "):
            parts = line.split(" ", 3)
            return (parts[2] if len(parts) > 2 else "NA"), (parts[3] if len(parts) > 3 else "")
    return "NA", f"no EV line (exit {r.returncode})"


# ------------------------------------------------------------------ SpotBugs
def spotbugs(t, analyze_dir: Path, only: list[str], aux_cp: str, out_xml: Path, timeout: int = 900) -> str | None:
    out_xml.parent.mkdir(parents=True, exist_ok=True)
    out_xml.unlink(missing_ok=True)
    args = [str(t.java), "-Xmx2g", "-jar", str(t.spotbugs), "-textui", "-quiet", "-effort:max", "-low",
            f"-xml:withMessages={out_xml}", "-auxclasspath", aux_cp]
    if only:
        args += ["-onlyAnalyze", ",".join(only)]
    args.append(str(analyze_dir))
    run(args, env=t.env(), timeout=timeout)
    try:
        return out_xml.read_text()
    except OSError:
        return None


# ------------------------------------------------------------------ JMH
def jmh_list(t, jar: Path, timeout: int = 300) -> list[str]:
    from .oracles import list_benchmarks
    r = run([str(t.java), *JMH_HOST, "-jar", str(jar), "-l"], env=t.env(), timeout=timeout)
    return list_benchmarks(r.stdout or "")


def jmh_stack(t, jar: Path, bench: str, timeout: int = 420, log: Path | None = None, hb=None) -> str:
    r = run([str(t.java), *JMH_HOST, "-jar", str(jar), f"^{re.escape(bench)}$", "-f", "1", "-wi", "1", "-w", "1s", "-i", "1",
             "-r", "2s", "-foe", "false", "-prof", "stack:lines=4;top=40;detailLine=false"],
            env=t.env(), timeout=timeout, log=log, hb=hb)
    return (r.stdout or "") + ("\n# OOPT stderr\n" + r.stderr[-4000:] if r.stderr else "")


def profiled(text: str) -> bool:
    """A cached profiling output is reusable only if JMH actually produced a stack profile."""
    return "[Thread state: RUNNABLE]" in text


def jmh_gc(t, overlay: Path | None, jar: Path, bench: str, params: dict | None, out_json: Path,
           timeout: int = 600, log: Path | None = None, hb=None) -> dict | None:
    """Allocation per operation with the overlay first on the classpath (the forked JVM inherits it)."""
    out_json.parent.mkdir(parents=True, exist_ok=True)       # JMH does not create the results directory
    out_json.unlink(missing_ok=True)
    log = log or out_json.with_suffix(".log")                # keep JMH's own output for diagnosis
    classpath = cp(overlay, jar)
    args = [str(t.java), *JMH_HOST, "-cp", classpath, "org.openjdk.jmh.Main", f"^{re.escape(bench)}$", "-f", "1", "-wi", "2",
            "-w", "1s", "-i", "3", "-r", "1s", "-foe", "false", "-prof", "gc", "-rf", "json", "-rff", str(out_json)]
    for k, v in sorted((params or {}).items()):
        args += ["-p", f"{k}={v}"]
    run(args, env=t.env(), timeout=timeout, log=log, hb=hb)
    try:
        rows = json.loads(out_json.read_text())
    except (OSError, ValueError):
        return None
    if not rows:
        return None
    r0 = rows[0]
    sec = r0.get("secondaryMetrics") or {}
    norm = next((v.get("score") for k, v in sec.items() if k.endswith("gc.alloc.rate.norm")), None)
    return {"bytes_per_op": norm, "score": (r0.get("primaryMetric") or {}).get("score"),
            "unit": (r0.get("primaryMetric") or {}).get("scoreUnit"), "mode": r0.get("mode"), "params": r0.get("params")}
