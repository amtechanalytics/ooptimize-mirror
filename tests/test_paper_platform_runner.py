"""The runner's JUnit Platform path against the real JUnit Platform launcher (the jars Gradle ships), with a stub
TestEngine that reports one passing and one failing test. Regression for the 7 Nov canary: LauncherFactory
returns a package-private launcher, so Launcher.execute must be looked up on the public interface. Also the
classpath completion rule (platform_needs) on the four real classpath shapes seen on the instance."""
import _ooptpaper  # noqa: F401  (registers the study as package 'ooptpaper')
import glob
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ooptpaper import env, javatools, s_build

PLUGINS = Path("/opt/gradle-8.14.3/lib/plugins")

ENGINE = """import java.util.Optional;
import org.junit.platform.engine.*;
import org.junit.platform.engine.support.descriptor.*;
public class StubEngine implements TestEngine {
    public String getId() { return "stub"; }
    public TestDescriptor discover(EngineDiscoveryRequest r, UniqueId id) {
        EngineDescriptor root = new EngineDescriptor(id, "stub");
        AbstractTestDescriptor cls = new AbstractTestDescriptor(id.append("class", "p.StubTest"), "StubTest") {
            public Type getType() { return Type.CONTAINER; } };
        cls.addChild(new AbstractTestDescriptor(cls.getUniqueId().append("method", "ok()"), "ok()") {
            public Type getType() { return Type.TEST; } });
        cls.addChild(new AbstractTestDescriptor(cls.getUniqueId().append("method", "bad()"), "bad()") {
            public Type getType() { return Type.TEST; } });
        root.addChild(cls);
        return root;
    }
    public void execute(ExecutionRequest req) {
        TestDescriptor root = req.getRootTestDescriptor();
        EngineExecutionListener l = req.getEngineExecutionListener();
        l.executionStarted(root);
        for (TestDescriptor c : root.getChildren()) {
            l.executionStarted(c);
            for (TestDescriptor t : c.getChildren()) {
                l.executionStarted(t);
                l.executionFinished(t, t.getDisplayName().startsWith("ok") ? TestExecutionResult.successful()
                        : TestExecutionResult.failed(new AssertionError("expected:<1> but was:<2>")));
            }
            l.executionFinished(c, TestExecutionResult.successful());
        }
        l.executionFinished(root, TestExecutionResult.successful());
    }
}
"""


@pytest.fixture(scope="module")
def platform(tmp_path_factory):
    jdk = os.environ.get("OOPT_TEST_JDK") or (str(Path(os.path.realpath(shutil.which("javac"))).parents[1])
                                              if shutil.which("javac") else None)
    jars = sorted(glob.glob(str(PLUGINS / "junit-platform-*.jar"))) + sorted(glob.glob(str(PLUGINS / "opentest4j-*.jar")))
    if not jdk or len(jars) < 4:
        pytest.skip("JDK or the JUnit Platform jars (Gradle's lib/plugins) not available")
    tmp = tmp_path_factory.mktemp("plat")
    t = env.Tools(Path(jdk), None, tmp / "nope.jar", tmp / "nope2.jar", None, tmp / "helpers")
    assert javatools.compile_helpers(t, t.helpers) == []
    eng = tmp / "engine"
    (eng / "META-INF" / "services").mkdir(parents=True)
    (eng / "StubEngine.java").write_text(ENGINE)
    (eng / "META-INF" / "services" / "org.junit.platform.engine.TestEngine").write_text("StubEngine\n")
    subprocess.run([str(t.javac), "-cp", ":".join(jars), "-d", str(eng), str(eng / "StubEngine.java")], check=True,
                   env=t.env())
    return t, javatools.cp(eng, *jars), tmp


def test_old_lookup_fails_like_the_instance(platform, tmp_path):
    """The bug as observed: looking execute up on the launcher's own (package-private) class."""
    t, cp, tmp = platform
    src = (Path(_ooptpaper.REPO) / "paper" / "java" / "OoptTestRunner.java").read_text()
    old = src.replace('launcherIface.getMethod("execute", reqIface, arr.getClass())',
                      'launcher.getClass().getMethod("execute", reqIface, arr.getClass())')
    assert old != src
    d = tmp_path / "old"
    d.mkdir()
    (d / "OoptTestRunner.java").write_text(old)
    subprocess.run([str(t.javac), "--release", "17", "-d", str(d), str(d / "OoptTestRunner.java")], check=True,
                   env=t.env())
    r = subprocess.run([str(t.java), "-cp", javatools.cp(cp, d), "OoptTestRunner", "--engine", "platform", "--out",
                        str(d / "r.json"), "p.StubTest"], env=t.env(), capture_output=True, text=True)
    res = json.loads((d / "r.json").read_text())
    assert res["run"] == 0 and "IllegalAccessException" in (res["error"] or ""), res


def test_fixed_runner_reports_each_platform_test(platform, tmp_path):
    t, cp, tmp = platform
    res = javatools.run_tests(t, cp, ["p.StubTest"], tmp_path / "r.json", engine="platform")
    assert res["error"] is None and res["engine"] == "platform", res
    assert res["run"] == 2 and res["failed"] == 1
    by = {x["id"].split("|")[0]: x for x in res["tests"]}
    assert by["p.StubTest#ok()"]["status"] == "PASS"
    assert by["p.StubTest#bad()"]["status"] == "FAIL" and "expected:<1>" in by["p.StubTest#bad()"]["message"]
    auto = javatools.run_tests(t, cp, ["p.StubTest"], tmp_path / "a.json")     # auto picks the platform
    assert auto["engine"] == "platform" and auto["run"] == 2


@pytest.mark.parametrize("names,expect", [
    # eclipse-collections unit-tests: API only -> engine, platform engine, launcher, vintage (junit 4 present)
    (["junit-4.13.2.jar", "junit-jupiter-api-6.1.3.jar", "opentest4j-1.3.0.jar", "junit-platform-commons-6.1.3.jar"],
     [("org.junit.jupiter", "junit-jupiter-engine", "6.1.3"), ("org.junit.platform", "junit-platform-engine", "6.1.3"),
      ("org.junit.platform", "junit-platform-launcher", "6.1.3"), ("org.junit.vintage", "junit-vintage-engine", "6.1.3")]),
    # netty: complete platform 1.14.3 -> nothing
    (["junit-jupiter-api-5.14.3.jar", "junit-jupiter-engine-5.14.3.jar", "junit-platform-engine-1.14.3.jar",
      "junit-platform-launcher-1.14.3.jar", "junit-platform-commons-1.14.3.jar"], []),
    # RoaringBitmap 5.12.2 complete -> nothing
    (["junit-platform-launcher-1.12.2.jar", "junit-jupiter-engine-5.12.2.jar", "junit-platform-engine-1.12.2.jar",
      "junit-jupiter-api-5.12.2.jar"], []),
    # caffeine: platform 1.14.3 complete, TestNG and JUnit 4 present -> TestNG engine 1.1.0 and vintage
    (["junit-platform-launcher-1.14.3.jar", "junit-jupiter-engine-5.14.3.jar", "junit-jupiter-api-5.14.3.jar",
      "junit-platform-engine-1.14.3.jar", "testng-7.12.0.jar", "junit-4.13.2.jar"],
     [("org.junit.vintage", "junit-vintage-engine", "5.14.3"), ("org.junit.support", "testng-engine", "1.1.0")]),
    # JCTools: JUnit 4 only -> nothing (JUnitCore path)
    (["junit-4.13.2.jar", "hamcrest-core-1.3.jar"], []),
    # older platform with TestNG -> 1.0.6
    (["junit-platform-launcher-1.10.2.jar", "junit-platform-engine-1.10.2.jar", "testng-7.8.0.jar"],
     [("org.junit.support", "testng-engine", "1.0.6")]),
])
def test_platform_needs(names, expect):
    assert s_build.platform_needs(names) == expect


def test_ensure_platform_appends_resolved_jars(monkeypatch, tmp_path):
    t = env.Tools(tmp_path / "jdk", tmp_path / "mvn" / "bin" / "mvn", tmp_path / "r.jar", tmp_path / "s.jar", None,
                  tmp_path / "h", m2=tmp_path / "m2")
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        coord = cmd.split("-Dartifact=")[1].split()[0]
        g, a, v = coord.split(":")
        if a != "junit-vintage-engine":                                        # one artifact fails to resolve
            jar = s_build._m2_jar(t, g, a, v)
            jar.parent.mkdir(parents=True, exist_ok=True)
            jar.write_bytes(b"jar")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(s_build, "run", fake_run)
    cp = ["/x/classes", "/x/junit-4.13.2.jar", "/x/junit-jupiter-api-6.1.3.jar"]
    out = s_build._ensure_platform(t, cp, tmp_path, tmp_path / "l.log", "ec")
    names = [os.path.basename(p) for p in out]
    assert names[:3] == ["classes", "junit-4.13.2.jar", "junit-jupiter-api-6.1.3.jar"]
    assert "junit-jupiter-engine-6.1.3.jar" in names and "junit-platform-launcher-6.1.3.jar" in names
    assert "junit-vintage-engine-6.1.3.jar" not in names and len(calls) == 4
    assert s_build._ensure_platform(t, out, tmp_path, tmp_path / "l.log", "ec") == out      # nothing to add
