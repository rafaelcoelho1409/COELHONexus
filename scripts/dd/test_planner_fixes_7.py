"""Offline checks for the 2026-10-14 batch-9 fixes (no LLM, no cluster): the assign re-ask skip rule,
directory-based sibling chapters in the order, conservative micro-chapter merge.

    cd apps/fastapi && .venv/bin/python ../../scripts/dd/test_planner_fixes_7.py [planner_artifacts.jsonl ...]

With dumps from dump_planner_artifacts.py it also replays the new rules on every stored plan."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "fastapi"))
import domains  # noqa: E402

nodes = domains.dd.planner.nodes
CA, SEL, OC = nodes.chapter_assign, nodes.chapter_select.domain, nodes.order_chapters.domain


# 1) assign: a vector missing ONE chapter whose best score is confident needs no reask --------------------------
S = CA.schemas
CA.params.FIXED_KEY_OUTPUT = False                                             # (the fixed-key path has its own test below)
props = [{"title": f"C{i}", "description": "d", "key_concepts": []} for i in range(28)]
sent = []
def vec(idxs, best_idx=3, best=0.9):
    return S.DocAssignment(scores=[S.ChapterScore(chapter_idx=i, confidence=best if i == best_idx else 0.0) for i in idxs])
async def run(first):
    sent.clear()
    async def _score(prompt):
        sent.append(prompt)
        return vec(range(16, 28), best_idx=20, best=0.8)
    CA.service._score_call = _score
    async def _first(prompt):
        if not sent:
            sent.append(prompt)
            return first
        return await _score(prompt)
    CA.service._score_call = _first
    return await CA.service.assign_one(asyncio.Semaphore(1), "fw", "k", {"summary": "s", "key_terms": ["t"]}, props)
res = asyncio.run(run(vec(range(27))))                                          # last chapter dropped, best 0.9 -> accepted
assert len(sent) == 1 and len(res[1]) == 27, (len(sent), len(res[1]))
res = asyncio.run(run(vec(range(27), best=0.3)))                                # last chapter dropped, but nothing confident yet -> reask
assert len(sent) == 2 and "[27]" in sent[1], sent[-1][-120:]
res = asyncio.run(run(vec(range(16))))                                          # tail 16-27 cut off -> reask finds chapter 20
assert len(sent) == 2 and {s["chapter_idx"]: s["confidence"] for s in res[1]}[20] == 0.8
res = asyncio.run(run(vec([i for i in range(28) if i not in (5, 27)])))        # two missing -> reask
assert len(sent) == 2
assert CA.domain.can_skip_reask([{"chapter_idx": 0, "confidence": 0.7}], [27]) and not CA.domain.can_skip_reask([], [27])
print("1 assign reask skip ok")

# 1b) fixed-key scoring: one required key per chapter; malformed -> the score list; short plans keep the list ----------------
CA.params.FIXED_KEY_OUTPUT = True
calls = []
async def _fixed_stub(prompt, schema=None):
    calls.append(("fixed" if schema is not None else "list", prompt))
    if schema is not None:
        return schema(**{f"c{i}": (0.9 if i == 4 else 0.0) for i in range(28)})
    return vec(range(28), best_idx=1, best=0.8)
CA.service._score_call = _fixed_stub
res = asyncio.run(CA.service.assign_one(asyncio.Semaphore(1), "fw", "k", {"summary": "s", "key_terms": ["t"]}, props))
sc = {x["chapter_idx"]: x["confidence"] for x in res[1]}
assert [c[0] for c in calls] == ["fixed"] and len(sc) == 28 and sc[4] == 0.9, (calls[:1], len(sc))
assert "EXACTLY 28 keys" in calls[0][1] and '"c27"' in calls[0][1] and "chapter_idx\": 0" not in calls[0][1].split("OUTPUT")[1].split("== FILE")[0]
calls.clear()
async def _bad_then_list(prompt, schema=None):
    calls.append("fixed" if schema is not None else "list")
    if schema is not None:
        schema(**{f"c{i}": 0.0 for i in range(27)})                              # a missing key -> pydantic ValidationError
    return vec(range(28), best_idx=1, best=0.8)
CA.service._score_call = _bad_then_list
res = asyncio.run(CA.service.assign_one(asyncio.Semaphore(1), "fw", "k", {"summary": "s", "key_terms": ["t"]}, props))
assert calls == ["fixed", "list"] and {x["chapter_idx"]: x["confidence"] for x in res[1]}[1] == 0.8, calls
calls.clear()
CA.service._score_call = _fixed_stub
short = [{"title": f"C{i}", "description": "d", "key_concepts": []} for i in range(8)]
asyncio.run(CA.service.assign_one(asyncio.Semaphore(1), "fw", "k", {"summary": "s", "key_terms": ["t"]}, short))
assert calls[0][0] == "list", calls                                              # < 12 chapters: unchanged
assert CA.prompts.to_fixed_key_prompt("x OUTPUT — STRICT JSON: y == FILE: z", 3).count("c2") >= 1
try:
    CA.prompts.to_fixed_key_prompt("a template without anchors", 3); raise SystemExit("expected ValueError")
except ValueError:
    pass
print("1b fixed-key scoring ok")

# 2) order: chapters whose pages sit mostly in the same URL directory stay together ------------------------------
def ck(d, i): return f"ingestion/fw/pages/{i:04d}-{d.replace('/', '-')}-{i}.md"
def chap(title, dirs, **kw):
    return {"title": title, "member_doc_keys": [ck(d, i + 100 * hash(title) % 9000) for i, d in enumerate(dirs)], **kw}
chs = [chap("Foundations and Diagnostics", ["docs"] * 3, origin="foundations", role="foundations"),
       chap("Scores and Feedback", ["docs/evaluation"] * 4 + ["docs/observability"]),
       chap("Prompt Versions", ["docs/prompt-management"] * 5),
       chap("Datasets and Experiments", ["docs/evaluation"] * 5),
       chap("Traces and Sessions", ["docs/observability"] * 6),
       chap("Prompt Playground", ["docs/prompt-management"] * 3 + ["integrations/no-code"]),
       chap("LLM-as-a-Judge", ["docs/evaluation"] * 3 + ["guides/videos"] * 2 + ["integrations/other"]),
       chap("Mixed Bag", ["docs/evaluation", "docs/observability", "docs/prompt-management", "integrations/other"]),
       chap("Upgrading Versions", ["docs/observability"] * 3, origin="migration")]
kd = {k: tuple(d for d in [k.split("/")[-1].split("-", 1)[1].rsplit("-", 1)[0]] ) for c in chs for k in c["member_doc_keys"]}
kd = {k: tuple(v[0].replace("docs-", "docs/").replace("integrations-", "integrations/").replace("guides-", "guides/").split("/")) if v else () for k, v in kd.items()}
dom_dirs = OC.chapter_home_dirs(chs, kd)
assert dom_dirs[1] == ("docs", "evaluation") and dom_dirs[2] == ("docs", "prompt-management") and dom_dirs[6] == ("docs", "evaluation"), dom_dirs
assert dom_dirs[7] is None and dom_dirs[0] is None and dom_dirs[8] is None, dom_dirs      # no clear home / pinned chapters
new, moved = OC.group_by_home_dir([0, 1, 2, 3, 4, 5, 6, 7, 8], chs, dom_dirs)
assert new == [0, 1, 2, 5, 3, 6, 4, 7, 8] and moved == 4, (new, moved)         # stragglers pulled forward; near siblings untouched
assert OC.group_by_home_dir([0, 4, 7], chs, dom_dirs) == ([0, 4, 7], 0)
print("2 sibling chapters by home directory ok:", new)

# 3) micro-chapters merge only into a neighbour that owns their directory ------------------------------------------------
def mchap(title, dirs, **kw):
    return {"title": title, "description": "d", "member_doc_keys": [ck(d, i + 7 * len(title)) for i, d in enumerate(dirs)], "origin": "proposal", **kw}
big = mchap("Administering Access", ["docs/administration"] * 6)
micro = mchap("Billing Alerts", ["docs/administration", "docs/administration", "docs/observability"], origin="rescued")
stray = mchap("Odd Pair", ["integrations/other", "docs/evaluation", "docs/prompt-management"], origin="rescued")
other = mchap("Evaluation", ["docs/evaluation"] * 8)
fnd = mchap("Foundations and Diagnostics", ["docs"] * 3, origin="foundations", role="foundations")
mig = mchap("Upgrading Versions", ["docs/observability"] * 2, origin="migration")
filler = mchap("Filler Topic", ["docs/observability"] * 60)
allk = [k for c in (big, micro, stray, other, fnd, mig, filler) for k in c["member_doc_keys"]]
kd3 = {k: tuple(k.split("/")[-1].split("-", 1)[1].rsplit("-", 1)[0].replace("docs-", "docs/").replace("integrations-", "integrations/").split("/")) for k in allk}
out, st = SEL.merge_micro_chapters([big, micro, stray, other, fnd, mig, filler], kd3)
titles = [c["title"] for c in out]
assert st["n_micro_merged"] == 1 and "Billing Alerts" not in titles and "Odd Pair" in titles and "Foundations and Diagnostics" in titles, (st, titles)
tgt = next(c for c in out if c["title"] == "Administering Access")
assert len(tgt["member_doc_keys"]) == 9 and tgt["description"].endswith("Also covers: Billing Alerts."), tgt
assert SEL.merge_micro_chapters([micro, other], kd3)[1]["n_micro_merged"] == 0           # no neighbour owns administration
print("3 micro-chapter merge ok:", st)

# 4) replay on stored plans ---------------------------------------------------------------------------------------------------
n_rows = 0
for path in sys.argv[1:]:
    for line in open(path):
        row = json.loads(line)
        chs_ = row["plan"]["chapters"]
        kd_ = SEL.page_dirs(row["manifest"]["entries"])
        merged, mst = SEL.merge_micro_chapters(chs_, kd_)
        assert sum(len(c["member_doc_keys"]) for c in merged) == sum(len(c["member_doc_keys"]) for c in chs_), row["slug"]
        homes = OC.chapter_home_dirs(chs_, kd_)
        order, moved = OC.group_by_home_dir(list(range(len(chs_))), chs_, homes)
        assert sorted(order) == list(range(len(chs_))), row["slug"]
        print(f"   {row['slug']}: {len(chs_)} chapters -> micro merged {mst['n_micro_merged']}, sibling moves {moved}")
        n_rows += 1
if n_rows:
    print(f"4 replay ok on {n_rows} stored plans")
print("ALL OK")
