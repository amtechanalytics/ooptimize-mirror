"""E08: the pinned EqualsVerifier, through the reflection helper, passes a correct equals/hashCode and fails a
broken one under the fixed configuration (the R4 oracle can fire)."""
import subprocess

import pytest

from conftest import fake

GOOD = ("public final class P { private final int a; public P(int a) { this.a = a; } "
        "@Override public boolean equals(Object o) { return o instanceof P && ((P) o).a == a; } "
        "@Override public int hashCode() { return a; } }")
BAD = ("public final class P { private final int a; public P(int a) { this.a = a; } "
       "@Override public boolean equals(Object o) { return o instanceof P && ((P) o).a == a; } "
       "@Override public int hashCode() { return System.identityHashCode(this); } }")


def test_ev(tmp_path):
    if fake():
        pytest.skip("dry run: no JDK")
    from paper import env, javatools
    t = env.tools()
    res = {}
    for name, src in (("good", GOOD), ("bad", BAD)):
        d = tmp_path / name
        d.mkdir()
        (d / "P.java").write_text(src)
        subprocess.run([str(t.javac), "-d", str(d), str(d / "P.java")], check=True, env=t.env())
        res[name] = javatools.equals_verifier(t, "P", str(d))
    assert res["good"][0] == "PASS", res
    assert res["bad"][0] == "FAIL", res
