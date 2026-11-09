"""Sizing rule, library interleaving, tag round-robin, hot-frame aggregation and context cutting (E12, E03)."""
import _ooptpaper  # noqa: F401  (registers the study as package 'ooptpaper')
from types import SimpleNamespace

import pytest

from ooptpaper import s_generate, s_select, subjects
from rh.exitcodes import GateFailed


def _m(lib, i, tags=None, share=5.0):
    return {"id": f"{lib}:C{i}#m()", "lib": lib, "file": f"C{i}.java", "class": f"p.C{i}", "src": "void m() {}",
            "ctx_r2": "x" * 4000, "ctx_r3": "y" * 2000, "tags": tags or {"T_SYNC": i % 2 == 0}, "share": share}


def test_interleave_round_robin_and_cap():
    ms = [_m(lib, i) for lib in subjects.ORDER for i in range(60)]
    out = subjects.interleave(ms, 50)
    assert len(out) == 250
    assert [m["lib"] for m in out[:5]] == subjects.ORDER
    assert max(sum(1 for m in out if m["lib"] == lib) for lib in subjects.ORDER) == 50


class _Ledger:
    def __init__(self, spent):
        self.spent = spent

    def total(self):
        return self.spent


def _ctx(ceiling_tier="high", spent=0.0):
    cfg = _ooptpaper.real_config()
    cfg.budget["tier"] = ceiling_tier
    return SimpleNamespace(cfg=cfg, ledger=_Ledger(spent), mode="canary")


def _patch(monkeypatch, pool, fake=False):
    monkeypatch.setattr(s_generate, "eligible", lambda mode: pool)
    monkeypatch.setattr(s_generate, "fake", lambda: fake)
    from ooptpaper import env
    for p in (env.scope_dir("full") / "sizing.json", env.scope_dir("full") / "sizing_failed.json"):
        p.unlink(missing_ok=True)


def test_sizing_caps_at_250(monkeypatch):
    pool = [_m(lib, i) for lib in subjects.ORDER for i in range(60)]
    _patch(monkeypatch, pool)
    rec = s_generate.sizing(_ctx())
    assert rec["n"] == 250 and rec["projected_full_api"] <= rec["available_api"]
    assert all(v <= 50 for v in rec["per_library"].values())
    assert s_generate.sizing(_ctx())["methods"] == rec["methods"]            # written once, reused on resume


def test_sizing_shrinks_to_fit_and_floor_stops(monkeypatch):
    big = [dict(_m(lib, i), ctx_r2="x" * 90000, ctx_r3="y" * 40000) for lib in subjects.ORDER for i in range(60)]
    _patch(monkeypatch, big)
    ctx = _ctx(spent=0.0)
    full = s_generate.method_cost(ctx.cfg, s_generate.calibration(ctx.cfg), big[0])
    # leave room for roughly 160 methods: just above the floor
    spent = ctx.cfg.ceiling() - 63.56 - 20 - (160 * full + 1) / 0.85
    rec = s_generate.sizing(_ctx(spent=spent))
    assert 150 <= rec["n"] < 250
    _patch(monkeypatch, big)
    with pytest.raises(GateFailed):
        s_generate.sizing(_ctx(spent=spent + 30))


def test_round_robin_over_tags():
    cands = [_m("JCTools", i, tags={"T_SYNC": True}, share=10 - i * 0.1) for i in range(10)] + \
            [_m("JCTools", 100 + i, tags={"T_EQUALS": True}, share=1 + i * 0.1) for i in range(3)] + \
            [_m("JCTools", 200 + i, tags={}, share=2.0) for i in range(2)]
    ch = s_select.round_robin(cands, 6)
    assert len(ch) == 6 and len({c["id"] for c in ch}) == 6
    assert ch[0]["tags"].get("T_EQUALS") and ch[1]["tags"].get("T_SYNC")
    assert any(not any(c["tags"].values()) for c in ch)


def test_hot_frames_take_max_share_over_runs():
    from test_paper_oracles import JMH
    hot = s_select.hot_frames([JMH])
    assert hot[("org.jctools.queues.MpscArrayQueue", "offer")]["share"] == 40.0
    assert hot[("org.jctools.queues.SpscArrayQueue", "offer")]["params"]["qType"] == "SpscArrayQueue"
    assert ("org.jctools.queues.MpscArrayQueue$1", "run") not in hot


def test_class_window_keeps_header_and_method():
    text = "package p;\nimport x.Y;\nclass A {\n" + "  int f;\n" * 8000 + "  void target() { }\n" + "  int g;\n" * 8000 + "}"
    s = text.index("void target")
    w = s_select.class_window(text, s, s + 18, cap=24000)
    assert len(w) <= 30000 and "void target() { }" in w and w.startswith("package p;")


def test_hierarchy_and_call_sites(tmp_path):
    root = tmp_path / "lib" / "src" / "main" / "java" / "p"
    root.mkdir(parents=True)
    (root / "Base.java").write_text("package p;\npublic abstract class Base {\n  public abstract int size();\n}\n")
    (root / "A.java").write_text("package p;\npublic class A extends Base {\n  public int size() { return 1; }\n}\n")
    (root / "B.java").write_text("package p;\npublic class B extends A {\n  int g() { return size(); }\n}\n")
    (root / "U.java").write_text("package p;\nclass U {\n  int h(A a) {\n    return a.size();\n  }\n}\n")
    s_select.LIBS["toy"] = {"roots": {"lib/src/main/java": ("lib", "lib")}, "packages": ["p"]}
    try:
        idx = s_select._index(tmp_path, "toy")
        out = s_select.hierarchy_and_calls(tmp_path, "toy", "lib/src/main/java/p/A.java", "A", "size", idx)
    finally:
        del s_select.LIBS["toy"]
    assert "supertype Base" in out and "subtype" in out and "U.java:4" in out
