"""A03: real replies parse under each llm stage's schema for every (arm, model) group in the pilot."""
from conftest import read_jsonl


def test_every_group_parsed(runs):
    import paper
    for st in paper.STAGES:
        if st.kind != "llm":
            continue
        rows = read_jsonl(runs / "pilot" / f"{st.name}.jsonl")
        assert rows, f"no pilot rows for {st.name}"
        groups = {f"{r.get('arm')}|{r['model']}" for r in rows}
        ok = {f"{r.get('arm')}|{r['model']}" for r in rows if r["status"] == "ok"}
        assert groups == ok, f"{st.name}: groups without a parsed reply: {sorted(groups - ok)}"
        assert not [r for r in rows if r.get("truncated")], "truncated replies in the pilot"
