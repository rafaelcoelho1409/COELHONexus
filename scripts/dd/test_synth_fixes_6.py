"""Offline check for the outline block count (2026-10-16): the section floor and the block-aware cap read the chapter's code-block count, which was
always 0 because it looked for a sentinel tag the raw bodies do not carry yet.

    cd apps/server && .venv/bin/python ../../scripts/dd/test_synth_fixes_6.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))
import ai, domains  # noqa: E402

OL = ai.dd.nodes.pipeline.synth.outline
count = OL.domain.count_code_blocks


def page(*blocks):
    return "# Page\n\nSome prose.\n\n" + "\n\n".join(f"```python\n{b}\n```" for b in blocks) + "\n"


# 1) raw bodies: distinct fenced blocks count once, however many pages repeat them -----------------------------------------------------------
a, b, c = "print('alpha')\nx = 1", "print('beta')\ny = 2", "print('gamma')\nz = 3"
assert count([page(a, b), page(b, c)]) == 3
assert count([]) == 0 and count(["", "no code here"]) == 0

# 2) sentinelized bodies count their sentinels -----------------------------------------------------------------------------------------------
sent, _ = domains.dd.ingestion.vault.domain.sentinelize_doc(page(a, b))
assert count([sent]) == 2, count([sent])
assert count([sent, page(c)]) == 3

# 3) the count drives the floor and the cap: 135 blocks over 18 sources -> floor 7, cap 12 --------------------------------------------------
P = OL.params
cap = P.max_h2_for_n_sources(18, 135)
assert (cap, P.min_h2_for_n_blocks(135, cap)) == (12, 7), (cap, P.min_h2_for_n_blocks(135, cap))
assert P.min_h2_for_n_blocks(count([page(a)]), P.max_h2_for_n_sources(1, 1)) == 0        # a prose-light chapter has no floor

# 4) an unmet section floor is recognised, gets a source-title hint, and earns extra repair attempts ---------------------------------------------
D = OL.domain
schemas = OL.schemas


_HEADINGS = ["Claude Code setup", "Cursor workspace files", "Goose deeplinks", "Gemini command line", "Stdio transport flags", "Environment variable handling"]


def outline_n(n):
    return schemas.ChapterOutline(sections=[
        schemas.OutlineSection(section_id=f"s{i + 1}", heading=_HEADINGS[i], description=f"Topic number {i} of the installation chapter, with its own commands and files.", prerequisites=[])
        for i in range(n)])


o4 = outline_n(4)
_, iss = D.validate_outline_structure(o4, D.derive_dag(o4.sections), n_sources=18, n_blocks=135)
assert [D.is_floor_issue(x) for x in iss] == [True], iss
assert not D.is_floor_issue("Outline has 30 H2 sections but only 5 source documents")
bodies = ["# Claude Code integration\n\ntext", "no title here", "# Goose\n\n```py\nx\n```", "# Claude Code integration\nagain"]
assert D.source_titles(bodies) == ["Claude Code integration", "Goose"]
assert "Claude Code integration; Goose" in D.floor_repair_hint(bodies) and D.floor_repair_hint(["no title"]) == ""
assert OL.params.FLOOR_REPAIR_EXTRA >= 1

# 5) the chat client waits out a Rotator capacity answer (Retry-After, bounded) instead of failing the attempt at once -----------------------------
import asyncio  # noqa: E402

chat = domains.settings.chat.service
CD, CP = domains.settings.chat.domain, domains.settings.chat.params


class FakeResp:
    def __init__(self, headers):
        self.headers = headers


class PoolExhausted(Exception):
    def __init__(self, msg="Error code: 429 - rotator pool exhausted: no healthy deployments available, retry after cooldown", headers=None):
        super().__init__(msg)
        self.response = FakeResp(headers or {})


kw = dict(default_s=20.0, min_s=3.0, cap_s=60.0)
assert CD.capacity_wait_s(PoolExhausted(headers={"retry-after": "45"}), **kw) == 45.0
assert CD.capacity_wait_s(PoolExhausted(headers={"retry-after": "600"}), **kw) == 60.0           # capped
assert CD.capacity_wait_s(PoolExhausted(headers={"retry-after": "1"}), **kw) == 3.0              # floored
assert CD.capacity_wait_s(PoolExhausted(), **kw) == 20.0                                          # no header -> default
assert CD.capacity_wait_s(Exception("No deployments available for selected model, Try again in 600.0 seconds."), **kw) == 60.0
assert CD.capacity_wait_s(Exception("Error code: 504 - TimeoutError"), **kw) is None              # a stall is not a capacity answer
assert CD.capacity_wait_s(Exception("Error code: 400 - bad schema"), **kw) is None

slept, calls = [], []
real_sleep = asyncio.sleep


async def fake_sleep(s):
    slept.append(s)

chat.asyncio.sleep = fake_sleep


async def flaky(n_fail, exc_factory):
    state = {"n": 0}

    async def call():
        state["n"] += 1
        if state["n"] <= n_fail:
            raise exc_factory()
        return "ok"
    return state, await chat._call_with_capacity_wait(call, 5.0)


async def run():
    st, out = await flaky(2, lambda: PoolExhausted(headers={"retry-after": "10"}))
    assert (out, st["n"], slept) == ("ok", 3, [10.0, 10.0]), (out, st, slept)
    slept.clear()
    try:
        await flaky(3, lambda: PoolExhausted(headers={"retry-after": "10"}))
        raise SystemExit("expected the third capacity answer to be raised")
    except PoolExhausted:
        assert slept == [10.0, 10.0]                                                                # attempts are bounded
    slept.clear()
    try:
        await flaky(1, lambda: Exception("Error code: 504 - TimeoutError"))
        raise SystemExit("a non-capacity error must not be retried here")
    except Exception as e:
        assert "504" in str(e) and slept == []
asyncio.run(run())
chat.asyncio.sleep = real_sleep


# 7) an over-deep prerequisite chain is flattened deterministically -----------------------------------------------------------------------------------
HEAD = ["Claude Code setup", "Cursor workspace files", "Goose deeplinks", "Gemini command line", "Stdio transport flags", "Environment variable handling",
        "Remote server auth", "Dependency flags", "JSON configuration output", "Workspace scoped installs", "Deeplink generation", "Client registry"]


def chain(n):
    return schemas.ChapterOutline(sections=[
        schemas.OutlineSection(section_id=f"s{i + 1}", heading=HEAD[i], description=f"Topic {HEAD[i]} of the installation chapter, with its own commands and files.",
                               prerequisites=[f"s{i}"] if i else [])
        for i in range(n)])


oc = chain(12)
dc = D.derive_dag(oc.sections)
assert dc.max_stage == 11 and OL.params.MAX_STAGE_DEPTH == 4
flat = D.flatten_prerequisites(oc, dc, OL.params.MAX_STAGE_DEPTH)
df = D.derive_dag(flat.sections)
assert df.max_stage <= OL.params.MAX_STAGE_DEPTH, df.max_stage
assert [x.section_id for x in flat.sections] == [x.section_id for x in oc.sections] and [x.heading for x in flat.sections] == [x.heading for x in oc.sections]
assert all(len(x.prerequisites) <= 3 for x in flat.sections)
assert max(len(v) for v in df.stages.values()) >= 6                                  # the tail now runs side by side
_, issues_flat = D.validate_outline_structure(flat, df, n_sources=18, n_blocks=135)
assert not any("DAG depth" in x for x in issues_flat), issues_flat
shallow = chain(4)
assert D.flatten_prerequisites(shallow, D.derive_dag(shallow.sections), OL.params.MAX_STAGE_DEPTH) == shallow   # within the cap: untouched


# 8) outline sections no source was routed to are folded away, not written from the whole chapter ------------------------------------------------------
DG = ai.dd.nodes.pipeline.synth.digest
DS = DG.schemas


def src(key, sid, rel="primary"):
    return DS.SourceDigest(source_key=key, source_title=key, overall_summary="s" * 40,
                           contributes_to=[DS.SectionContribution(section_id=sid, source_key=key, relevance=rel, summary="y" * 30, key_facts=["a fact long enough"], code_refs=[])])


outline8 = [
    {"section_id": "s1", "heading": "Claude Desktop install", "description": "install the server into Claude Desktop"},
    {"section_id": "s2", "heading": "Cursor workspace install", "description": "install the server into a Cursor workspace"},
    {"section_id": "s3", "heading": "Streaming responses", "description": "stream partial results back from the server to the client"},
    {"section_id": "s4", "heading": "Install into Cursor deeplinks", "description": "generate a Cursor deeplink for the install"},
]
per = [src("a", "s1"), src("b", "s2")]
out8 = DG.domain.drop_empty_sections(per, outline8, {})
assert sorted(out8) == ["s3", "s4"] and out8["s4"] == "s2" and out8["s3"] in ("s1", "s2"), out8      # s4 resembles s2 (Cursor install) most
assert DG.domain.drop_empty_sections(per, outline8, {"s4": "s2"}) == {"s4": "s2", "s3": out8["s3"]}      # an already merged section is kept as is
assert DG.domain.drop_empty_sections([src("a", "s1"), src("b", "s2"), src("c", "s3"), src("d", "s4")], outline8, {}) == {}   # nothing empty -> nothing dropped
assert DG.domain.drop_empty_sections([], outline8, {}) == {}                                              # no source at all: keep everything (nothing to fold into)


# 9) near-copies of a printed block count as covered; a harsh judge gets a second opinion --------------------------------------------------------------
SWD, CK = ai.dd.nodes.pipeline.synth.sawc.domain, ai.dd.nodes.pipeline.synth.checklist
base = "fastmcp install claude-code server.py --server-name Demo --with pandas --python 3.12"
bodies9 = {"a" * 16: base, "b" * 16: base.replace("pandas", "numpy"), "c" * 16: "import asyncio\nasync def main():\n    await asyncio.sleep(1)\nasyncio.run(main())",
           "d" * 16: "x = 1", "e" * 16: base.replace("claude-code", "cursor")}
dups9 = SWD.near_duplicate_hashes(["b" * 16, "c" * 16, "d" * 16, "e" * 16], ["a" * 16], bodies9)
assert dups9 == {"b" * 16, "e" * 16}, dups9                                  # the other client's variant and the --with variant; not the asyncio snippet, not the 1-token block

routed9 = [f"{i:016x}" for i in range(7)]
b9 = {routed9[0]: base}
b9.update({h: base.replace("pandas", f"lib{i}") for i, h in enumerate(routed9[1:6])})
b9[routed9[6]] = "import asyncio\nasync def main():\n    await asyncio.sleep(1)\nasyncio.run(main())"
sawc9 = {"sections": [{"section_id": "s1", "subtopics": [{"code_ref_hash": routed9[0], "subheading": "Install", "explanation": "x"}], "citations": [{"source_key": "k"}]}],
         "_routed_hashes": routed9, "_digest_sources": ["k"]}
assert not CK.domain.check_code_coverage(sawc9).passed                       # 1 of 7 printed, no bodies to compare
assert CK.domain.check_code_coverage({**sawc9, "_vault_bodies": b9}).passed  # 6 of 7 printed or repeated by a printed near-copy

R = CK.schemas.CriterionResult
names = ["c1", "c2", "c3", "c4", "c5"]
run = lambda *flags: [R(name=n, passed=f, kind="llm_judge", feedback="" if f else f"{n} is bad") for n, f in zip(names, flags)]
mj = CK.domain.majority_verdicts([run(False, False, False, False, False), run(True, True, True, False, True), run(True, False, True, False, True)])
assert [r.passed for r in mj] == [True, False, True, False, True] and mj[1].feedback == "c2 is bad" and mj[0].feedback == "", mj
assert [r.passed for r in CK.domain.majority_verdicts([run(False, False, False, False, False), run(True, True, True, True, True)])] == [False] * 5   # a tie is not a pass

W = CK.domain.wants_second_opinions
assert W(run(False, False, False, False, False))                               # harsh: >= JUDGE_HARSH_FAILS failures
assert not W(run(True, True, False, False, False))                             # 3 failures: not harsh enough to ask again
down = [R(name=n, passed=False, kind="llm_judge", feedback="judge_unavailable: x") for n in names]
assert not W(down) and not W([])                                               # an outage is not a verdict
# 11) a judge verdict that alone sends the chapter to a rewrite gets a second opinion, even with only 3 of 5 failures
three_fail = run(True, True, False, False, False)                              # 2/5 pass
assert not W(three_fail, pre_passed=12, pre_total=12)                          # gates 12/12 + 2/5 judge = 14/17 = 82% >= 80%: this verdict does not cause a rewrite
assert W(three_fail, pre_passed=11, pre_total=12)                              # gates 11/12 + 2/5 judge = 13/17 = 76% < 80% (langchain ch-04): rewrite -> more runs
print("ALL OK")
