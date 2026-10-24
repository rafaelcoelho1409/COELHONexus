"""Offline checks for the 2026-10-16 "Nexus side" batch after the fastmcp ch-01 runs (no LLM, no cluster): the outline section floor and the
USC fallback, heuristic stubs for failed digest sources, the judge's wait budget, the late retry of lost sections, writer subtopic ranges,
top-up reject reasons and failed-call counters.

    cd apps/server && .venv/bin/python ../../scripts/dd/test_synth_fixes_5.py"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))
import domains  # noqa: E402

N = domains.dd.synth.nodes
OL, DG, CK, SW = N.outline, N.digest, N.checklist, N.sawc
SWS, SWD, SWP = SW.schemas, SW.domain, SW.params
chat = domains.settings.chat.service

# 1) outline: a code-heavy chapter needs enough sections; a failed USC picker falls back to the best-validated candidate ------------------------
P = OL.params
assert [P.min_h2_for_n_blocks(b, 12) for b in (0, 35, 40, 48, 100, 135, 300)] == [0, 0, 0, 3, 5, 7, 12], [P.min_h2_for_n_blocks(b, 12) for b in (0, 35, 40, 48, 100, 135, 300)]
assert P.min_h2_for_n_blocks(135, 4) == 4                                                    # never above the cap


def outline(n):
    return OL.schemas.ChapterOutline(sections=[
        OL.schemas.OutlineSection(section_id=f"s{i + 1}", heading=f"Install for client {chr(65 + i)}", description="Installing the server for one specific client and its config file.", prerequisites=[])
        for i in range(n)])


o4, o7 = outline(4), outline(7)
iss4 = OL.domain.validate_outline_structure(o4, OL.domain.derive_dag(o4.sections), n_sources=18, n_blocks=135)[1]
iss7 = OL.domain.validate_outline_structure(o7, OL.domain.derive_dag(o7.sections), n_sources=18, n_blocks=135)[1]
assert any("AT LEAST 7 sections" in x for x in iss4) and not any("AT LEAST" in x for x in iss7), (iss4, iss7)
assert not any("AT LEAST" in x for x in OL.domain.validate_outline_structure(o4, OL.domain.derive_dag(o4.sections), n_sources=18)[1])   # no block count: no floor
cands = [(o4, OL.domain.derive_dag(o4.sections), ["a violation"]), (o7, OL.domain.derive_dag(o7.sections), []), (o4, OL.domain.derive_dag(o4.sections), [])]
assert OL.domain.fallback_candidate_index(cands, 12) == 1                                     # fewest issues, then most sections
cands2 = [(o4, OL.domain.derive_dag(o4.sections), []), (o7, OL.domain.derive_dag(o7.sections), [])]
assert OL.domain.fallback_candidate_index(cands2, 12) == 1 and OL.domain.fallback_candidate_index(cands2, 5) == 1 and OL.domain.fallback_candidate_index([cands2[0]], 12) == 0
# the picker itself: a failing LLM call returns the fallback, not 0
OS = OL.service


async def boom(*a, **k):
    raise RuntimeError("504")

orig_struct = chat.chat_structured_async
chat.chat_structured_async = boom
try:
    assert asyncio.run(OS._usc_pick(cands, "ch-01", "Install", 12)) == 1
finally:
    chat.chat_structured_async = orig_struct
print("1 outline floor + best-validated fallback ok")

# 2) digest: a source whose digest failed keeps its title and ALL its code blocks, placed by overlap ------------------------------------------------
md = ('# MCP JSON configuration\n\nHow the mcp.json file is generated for each client and which keys it holds for the server entry.\n\n'
      '<code-ref hash="aaaaaaaaaaaaaaaa" lang="json"/>\n\nThe `fastmcp install mcp-json` command prints a configuration block you paste into the client.\n<code-ref hash="bbbbbbbbbbbbbbbb"/>')
stub = DG.domain.stub_digest_for_failed("ingestion/fastmcp/pages/0237-mcp-json.md", md)
assert stub is not None and stub.source_title == "MCP JSON configuration" and stub.contributes_to == []
assert stub.unassigned_code_refs == ["aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"] and "<code-ref" not in stub.overall_summary
assert DG.domain.stub_digest_for_failed("k", "tiny") is None and DG.domain.stub_digest_for_failed("k", "") is None
outline_secs = [{"section_id": "s1", "heading": "Install basics", "description": "fastmcp install and uv"},
                {"section_id": "s2", "heading": "Generating MCP JSON", "description": "mcp.json configuration for each client"}]
placed, routed = DG.domain.route_unrouted_sources([stub], outline_secs, {stub.source_key: md})
assert routed == [stub.source_key] and placed[0].contributes_to[0].section_id == "s2" and placed[0].contributes_to[0].code_refs == ["aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"], placed
assert DG.params.WAVE_PAUSE_S >= 30
print("2 failed digest sources kept as stubs ok")

# 3) judge: its total wait is bounded, and the first failure re-renders a lighter prompt ---------------------------------------------------------------------
CS = CK.service
calls, renders = [], []


async def judge_fail(prompt, schema, **kw):
    calls.append(len(prompt))
    raise RuntimeError("InternalServerError: Error code: 504")

real_render = CK.domain.render_chapter_for_judge


def spy_render(sawc, *, char_cap=CK.params.MAX_RENDERED_CHAPTER_CHARS):
    renders.append(char_cap)
    return real_render(sawc, char_cap=char_cap)


async def no_sleep(*a, **k):
    return None

sawc_blob = {"sections": [{"section_id": "s1", "heading": "H", "intro": "i", "subtopics": [{"subheading": "t", "explanation": "e " * 20, "code_ref_hash": ""}], "citations": []}]}


def run_judge():
    return asyncio.run(CS._run_llm_judge(
        thread_id="t", chapter_id="ch-01", chapter_title="T", framework="f", sawc=sawc_blob, digest={}, rendered_chapter="x" * 5000,
        rendered_digest="y" * 500, truncated=False,
    ))


chat.chat_structured_async = judge_fail
CK.domain.render_chapter_for_judge = spy_render
real_sleep = asyncio.sleep
CS.asyncio.sleep = no_sleep
old_budget, old_cool = CK.params.JUDGE_TOTAL_BUDGET_S, CK.params.JUDGE_COOLOFF_S
try:
    CK.params.JUDGE_COOLOFF_S = 0.0
    CK.params.JUDGE_TOTAL_BUDGET_S = 10_000.0
    calls.clear(); renders.clear()
    out = run_judge()
    assert len(calls) == CK.params.MAX_CALL_ATTEMPTS + CK.params.JUDGE_EXTRA_ATTEMPTS == 4, len(calls)     # roomy budget: every attempt is used
    assert renders and renders[0] == CK.params.MAX_RENDERED_CHAPTER_CHARS // 2 and calls[1] < calls[0], (renders, calls)   # after the 1st failure: a lighter prompt
    CK.params.JUDGE_TOTAL_BUDGET_S = 1.0                                                                    # tight budget: a retry that cannot finish is not started
    calls.clear()
    out = run_judge()
    assert len(calls) == 1, len(calls)
    assert out[0] and all(not r.passed for r in out[0])                                                     # the conservative fallback verdicts, as before
finally:
    chat.chat_structured_async = orig_struct
    CK.domain.render_chapter_for_judge = real_render
    CS.asyncio.sleep = real_sleep
    CK.params.JUDGE_TOTAL_BUDGET_S, CK.params.JUDGE_COOLOFF_S = old_budget, old_cool
print("3 judge wait budget ok")

# 4) a section that lost every draft is written once more, lighter; a recovered one replaces the placeholder and its memory entry ----------------------
SV = SW.service


def real_section(sid, n=3):
    return SWS.Section(section_id=sid, heading=f"Heading {sid}", intro="An intro sentence that is long enough to pass.",
                       subtopics=[SWS.Subtopic(code_ref_hash=f"{i}".ljust(16, "a") if sid == "s1" else "", subheading=f"Topic {sid} {i}",
                                               explanation="A distinct explanation sentence for this subtopic that has enough words in it.") for i in range(n)],
                       citations=[SWS.Citation(source_key="k", claim="a claim that is long enough")])


ph = lambda sid: SWD.placeholder_section(section_id=sid, heading=f"Heading {sid}", n_repairs=0, deployment_writer=None, error_tags=["ChatError"])
completed = {"s1": ph("s1"), "s2": real_section("s2"), "s3": ph("s3")}
ledger = [SWD.extract_memory_entry(completed["s1"], section_contributions=[], section_heading="Heading s1"), SWD.extract_memory_entry(completed["s2"], section_contributions=[], section_heading="Heading s2")]
used: set[str] = set()
seen_budget = []


async def run_section(sid, vault_char_budget=None):
    seen_budget.append((sid, vault_char_budget))
    return real_section(sid) if sid == "s1" else ph(sid)                  # s1 comes back, s3 fails again

recovered = asyncio.run(SV._late_retry_sections(section_ids=["s1", "s2", "s3", "s9"], run_section=run_section, completed_sections=completed, memory_ledger=ledger,
                                                chapter_used_hashes=used, per_section_index={}, known_section_ids={"s1", "s2", "s3"}, label="t"))
assert recovered == ["s1"] and sorted(seen_budget) == [("s1", SWP.LATE_RETRY_VAULT_CHARS), ("s3", SWP.LATE_RETRY_VAULT_CHARS)]
assert "placeholder" not in completed["s1"].issues and "placeholder" in completed["s3"].issues and len(completed["s1"].subtopics) == 3
assert [m.section_id for m in ledger].count("s1") == 1 and len(ledger) == 2 and used                  # one entry per section; recovered blocks counted as used
assert asyncio.run(SV._late_retry_sections(section_ids=["s2"], run_section=run_section, completed_sections=completed, memory_ledger=ledger,
                                           chapter_used_hashes=used, per_section_index={}, known_section_ids={"s2"}, label="t")) == []   # nothing lost: no calls
assert SV._draft_one_section.__kwdefaults__.get("vault_char_budget", "missing") is None
print("4 late retry of lost sections ok")

# 5) the writer prompt asks for what the validator requires -----------------------------------------------------------------------------------------
assert [SWD.writer_subtopic_range(n)[0] for n in (0, 9, 10, 19, 20, 46)] == ["3-8", "3-8", "4-7", "4-7", "6-9", "6-9"]
assert SWD.code_density_floor(26) == 6 and SWD.code_density_floor(12) == 4 and SWD.code_density_floor(7) == 3 and SWD.code_density_floor(1) == SWP.SUBTOPICS_MIN
VE = N.vault.schemas.VaultEntry
vr = {f"{i:016x}": VE(hash=f"{i:016x}", fence_text=f"```python\nvalue_{i} = compute_{i}()\n```", info_string="python", lang="python", line_count=1, char_count=30, sentinel_kind="fence_backtick") for i in range(26)}
big = SWD.build_writer_prompt(framework="f", chapter_id="ch", chapter_title="T", section_id="s1", section_heading="H", section_description="d", section_prerequisites=[],
                              contributions=[], allowed_hashes=sorted(vr), valid_source_keys=["k"], memory=[], n_primary_contribs=1, vault_rich=vr)
small = SWD.build_writer_prompt(framework="f", chapter_id="ch", chapter_title="T", section_id="s1", section_heading="H", section_description="d", section_prerequisites=[],
                                contributions=[], allowed_hashes=sorted(vr)[:5], valid_source_keys=["k"], memory=[], n_primary_contribs=1, vault_rich=vr)
assert "6-9 BEST ONES" in big and "aim for 6-9" in big and "6-9 times per section" in big, big[big.index("PICK") - 30: big.index("PICK") + 60]
assert "3-8 BEST ONES" in small and "aim for 4-6" in small
light = SWD.build_writer_prompt(framework="f", chapter_id="ch", chapter_title="T", section_id="s1", section_heading="H", section_description="d", section_prerequisites=[],
                                contributions=[], allowed_hashes=sorted(vr), valid_source_keys=["k"], memory=[], n_primary_contribs=1, vault_rich=vr, vault_char_budget=400)
assert len(light) < len(big)
print("5 writer subtopic range follows the validator ok")

# 6) top-up answers say why they were rejected ---------------------------------------------------------------------------------------------------
H1, H2 = "1" * 16, "2" * 16
rej: dict = {}
answer = {"subtopics": [
    {"code_ref_hash": "f" * 16, "subheading": "Unknown block", "explanation": "The compute function runs and returns the value for the block here."},
    {"code_ref_hash": H1, "subheading": "Prose about nothing", "explanation": "Completely unrelated words about kubernetes operators reconciling desired state."},
    {"code_ref_hash": H2, "subheading": "x", "explanation": "too short"}]}
blk = "```python\nimport asyncio\n\nasync def run_workers(queue_one, queue_two):\n    result_one = await queue_one.get()\n    await queue_two.put(result_one)\n```"
vr6 = {H1: {"fence_text": blk}, H2: {"fence_text": blk.replace("run_workers", "run_pipeline")}}
subs, _ = SWD.parse_topup(answer, unused={H1, H2}, vault_rich=vr6, valid_source_keys=set(), existing_subheads=set(), max_new=4, rejects=rej)
assert subs == [] and rej == {"hash_not_unused": 1, "prose_not_about_block": 1, "schema": 1}, rej
rej2: dict = {}
SWD.parse_topup(None, unused={H1}, vault_rich=vr6, valid_source_keys=set(), existing_subheads=set(), max_new=4, rejects=rej2)
assert rej2 == {"unparseable_json": 1}
rej3: dict = {}
SWD.parse_source_topup({"subtopics": [{"subheading": "Docs", "explanation": "The documentation provides examples of how to use the limit argument with buffers today."},
                                      {"subheading": "Off topic", "explanation": "Kubernetes operators reconcile desired state with cluster state on every watch event they receive."}]},
                       sources=[("k1", "exec reference", ["asyncio.create_subprocess_exec accepts a limit argument for the StreamReader buffers."])],
                       existing_subheads=set(), max_new=2, rejects=rej3)
assert rej3 == {"meta_framing": 1, "not_grounded_in_facts": 1}, rej3
print("6 top-up reject reasons ok")

# 7) failed LLM calls are classified for the per-thread error counters -------------------------------------------------------------------------------------
RS = domains.settings.llm_counter.domain
assert RS.failure_kind(RuntimeError("InternalServerError: Error code: 504 - {'error': ...}")) == "504"
assert RS.failure_kind(domains.settings.chat.errors.ChatTimeoutError("chat_text_async hard backstop fired after 160s")) == "timeout"
assert RS.failure_kind(RuntimeError("RateLimitError: Error code: 429 - slow down")) == "rate_limit" and RS.failure_kind(ValueError("bad")) == "ValueError"
assert asyncio.run(domains.settings.llm_counter.service.bump_current_failure(RuntimeError("504"))) is None   # outside a context: a no-op, no Redis touched
print("7 failure counters ok")
print("ALL OK")
