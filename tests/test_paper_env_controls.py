"""Child-process hygiene (E20, E21) and the throw control's signature handling (E05)."""
import _ooptpaper  # noqa: F401  (registers the study as package 'ooptpaper')
import time

from ooptpaper import env, s_score


class _HB:
    def __init__(self):
        self.n = 0

    def touch(self, **kw):
        self.n += 1


def test_child_env_strips_credentials(monkeypatch, tmp_path):
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GROQ_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.setenv(k, "secret")
    monkeypatch.setenv("JAVA_TOOL_OPTIONS", "-Dx=y")
    t = env.Tools(tmp_path / "jdk", tmp_path / "mvn" / "bin" / "mvn", tmp_path / "r.jar", tmp_path / "s.jar", None,
                  tmp_path / "h")
    e = t.env()
    assert not [k for k in e if k.endswith("_API_KEY") or k in ("CLAUDE_CODE_OAUTH_TOKEN", "GITHUB_TOKEN")]
    assert "JAVA_TOOL_OPTIONS" not in e and e["JAVA_HOME"] == str(tmp_path / "jdk")
    assert f"-Dmaven.repo.local={t.m2}" in e["MAVEN_OPTS"]


def test_run_timeout_and_heartbeat(monkeypatch, tmp_path):
    monkeypatch.setattr(env, "BEAT_SECONDS", 0.1)
    hb = _HB()
    t0 = time.time()
    r = env.run(["sleep", "5"], timeout=1, hb=hb, log=tmp_path / "x.log")
    assert r.returncode == 124 and time.time() - t0 < 4 and "timeout" in r.stderr
    assert hb.n >= 3 and (tmp_path / "x.log").read_text().count("exit 124") == 1
    assert env.run(["true"]).returncode == 0


def test_throw_variant_keeps_signature():
    m = {"name": "get", "src": "@SuppressWarnings({\"unchecked\"})\npublic <T extends Comparable<T>> T get(int i, "
                               "java.util.function.Function<int[], T> f) throws java.io.IOException {\n  return null;\n}"}
    v = s_score.throw_variant(m)
    assert v.startswith(m["src"][:m["src"].index(") throws") + 1])
    assert "throws java.io.IOException {" in v and "return null" not in v
    assert v.rstrip().endswith("}") and "oopt-control" in v
