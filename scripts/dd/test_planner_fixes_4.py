"""Offline checks for the 2026-10-13 round-4 fixes (no LLM, no cluster): assigner-tie break by word
overlap, cluster-based split (+ naming), catch-all appendix split, title-based shuffled ordering,
migration pin, sample angles, assign length retry.

    cd apps/fastapi && .venv/bin/python ../../scripts/dd/test_planner_fixes_4.py [planner_artifacts.jsonl]

With a dump from dump_planner_artifacts.py it also replays selection + the computed split on every
stored framework."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "fastapi"))
import domains  # noqa: E402

nodes = domains.dd.planner.nodes
SEL, SELS = nodes.chapter_select.domain, nodes.chapter_select
ORD, PR, CA = nodes.order_chapters, nodes.chapter_propose, nodes.chapter_assign
chat = domains.settings.chat


def scores(**by_idx):
    return [{"chapter_idx": int(i), "confidence": c} for i, c in by_idx.items()]


# 1) assigner ties go to the chapter the page overlaps most, not to the lowest index
props = [
    {"title": "Customizing Agents", "description": "Tools, prompts and middleware for agents.", "key_concepts": ["tools", "middleware"]},
    {"title": "Model Integrations", "description": "Chat model provider integrations.", "key_concepts": ["anthropic", "openai", "ollama", "provider"]},
    {"title": "Sandboxes", "description": "Sandbox isolation and permissions.", "key_concepts": ["sandbox", "permissions"]},
]
docs = {f"d{i}": f"anthropic provider integration chat model page{i}" for i in range(4)}
docs |= {f"t{i}": f"tools middleware prompts customizing page{i}" for i in range(4)}
docs |= {f"s{i}": f"sandbox permissions isolation page{i}" for i in range(4)}
assign = {k: scores(**{"0": 0.7, "1": 0.7, "2": 0.0}) for k in docs if k.startswith(("d", "t"))}
assign |= {k: scores(**{"0": 0.0, "1": 0.0, "2": 1.0}) for k in docs if k.startswith("s")}
aff = SEL.doc_affinity(props, docs)
sel_idx, d2c = SEL.greedy_select(proposals=props, assignments=assign, pinned_indices=set(), affinity=aff)
assert all(d2c[f"d{i}"] == 1 for i in range(4)) and all(d2c[f"t{i}"] == 0 for i in range(4)), d2c
sel_idx0, d2c0 = SEL.greedy_select(proposals=props, assignments=assign, pinned_indices=set())
assert all(d2c0[f"d{i}"] == 0 for i in range(4)), "without affinity the lowest index wins (the old behaviour)"
# a real confidence gap still beats overlap
assign2 = {"d0": scores(**{"0": 1.0, "1": 0.3, "2": 0.0}), "s0": scores(**{"2": 1.0})}
_, d2c2 = SEL.greedy_select(proposals=props, assignments=assign2, pinned_indices=set(), affinity=aff)
assert d2c2["d0"] == 0
print("1 tie-break by word overlap ok")

# 2) cluster_units: two topics + a straggler -> two groups at the floor; deterministic
topic_a = [f"streams reader writer connection socket{i}" for i in range(12)]
topic_b = [f"queues producer consumer priority lifo{i}" for i in range(12)]
straggler = ["exceptions cancelled timeout"]
texts = topic_a + topic_b + straggler
units = [{"id": i, "keys": [f"k{i}"], "label": f"u{i}", "summary": ""} for i in range(len(texts))]
floor = SEL.split_floor(len(units))
g1 = SEL.cluster_units(units, texts, 2, floor)
g2 = SEL.cluster_units(units, texts, 2, floor)
assert g1 == g2 and len(g1) == 2, g1
sizes = sorted(len(g) for g in g1)
assert sizes[0] >= floor and sum(sizes) == len(units), (sizes, floor)
topics = [{i for i in g if i < 12} for g in g1]
assert any(len(t) == 12 for t in topics) or any(len(t) == 0 for t in topics), g1   # topics are not mixed
assert SEL.cluster_units(units[:1], texts[:1], 2, 3) is None
# no group above the share cap while a smaller merge exists
big = SEL.cluster_units(units, texts, 4, floor)
assert big and len(big) <= 4 and all(sum(len(units[i]["keys"]) for i in g) >= floor for g in big)
print("2 cluster_units ok:", sizes, "| up to 4 parts:", [len(g) for g in big])

# 3) plan_splits: oversize chapter and a catch-all appendix over 8%; apply_split drops the appendix role
def chap(title, n, **kw):
    return {"title": title, "description": "d", "member_doc_keys": [f"{title}-{i}" for i in range(n)], "n_member_docs": n, **kw}
plan = [chap("Big", 40, origin="proposal"), chap("A", 8), chap("B", 8), chap("C", 8), chap("D", 6),
        chap("Additional Topics", 20, origin="gap", role="appendix")]
splits = SEL.plan_splits(plan)
assert [p for p, _ in splits] == [0, 5], splits
assert dict(splits)[5] == 1 + 0 or dict(splits)[5] >= 2
small_app = [chap("Big", 10), chap("A", 8), chap("B", 8), chap("C", 8), chap("Additional Topics", 5, origin="gap", role="appendix")]
assert SEL.plan_splits(small_app) == []                                 # under the share/size gate
u2 = [{"id": i, "keys": [f"x{i}"], "label": f"u{i}", "summary": ""} for i in range(20)]
parts = [{"title": "Alpha Part", "description": "a", "unit_ids": list(range(10))}, {"title": "Beta Part", "description": "b", "unit_ids": list(range(10, 20))}]
out = SEL.apply_split(plan[5], parts, u2)
assert all("role" not in c and c["origin"] == "split" for c in out), out
print("3 plan_splits + appendix split ok:", splits)

# 4) _split_by_clusters: the model only names the groups; bad names -> retry -> deterministic names
ch = chap("Customizing Deep Agents", len(units), origin="proposal")
ch["member_doc_keys"] = [u["keys"][0] for u in units]
dist = {u["keys"][0]: {"summary": t, "key_terms": t.split()[:3]} for u, t in zip(units, texts)}
calls = []
async def _name(prompt, schema, **kw):
    calls.append(prompt)
    n_groups = prompt.count("\n[") if False else sum(1 for l in prompt.splitlines() if l.startswith("["))
    gens = ["Overview", "Streams and Writers", "Queues"]
    if len(calls) == 1:                       # first answer: generic title -> rejected
        return schema(groups=[{"id": i, "title": "Overview", "description": "d"} for i in range(n_groups)]), {}
    return schema(groups=[{"id": i, "title": f"Topic {'AB'[i]} Pages", "description": "One sentence here."} for i in range(n_groups)]), {}
chat.service.chat_structured_async = _name
res = asyncio.run(SELS.service._split_by_clusters("deepagents", ch, units, 2, ["Other Chapter"], dist))
assert res and len(res) == 2 and {c["title"] for c in res} == {"Topic A Pages", "Topic B Pages"}, res
assert len(calls) == 2 and "PRIOR OUTPUT WAS REJECTED" in calls[1], len(calls)
assert sorted(k for c in res for k in c["member_doc_keys"]) == sorted(ch["member_doc_keys"])
calls.clear()
async def _never(prompt, schema, **kw):
    raise chat.errors.ChatError("InternalServerError: 504")
chat.service.chat_structured_async = _never
res = asyncio.run(SELS.service._split_by_clusters("deepagents", ch, units, 2, ["Other Chapter"], dist))
assert res and len(res) == 2 and all(1 <= len(c["title"].split()) <= 8 for c in res), res
assert len({c["title"].casefold() for c in res}) == 2
# pages that do not cluster (one unit) -> None, the caller falls back to the model-proposed partition
assert asyncio.run(SELS.service._split_by_clusters("fw", ch, units[:1], 2, [], dist)) is None
print("4 cluster split: model names, retry on bad names, word-based names when the model fails ok:", [c["title"] for c in res])

# 5) order: title mapping, labels, shuffle, migration pin
chs = [{"title": t, "description": f"about {t}"} for t in ("Coroutines and Tasks", "Event Loop", "Queues", "Migration Guides", "Streams")]
labels = ORD.domain.chapter_labels(chs)
assert ORD.domain.titles_to_order(["Event Loop", "[0] coroutines and tasks", "Queues", "Streams", "Migration Guides"], labels) == [1, 0, 2, 4, 3]
assert ORD.domain.titles_to_order(["Coroutines & Tasks", "Event-Loop", "Queues", "Streams", "Migration Guides"], labels) == [0, 1, 2, 4, 3]   # near-matches
assert ORD.domain.titles_to_order(["Queues", "Streams"], labels) is None                                       # < 80% named
assert ORD.domain.titles_to_order(["Event Loop", "Coroutines and Tasks", "Queues", "Streams"], labels) == [1, 0, 2, 4, 3]   # one missing is appended
dup = ORD.domain.chapter_labels([{"title": "A"}, {"title": "a"}, {"title": "B"}])
assert dup == ["A", "a (2)", "B"], dup
sh = [ORD.domain.shuffled_indices(9, s) for s in range(5)]
assert all(sorted(x) == list(range(9)) for x in sh) and len({tuple(x) for x in sh}) >= 4 and sh == [ORD.domain.shuffled_indices(9, s) for s in range(5)]
o, pinned = ORD.domain.pin_trailing_last([3, 0, 1, 2, 4], chs)
assert o == [0, 1, 2, 4, 3] and pinned == [3], (o, pinned)
mix = chs + [{"title": "Additional Topics", "role": "appendix", "origin": "gap"}]
o2, p2 = ORD.domain.pin_trailing_last([5, 3, 0, 1, 2, 4], mix)
assert o2 == [5, 0, 1, 2, 4, 3] and p2 == [3], (o2, p2)                  # appendix left to its own pin
pr1, pr2 = (ORD.prompts.build_order_prompt(chs, None, "asyncio", s) for s in (0, 1))
assert "Migration and upgrade chapters" in pr1 and '"order": ["<chapter title' in pr1 and pr1 != pr2
assert all(t in pr1 for t in labels)
async def _title_order(prompt, schema, **kw):
    return schema(rationale="x", order=["Coroutines and Tasks", "Event Loop", "Queues", "Streams", "Migration Guides"]), {"deployment": "m"}
chat.service.chat_structured_async = _title_order
r, meta = asyncio.run(ORD.service.sample_one_ordering(asyncio.Semaphore(1), chs, None, "asyncio", 0))
assert r == [0, 1, 2, 4, 3], r
bad = []
async def _bad_then_ok(prompt, schema, **kw):
    bad.append(prompt)
    return schema(rationale="x", order=["Queues"] if len(bad) == 1 else ["Coroutines and Tasks", "Event Loop", "Queues", "Streams", "Migration Guides"]), {}
chat.service.chat_structured_async = _bad_then_ok
r, _ = asyncio.run(ORD.service.sample_one_ordering(asyncio.Semaphore(1), chs, None, "asyncio", 1))
assert r == [0, 1, 2, 4, 3] and len(bad) == 2 and "PRIOR OUTPUT was invalid" in bad[1]
print("5 title-based order, shuffle, migration pin ok")

# 6) propose angles
base = "PROMPT BODY"
assert PR.domain.with_angle(base, 0) == base
angles = {PR.domain.with_angle(base, i) for i in range(5)}
assert len(angles) == 5 and all(a.startswith(base) for a in angles)
assert PR.domain.with_angle(base, 12) == PR.domain.with_angle(base, 2)
print("6 sample angles ok")

# 7) assign: a score list cut at max_tokens is retried once at 2x
seen = []
async def _cut_then_ok(prompt, schema, *, max_tokens, **kw):
    seen.append(max_tokens)
    if len(seen) == 1:
        raise chat.errors.ChatError("LengthFinishReasonError: Could not parse response content as the length limit was reached")
    return CA.schemas.DocAssignment(scores=[CA.schemas.ChapterScore(chapter_idx=0, confidence=0.9)]), {}
chat.service.chat_structured_async = _cut_then_ok
a = asyncio.run(CA.service._score_call("p"))
assert seen == [CA.params.MAX_TOKENS, 2 * CA.params.MAX_TOKENS] and a.scores[0].confidence == 0.9, seen
seen.clear()
async def _always_cut(prompt, schema, *, max_tokens, **kw):
    seen.append(max_tokens)
    raise chat.errors.ChatError("LengthFinishReasonError: length limit was reached")
chat.service.chat_structured_async = _always_cut
try:
    asyncio.run(CA.service._score_call("p")); raise SystemExit("expected ChatError")
except chat.errors.ChatError:
    assert len(seen) == 2, seen              # one retry only, then the lexical fallback upstream
assert CA.params.CONCURRENCY == 12
print("7 assign length retry ok:", seen)

# 8) replay on stored artifacts: new selection + the computed split (no LLM)
if len(sys.argv) > 1:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import replay_select as rs
    for line in open(sys.argv[1]):
        row = json.loads(line)
        out = rs.run_pipeline(row)
        chapters = out["out_chapters"]
        by = {e["key"]: e for e in row["manifest"]["entries"]}
        total = sum(c["n_member_docs"] for c in chapters)
        sizes = [c["n_member_docs"] for c in chapters]
        after = list(sizes)
        for pos, k in sorted(SEL.plan_splits(chapters), reverse=True):
            c = chapters[pos]
            us = SEL.build_split_units(
                c["member_doc_keys"], anchored_family=SEL.anchored_family_map(row["manifest"]["entries"]),
                doc_title={k_: e["title"] for k_, e in by.items()}, distillates=row["distillates"],
                key_idx={k_: e["idx"] for k_, e in by.items()})
            groups = SEL.cluster_units(us, [SEL.unit_text(u, row["distillates"]) for u in us], k, SEL.split_floor(c["n_member_docs"]))
            assert groups, (row["slug"], c["title"])
            parts = [sum(len(us[i]["keys"]) for i in g) for g in groups]
            after[pos:pos + 1] = parts
            print(f"  replay {row['slug']}: {c['title']!r} ({c['n_member_docs']} pages, {round(100 * c['n_member_docs'] / total)}%) -> {parts}")
        print(f"  replay {row['slug']}: largest {round(100 * max(sizes) / total)}% -> {round(100 * max(after) / total)}% of {total}, chapters {len(sizes)} -> {len(after)}")
print("ALL OK")
