"""Self-checking tests for the non-select planner changes: SOURCE STRUCTURE
signal + proposer prompt rules, source-order voting and Foundations pin in
order_chapters, doc role plumbing, settle-poll criterion. Run:

    cd apps/fastapi && .venv/bin/python ../../scripts/dd/test_planner_batch.py asyncio_fixture.json
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "fastapi"))
import domains  # noqa: E402

nodes = domains.dd.planner.nodes
fx = json.load(open(sys.argv[1]))
entries = fx["entries"]

# --- SOURCE STRUCTURE: only for corpora with multi-page families
block = nodes.chapter_propose.domain.build_source_structure(entries)
print(block[:600])
assert "Coroutines and tasks (21 pages)" in block and block.splitlines()[0].startswith("  1. ")
assert "(+ " in block and "single-page docs" in block
assert nodes.chapter_propose.domain.build_source_structure([{**e, "url": "https://x/one.txt"} for e in entries]) == ""      # single-URL dump
assert nodes.chapter_propose.domain.build_source_structure([{**e, "url": f"https://x/{i}"} for i, e in enumerate(entries)]) == ""  # flat site
pos = [block.index(t) for t in ("Coroutines and tasks", "Streams", "Event loop", "Extending")]
assert pos == sorted(pos), "families must be listed in navigation (idx) order"

# --- proposer prompt: structure block + rules on the local path; nothing extra without structure
mk = lambda ss: nodes.chapter_propose.prompts.build_propose_prompt(
    framework="asyncio", source_keys=[e["key"] for e in entries[:5]], distillates=fx["distillates"], bodies_by_key=None,
    seeds={"headings": [], "namespaces": []}, body_chars_per_doc=100, target_chapters=9, source_structure=ss)
with_ss, without = mk(block), mk("")
# NB: when the deployed LangFuse template is reachable it wins and _extra_signal_block is
# appended; otherwise the local f-string runs. Both paths must satisfy these:
assert "SOURCE STRUCTURE" in with_ss and "Foundations chapter automatically" in with_ss
assert "SOURCE STRUCTURE" not in without and "Foundations chapter automatically" not in without
assert "30% of the docs" in with_ss and "30% of the docs" in without
extra = nodes.chapter_propose.prompts._extra_signal_block
assert "SOURCE STRUCTURE" in extra(block) and "SOURCE STRUCTURE" not in extra("") and "30%" in extra("")
assert extra(block).endswith("no markdown wrap.") and extra("").endswith("no markdown wrap.")

# --- order_chapters: source ranking, voting, Foundations pin
od = nodes.order_chapters.domain
key_idx = {e["key"]: e["idx"] for e in entries}
chapters = [
    {"title": "Late", "member_doc_keys": [entries[90]["key"], entries[91]["key"]]},
    {"title": "Foundations and Diagnostics", "role": "foundations", "member_doc_keys": [entries[100]["key"]]},
    {"title": "Early", "member_doc_keys": [entries[1]["key"], entries[2]["key"]]},
    {"title": "Mid", "member_doc_keys": [entries[40]["key"]]},
    {"title": "Unknown", "member_doc_keys": ["not-in-manifest"]},
]
rank = od.source_order_ranking(chapters, key_idx)
assert rank == [2, 3, 0, 1, 4], rank                       # by mean idx; unknown last
assert od.source_order_ranking([{"member_doc_keys": ["zzz"]}], key_idx) is None
llm = [[0, 3, 2, 1, 4]] * 3                                # LLM samples say Late first
agg = od.borda_aggregate(llm + [rank] * 2, 5)
print("borda with source votes:", agg, "| llm alone:", od.borda_aggregate(llm, 5))
final, fnd = od.pin_foundations_first(agg, chapters)
assert final[0] == 1 and fnd == [1] and sorted(final) == list(range(5))
assert od.pin_foundations_first([2, 0], [{"title": "a"}, {"title": "b"}, {"title": "c"}]) == ([2, 0], [])

# --- doc_distill role plumbing
ds = nodes.doc_distill
d = ds.domain.normalize_distillate("Explains the asyncio runner API for running coroutines in scripts and applications.", ["a", "bb", "ccc"], "k", "# t", "overview")
assert d.role == "overview" and ds.domain.build_fallback_distillate("k", "# t").role == "reference"
try:
    ds.schemas.DocDistillateRaw(summary="x", key_terms=["a"], role="nonsense"); raise SystemExit("bad role accepted")
except Exception as e:
    assert "role" in str(e)

# --- settle criterion
pr = domains.settings.chat.domain.pool_recovered
assert pr(0, 71, 0.25) and pr(17, 71, 0.25) and not pr(18, 71, 0.25) and not pr(0, 0, 0.25)
assert domains.settings.chat.domain.rotator_health_url("http://h:8000/api/v1/llm/openai/v1") == "http://h:8000/api/v1/llm/routing/health"
print("OK")
