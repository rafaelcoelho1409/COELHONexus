"""Offline checks for the 2026-10-13 round-5 fixes (no LLM, no cluster): slug-aware page clustering,
catch-all split sizing, migration page pull, naming prompt, SOURCE STRUCTURE from `source_path`,
compact propose output + sample top-up, partial score re-ask.

    cd apps/fastapi && .venv/bin/python ../../scripts/dd/test_planner_fixes_5.py [planner_artifacts.jsonl]

With a dump from dump_planner_artifacts.py it also replays the full selection on every stored framework."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "fastapi"))
import domains  # noqa: E402

nodes = domains.dd.planner.nodes
SEL, SELS = nodes.chapter_select.domain, nodes.chapter_select
PR, CA = nodes.chapter_propose, nodes.chapter_assign
chat = domains.settings.chat


def chap(title, n, **kw):
    return {"title": title, "description": "d", "member_doc_keys": [f"ingestion/fw/pages/{i:04d}-{title.lower().replace(' ', '-')}-{i}.md" for i in range(n)],
            "n_member_docs": n, "origin": "proposal", **kw}


# 1) clustering follows the slug (module path) when the summaries are noise
stems = (["fw-utilities-openapi-models", "fw-utilities-openapi-parser", "fw-utilities-openapi-schemas", "fw-utilities-openapi-formatters",
          "fw-utilities-openapi-json-schema", "fw-utilities-openapi-routes", "fw-utilities-openapi-types", "fw-utilities-openapi-utils"]
         + ["fw-server-auth-providers-github", "fw-server-auth-providers-google", "fw-server-auth-providers-azure", "fw-server-auth-providers-okta",
            "fw-server-auth-providers-auth0", "fw-server-auth-providers-aws", "fw-server-auth-providers-jwt", "fw-server-auth-providers-oci"]
         + ["fw-client-transports-http", "fw-client-transports-sse", "fw-client-transports-stdio", "fw-client-transports-memory",
            "fw-client-transports-base", "fw-client-transports-config", "fw-client-transports-inference", "fw-client-transports-ws"])
units = [{"id": i, "keys": [f"ingestion/fw/pages/{i:04d}-{s}.md"], "label": s, "summary": "", "slug": s, "role": "reference"} for i, s in enumerate(stems)]
noise = [f"this module provides functions word{i} other{i % 7} thing{i % 5}" for i in range(len(stems))]   # no shared topic words
groups = SEL.cluster_units(units, noise, 3, 6)
assert groups and len(groups) == 3, groups
by_prefix = [{stems[i].split("-")[1] for i in g} for g in groups]
assert all(len(p) == 1 for p in by_prefix) and len({next(iter(p)) for p in by_prefix}) == 3, (groups, by_prefix)
# units without a slug fall back to the text cosine (hand-built units of the older tests)
plain = [{"id": i, "keys": [f"k{i}"], "label": f"u{i}", "summary": ""} for i in range(24)]
tx = [f"streams reader writer socket{i}" for i in range(12)] + [f"queues producer consumer lifo{i}" for i in range(12)]
g2 = SEL.cluster_units(plain, tx, 2, 4)
assert g2 and len(g2) == 2 and all(len({i < 12 for i in g}) == 1 for g in g2), g2
print("1 slug-aware clustering ok:", [len(g) for g in groups])

# 2) catch-all appendix: ~10 pages per part, up to 6 parts; the gate is unchanged
app = lambda n: [chap("Big", 60), chap("A", 40), chap("B", 40), chap("Additional Topics", n, origin="gap", role="appendix")]
assert dict(SEL.plan_splits(app(47)))[3] == 5, SEL.plan_splits(app(47))
assert dict(SEL.plan_splits(app(30)))[3] == 3
assert dict(SEL.plan_splits(app(100))).get(3) == 8, SEL.plan_splits(app(100))      # 2026-10-14: up to 8 parts
assert 3 not in dict(SEL.plan_splits([chap("Big", 200), chap("Additional Topics", 14, origin="gap", role="appendix")]))
crowded = [chap("Big", 40)] + [chap(f"C{i}", 8) for i in range(27)] + [chap("Additional Topics", 47, origin="gap", role="appendix")]   # 29 chapters
assert dict(SEL.plan_splits(crowded)).get(len(crowded) - 1) == 2, SEL.plan_splits(crowded)        # only 2 parts fit under the 30-chapter cap
# validate_split accepts more parts than SPLIT_MAX_PARTS only when told to
u5 = [{"id": i, "keys": [f"x{i}"], "label": f"u{i}", "summary": ""} for i in range(90)]
parts5 = [{"title": f"Topic {'ABCDEFGHI'[j]} Pages", "description": "d", "unit_ids": list(range(10 * j, 10 * j + 10))} for j in range(9)]
assert not SEL.validate_split(parts5, u5, set())[0]
assert SEL.validate_split(parts5, u5, set(), max_parts=9)[0]
print("2 appendix split sizing ok:", SEL.plan_splits(app(47)))

# 3) migration pages are pulled into one chapter
doc_title = {}
def mk(title, slugs, **kw):
    c = chap(title, 0, **kw)
    c["member_doc_keys"] = [f"ingestion/fw/pages/{i:04d}-{s}.md" for i, s in slugs]
    c["n_member_docs"] = len(slugs)
    return c
core = mk("Core Concepts", [(1, "intro-to-fw"), (2, "langchain-v1-migration-guide-docs-by-x"), (3, "agents"), (4, "tools"), (5, "memory")])
tools = mk("Tools", [(6, "migrate-from-old-adapters"), (7, "tools-guide"), (8, "tool-calling"), (9, "mcp")])
subs = mk("Subagents", [(10, "migrate-from-supervisor"), (11, "subagents"), (12, "async-subagents")])
other = mk("Streaming", [(13, "upgrade-path-for-streaming"), (14, "streaming"), (15, "events")])
filler = mk("Filler", [(200 + i, f"filler-page-{i}") for i in range(40)])
chs = [core, tools, subs, other, filler]
out, st = SEL.pull_migration_pages(chs, doc_title)
mig = [c for c in out if c["origin"] == "migration"]
assert len(mig) == 1 and mig[0]["title"] == SEL.params.MIGRATION_TITLE and len(mig[0]["member_doc_keys"]) == 4, (out, st)
assert st["n_migration_pulled"] == 4 and sum(len(c["member_doc_keys"]) for c in out) == sum(len(c["member_doc_keys"]) for c in chs)
assert all(not SEL.is_migration_page(k) for c in out if c["origin"] != "migration" for k in c["member_doc_keys"])
assert [c["order"] for c in out] == list(range(1, len(out) + 1)) and len(chs[0]["member_doc_keys"]) == 5      # input not mutated
assert nodes.order_chapters.patterns.TRAILING_RE.search(mig[0]["title"])                                       # order_chapters pins it last
# below the minimum, or too large a share, nothing moves
few, st_few = SEL.pull_migration_pages([core, mk("Tools", [(6, "tools"), (7, "mcp"), (8, "tool-calling")])], doc_title)
assert st_few["n_migration_pulled"] == 0                                    # one page < MIGRATION_MIN_DOCS, no existing chapter
heavy = [mk("Upgrades", [(i, f"upgrade-step-{i}") for i in range(20)]), mk("Rest", [(100, "a")])]
assert SEL.pull_migration_pages(heavy, doc_title)[1]["n_migration_pulled"] == 0
# an existing migration chapter receives the stray pages (even a single one)
existing = mk("Migration and Upgrading", [(30, "upgrading-from-v2"), (31, "installation"), (32, "sampling")])
core2 = mk("Core", [(1, "intro"), (2, "upgrading-from-the-low-level-sdk-v2"), (3, "agents")])
out2, st2 = SEL.pull_migration_pages([core2, existing], doc_title)
assert st2["n_migration_pulled"] == 1 and st2["migration_chapter"] == "Migration and Upgrading"
assert len(out2) == 2 and len(out2[1]["member_doc_keys"]) == 4 and out2[1]["origin"] == "proposal"
# a chapter emptied by the move disappears
lone = mk("Misc Stuff", [(40, "migrate-a")])
out3, st3 = SEL.pull_migration_pages([lone, mk("Existing Upgrade Guides", [(41, "x"), (42, "y")]), core2], doc_title)
assert [c["title"] for c in out3] == ["Existing Upgrade Guides", "Core"] and st3["n_migration_pulled"] == 2, out3
assert SEL.is_migration_page("ingestion/fw/pages/0005-what-s-new-in-fw-4.md") and SEL.is_migration_page("ingestion/fw/pages/0006-x.md", "Changelog")
assert not SEL.is_migration_page("ingestion/fw/pages/0007-streams-and-queues.md", "Streams")
print("3 migration pull ok:", st["n_migration_pulled"], "pages ->", mig[0]["title"])

# 4) naming prompt: every listed page + summaries spread over the group
names = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliet", "kilo", "lima"]
us = [{"id": i, "keys": [f"k{i}"], "label": f"site-com-llms-full-{names[i]}", "summary": f"summary number {i} about module {i}", "slug": names[i], "role": None} for i in range(12)]
pr = SELS.prompts.build_name_prompt(framework="fw", title="Additional Topics", groups=[list(range(0, 6)), list(range(6, 12))], units=us, other_titles=["Core"])
assert all(n in pr for n in names) and "summaries:" in pr and "ALL the page names" in pr
assert "summary number 0" in pr and "summary number 6" in pr
print("4 naming prompt ok")

# 5) SOURCE STRUCTURE: single-URL dump -> sections from `source_path`, relevant pages only
dom = PR.domain
es = []
for i in range(80):
    d = ("integrations", "servers", "changelog", "guides")[i // 20]
    es.append({"key": f"k{i}", "url": "https://x.com/llms-full.txt", "source_path": f"https://x.com/{d}/p{i}", "idx": i, "title": f"t{i}"})
s_all = dom.build_source_structure(es)
s_rel = dom.build_source_structure([e for e in es if not e["source_path"].startswith("https://x.com/changelog")])
assert "integrations (20 pages)" in s_all and "servers (20 pages)" in s_all and "changelog (20 pages)" in s_all, s_all
assert "changelog" not in s_rel and "integrations (20 pages)" in s_rel and "x.com/" not in s_rel, s_rel
assert dom.build_source_structure([{**e, "source_path": None} for e in es]) == ""          # no kinship to report
print("5 source structure from source_path ok")

# 6) compact output block only for big targets; both prompt paths
seeds = {"headings": [], "namespaces": []}
kw = dict(framework="fw", source_keys=["k1"], distillates={"k1": {"summary": "s", "key_terms": ["a"]}}, bodies_by_key=None,
          seeds=seeds, body_chars_per_doc=100)
big = PR.prompts.build_propose_prompt(target_chapters=24, **kw)
small = PR.prompts.build_propose_prompt(target_chapters=8, **kw)
assert "OUTPUT SIZE" in big and "OUTPUT SIZE" not in small
print("6 compact propose block ok")

# 7) sample top-up: <4 valid of 5 -> failed indices retried once; >=4 valid -> no extra calls
class FakeMinio:
    def __init__(self):
        self.store = {}
    async def exists(self, key):
        return key in self.store
    async def read_text(self, key):
        if key not in self.store:
            raise FileNotFoundError(key)
        return self.store[key]
    async def write(self, key, data, content_type=None):
        self.store[key] = data if isinstance(data, str) else data.decode()
    async def read_many(self, keys):
        return ["body"] * len(keys)

async def _noop(*a, **k):
    return None
async def _settle(cap):
    return {"waited_s": 0, "reason": "test"}
def good(n):
    return PR.schemas.ChapterProposalList(proposals=[
        PR.schemas.ChapterProposal(title=f"Topic Number {j} {n}", description="Covers a distinct topic of the framework in depth.", key_concepts=["alpha", "beta", "gamma"])
        for j in range(6)])
async def run_propose(fail_idx):
    minio = FakeMinio()
    domains.dd.ingestion.storage.service.get_storage = lambda: minio
    async def _manifest(m, slug):
        return {"entries": []}
    domains.dd.ingestion.storage.service.read_framework_manifest = _manifest
    chat.service.wait_for_rotator_settle = _settle
    domains.dd.planner.runtime.progress.service.emit_progress = _noop
    async def _dist(m, slug):
        return {f"k{i}": {"summary": f"summary {i}", "key_terms": ["t"]} for i in range(30)}
    nodes.doc_distill.service.load_distillates = _dist
    called = []
    async def _draft(prompt, idx, failures=None, max_tokens=None):
        called.append(idx)
        return None if idx in fail_idx else good(idx)
    PR.service.draft_one = _draft
    async def _vote(*a, **k):
        return 0
    PR.service.usc_pick = _vote
    state = {"framework_slug": "fw", "thread_id": "t", "relevant_files": [f"k{i}" for i in range(30)], "doc_distill_ref": "x"}
    out = await PR.service.chapter_propose_run(state)
    return called, out["propose_stats"]
PR.params.WEAK_RETRY_SAMPLES = 0                                                                  # (weak-batch retry is covered by test_planner_fixes_6)
called, stats = asyncio.run(run_propose({1, 3, 4}))
assert sorted(called[:5]) == [0, 1, 2, 3, 4] and sorted(called[5:]) == [11, 13, 14], called       # 2 valid -> top up the failed ones (+1 spare)
assert stats["n_samples_valid"] == 5, stats
called, stats = asyncio.run(run_propose({4}))
assert len(called) == 5 and stats["n_samples_valid"] == 4, (called, stats)                          # 4 valid: enough
called, stats = asyncio.run(run_propose({0, 1, 2, 3, 4}))
assert stats["fallback_used"] is False or len(called) >= 10                                           # all-failed path unchanged
print("7 sample top-up ok")

# 8) assign: a partial vector is completed by a reask for the MISSING chapters only
sent = []
async def _score(prompt):
    sent.append(prompt)
    if len(sent) == 1:
        return CA.schemas.DocAssignment(scores=[CA.schemas.ChapterScore(chapter_idx=i, confidence=0.3) for i in range(0, 6)])
    return CA.schemas.DocAssignment(scores=[CA.schemas.ChapterScore(chapter_idx=i, confidence=0.7 if i == 7 else 0.0) for i in range(6, 8)]
                                    + [CA.schemas.ChapterScore(chapter_idx=0, confidence=1.0)])      # a stray already-scored chapter is ignored
CA.service._score_call = _score
props = [{"title": f"C{i}", "description": "d", "key_concepts": []} for i in range(8)]
res = asyncio.run(CA.service.assign_one(asyncio.Semaphore(1), "fw", "k", {"summary": "s", "key_terms": ["t"]}, props))
scores = {s["chapter_idx"]: s["confidence"] for s in res[1]}
assert sorted(scores) == list(range(8)) and scores[0] == 0.3 and scores[7] == 0.7, scores
assert len(sent) == 2 and "[6, 7]" in sent[1] and "and no others" in sent[1], sent[1][-300:]
print("8 partial re-ask ok")

# 9) replay on stored artifacts: the full selection incl. migration pull, no unplaced page
if len(sys.argv) > 1:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import replay_select as rs
    for line in open(sys.argv[1]):
        row = json.loads(line)
        out = rs.run_pipeline(row)
        chapters = out["out_chapters"]
        assert out["stats"]["n_unplaced_final"] == 0, row["slug"]
        placed = sorted(k for c in chapters for k in c["member_doc_keys"])
        assert placed == sorted(row["assignments"]), (row["slug"], len(placed), len(row["assignments"]))
        assert len({c["title"].casefold() for c in chapters}) == len(chapters), row["slug"]
        print(f"  replay {row['slug']}: {len(chapters)} chapters, migration pulled {out['stats'].get('n_migration_pulled')}, "
              f"largest {round(100 * max(len(c['member_doc_keys']) for c in chapters) / len(placed))}%")
print("ALL OK")
