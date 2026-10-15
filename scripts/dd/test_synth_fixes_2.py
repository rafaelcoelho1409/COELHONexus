"""Offline checks for the 2026-10-15 Synth fixes found on the asyncio full study after batch 9 (no LLM, no cluster):
a source the digest routed nowhere is placed, the code-density floor follows the blocks the chapter has, the source top-up
writes one subtopic per page, and book_harmonize only patches grounded violations, one paragraph at a time.

    cd apps/fastapi && .venv/bin/python ../../scripts/dd/test_synth_fixes_2.py"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "fastapi"))
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
print("ALL OK")
