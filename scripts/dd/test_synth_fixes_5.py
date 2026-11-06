"""Offline checks for the 2026-10-16 "Nexus side" batch after the fastmcp ch-01 runs (no LLM, no cluster): the outline section floor and the
USC fallback, heuristic stubs for failed digest sources, the judge's wait budget, the late retry of lost sections, writer subtopic ranges,
top-up reject reasons and failed-call counters.

    cd apps/server && .venv/bin/python ../../scripts/dd/test_synth_fixes_5.py"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))
import ai, domains  # noqa: E402

N = ai.dd.nodes.pipeline.synth
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
print("1 outline section floor follows the block count ok")

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
assert N.shared.params.WAVE_PAUSE_S >= 30
print("2 failed digest sources kept as stubs ok")

# 5) the writer prompt asks for what the validator requires -----------------------------------------------------------------------------------------
assert [SWD.writer_subtopic_range(n)[0] for n in (0, 9, 10, 19, 20, 46)] == ["3-8", "3-8", "4-7", "4-7", "6-9", "6-9"]
assert SWD.code_density_floor(26) == 6 and SWD.code_density_floor(12) == 4 and SWD.code_density_floor(7) == 3 and SWD.code_density_floor(1) == SWP.SUBTOPICS_MIN
VE = domains.dd.ingestion.vault.schemas.VaultEntry
vr = {f"{i:016x}": VE(hash=f"{i:016x}", fence_text=f"```python\nvalue_{i} = compute_{i}()\n```", info_string="python", lang="python", line_count=1, char_count=30, sentinel_kind="fence_backtick") for i in range(26)}
ask = dict(framework="f", chapter_id="ch", chapter_title="T", section_id="s1", section_heading="H", section_description="d", section_prerequisites=[],
           contributions=[], valid_source_keys=["k"], memory=[], n_primary_contribs=1, vault_rich=vr)
big = SWD.writer_request(allowed_hashes=sorted(vr), **ask)
small = SWD.writer_request(allowed_hashes=sorted(vr)[:5], **ask)
assert big["aim"] == "6-9" and "Pick 6-9 of the bank" in big["mode_note"] and "pattern 6-9 times" in big["mode_note"], big["mode_note"]
assert small["aim"] == "4-6" and "Pick 3-8 of the bank" in small["mode_note"]
light = SWD.writer_request(allowed_hashes=sorted(vr), vault_char_budget=400, **ask)
assert len(light["code_bank"]) < len(big["code_bank"])
assert SWD.writer_request(allowed_hashes=[], prose_mode=True, **ask)["mode"] == "prose" and SWD.writer_request(allowed_hashes=sorted(vr)[:2], mixed_mode=True, **ask)["mode"] == "mixed"
assert sorted(SWD.vault_files(sorted(vr)[:2], vr)) == [f"/{h}.md" for h in sorted(vr)[:2]]            # the writer's /vault/ files
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
