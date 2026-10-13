"""Offline checks for the 2026-10-13 live-round fixes (no LLM, no cluster):
Foundations catalog guard, generic appendix for gap chapters, foundational-title rule,
appendix-last pin, propose length re-ask, Rotator wall-budget extra, narrowed role prompt.

    cd apps/fastapi && .venv/bin/python ../../scripts/dd/test_planner_fixes_2.py [planner_artifacts.jsonl]

With a dump from dump_planner_artifacts.py it also replays the stored frameworks."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "fastapi"))
import domains  # noqa: E402

nodes = domains.dd.planner.nodes
sel, order = nodes.chapter_select.domain, nodes.order_chapters.domain
chat = domains.settings.chat

# 1) Foundations catalog guard: 7 provider landing pages under one directory are a list of
#    like pages; 4 genuine overviews spread over distinct directories still qualify.
def corpus(provider_dir_share_ok=True):
    docs, role, fam = {}, {}, {}
    chapters = [{"title": "Everything", "member_doc_keys": [], "origin": "proposal"},
                {"title": "Else", "member_doc_keys": [], "origin": "proposal"}]
    n = 0
    def add(url, r, ch):
        nonlocal n
        k = f"k{n}"; n += 1
        fam[k] = url; role[k] = r; chapters[ch]["member_doc_keys"].append(k)
    for i in range(7):
        add(f"https://x.dev/integrations/providers/p{i}", "overview", 0)
    for i in range(40):
        add(f"https://x.dev/guide/g{i}", "reference", 1)
    for i, d in enumerate(("a", "b", "c", "d")):
        add(f"https://x.dev/{d}/overview", "overview", 1)
    return chapters, role, fam
ch, role, fam = corpus()
out, st = sel.apply_foundations(out_chapters=ch, doc_role=role, doc_family=fam, key_idx={})
assert st["foundations_catalog_excluded"] == 7 and st["n_foundations"] == 4, st
assert not any(k for k in out[0]["member_doc_keys"] if "providers" in fam[k]), out[0]
print("1 catalog guard ok", st)
# flat site: every page in one directory → the directory carries no signal, nothing excluded
flat = {"https://x.dev/library/page%d" % i: "overview" for i in range(6)}
ch2 = [{"title": "A", "member_doc_keys": [f"f{i}" for i in range(6)] + [f"g{i}" for i in range(10)], "origin": "proposal"}]
role2 = {f"f{i}": "overview" for i in range(6)}
fam2 = {f"f{i}": f"https://x.dev/library/page{i}" for i in range(6)} | {f"g{i}": f"https://x.dev/library/g{i}" for i in range(10)}
_, st2 = sel.apply_foundations(out_chapters=ch2, doc_role=role2, doc_family=fam2, key_idx={})
assert st2["foundations_catalog_excluded"] == 0, st2
print("1b flat-site directory not treated as a catalog ok")

# 2) gap chapters: page family keeps its name, single-URL dump becomes the appendix
assert sel._is_page_family("u", ["a", "b", "c"], {"a": "Watcher — x", "b": "Watcher — y", "c": "Watcher — z"})
assert not sel._is_page_family("u", ["a", "b", "c"], {"a": "site-one", "b": "site-two", "c": "site-three"})
d = sel._appendix_description(["a", "b"], {"a": "gofast-com-llms-full-installation", "b": "gofast-com-llms-full-telemetry"})
assert d.startswith("Pages that fit no other chapter (2): installation, telemetry"), d
print("2 gap titles ok:", d)

# 3) order rules
chs = [
    {"title": "FastMCP CLI Tooling", "origin": "proposal"},
    {"title": "Additional Topics", "origin": "gap", "role": "appendix"},
    {"title": "gofastmcp quickstart", "origin": "gap"},
    {"title": "Installation and Setup", "origin": "proposal"},
    {"title": "Server", "origin": "proposal"},
]
o, pinned = order.apply_foundational_prefix_rule([0, 1, 2, 3, 4], chs)
assert pinned == [3], pinned          # only the real install chapter; not CLI tooling, not gap/appendix
o2, ap = order.pin_appendix_last([1, 0, 3, 4, 2], chs)
assert o2[-1] == 1 and ap == [1], (o2, ap)
print("3 foundational rule + appendix-last ok")

# 4) propose length re-ask detection
P = nodes.chapter_propose.domain
assert P.is_length_error(RuntimeError("LengthFinishReasonError: Could not parse response content as the length limit was reached"))
assert not P.is_length_error(RuntimeError("InternalServerError: 504 TimeoutError"))
print("4 length-error detection ok")

# 5) Rotator wall budget
rot = "http://r:8000/api/v1/llm/openai/v1"
assert chat.domain.rotator_wall_extra(rot, 240.0, 3.0, 2.5) == {"max_wall_s": 237.0}
assert chat.domain.rotator_wall_extra("https://api.openai.com/v1", 240.0, 3.0, 2.5) == {}
assert chat.domain.rotator_wall_extra(rot, None, 3.0, 2.5) == {}
print("5 rotator wall extra ok")

# 6) role prompt
pr = nodes.doc_distill.prompts.build_prompt("deepagents", "k", "body")
assert "is NOT an overview" in pr and "AS A WHOLE" in pr
print("6 role prompt ok")

# 8) site sections in SOURCE STRUCTURE + the section rule
def _entries(spec):
    es, i = [], 0
    for d, n in spec:
        for j in range(n):
            es.append({"key": f"k{i}", "idx": i, "url": f"https://x.dev/{d}/page{j}", "title": f"{d} {j}"}); i += 1
    return es
St = nodes.chapter_propose.domain.build_source_structure
blk = St(_entries([("guide", 30), ("integrations/providers", 12), ("integrations", 10), ("misc", 3)]))
assert "Site sections" in blk and "- integrations/providers (12 pages)" in blk and "- integrations (10 pages)" in blk, blk
assert "guide (" not in blk, blk                  # 30/55 = 55% of the corpus: the trunk, not a section
assert St(_entries([("library", 60)])) == ""      # flat site: one directory, no signal
PR = nodes.chapter_propose.prompts
assert "OWN chapter" in PR._structure_rules(blk) and "OWN chapter" not in PR._structure_rules("  1. A (3 pages)")
assert "OWN chapter" in PR._extra_signal_block(blk)
print("8 site sections + rule ok")

# 9) chat_structured_async: schema-validation failure is NOT wrapped in ChatError
import asyncio
from pydantic import ValidationError
Sch = nodes.chapter_propose.schemas.ChapterProposalList
try:
    Sch(proposals=[]); raise SystemExit("schema should reject an empty list")
except ValidationError as ve:
    boom = ve
class _FakeStructured:
    async def ainvoke(self, msgs): raise boom
class _FakeModel:
    def with_structured_output(self, *a, **k): return _FakeStructured()
chat.service._apply_endpoint = lambda *a, **k: None
chat.service.build_chat_model = lambda **k: _FakeModel()
try:
    asyncio.run(chat.service.chat_structured_async("p", Sch, max_tokens=10, timeout_s=5.0))
    raise SystemExit("expected ValidationError")
except ValidationError:
    pass
except chat.errors.ChatError:
    raise SystemExit("ValidationError was wrapped in ChatError (repair path unreachable)")
print("9 ValidationError reaches the callers' repair path ok")

# 10) chapter_assign: one transient retry recovers a 504 before the lexical fallback
CA = nodes.chapter_assign
calls = {"n": 0}
async def _flaky(prompt):
    calls["n"] += 1
    if calls["n"] == 1:
        raise chat.errors.ChatError("InternalServerError: Error code: 504 - {'error': {'message': 'TimeoutError: '}}")
    return CA.schemas.DocAssignment(scores=[CA.schemas.ChapterScore(chapter_idx=0, confidence=0.9),
                                            CA.schemas.ChapterScore(chapter_idx=1, confidence=0.1)])
CA.service._score_call = _flaky
CA.params.TRANSIENT_RETRY_PAUSE_S = 0.0
props = [{"title": "A", "description": "a", "key_concepts": ["a"]}, {"title": "B", "description": "b", "key_concepts": ["b"]}]
out = asyncio.run(CA.service.assign_one(asyncio.Semaphore(1), "fw", "k.md", {"summary": "about A", "key_terms": ["a"]}, props))
assert calls["n"] == 2 and out[3] is False and out[1][0]["confidence"] == 0.9, (calls, out)
calls["n"] = 0
async def _always504(prompt):
    calls["n"] += 1
    raise chat.errors.ChatError("InternalServerError: Error code: 504 - TimeoutError")
CA.service._score_call = _always504
out = asyncio.run(CA.service.assign_one(asyncio.Semaphore(1), "fw", "k.md", {"summary": "about A", "key_terms": ["a"]}, props))
assert calls["n"] == 2 and out[3] is True, (calls, out)      # one retry only, then the lexical fallback
print("10 chapter_assign transient retry ok")

# 7) optional replay on stored artifacts
if len(sys.argv) > 1:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import replay_select as rs
    for line in open(sys.argv[1]):
        row = json.loads(line)
        r = rs.run_pipeline(row)
        by = {e["key"]: e for e in row["manifest"]["entries"]}
        titles = [c["title"] for c in r["out_chapters"]]
        fnd = [c for c in r["out_chapters"] if c.get("origin") == "foundations"]
        for c in fnd:
            per_dir = {}
            for k in c["member_doc_keys"]:
                u = by[k].get("url") or ""
                if "integrations" in u:
                    per_dir[u.rsplit("/", 1)[0]] = per_dir.get(u.rsplit("/", 1)[0], 0) + 1
            assert all(n < 3 for n in per_dir.values()), (row["slug"], c["title"], per_dir)       # the guard is about catalog LISTS (>= 3 under one directory)
        assert not any(t.startswith(("gofastmcp-com-llms-full", "Gofastmcp")) for t in titles), (row["slug"], titles)
        print("  replay", row["slug"], "chapters", len(titles), "foundations", sum(c["n_member_docs"] for c in fnd),
              "appendix", [c["n_member_docs"] for c in r["out_chapters"] if c.get("role") == "appendix"])
print("ALL OK")
