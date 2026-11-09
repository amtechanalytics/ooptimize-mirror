"""S0 toolchain and S0 build.
toolchain: the pinned JDK, Maven, Randoop, SpotBugs and EqualsVerifier answer; the Java helpers compile.
build:     each library is copied out of its verified pin, built once (library, test classes, JMH jar), and its
           test-runtime classpath per module is recorded, so scoring never runs Maven or Gradle again."""
from __future__ import annotations

import os
import re
import shutil
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rh.exitcodes import GateFailed

from . import fakes
from .env import bind, fake, note, read_json, run, scope_dir, tools, work_dir, write_json
from .javatools import compile_helpers, jmh_list, version
from .libraries import GRADLE_COMMON, LIBS, MVN_COMMON
from .subjects import libs_for

MIN_BENCHMARKS = 20
BUILD_TIMEOUT = 5400

GRADLE_INIT = """
allprojects {
  afterEvaluate { p ->
    if (p.extensions.findByName('sourceSets') != null) {
      p.tasks.register('ooptClasspath') {
        doLast {
          def ss = p.extensions.getByName('sourceSets')
          def d = p.layout.buildDirectory.get().asFile
          d.mkdirs()
          new File(d, 'oopt-test-cp.txt').text = ss.getByName('test').runtimeClasspath.files.join(File.pathSeparator)
        }
      }
    }
  }
}
"""


# ------------------------------------------------------------------ toolchain
def run_toolchain(ctx) -> dict:
    bind(ctx)
    out = scope_dir(ctx.mode) / "toolchain.json"
    if fake():
        d = fakes.toolchain()
        write_json(out, d)
        return {"ok": True, **d}
    t = tools()
    d = {"java": version(t, "java"), "javac": version(t, "javac"), "mvn": version(t, "mvn"),
         "randoop": t.randoop.exists() and t.randoop.stat().st_size > 1_000_000,
         "spotbugs": t.spotbugs.exists(), "equalsverifier": bool(t.ev and t.ev.exists())}
    d["helper_errors"] = compile_helpers(t, t.helpers, log=scope_dir(ctx.mode) / "toolchain.log")
    write_json(out, d)
    note("TOOLS", f"java={d['java']!r} mvn={d['mvn']!r} helpers={'ok' if not d['helper_errors'] else 'FAIL'}")
    return {"ok": True, **d}


def check_toolchain(ctx, m) -> list[str]:
    errs = []
    if "21" not in str(m.get("java", "")) and not fake():
        errs.append(f"java is not 21: {m.get('java')!r}")
    for k in ("javac", "mvn"):
        if not m.get(k):
            errs.append(f"{k} does not run")
    for k in ("randoop", "spotbugs", "equalsverifier"):
        if not m.get(k):
            errs.append(f"{k} jar missing or empty")
    errs += m.get("helper_errors") or []
    return errs


# ------------------------------------------------------------------ build
def _copy_tree(src: Path, dst: Path) -> None:
    marker = dst / ".oopt_copied"
    if marker.exists():
        return
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src.resolve(), dst, symlinks=True, ignore=shutil.ignore_patterns(".git"))
    for g in dst.glob("gradlew"):
        g.chmod(g.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    marker.write_text("ok\n")


def _cmd(t, template: str) -> str:
    return template.replace("{mvn}", str(t.mvn)).replace("{java_home}", str(t.java_home))


def _module_dirs(tool: str, tree: Path, mod: str) -> dict:
    base = tree / mod
    if tool == "maven":
        return {"classes": str(base / "target" / "classes"), "test_classes": str(base / "target" / "test-classes"),
                "test_src": str(base / "src" / "test" / "java")}
    return {"classes": str(base / "build" / "classes" / "java" / "main"),
            "test_classes": str(base / "build" / "classes" / "java" / "test"),
            "test_src": str(base / "src" / "test" / "java")}


def _classpath_maven(t, tree: Path, mod: str, log: Path) -> list[str]:
    outf = tree / mod / "target" / "oopt-test-cp.txt"
    run(f"{t.mvn} {MVN_COMMON} -o dependency:build-classpath -Dmdep.includeScope=test "
        f"-Dmdep.outputFile={outf} -pl {mod}", cwd=tree, env=t.env(), timeout=1200, log=log, shell=True)
    if not outf.exists():   # offline resolution can miss the plugin itself on a cold cache: retry online
        run(f"{t.mvn} {MVN_COMMON} dependency:build-classpath -Dmdep.includeScope=test "
            f"-Dmdep.outputFile={outf} -pl {mod}", cwd=tree, env=t.env(), timeout=1200, log=log, shell=True)
    return [p for p in outf.read_text().strip().split(":") if p] if outf.exists() else []


def _classpath_gradle(t, tree: Path, mods: list[str], log: Path, flags: str = GRADLE_COMMON) -> dict:
    init = tree / "oopt.init.gradle"
    init.write_text(GRADLE_INIT)
    tasks = " ".join(f":{m}:ooptClasspath" for m in mods)
    run(f"./gradlew {_cmd(t, flags)} -I {init} {tasks}", cwd=tree, env=t.env(), timeout=1800, log=log,
        shell=True)
    out = {}
    for m in mods:
        f = tree / m / "build" / "oopt-test-cp.txt"
        out[m] = [p for p in f.read_text().strip().split(":") if p] if f.exists() else []
    return out


_JAR = re.compile(r"^(junit-jupiter-api|junit-jupiter-engine|junit-platform-engine|junit-platform-launcher|"
                  r"junit-vintage-engine|testng-engine|testng|junit)-(\d+(?:\.\d+)+(?:-[\w.]+)?)\.jar$")


def _platform_version(jupiter: str) -> str:
    """Jupiter 5.x.y pairs with Platform 1.x.y; from JUnit 6 the two share one version."""
    major = int(jupiter.split(".")[0])
    return jupiter if major >= 6 else "1" + jupiter[1:]


def platform_needs(names: list[str]) -> list[tuple[str, str, str]]:
    """Maven coordinates (group, artifact, version) of the JUnit Platform runtime missing from a test classpath.
    Build tools add the engines and the launcher at test time (surefire/Gradle), so dependency:build-classpath
    often lists only the API: eclipse-collections (jupiter-api 6.1.3, no engine), netty/RoaringBitmap (launcher
    present), caffeine (TestNG tests need the TestNG engine). Without them the runner falls back to JUnit 4
    and keeps no test (7 Nov canary: 8/197 methods with a kept test)."""
    have = {}
    for n in names:
        m = _JAR.match(n)
        if m:
            have.setdefault(m.group(1), m.group(2))
    need = []
    jup = have.get("junit-jupiter-api") or have.get("junit-jupiter-engine")
    plat = have.get("junit-platform-engine") or have.get("junit-platform-launcher") or (jup and _platform_version(jup))
    if not plat:
        return need
    if jup and "junit-jupiter-engine" not in have:
        need.append(("org.junit.jupiter", "junit-jupiter-engine", jup))
    if "junit-platform-engine" not in have:
        need.append(("org.junit.platform", "junit-platform-engine", plat))
    if "junit-platform-launcher" not in have:
        need.append(("org.junit.platform", "junit-platform-launcher", plat))
    if "junit" in have and jup and "junit-vintage-engine" not in have:
        need.append(("org.junit.vintage", "junit-vintage-engine", jup))          # JUnit 4 tests under the platform
    if "testng" in have and "testng-engine" not in have:
        pm = tuple(int(x) for x in plat.split(".")[:2])
        need.append(("org.junit.support", "testng-engine", "1.1.0" if pm >= (1, 14) else "1.0.6"))
    return need


def _m2_jar(t, group: str, artifact: str, version: str) -> Path:
    return t.m2.joinpath(*group.split("."), artifact, version, f"{artifact}-{version}.jar")


def _ensure_platform(t, cp_list: list[str], tree: Path, log: Path, note_lib: str = "") -> list[str]:
    """Resolve the missing JUnit Platform runtime from Maven Central into the harness's local repository and append
    the jars to the classpath. Best effort per artifact: a jar that cannot be resolved is logged and skipped."""
    names = [os.path.basename(p) for p in cp_list]
    out = list(cp_list)
    for group, artifact, version in platform_needs(names):
        jar = _m2_jar(t, group, artifact, version)
        if not jar.exists():
            run(f"{t.mvn} -B -q dependency:get -Dartifact={group}:{artifact}:{version}", cwd=tree, env=t.env(),
                timeout=600, log=log, shell=True)
        if jar.exists():
            out.append(str(jar))
        else:
            note("BUILD", f"{note_lib}: could not resolve {group}:{artifact}:{version}; tests needing it will not run")
    return out


def build_present(rec: dict) -> bool:
    """A build record is only reusable when its tree is on this machine: on a resume on a new instance
    /opt/data (trees, Maven/Gradle caches) is empty while runs/ came back from the backup."""
    if not rec.get("tree") or not Path(rec["tree"]).exists() or not rec.get("jmh_jar") or \
            not Path(rec["jmh_jar"]).exists():
        return False
    for d in (rec.get("modules") or {}).values():
        if not Path(d["classes"]).exists() or any(not Path(p).exists() for p in d["test_cp"] if p.endswith(".jar")):
            return False
    return True


def _build_one(ctx, t, lib: str) -> dict:
    from rh import datapins
    spec = LIBS[lib]
    outdir = scope_dir(ctx.mode) / "build"
    prev = read_json(outdir / f"{lib}.json")
    if prev and prev.get("ok") and build_present(prev):
        return prev
    log = outdir / f"{lib}.log"
    tree = work_dir(ctx.mode) / "src" / lib
    rec = {"lib": lib, "ok": False, "tree": str(tree), "tool": spec["tool"], "errors": []}
    try:
        _copy_tree(datapins.path(spec["pin"]), tree)
        for c in spec["build"]:
            r = run(_cmd(t, c), cwd=tree, env=t.env(), timeout=BUILD_TIMEOUT, hb=ctx.hb, log=log, shell=True)
            if r.returncode != 0:
                rec["errors"].append(f"build step failed (exit {r.returncode}): {c[:120]} :: {(r.stderr or r.stdout)[-600:]}")
                write_json(outdir / f"{lib}.json", rec)
                return rec
        mods = sorted({m for pair in spec["roots"].values() for m in pair})
        modules = {m: _module_dirs(spec["tool"], tree, m) for m in mods}
        if spec["tool"] == "maven":
            for m in mods:
                modules[m]["test_cp"] = _classpath_maven(t, tree, m, log)
        else:
            cps = _classpath_gradle(t, tree, mods, log, spec.get("gradle_flags", GRADLE_COMMON))
            for m in mods:
                modules[m]["test_cp"] = cps.get(m, [])
        for m in mods:
            cpl = modules[m]["test_cp"]
            for d in (modules[m]["classes"], modules[m]["test_classes"]):
                if d not in cpl:
                    cpl.insert(0, d)
            modules[m]["test_cp"] = _ensure_platform(t, cpl, tree, log, lib)
            if len(modules[m]["test_cp"]) < 2:
                rec["errors"].append(f"{m}: empty test classpath")
        jar = spec.get("jmh_jar")
        jar_path = tree / jar if jar else next(iter(sorted(tree.glob(spec["jmh_jar_glob"]))), None)
        if not jar_path or not Path(jar_path).exists():
            rec["errors"].append(f"JMH jar not found ({jar or spec.get('jmh_jar_glob')})")
            benches = []
        else:
            benches = jmh_list(t, Path(jar_path))
        rec.update(modules=modules, jmh_jar=str(jar_path) if jar_path else None, benchmarks=benches,
                   roots={r: list(v) for r, v in spec["roots"].items()})
        if len(benches) < MIN_BENCHMARKS:
            rec["errors"].append(f"JMH jar lists {len(benches)} benchmarks < {MIN_BENCHMARKS}")
        rec["ok"] = not rec["errors"]
    except Exception as e:  # noqa: BLE001 — recorded, the gate decides
        rec["errors"].append(f"{type(e).__name__}: {e}")
    write_json(outdir / f"{lib}.json", rec)
    note("BUILD", f"{lib}: {'OK' if rec['ok'] else 'FAIL'} benchmarks={len(rec.get('benchmarks') or [])} "
                  f"{'; '.join(rec['errors'])[:300]}")
    return rec


def run_build(ctx) -> dict:
    bind(ctx)
    libs = libs_for(ctx.mode)
    outdir = scope_dir(ctx.mode) / "build"
    outdir.mkdir(parents=True, exist_ok=True)
    if fake():
        recs = [fakes.build(lib, scope_dir(ctx.mode)) for lib in libs]
        for r in recs:
            write_json(outdir / f"{r['lib']}.json", r)
    else:
        t = tools()
        ctx.hb.set(total=len(libs), done=0)
        with ThreadPoolExecutor(max_workers=len(libs), thread_name_prefix="build") as ex:
            futs = [ex.submit(_build_one, ctx, t, lib) for lib in libs]
            recs = []
            for f in futs:
                recs.append(f.result())
                ctx.hb.progress(lane="build")
    built = [r["lib"] for r in recs if r.get("ok")]
    return {"libraries": len(libs), "built": built, "failed": {r["lib"]: r.get("errors") for r in recs if not r.get("ok")},
            "benchmarks": {r["lib"]: len(r.get("benchmarks") or []) for r in recs}}


def check_build(ctx, m) -> list[str]:
    need = 1 if ctx.mode == "pilot" else 3
    if len(m["built"]) < need:
        return [f"{len(m['built'])} libraries built < {need}: {m['failed']}"]
    return []


def require_built(ctx, lib: str) -> dict:
    from .subjects import build_info
    b = build_info(ctx.mode, lib)
    if not b or not b.get("ok"):
        raise GateFailed(f"library {lib} has no successful build record")
    return b
