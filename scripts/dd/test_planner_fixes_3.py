"""Offline checks for the 2026-10-13 round-3 fixes (no LLM, no cluster):
one-word chapter titles, evidence-based sample pick, split repair, source-order gate,
chapter composition in the order prompt, token budgets, doc_distill length retry.

    cd apps/server && .venv/bin/python ../../scripts/dd/test_planner_fixes_3.py [planner_artifacts.jsonl [langfuse_gens.json]]

With a dump from dump_planner_artifacts.py (+ the LangFuse chapter_propose generations as a JSON
list of {input, output}) it also replays the real samples."""
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))
import domains  # noqa: E402

nodes = domains.dd.planner.nodes
PR, sel, order = nodes.chapter_propose, nodes.chapter_select.domain, nodes.order_chapters
chat = domains.settings.chat


def prop(title, concepts=("a_one", "b_two", "c_three"), desc="Learn how this topic works in the framework."):
    return {"title": title, "description": desc, "key_concepts": list(concepts)}


# 1) one-word titles survive; generic one-word titles and short concept lists are handled
Sch = PR.schemas.ChapterProposalList
raw = [prop(t) for t in ("Streams", "Queues", "Subprocesses", "Event Loop", "Overview", "Foundations")]
raw.append(prop("Futures", concepts=("Future", "wrap_future")))      # 2 concepts: small topic, kept
raw.append(prop("Hopeless", concepts=("only_one",)))                  # 1 concept: still dropped
kept = [p.title for p in Sch.model_validate({"proposals": raw}).proposals]
assert kept == ["Streams", "Queues", "Subprocesses", "Event Loop", "Futures"], kept
print("1 one-word titles kept, generic + 1-concept dropped:", kept)

# 2) candidate scoring: a 4-chapter sample that leaves half the corpus unmatched loses to a
#    9-chapter one; the shortlist drops it before any LLM vote
topics = ["streams", "queues", "subprocess", "futures", "policies", "transport", "locks", "tasks", "loop"]
docs = [f"{t} {t}core {t}extra detail{i}" for t in topics for i in range(4)]
good = [prop(t.title(), (t, f"{t}core", f"{t}extra"), f"Everything the reader needs about {t}.") for t in topics]
poor = good[:4]
sc = PR.domain.score_candidates([[*poor], [*good]], docs, 9)
assert sc[1]["score"] > sc[0]["score"] and sc[0]["unmatched_pct"] > 40 and sc[1]["unmatched_pct"] == 0, sc
assert PR.domain.shortlist(sc, 9) == [1], sc
tie = PR.domain.score_candidates([good, list(reversed(good))], docs, 9)
assert sorted(PR.domain.shortlist(tie, 9)) == [0, 1]
print("2 evidence score ok:", [(s["n"], s["unmatched_pct"], s["score"]) for s in sc])

# 3) usc_pick: decisive evidence skips the LLM; a tie votes among the shortlist only
calls = []
async def _vote(prompt, schema, **kw):
    calls.append(prompt)
    return PR.schemas.VotePick(chosen_index=1, reason="x"), {}
chat.service.chat_structured_async = _vote
L = lambda props: PR.schemas.ChapterProposalList(proposals=[PR.schemas.ChapterProposal(**p) for p in props])
cands = [L(poor), L(good)]
assert asyncio.run(PR.service.usc_pick("fw", cands, sc, 9)) == 1 and not calls
cands3 = [L(good), L(poor), L(list(reversed(good)))]
sc3 = PR.domain.score_candidates([good, poor, list(reversed(good))], docs, 9)
pick = asyncio.run(PR.service.usc_pick("fw", cands3, sc3, 9))
assert len(calls) == 1 and pick == 2 and "CANDIDATE 1" in calls[0] and "CANDIDATE 2" not in calls[0], (pick, calls)
assert "match no chapter" in calls[0] and "target is about 9" in calls[0]
print("3 usc_pick: evidence decides, vote only on ties ok")

# 4) token budgets
assert PR.domain.propose_max_tokens(5) == 10000 and PR.domain.propose_max_tokens(24) == 10420
assert PR.domain.retry_max_tokens(10000) == 16000 and PR.domain.retry_max_tokens(16000) == 16000
print("4 token budgets ok")

# 5) draft_one: a length-truncated draft is re-asked with a bigger budget
seen = []
async def _len_then_ok(prompt, schema, *, max_tokens, **kw):
    seen.append(max_tokens)
    if len(seen) == 1:
        raise chat.errors.ChatError("LengthFinishReasonError: Could not parse response content as the length limit was reached")
    return L(good), {}
chat.service.chat_structured_async = _len_then_ok
out = asyncio.run(PR.service.draft_one("p", 0, [], 10000))
assert out is not None and seen == [10000, 16000], seen
print("5 length re-ask uses a bigger budget ok:", seen)

# 6) split repair
units = [{"id": i, "keys": [f"k{i}a", f"k{i}b"] if i % 5 == 0 else [f"k{i}a"], "label": f"u{i}", "summary": ""} for i in range(20)]
n_docs = sum(len(u["keys"]) for u in units)
parts = [
    {"title": "Alpha", "unit_ids": [2, 3, 4, 5, 6, 7, 8, 9]},                   # 0, 1 forgotten
    {"title": "Beta", "unit_ids": [10, 11, 12, 13, 14, 15, 16, 17, 99, 3]},      # unknown id, duplicate
    {"title": "Tiny", "unit_ids": [18]},                                          # below the floor
    {"title": "Empty", "unit_ids": []},
]
fixed = sel.normalize_split(parts, units)
ok, why = sel.validate_split(fixed, units, set())
assert ok, why
placed = sorted(u for p in fixed for u in p["unit_ids"])
assert len(fixed) == 2 and {1, 0} <= set(fixed[0]["unit_ids"]), fixed          # forgotten 0,1 joined Alpha
assert 18 in fixed[1]["unit_ids"] and 19 in fixed[1]["unit_ids"], fixed         # tiny merged into Beta, 19 attached
assert sorted(placed) == list(range(20)), placed
assert not sel.validate_split([{"title": "Overview", "unit_ids": list(range(0, 10))}, {"title": "Beta Part", "unit_ids": list(range(10, 20))}], units, set())[0]
assert sel.validate_split([{"title": "Alpha", "unit_ids": list(range(0, 10))}, {"title": "Beta Part", "unit_ids": list(range(10, 20))}], units, set())[0]
only_two = sel.normalize_split([{"title": "A", "unit_ids": list(range(0, 19))}, {"title": "B", "unit_ids": [19]}], units)
assert not sel.validate_split(only_two, units, set())[0]                        # two groups never merge away: back to the model
print("6 split repair ok:", [(p["title"], len(p["unit_ids"])) for p in fixed])

# 7) source-order gate
def ents(paths):
    return [{"key": f"k{i}", "idx": i, "url": "https://x.dev/llms-full.txt", "source_path": p} for i, p in enumerate(paths)]
assert not order.domain.source_order_informative(ents(["https://x.dev/a"] * 20))                       # single page
assert not order.domain.source_order_informative(ents(sorted(f"https://x.dev/{c}/p" for c in "abcdefghij")))  # alphabetical
assert order.domain.source_order_informative(ents([f"https://x.dev/{c}" for c in ("start", "install", "tasks", "queues", "streams", "advanced", "api")]))
print("7 source-order gate ok")

# 8) composition in the order prompt
chs = [{"title": "Server Core", "description": "d", "member_doc_keys": ["a", "b", "c", "d"]},
       {"title": "FastMCP CLI", "description": "d", "member_doc_keys": ["e", "f"]},
       {"title": "Ghost", "description": "d", "member_doc_keys": ["zz"]}]
dist = {"a": {"role": "how_to"}, "b": {"role": "reference"}, "c": {"role": "tutorial"}, "d": {"role": "explanation"},
        "e": {"role": "reference"}, "f": {"role": "reference"}}
comp = order.domain.chapter_composition(chs, dist)
assert comp == [{"n_docs": 4, "reference_pct": 25}, {"n_docs": 2, "reference_pct": 100}, None], comp
pr = order.prompts.build_order_prompt(chs, comp)
assert "(4 pages, 25% API/reference)" in pr and "(2 pages, 100% API/reference)" in pr and "after the chapters that teach" in pr
assert "pages," not in order.prompts.build_order_prompt(chs).split("CHAPTERS")[1].split("Respond")[0]
print("8 composition in order prompt ok")

# 9) doc_distill: a length-truncated distill is retried once with a doubled budget
DD = nodes.doc_distill
budgets = []
async def _distill(prompt, schema, *, max_tokens, **kw):
    budgets.append(max_tokens)
    if len(budgets) == 1:
        raise chat.errors.ChatError("LengthFinishReasonError: Could not parse response content as the length limit was reached")
    return DD.schemas.DocDistillateRaw(
        summary="This page explains how tasks are scheduled by the event loop in asyncio programs.",
        key_terms=["asyncio", "task", "event loop"], role="reference"), {"deployment": "x"}
chat.service.chat_structured_async = _distill
res = asyncio.run(DD.service.distill_one(asyncio.Semaphore(1), "fw", "k", "body " * 50)) if "distill_one" in dir(DD.service) else None
if res is not None:
    assert budgets == [DD.params.MAX_TOKENS, 2 * DD.params.MAX_TOKENS] and not res[3], (budgets, res[3])
    print("9 distill length retry ok:", budgets)

# 10) replay on real artifacts
if len(sys.argv) > 2:
    rows = {json.loads(l)["slug"]: json.loads(l) for l in open(sys.argv[1])}
    gens = json.load(open(sys.argv[2]))
    byrun: dict = {}
    for o in sorted(gens, key=lambda o: o["start"]):
        m = re.search(r"Chapter Planner for the (\S+) documentation", o["input"])
        if not m:
            continue
        try:
            j = json.loads(json.loads(o["output"])["content"])
            lst = Sch.model_validate(j)
        except Exception:
            continue
        byrun.setdefault((m.group(1), o["start"][:16]), []).append([p.model_dump() for p in lst.proposals])
    for (slug, when), cs in byrun.items():
        r = rows.get(slug)
        if not r or len(cs) < 3:
            continue
        keys = [e["key"] for e in r["manifest"]["entries"] if e["key"] in r["distillates"]]
        tgt = PR.domain.target_chapters_for_n_docs(len(keys))
        s = PR.domain.score_candidates(cs, [PR.domain.doc_match_text(k, r["distillates"].get(k)) for k in keys], tgt)
        print(f"10 replay {slug} {when}: chapters {[x['n'] for x in s]} shortlist {PR.domain.shortlist(s, tgt)}")

print("ALL OK")
