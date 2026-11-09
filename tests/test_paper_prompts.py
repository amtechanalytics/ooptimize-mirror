"""Prompt prefix, item rendering and the reply schema (certificate E13)."""
import _ooptpaper  # noqa: F401  (registers the study as package 'ooptpaper')
import json
import re

import pytest

from ooptpaper import prompts

M = {"lib": "JCTools", "file": "jctools-core/src/main/java/org/jctools/Q.java", "class": "org.jctools.Q$Node",
     "src": "public int size() {\n  return n;\n}", "ctx_r2": "class Q { int n; }", "ctx_r3": "// call sites"}


def test_prefix_is_cacheable_and_clean():
    assert len(prompts.PREFIX) >= 4800            # >= ~1,200 tokens: clears the 1,024-token cache minimum
    assert not re.search(r"\d{4}-\d{2}-\d{2}", prompts.PREFIX)      # no dates or ids in the stable prefix
    assert "{" in prompts.PREFIX and "claimed_change" in prompts.PREFIX


def test_items_per_rung_and_target_last():
    r1, r2, r3 = (prompts.render_item(M, r) for r in ("R1", "R2", "R3"))
    assert "class Q { int n; }" not in r1 and "class Q { int n; }" in r2 and "// call sites" in r3
    for it in (r1, r2, r3):
        assert it.rstrip().endswith("```") and it.rindex(M["src"]) > it.rindex("Target method")
    assert "org.jctools.Q.Node" in r1


def _ok(**kw):
    d = {"patched_method": "public int size() { return n; }", "rationale": "r", "claimed_change": "NONE",
         "confidence": "HIGH"}
    d.update(kw)
    return d


def test_parse_plain_fenced_and_wrapped():
    assert prompts.parse_generate(json.dumps(_ok()))["claimed_change"] == "NONE"
    assert prompts.parse_generate("```json\n" + json.dumps(_ok()) + "\n```")["confidence"] == "HIGH"
    assert prompts.parse_generate("Here you go:\n" + json.dumps(_ok(claimed_change="loop")) + "\nThanks")[
        "claimed_change"] == "LOOP"
    p = prompts.parse_generate(json.dumps(_ok(patched_method="```java\npublic int size() { return n; }\n```")))
    assert not p["patched_method"].startswith("```")


@pytest.mark.parametrize("bad", [_ok(patched_method=""), _ok(patched_method="return n;"), _ok(claimed_change="FAST"),
                                 _ok(confidence="0.9"), {"x": 1}])
def test_parse_rejects(bad):
    with pytest.raises(ValueError):
        prompts.parse_generate(json.dumps(bad))


def test_parse_rejects_non_json():
    with pytest.raises(ValueError):
        prompts.parse_generate("I cannot help with that.")
