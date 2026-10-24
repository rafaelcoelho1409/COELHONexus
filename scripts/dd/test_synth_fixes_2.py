"""Offline checks for the 2026-10-15 Synth fixes found on the asyncio full study after batch 9 (no LLM, no cluster):
a source the digest routed nowhere is placed, the code-density floor follows the blocks the chapter has, the source top-up
writes one subtopic per page, and book_harmonize only patches grounded violations, one paragraph at a time.

    cd apps/server && .venv/bin/python ../../scripts/dd/test_synth_fixes_2.py"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))
import domains  # noqa: E402

DG = domains.dd.synth.nodes.digest.domain
DS = domains.dd.synth.nodes.digest.schemas
CK = domains.dd.synth.nodes.checklist.domain
SW = domains.dd.synth.nodes.sawc.domain
BH = domains.dd.synth.nodes.book_harmonize
BHD = BH.domain
BHS = BH.service

# 1) a source the digest routed to no section is placed by title/summary overlap, with all of its code blocks -----------------
ha, hb = "a" * 16, "b" * 16
empty = DS.SourceDigest(
    source_key="k_dev", source_title="Developing with asyncio",
    overall_summary="Debug mode, logging and running blocking code in an executor for asyncio programs. It also covers never-awaited coroutines.",
    contributes_to=[], unassigned_code_refs=[ha, hb],
)
fine = DS.SourceDigest(
    source_key="k_ok", source_title="Runners", overall_summary="asyncio.run and the Runner class that manage the event loop lifecycle.",
    contributes_to=[DS.SectionContribution(section_id="s1", source_key="k_ok", relevance="primary", summary="x" * 30, key_facts=["a fact that is long enough"], code_refs=[])],
)
outline = [
    {"section_id": "s1", "heading": "Running the loop", "description": "asyncio.run and run_until_complete"},
    {"section_id": "s2", "heading": "Blocking code and debug mode", "description": "run_in_executor, debug mode and logging"},
]
bodies = {"k_dev": f'Text <code-ref hash="{ha}" lang="python"/> and <code-ref hash="{hb}"/> and again <code-ref hash="{ha}"/>', "k_ok": "no code"}
out, routed = DG.route_unrouted_sources([empty, fine], outline, bodies)
c = out[0].contributes_to[0]
assert routed == ["k_dev"] and out[1] is fine, routed
assert (c.section_id, c.relevance, c.source_key, c.code_refs) == ("s2", "supporting", "k_dev", [ha, hb]) and out[0].unassigned_code_refs == [], c
assert len(c.key_facts) == 2 and c.summary.startswith("Debug mode"), c.key_facts
assert DG.route_unrouted_sources([empty], [], bodies) == ([empty], [])                    # no outline: nothing to do
tied = DS.SourceDigest(source_key="k_x", source_title="Zzz", overall_summary="Something unrelated to every section heading here.", contributes_to=[])
assert DG.route_unrouted_sources([tied], outline, {})[0][0].contributes_to[0].section_id == "s1"   # no overlap: the earliest section
# the merge step then sees a normal contribution and the section index lists the source
idx = DG.build_per_section_index(out, ["s1", "s2"])
assert [x.source_key for x in idx["s2"]] == ["k_dev"] and [x.source_key for x in idx["s1"]] == ["k_ok"]
print("1 unrouted source placed with its code ok")

# 2) code density floor follows the routed blocks the chapter has --------------------------------------------------------------
def dens(used_per_section, routed):
    secs = [{"section_id": f"s{i + 1}", "subtopics": [{"code_ref_hash": f"{i}{j}".ljust(16, "0")} for j in range(n)] + [{"code_ref_hash": ""}]} for i, n in enumerate(used_per_section)]
    sawc = {"sections": secs}
    if routed is not None:
        sawc["_routed_hashes"] = [f"{k:016x}" for k in range(routed)]
    return CK.check_code_density_appropriate(sawc)
assert not dens([3, 0], None).passed                                                       # no signal: fixed floor of 2.0, as before
assert dens([3, 0], 4).passed                                                              # asyncio ch-04: 4 blocks, 3 printed
assert dens([1, 0, 0], 1).passed                                                           # asyncio ch-09: a single block, printed
assert not dens([0, 0, 0], 1).passed                                                       # the block exists and is not printed
assert not dens([1, 0], 30).passed                                                         # plenty of blocks: the floor is still 2.0
assert dens([2, 2], 30).passed
print("2 density floor scales to the available blocks ok")

# 3) the source top-up writes ONE subtopic per page; short fragments are dropped ------------------------------------------------
sources = [
    ("k_sub", "SubprocessTransport connects a parent process and its child", [
        "SubprocessTransport is a subclass of BaseTransport for a parent process and its child OS process",
        "Instances are created by loop.subprocess_shell() and loop.subprocess_exec() when they spawn the child",
        "It inherits transport methods from BaseTransport for subprocess communication",
    ]),
    ("k_two", "get_event_loop returns the current loop", ["asyncio.get_event_loop() returns the current event loop for the thread"]),
]
frag = {"subtopics": [
    {"subheading": "SubprocessTransport Overview", "explanation": "SubprocessTransport is a specialized subclass of BaseTransport used for communication between a parent process and its child OS process.", "sources": ["k_sub"]},
    {"subheading": "Inheritance and Features", "explanation": "It inherits from BaseTransport, providing transport methods for subprocess communication.", "sources": ["k_sub"]},
    {"subheading": "Creation via Loop Methods", "explanation": "Instances are created and returned by loop.subprocess_shell() and loop.subprocess_exec(), which spawn child processes.", "sources": ["k_sub"]},
    {"subheading": "Current loop", "explanation": "asyncio.get_event_loop() returns the loop.", "sources": ["k_two"]},
]}
subs, cites = SW.parse_source_topup(frag, sources=sources, existing_subheads=set(), max_new=4)
assert [s.subheading for s in subs] == ["SubprocessTransport Overview"], [s.subheading for s in subs]   # three fragments -> one; the 6-word one is dropped
assert len(subs[0].explanation.split()) >= SW.params.TOPUP_SRC_MIN_WORDS and "subprocess_exec" in subs[0].explanation and "Inheritance" not in subs[0].explanation
assert [c.source_key for c in cites] == ["k_sub"], cites                                      # the dropped subtopic cites nothing
assert "ONE subtopic" in SW.build_source_topup_prompt(framework="asyncio", chapter_title="T", section_heading="S", section_description="d",
                                                     existing_subheadings=[], sources=sources, max_new=4)
print("3 source top-up folds fragments of one page ok")

# 4) a violation is kept only when it quotes the chapter and names a real sibling claim or canonical term ---------------------
prose = ("# Chapter\n\nInvalidStateError occurs when a Future set_result method is called after a result has already been set.\n\n"
         "ProactorEventLoop, a subclass of AbstractEventLoop, is implemented in asyncio/windows_events.py.\n")
claims = {"ch-06": ["Future.result() raises InvalidStateError when the Future is not done yet"], "ch-08": ["x"]}
terms = [{"term": "Event Loop Policy", "canonical_definition": "an object that creates event loops"}]
live = [  # the two violations the live study applied: no sibling evidence at all
    {"kind": "CONTRADICTION", "this_chapter_says": "InvalidStateError occurs when a Future's set_result method is called after a result has already been set.",
     "should_say": "Calling Future.result() raises InvalidStateError when the Future is not done.", "evidence": "Section 'When InvalidStateError is Raised'"},
    {"kind": "definition_drift", "this_chapter_says": "ProactorEventLoop, a subclass of AbstractEventLoop", "should_say": "ProactorEventLoop is a subclass of BaseEventLoop",
     "evidence": "Proactor Event Loop Implementation section"},
]
kept, dropped = BHD.validate_violations(live, this_id="ch-08", this_prose=prose, claims_by_id=claims, canonical_terms=terms)
assert kept == [] and len(dropped) == 2, (kept, dropped)
good = {"kind": "contradiction", "this_chapter_says": "a Future set_result method is called after a result has already been set", "should_say": "x",
        "sibling_chapter": "ch-06", "sibling_claim": "Future.result() raises InvalidStateError when the Future is not done"}
assert BHD.validate_violations([good], this_id="ch-08", this_prose=prose, claims_by_id=claims, canonical_terms=terms)[0] == [good]
assert BHD.validate_violations([{**good, "sibling_chapter": "ch-08"}], this_id="ch-08", this_prose=prose, claims_by_id=claims, canonical_terms=terms)[0] == []   # itself
assert BHD.validate_violations([{**good, "sibling_chapter": "ch-99"}], this_id="ch-08", this_prose=prose, claims_by_id=claims, canonical_terms=terms)[0] == []   # no such chapter
assert BHD.validate_violations([{**good, "sibling_claim": "Kubernetes operators reconcile desired state with cluster state"}], this_id="ch-08", this_prose=prose,
                               claims_by_id=claims, canonical_terms=terms)[0] == []                                     # invented sibling claim
assert BHD.validate_violations([{**good, "this_chapter_says": "a Task is cancelled when the loop stops running"}], this_id="ch-08", this_prose=prose,
                               claims_by_id=claims, canonical_terms=terms)[0] == []                                     # quote not in the chapter
term_v = {"kind": "terminology_divergence", "this_chapter_says": "ProactorEventLoop, a subclass of AbstractEventLoop", "should_say": "use the name event loop policy",
          "sibling_chapter": "", "sibling_claim": "Event Loop Policy"}
assert BHD.validate_violations([term_v], this_id="ch-08", this_prose=prose, claims_by_id=claims, canonical_terms=terms)[0] == [term_v]   # a canonical term backs it
assert BHD.validate_violations([{**term_v, "kind": "contradiction"}], this_id="ch-08", this_prose=prose, claims_by_id=claims, canonical_terms=terms)[0] == []   # not for a contradiction
print("4 harmonize violations must be grounded ok")

# 5) a kept violation is fixed by editing only the paragraph that holds it ---------------------------------------------------------
book = ("# Asyncio Exceptions\n\n## Contents\n\n- [A](#a)\n\n## Section\n\nCancelledError signals a cancelled operation and is a BaseException since 3.8.\n\n"
        "InvalidStateError occurs when a Future set_result method is called after a result has already been set. This guard protects against races.\n\n"
        "```python\nfut.set_result(1)\nfut.set_result(2)\n```\n\n**Sources for this section:**\n\n- `0050.md` — InvalidStateError text.\n")
v = {"kind": "contradiction", "this_chapter_says": "a Future set_result method is called after a result has already been set", "should_say": "result() on a pending Future"}
seen_prompts: list[str] = []
def stub(reply):
    async def _stub(prompt, **kw):
        seen_prompts.append(prompt)
        return reply, None
    return _stub
par = "InvalidStateError occurs when a Future set_result method is called after a result has already been set. This guard protects against races."
edited = "InvalidStateError occurs when result() is called on a Future that is not done. This guard protects against races."
BHS._call_with_retry = stub(edited)
res = asyncio.run(BHS._patch_chapter(sem=asyncio.Semaphore(2), chapter_id="ch-08", framework_name="asyncio", original_prose=book, violations=[v], canonical_terms=[]))
assert res is not None and res.replace(edited, par) == book, res                           # everything but that paragraph is byte-identical
assert len(seen_prompts) == 1 and par in seen_prompts[0] and "CancelledError" not in seen_prompts[0] and "Sources for this section" not in seen_prompts[0]
assert BHD.code_blocks_preserved(book, res)
for bad in ("", "```python\nx = 1\n```", edited * 4, "ok"):                                 # empty, fenced, far too long, far too short
    BHS._call_with_retry = stub(bad)
    assert asyncio.run(BHS._patch_chapter(sem=asyncio.Semaphore(2), chapter_id="c", framework_name="a", original_prose=book, violations=[v], canonical_terms=[])) is None, bad
BHS._call_with_retry = stub(edited)
heading_v = {**v, "this_chapter_says": "Asyncio Exceptions Contents"}                       # a quote that is only in headings / lists is not editable
assert asyncio.run(BHS._patch_chapter(sem=asyncio.Semaphore(2), chapter_id="c", framework_name="a", original_prose=book, violations=[heading_v], canonical_terms=[])) is None
print("5 harmonize patch edits one paragraph ok")

# 6) harmonize_book end to end: the ungrounded violation is reported but never applied -------------------------------------------
async def _extract(sem, ch):
    return {"claims": claims.get(ch["chapter_id"], []), "terms": []}
async def _canon(name, terms_by_id):
    return terms
async def _detect(**kw):
    if kw["chapter_id"] == "ch-08":
        return {"has_violations": True, "violations": [good, live[1]], "summary": "s"}
    return {"has_violations": True, "violations": live, "summary": "s"}
BHS._extract_claims_and_terms, BHS._canonicalize_terms, BHS._detect_violations = _extract, _canon, _detect
BHS._call_with_retry = stub(edited)
chapters = [{"chapter_id": "ch-08", "prose": book}, {"chapter_id": "ch-06", "prose": prose}]
rep = asyncio.run(BHS.harmonize_book(framework_slug="asyncio", framework_name="asyncio", chapters=chapters))
by = {p["chapter_id"]: p for p in rep["patches"]}
assert by["ch-08"]["patched"] and by["ch-08"]["n_violations"] == 1 and by["ch-08"]["n_violations_raw"] == 2 and edited in by["ch-08"]["new_prose"], by["ch-08"]
assert not by["ch-06"]["patched"] and by["ch-06"]["n_violations"] == 0 and by["ch-06"]["n_violations_raw"] == 2 and len(by["ch-06"]["dropped"]) == 2, by["ch-06"]
assert rep["n_chapters_with_issues"] == 1 and rep["n_chapters_patched"] == 1
print("6 harmonize_book keeps only grounded patches ok")

# 7) prompts format ----------------------------------------------------------------------------------------------------------------
dp = BH.prompts.build_detect_prompt(canonical_terms="t", chapter_id="ch-1", framework="asyncio", sibling_claims="  [ch-2] c", this_prose="p")
assert "EXACT words" in dp and "sibling_chapter" in dp and "Do NOT correct this chapter" in dp and "{{" not in dp
pp = BH.prompts.build_patch_paragraph_prompt(canonical_terms="t", chapter_id="ch-1", framework="asyncio", paragraph="PARA", violations_block="V")
assert "PARA" in pp and "ONE paragraph" in pp
print("7 prompts ok")

# 8) sections are not merged when the result would hold more routed code blocks than a section can print ------------------------------
def src_with(key, sid_hashes):
    return DS.SourceDigest(source_key=key, source_title=key, overall_summary="x" * 40, contributes_to=[
        DS.SectionContribution(section_id=sid, source_key=key, relevance="primary", summary="y" * 30, key_facts=["a fact long enough"], code_refs=hs)
        for sid, hs in sid_hashes.items()])
hx = [f"{i:016x}" for i in range(40)]
outline2 = [{"section_id": "s1", "heading": "A", "description": ""}, {"section_id": "s2", "heading": "B", "description": ""}]
def merge_case(n_big, n_small):
    ps = [src_with("p1", {"s1": hx[:n_big]}), src_with("p2", {"s1": [], "s2": hx[n_big:n_big + n_small]})]
    ps.append(src_with("p3", {"s2": []}))
    # s2's whole primary pool {p2, p3}: p2 is also in s1, p3 only in s2 -> force containment by giving p3 to s1 as well
    ps[2] = src_with("p3", {"s1": [], "s2": []})
    return DG.merge_overlapping_sections(ps, outline2)[1]
assert merge_case(6, 4) == {"s2": "s1"} or merge_case(6, 4) == {}      # small chapters may still fold (existing behaviour)
heavy = [src_with("p1", {"s1": hx[:20]}), src_with("p2", {"s1": hx[20:22], "s2": hx[22:28]}), src_with("p3", {"s1": [], "s2": []})]
assert DG.merge_overlapping_sections(heavy, outline2)[1] == {}, "20+8 blocks must stay two sections"
light = [src_with("p1", {"s1": hx[:5]}), src_with("p2", {"s1": hx[5:6], "s2": hx[6:8]}), src_with("p3", {"s1": [], "s2": []})]
assert DG.merge_overlapping_sections(light, outline2)[1] == {"s2": "s1"}, "a light pair still folds"
print("8 merge is capped by routed blocks ok")

# 9) an over-long intro is trimmed, not rejected ---------------------------------------------------------------------------------------
long_intro = ("This section walks through the loop. " * 11).strip()          # 405 chars of whole sentences
assert len(long_intro) > SW.params.INTRO_CHARS_MAX
t = SW.trim_intro(long_intro)
assert SW.params.INTRO_CHARS_MIN <= len(t) <= SW.params.INTRO_CHARS_MAX and t.endswith("."), (len(t), t[-30:])
noend = "word " * 90
t2 = SW.trim_intro(noend)
assert len(t2) <= SW.params.INTRO_CHARS_MAX and t2.endswith(".")
assert SW.trim_intro("Short intro that is fine and long enough to pass.") == "Short intro that is fine and long enough to pass."
SCH2 = domains.dd.synth.nodes.sawc.schemas
raw = {"heading": "H", "intro": long_intro, "subtopics": [{"code_ref_hash": "", "subheading": f"Topic {i}", "explanation": "word " * 10} for i in range(3)],
       "citations": [{"source_key": "k", "claim": "a claim that is long enough"}]}
d, err = SW.try_parse_draft(raw, expected_heading="H")
assert d is not None and err is None, err
print("9 over-long intro trimmed ok")

# 10) the coverage top-up may grow a section past the writer's 12 --------------------------------------------------------------------
import json as _json
subs10 = [SCH2.Subtopic(code_ref_hash=f"{i:016x}", subheading=f"Topic {i}", explanation="word " * 10) for i in range(11)]
sec10 = SCH2.Section(section_id="s1", heading="Sec s1", intro="x" * 70, subtopics=subs10, citations=[SCH2.Citation(source_key="k", claim="a claim that is long enough")])
idx10 = {"s1": [{"source_key": "k", "relevance": "primary", "summary": "s", "key_facts": ["a fact long enough"], "code_refs": [f"{100 + i:016x}" for i in range(9)]}]}
vault10 = {f"{100 + i:016x}": {"fence_text": f"```python\nawait thing_{i}()\n```"} for i in range(9)}
seen10 = []
_orig_prompt = SW.build_topup_prompt
SW.build_topup_prompt = lambda **kw: (seen10.append(kw["max_new"]), "p")[1]
SV = domains.dd.synth.nodes.sawc.service
async def _chat10(prompt, **kw):
    return _json.dumps({"subtopics": []}), {}
domains.settings.chat.service.chat_text_async = _chat10
asyncio.run(SV._topup_coverage(final_sections=[sec10], per_section_index=idx10, vault_rich=vault10, sections_by_id={}, framework="a", chapter_id="c", chapter_title="t"))
SW.build_topup_prompt = _orig_prompt
assert seen10 == [5], seen10                          # 16 - 11 = 5 (was 12 - 11 = 1, so a section routed many blocks could not print them)
print("10 coverage top-up capacity ok")

# 11) a judge outage is retried with a cool-off, and a chapter whose judge never answers is scored on its gates, not rewritten -----------------
CKS = domains.dd.synth.nodes.checklist.service
CKP = domains.dd.synth.nodes.checklist.params
CKSC = domains.dd.synth.nodes.checklist.schemas
calls11 = []
sleeps11 = []
async def _down(prompt, schema, **kw):
    calls11.append(1)
    raise RuntimeError("503 Service Unavailable")
real_sleep = asyncio.sleep
async def _sleep(t, *a, **k):
    sleeps11.append(t)
domains.settings.chat.service.chat_structured_async = _down
CKS.asyncio.sleep = _sleep
res11, dep11, _, _ = asyncio.run(CKS._run_llm_judge(thread_id="t", chapter_id="c", chapter_title="T", framework="asyncio", sawc={"sections": []}, digest={},
                                                    rendered_chapter="x", rendered_digest="y", truncated=False))
CKS.asyncio.sleep = real_sleep
assert len(calls11) == CKP.MAX_CALL_ATTEMPTS + CKP.JUDGE_EXTRA_ATTEMPTS == 4, calls11
assert CKP.JUDGE_COOLOFF_S in sleeps11 and dep11 is None and all(r.feedback.startswith("judge_unavailable") and not r.passed for r in res11) and len(res11) == 5
print("11 judge outage retried with a cool-off ok")

# 12) an unexplained FAIL ("some feedback") is asked again, then counted as passed ----------------------------------------------------------------
assert CK.is_placeholder_feedback("some feedback") and CK.is_placeholder_feedback("") and CK.is_placeholder_feedback("N/A.") and not CK.is_placeholder_feedback("s1 intro is meta-framing")
def payload12(fb):
    v = {n: {"passed": True, "feedback": ""} for n in CKP.LLM_CRITERIA}
    v["chapter_reads_coherently"] = {"passed": False, "feedback": fb}
    return CKSC.LLMJudgePayload.model_validate(v)
seq12 = [payload12("some feedback"), payload12("some feedback")]
async def _ask(prompt, schema, **kw):
    return seq12.pop(0), {"deployment": "x/y"}
domains.settings.chat.service.chat_structured_async = _ask
res12, _, _, _ = asyncio.run(CKS._run_llm_judge(thread_id="t", chapter_id="c", chapter_title="T", framework="asyncio", sawc={"sections": []}, digest={},
                                                rendered_chapter="x", rendered_digest="y", truncated=False))
assert all(r.passed for r in res12) and not seq12, ([r.name for r in res12 if not r.passed], len(seq12))   # asked twice, still unexplained -> pass
seq12 = [payload12("some feedback"), payload12("s1 and s2 are disconnected fragments with no linking prose")]
res12b = asyncio.run(CKS._run_llm_judge(thread_id="t", chapter_id="c", chapter_title="T", framework="asyncio", sawc={"sections": []}, digest={},
                                        rendered_chapter="x", rendered_digest="y", truncated=False))[0]
assert [r.name for r in res12b if not r.passed] == ["chapter_reads_coherently"] and "disconnected" in res12b[0].feedback, res12b[0]   # the second answer explains it
seq12 = [payload12("s1 repeats s2 almost word for word")]
res12c = asyncio.run(CKS._run_llm_judge(thread_id="t", chapter_id="c", chapter_title="T", framework="asyncio", sawc={"sections": []}, digest={},
                                        rendered_chapter="x", rendered_digest="y", truncated=False))[0]
assert [r.name for r in res12c if not r.passed] == ["chapter_reads_coherently"] and not seq12   # a real reason: one call, kept
print("12 unexplained FAIL asked again ok")

# 13) the picker ceiling is inclusive; the intro field asks for substance -----------------------------------------------------------------------------
assert CK.check_picker_fallback_rate_low({"coverage_stats": {"n_picker_fallbacks": 1, "n_critic_picks": 2}}).passed
assert not CK.check_picker_fallback_rate_low({"coverage_stats": {"n_picker_fallbacks": 2, "n_critic_picks": 3}}).passed
wp = SW.build_writer_prompt.__wrapped__ if hasattr(SW.build_writer_prompt, "__wrapped__") else SW.build_writer_prompt
import inspect
src = inspect.getsource(SW)
assert "NEVER open with" in src and "framing what this section covers" not in src
print("13 picker ceiling and intro prompt ok")

# 14) writer output loses U+2011 / no-break spaces -----------------------------------------------------------------------------------------------
raw14 = {"heading": "H", "intro": "Low\u2011level APIs for top\u2011level code in the loop, written for readers of asyncio.", "subtopics": [
    {"code_ref_hash": "", "subheading": f"Low\u2011Level Socket {i}", "explanation": "Uses the\u00a0loop and a non\u2011blocking socket to read data from peers."} for i in range(3)],
    "citations": [{"source_key": "k", "claim": "a claim that is long enough"}]}
d14, err14 = SW.try_parse_draft(raw14, expected_heading="H")
assert d14 is not None, err14
assert "\u2011" not in d14.intro and d14.intro.startswith("Low-level APIs") and d14.subtopics[0].subheading == "Low-Level Socket 0"
assert "\u00a0" not in d14.subtopics[0].explanation and "non-blocking" in d14.subtopics[0].explanation
sub14, _ = SW.parse_source_topup({"subtopics": [{"subheading": "Platform\u2011Specific Limitations", "explanation": "asyncio.get_event_loop() returns the current event loop for the thread, with a non\u2011blocking default policy that callers on other threads must not rely on.", "sources": ["k_two"]}]},
                                  sources=[("k_two", "get_event_loop returns the current loop", ["asyncio.get_event_loop() returns the current event loop for the thread"])], existing_subheads=set(), max_new=2)
assert sub14 and sub14[0].subheading == "Platform-Specific Limitations" and "non-blocking" in sub14[0].explanation        # the top-up path too
print("14 typography normalized ok")

# 15) the saved evaluation is the shipped iteration's --------------------------------------------------------------------------------------------------
RS = domains.dd.synth.nodes.render.service
CKK = domains.dd.synth.nodes.checklist.keys
class FakeMinio:
    def __init__(self, files): self.files = dict(files)
    async def exists(self, k): return k in self.files
    async def read_text(self, k): return self.files[k]
    async def write(self, k, v, content_type=None): self.files[k] = v
mn = FakeMinio({CKK.latest_blob_key("a", "c"): "LAST ITERATION", CKK.best_blob_key("a", "c"): "BEST ITERATION"})
assert asyncio.run(RS._publish_best_checklist(mn, "a", "c")) is True and mn.files[CKK.latest_blob_key("a", "c")] == "BEST ITERATION"
mn2 = FakeMinio({CKK.latest_blob_key("a", "c"): "LAST ITERATION"})
assert asyncio.run(RS._publish_best_checklist(mn2, "a", "c")) is False and mn2.files[CKK.latest_blob_key("a", "c")] == "LAST ITERATION"   # no best blob: untouched
class Broken(FakeMinio):
    async def write(self, k, v, content_type=None): raise RuntimeError("minio down")
assert asyncio.run(RS._publish_best_checklist(Broken({CKK.best_blob_key("a", "c"): "B"}), "a", "c")) is False                              # fail-soft
assert CKK.best_blob_key("a", "c").endswith("a/c/checklist-best.json")
print("15 shipped evaluation published ok")

# 16) /active reports a single-chapter run (the strip's per-chapter ▶ button) so a page refresh can re-attach ---------------------------------------------
import importlib
from fastapi import Response
import os
for _ in range(30):                 # the router's module-level config reads env vars the cluster sets; give missing ones a dummy
    try:
        R = importlib.import_module("entrypoints.api.v1.dd.synth.router")
        S = importlib.import_module("entrypoints.api.v1.dd.synth.service")
        break
    except KeyError as e:
        os.environ.setdefault(str(e.args[0]), "localhost")
        for m in [m for m in sys.modules if m == "entrypoints" or m.startswith("entrypoints.")]:
            del sys.modules[m]
class FakeRedis:
    def __init__(self, kv): self.kv = kv
    async def get(self, k): return self.kv.get(k)
    async def aclose(self): pass
def active_with(kv):
    R.redis_aio.from_url = lambda *a, **k: FakeRedis(kv)
    return asyncio.run(R.synth_active("asyncio", Response()))
KEYS = domains.dd.synth.keys
assert active_with({}) == {"active": False}
study = active_with({KEYS.active_study_key("asyncio"): '{"study_thread_id": "docs-distiller/study/asyncio/S1", "started_ts": 12.5}'})
assert study["active"] and study["study_thread_id"] == "docs-distiller/study/asyncio/S1" and study["started_ts"] == 12.5 and "chapter_thread_id" not in study
one = active_with({KEYS.lock_key("asyncio"): b"docs-distiller/synth/asyncio/T1"})
assert one == {"active": True, "chapter_thread_id": "docs-distiller/synth/asyncio/T1", "study_thread_id": None, "started_ts": None}, one
held_by_study = active_with({KEYS.lock_key("asyncio"): "docs-distiller/study/asyncio/S1"})
assert held_by_study == {"active": False}, held_by_study       # a study's own lock without a registry entry is not a chapter run
print("16 /active reports a single-chapter run ok")

# 17) a chapter wipe (the ▶ re-run of a done chapter) refuses while ANY synth run holds the slug's lock ------------------------------------------------------
from fastapi import HTTPException
class FakeStore:
    def __init__(self): self.deleted = []
    async def delete_prefix(self, p): self.deleted.append(p); return 3
    async def read_text(self, k): raise KeyError(k)
store17 = FakeStore()
domains.dd.ingestion.storage.service.get_storage = lambda: store17
def wipe_with(kv):
    R.redis_aio.from_url = lambda *a, **k: FakeRedis(kv)
    return asyncio.run(S.wipe_one_chapter("asyncio", "ch-01-a"))
for kv in ({KEYS.lock_key("asyncio"): b"docs-distiller/synth/asyncio/T1"},                                     # a single-chapter run in another tab
           {KEYS.active_study_key("asyncio"): '{"study_thread_id": "docs-distiller/study/asyncio/S1"}'}):      # a study
    try:
        wipe_with(kv)
        raise AssertionError("wipe must be refused")
    except HTTPException as e:
        assert e.status_code == 409, e.status_code
assert store17.deleted == []                                                                                    # nothing was deleted
assert wipe_with({})["minio_deleted"] == 3 and store17.deleted == ["synth/asyncio/ch-01-a/"]                    # idle: wipes just that chapter
print("17 chapter wipe refused while a run holds the lock ok")
print("ALL OK")
