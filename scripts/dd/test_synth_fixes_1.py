"""Offline checks for the 2026-10-14 Synth fixes found on asyncio ch-01 (no LLM, no cluster):
other sections' code blocks are not bank padding; vault blocks render flush left.

    cd apps/fastapi && .venv/bin/python ../../scripts/dd/test_synth_fixes_1.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "fastapi"))
import domains  # noqa: E402

SW = domains.dd.synth.nodes.sawc.domain
RD = domains.dd.synth.nodes.render.domain

# 1) a section's chapter-wide padding must skip blocks routed to ANOTHER section --------------------------------------------
idx = {
    "s2": [{"relevance": "primary", "code_refs": ["a" * 16]}],
    "s3": [{"relevance": "primary", "code_refs": ["b" * 16, "c" * 16]}, {"relevance": "supporting", "code_refs": ["a" * 16]}],
}
n_dedup, _ = SW.dedupe_vault_hashes_across_sections(idx)
assert SW.hashes_claimed_elsewhere(idx, "s2") == {"b" * 16, "c" * 16}
assert SW.hashes_claimed_elsewhere(idx, "s3") == {"a" * 16}
assert SW.hashes_claimed_elsewhere({"s1": []}, "s1") == set()
print("1 hashes claimed by other sections ok")

# 2) a vault block lifted from a list item keeps its indentation: rendered flush left, drift check unchanged ---------------
block = "  ```python\n  import asyncio\n\n  async def main():\n      await asyncio.sleep(1)\n  ```"
h = RD.hash_block(block)
log = []
ctx = RD.build_section_context(
    {"section_id": "s1", "heading": "H", "intro": "", "subtopics": [{"subheading": "S", "explanation": "e", "code_ref_hash": h}], "citations": []},
    vault={h: block}, resolution_log=log,
)
rendered = ctx["subtopics"][0]["code_block"]
assert rendered.startswith("```python\nimport asyncio\n") and rendered.endswith("\n```"), rendered
assert "    await asyncio.sleep(1)" in rendered                     # relative indentation inside the code is kept
assert log[0].tier == "verbatim" and not log[0].byte_drift, log[0]
flush = "```python\nx = 1\n```"
ctx2 = RD.build_section_context(
    {"section_id": "s1", "heading": "H", "intro": "", "subtopics": [{"subheading": "S", "explanation": "e", "code_ref_hash": RD.hash_block(flush)}], "citations": []},
    vault={RD.hash_block(flush): flush}, resolution_log=[],
)
assert ctx2["subtopics"][0]["code_block"] == flush                # already flush left: untouched
print("2 vault code blocks render flush left ok")
# 3) a subheading naming `asyncio.gather()` over a block without it gets the block that has it -------------------------------
SCH = domains.dd.synth.nodes.sawc.schemas
seq = "```python\nasync def main():\n    await say_after(1, 'hello')\n    await say_after(2, 'world')\n```"
gat = "```python\nasync def main():\n    L = await asyncio.gather(factorial('A', 2), factorial('B', 3))\n```"
oth = "```python\nasync with asyncio.TaskGroup() as tg:\n    tg.create_task(job())\n```"
oth2 = "```python\nasync with asyncio.timeout(10):\n    await long_running_task()\n```"
vh = {x: {"fence_text": t} for x, t in {"a" * 16: seq, "b" * 16: gat, "c" * 16: oth, "d" * 16: oth2}.items()}
def sub(h, head): return SCH.Subtopic(code_ref_hash=h, subheading=head, explanation="The block runs the calls in order and prints each result when it finishes.")
cites = [SCH.Citation(source_key="k", claim="a claim about gather that is long enough")]
d = SCH.LLMSectionDraft(heading="H", intro="x" * 70, subtopics=[sub("a" * 16, "Scheduling Coroutines with `asyncio.gather()`"), sub("c" * 16, "Task groups"), sub("d" * 16, "Timeouts")], citations=cites)
fixed, n = SW.realign_api_subheadings(d, vh, set(vh))
assert n == 1 and fixed.subtopics[0].code_ref_hash == "b" * 16 and fixed.subtopics[1].code_ref_hash == "c" * 16, (n, [s.code_ref_hash[:1] for s in fixed.subtopics])
d2 = SCH.LLMSectionDraft(heading="H", intro="x" * 70, subtopics=[sub("b" * 16, "Scheduling with `asyncio.gather()`"), sub("c" * 16, "Groups"), sub("a" * 16, "Sequential")], citations=cites)
assert SW.realign_api_subheadings(d2, vh, set(vh))[1] == 0                                  # block already has the API
d3 = SCH.LLMSectionDraft(heading="H", intro="x" * 70, subtopics=[sub("a" * 16, "Scheduling with `asyncio.gather()`"), sub("b" * 16, "Gather again"), sub("c" * 16, "Groups")], citations=cites)
assert SW.realign_api_subheadings(d3, vh, set(vh))[1] == 0                                  # the block with the API is already used: left alone
assert SW.realign_api_subheadings(d, None, set())[1] == 0
print("3 API subheading realigned to its block ok")
# 4) a block printed in an earlier section is not printed again (while the section keeps 3 subtopics) -------------------------
def sec(sid, hashes):
    return SCH.Section(section_id=sid, heading=sid, intro="x" * 70, subtopics=[sub(h * 16, f"{sid} {h}") for h in hashes], citations=cites)
secs = [sec("s3", "abcd"), sec("s4", "0123"), sec("s5", "4567")]
secs[1].subtopics[0] = secs[1].subtopics[0].model_copy(update={"code_ref_hash": "a" * 16})       # s4 repeats a block of s3
secs[2].subtopics[3] = secs[2].subtopics[3].model_copy(update={"code_ref_hash": "b" * 16})       # s5 repeats another
out, n = SW.drop_repeated_code_blocks(secs)
assert n == 2 and [len(x.subtopics) for x in out] == [4, 3, 3], (n, [len(x.subtopics) for x in out])
assert all(st.code_ref_hash != "a" * 16 for st in out[1].subtopics) and all(st.code_ref_hash != "b" * 16 for st in out[2].subtopics)
tight = [sec("s1", "abc"), sec("s2", "789")]
tight[1].subtopics[0] = tight[1].subtopics[0].model_copy(update={"code_ref_hash": "a" * 16})
out2, n2 = SW.drop_repeated_code_blocks(tight)
assert n2 == 1 and len(out2[1].subtopics) == 3 and out2[1].subtopics[0].code_ref_hash == ""   # would leave < 3: becomes a prose subtopic
print("4 repeated code blocks dropped ok")
# 5) a subtopic whose prose barely mentions its code block gets the block it describes (asyncio ch-03) ----------------------
evt = "```python\nasync def waiter(event):\n    await event.wait()\n\nasync def main():\n    event = asyncio.Event()\n    waiter_task = asyncio.create_task(waiter(event))\n    event.set()\n```"
bar = "```python\nb = asyncio.Barrier(3)\nasyncio.create_task(b.wait())\nawait b.wait()\n```"
qbd = "```python\nqueue = asyncio.Queue(maxsize=2)\ntry:\n    queue.put_nowait(item)\nexcept asyncio.QueueFull:\n    await queue.put(item)\n```"
qsd = "```python\nqueue = asyncio.Queue()\nqueue.shutdown()\ntry:\n    await queue.get()\nexcept asyncio.QueueShutDown:\n    pass\n```"
wrk = "```python\nqueue = asyncio.Queue()\nawait queue.get()\nqueue.task_done()\nawait queue.join()\n```"
fill = {c * 16: {"fence_text": f"```python\nasync def {nm}():\n    await asyncio.sleep(0)\n```"} for c, nm in zip("6789abc", ["alpha", "beta", "gamma", "delta", "omega", "sigma", "theta"])}
H = {c * 16: {"fence_text": t} for c, t in {"1": evt, "2": bar, "3": qbd, "4": qsd, "5": wrk}.items()} | fill   # 12 blocks: `asyncio` / `await` are generic
expl = {
    "1": "The waiter coroutine blocks at await event.wait() until main calls event.set().",
    "2": "asyncio.Barrier(3) needs three wait() calls before it releases the tasks.",
    "3": "Setting maxsize on asyncio.Queue creates a bounded queue; put_nowait() raises QueueFull while await queue.put() applies back-pressure.",
    "4": "After queue.shutdown(), a get() on the asyncio Queue raises QueueShutDown so workers can stop.",
}
def sub2(h, head, e): return SCH.Subtopic(code_ref_hash=h * 16, subheading=head, explanation=e)
d5 = SCH.LLMSectionDraft(heading="Queues", intro="x" * 70, subtopics=[
    sub2("5", "Workload Distribution with asyncio.Queue", "Workers call await queue.get(), queue.task_done() and the main coroutine awaits queue.join()."),
    sub2("1", "Bounded Queues for Back-Pressure", expl["3"]),
    sub2("2", "Graceful Shutdown with QueueShutDown", expl["4"]),
], citations=cites)
fx, n5 = SW.realign_code_by_overlap(d5, H, set(H))
assert SW.realign_code_by_overlap(d5, {k: H[k] for k in list(H)[:5]}, set(list(H)[:5]))[1] == 0      # bank under REALIGN_MIN_BANK: never swaps
assert n5 == 2 and [st.code_ref_hash[0] for st in fx.subtopics] == ["5", "3", "4"], (n5, [st.code_ref_hash[0] for st in fx.subtopics])
ok5 = SCH.LLMSectionDraft(heading="Queues", intro="x" * 70, subtopics=[
    sub2("1", "Event wait and set", expl["1"]), sub2("2", "Barrier with three parties", expl["2"]), sub2("3", "Bounded queues", expl["3"])], citations=cites)
assert SW.realign_code_by_overlap(ok5, H, set(H))[1] == 0                      # well-aligned prose: untouched
print("5 code realigned by prose overlap ok")

# 6) the copy whose prose DESCRIBES the block keeps it, wherever it is (asyncio ch-03) ----------------------------------------
evt6 = "```python\nasync def waiter(event):\n    await event.wait()\n    event.set()\n```"
vq = {"a" * 16: {"fence_text": evt6}}
early = SCH.Subtopic(code_ref_hash="a" * 16, subheading="Event wait and set", explanation="The waiter awaits event.wait() until event.set() is called.")
late = SCH.Subtopic(code_ref_hash="a" * 16, subheading="Bounded queues", explanation="Setting maxsize on a Queue blocks put() when it is full.")
def mk(sid, first, rest): return SCH.Section(section_id=sid, heading=sid, intro="x" * 70, subtopics=[first] + [sub(h * 16, f"{sid} {h}") for h in rest], citations=cites)
secs6 = [mk("s1", early, "bcd"), mk("s2", late, "ef")]
out6, n6 = SW.drop_repeated_code_blocks(secs6, vq)
assert n6 == 1 and out6[0].subtopics[0].code_ref_hash == "a" * 16 and out6[1].subtopics[0].code_ref_hash == "" and len(out6[1].subtopics) == 3
secs6b = [mk("s1", late, "bcd"), mk("s2", early, "ef")]                                           # the describing copy is the LATER one
out6b, n6b = SW.drop_repeated_code_blocks(secs6b, vq)
assert n6b == 1 and len(out6b[0].subtopics) == 3 and all(st.code_ref_hash != "a" * 16 for st in out6b[0].subtopics) and out6b[1].subtopics[0].code_ref_hash == "a" * 16
print("6 the copy that describes the block keeps it ok")
# 7) writer-authored derived code that does not parse is not shipped (asyncio ch-03 RETHINK iteration) ----------------------
good_code = "import asyncio\n\nasync def main():\n    q = asyncio.Queue(maxsize=2)\n    await q.put(1)\n\nasyncio.run(main())"
flat_code = "import asyncio async def main(): q = asyncio.Queue(maxsize=2) await q.put(1) asyncio.run(main())"
def dsub(head, code): return SCH.Subtopic(code_ref_hash="", subheading=head, explanation="A bounded asyncio.Queue blocks put() when it is full until a consumer calls get().", code_source="derived", derived_code=code)
d7 = SCH.LLMSectionDraft(heading="Queues", intro="x" * 70, subtopics=[dsub("Valid derived example", good_code), dsub("Collapsed derived example", flat_code), sub("5" * 16, "Verbatim block")], citations=cites)
fx7, n7 = SW.strip_invalid_derived(d7)
assert n7 == 1 and fx7.subtopics[0].code_source == "derived" and fx7.subtopics[1].code_source == "verbatim" and fx7.subtopics[1].derived_code is None and fx7.subtopics[1].code_ref_hash == ""
assert fx7.subtopics[1].explanation == d7.subtopics[1].explanation and fx7.subtopics[2].code_ref_hash == "5" * 16
assert SW.strip_invalid_derived(SCH.LLMSectionDraft(heading="Q", intro="x" * 70, subtopics=[sub("1" * 16, "a"), sub("2" * 16, "b"), sub("3" * 16, "c")], citations=cites))[1] == 0
print("7 invalid writer-derived code stripped ok")
# 8) a misrouted block is omitted as an invisible HTML comment, not a visible note -------------------------------------------
ctx8 = [{"subtopics": [{"subheading": "Bounded queues", "explanation": "put() blocks when the queue is full.", "code_block": "```python\nb = asyncio.Barrier(3)\nawait b.wait()\nprint(b)\nbarrier_done = True\n```"}]}]
st8 = RD.dedupe_and_align_sections(ctx8)
assert st8["n_mismatch"] == 1 and ctx8[0]["subtopics"][0]["code_block"].startswith("<!--") and "Code example omitted" not in ctx8[0]["subtopics"][0]["code_block"]
print("8 misrouted block hidden in an HTML comment ok")
# 9) coverage top-up: unused routed blocks get subtopics; invalid answers are filtered --------------------------------------
import asyncio, json as _json
SV = domains.dd.synth.nodes.sawc.service
blk = lambda body: {"fence_text": "```python\n" + body + "\n```"}
vlt = {
    "a" * 16: blk("lock = asyncio.Lock()\nasync with lock:\n    shared += 1\n    asyncio.sleep(0)"),
    "b" * 16: blk("sem = asyncio.Semaphore(2)\nasync with sem:\n    await work()\n    sem.release()"),
    "c" * 16: blk("event = asyncio.Event()\nawait event.wait()\nevent.set()\nevent.clear()"),
    "d" * 16: blk("queue = asyncio.Queue(maxsize=2)\nawait queue.put(1)\nitem = await queue.get()\nqueue.task_done()"),
}
idx = {"s1": [{"source_key": "k1", "summary": "sync primitives", "key_facts": ["Lock is a mutex"], "code_refs": ["a" * 16, "b" * 16, "c" * 16, "d" * 16]}]}
sec1 = SCH.Section(section_id="s1", heading="Primitives", intro="x" * 70, subtopics=[sub("a" * 16, "Lock usage")] + [SCH.Subtopic(code_ref_hash="", subheading=f"Prose {i}", explanation="The Lock class guards a shared resource so only one task runs the block at a time.") for i in range(2)],
                   citations=[SCH.Citation(source_key="k1", claim="Lock is a mutex for tasks")])
assert SW.unused_routed_hashes(idx, [sec1]) == {"s1": ["b" * 16, "c" * 16, "d" * 16]}
good = {"subtopics": [
    {"code_ref_hash": "b" * 16, "subheading": "Semaphore limiting", "explanation": "asyncio.Semaphore(2) lets two tasks into the async with sem block; release() frees a slot."},
    {"code_ref_hash": "c" * 16, "subheading": "Event signalling", "explanation": "event.wait() blocks until event.set() is called; event.clear() resets the flag."},
    {"code_ref_hash": "d" * 16, "subheading": "Wrong prose", "explanation": "This paragraph talks about thread pools and process executors only, nothing else at all."},
    {"code_ref_hash": "z" * 16, "subheading": "Unknown hash", "explanation": "event.wait() is used here for something that is not in the routed list at all."},
    {"code_ref_hash": "a" * 16, "subheading": "Already used", "explanation": "asyncio.Lock guards the shared counter inside the async with lock block that is printed already."},
], "citations": [{"source_key": "k1", "claim": "Semaphore limits concurrency to n tasks"}, {"source_key": "nope", "claim": "not a routed source at all"}]}
subs9, cites9 = SW.parse_topup(good, unused={"b" * 16, "c" * 16, "d" * 16}, vault_rich=vlt, valid_source_keys={"k1"}, existing_subheads={"Lock usage"}, max_new=6)
assert [x.code_ref_hash[0] for x in subs9] == ["b", "c"] and [c.source_key for c in cites9] == ["k1"], ([x.code_ref_hash[0] for x in subs9], cites9)
calls9 = []
async def _chat9(prompt, **kw):
    calls9.append(prompt)
    return _json.dumps(good), {}
domains.settings.chat.service.chat_text_async = _chat9
out9, n9, k9 = asyncio.run(SV._topup_coverage(final_sections=[sec1], per_section_index=idx, vault_rich=vlt, sections_by_id={"s1": {"description": "locks and friends"}}, framework="asyncio", chapter_id="ch", chapter_title="Sync"))
assert (n9, k9) == (2, 1) and len(out9[0].subtopics) == 5 and len(out9[0].citations) == 2 and len(calls9) == 1
assert "[b" + "b" * 15 + "]" in calls9[0] and "Lock usage" in calls9[0] and "lock = asyncio.Lock()" not in calls9[0]   # only unused blocks are shown
SV.params.TOPUP_MIN_UNUSED = 9
assert asyncio.run(SV._topup_coverage(final_sections=[sec1], per_section_index=idx, vault_rich=vlt, sections_by_id={}, framework="a", chapter_id="c", chapter_title="t"))[1] == 0 and len(calls9) == 1
SV.params.TOPUP_MIN_UNUSED = 2
print("9 coverage top-up ok")

# 10) misaligned code -> prose; prose-only sections capped by key facts ------------------------------------------------------
bad_d = SCH.LLMSectionDraft(heading="Queues", intro="x" * 70, subtopics=[
    SCH.Subtopic(code_ref_hash="d" * 16, subheading="Bounded queues", explanation="Setting maxsize on asyncio.Queue creates a bounded queue; put() blocks while it is full."),
    SCH.Subtopic(code_ref_hash="c" * 16, subheading="Graceful shutdown", explanation="Calling shutdown on the queue makes later put calls raise QueueShutDown for the workers."),
    SCH.Subtopic(code_ref_hash="a" * 16, subheading="Lock usage", explanation="The async with lock block guards the shared counter."),
], citations=cites)
fx10, n10 = SW.strip_misaligned_code(bad_d, vlt)
assert n10 == 1 and fx10.subtopics[0].code_ref_hash == "d" * 16 and fx10.subtopics[1].code_ref_hash == "" and fx10.subtopics[2].code_ref_hash == "a" * 16
assert SW.strip_misaligned_code(bad_d, None)[1] == 0
prose_d = SCH.LLMSectionDraft(heading="Exc", intro="x" * 70, subtopics=[SCH.Subtopic(code_ref_hash="", subheading=f"Fact {i}", explanation="The IncompleteReadError exposes expected and partial attributes after a short read.") for i in range(5)], citations=cites)
capped, nc = SW.cap_prose_subtopics(prose_d, 4)
assert nc == 2 and len(capped.subtopics) == 3                                       # ceil(4 / 1.5) = 3
assert SW.cap_prose_subtopics(prose_d, 21)[1] == 0 and SW.cap_prose_subtopics(fx10, 2)[1] == 0     # enough facts / has code: untouched
print("10 misaligned code stripped, prose-only sections capped ok")
# 11) checklist: coverage + alignment gates, best-seen prefers a clean iteration -----------------------------------------------
CK = domains.dd.synth.nodes.checklist.domain
def csec(hs, cites):
    return {"section_id": "s1", "heading": "H", "subtopics": [{"code_ref_hash": h, "subheading": f"Sub {i}", "explanation": "x"} for i, h in enumerate(hs)],
            "citations": [{"source_key": k, "claim": "c"} for k in cites]}
routed = [chr(97 + i) * 16 for i in range(10)]
srcs = [f"ingestion/fw/pages/{i:04d}-page-{i}.md" for i in range(8)]
low = {"sections": [csec(routed[:3], srcs[:4])], "_routed_hashes": routed, "_digest_sources": srcs}
r_low = CK.check_code_coverage(low)
assert not r_low.passed and "3 of 10" in r_low.feedback and "4 of 8" in r_low.feedback and "page-4" in r_low.feedback, r_low.feedback
ok = {"sections": [csec(routed[:8], srcs[:7])], "_routed_hashes": routed, "_digest_sources": srcs}
assert CK.check_code_coverage(ok).passed
assert CK.check_code_coverage({"sections": [csec(routed[:1], srcs[:1])], "_routed_hashes": routed[:3], "_digest_sources": srcs[:3]}).passed   # small chapter: vacuous
bodies = {"a" * 16: "```python\nlock = asyncio.Lock()\nasync with lock:\n    shared += 1\n```", "b" * 16: "```python\nb = asyncio.Barrier(3)\nawait b.wait()\nprint(b)\n```"}
al = {"sections": [{"section_id": "s2", "subtopics": [
    {"code_ref_hash": "a" * 16, "subheading": "Lock usage", "explanation": "async with lock guards shared"},
    {"code_ref_hash": "b" * 16, "subheading": "Bounded queues", "explanation": "maxsize makes put block when the queue is full"}]}], "_vault_bodies": bodies}
r_al = CK.check_code_prose_alignment(al)
assert not r_al.passed and "s2/Bounded queues" in r_al.feedback and "Lock usage" not in r_al.feedback
assert CK.check_code_prose_alignment({"sections": al["sections"]}).passed                     # no bodies: vacuous
n_chk = len(CK.DETERMINISTIC_CHECKS)
assert CK.best_seen_key(0.73, n_chk) > CK.best_seen_key(0.87, n_chk - 1) and CK.best_seen_key(0.9, n_chk) > CK.best_seen_key(0.8, n_chk)
print("11 coverage / alignment gates + clean-first best-seen ok")
# 12) harmonize: a call cut at its token budget is retried with double the budget -------------------------------------------
BH = domains.dd.synth.nodes.book_harmonize
seen12 = []
async def _chat12(prompt, **kw):
    seen12.append(kw.get("max_tokens"))
    if len(seen12) < 3:
        raise RuntimeError("LengthFinishReasonError: Could not parse response content as the length limit was reached")
    return "{}", {}
domains.settings.chat.service.chat_text_async = _chat12
raw12, err12 = asyncio.run(BH.service._call_with_retry("p", max_tokens=1500))
assert raw12 == "{}" and err12 is None and seen12 == [1500, 3000, 6000], seen12
seen12.clear()
async def _chat12b(prompt, **kw):
    seen12.append(kw.get("max_tokens")); raise RuntimeError("LengthFinishReasonError: length limit was reached")
domains.settings.chat.service.chat_text_async = _chat12b
raw12b, err12b = asyncio.run(BH.service._call_with_retry("p", max_tokens=5000))
assert raw12b is None and err12b is not None and seen12 == [5000, 8000, 8000], seen12     # capped at LENGTH_RETRY_CAP
print("12 harmonize length retry ok")
# 13) quality feedback to the Rotator: id read from the response headers, empty assign vectors / rescues reported ---------------
CS = domains.settings.chat.service
posted = []
async def _fake_post(rid, quality):
    posted.append((rid, quality)); return True
CS._post_quality_feedback = _fake_post
async def _go13():
    ok = CS.report_quality({"rotator_request_id": "req-1"}, 0.1)
    no = CS.report_quality({}, 0.1)
    await asyncio.sleep(0.05)
    return ok, no
assert asyncio.run(_go13()) == (True, False) and posted == [("req-1", 0.1)]
assert CS.report_quality({"rotator_request_id": "x"}, 0.5) is False                          # no running loop: nothing to report on
CA = domains.dd.planner.nodes.chapter_assign
reported = []
CS.report_quality = lambda meta, q: reported.append((meta.get("rotator_request_id"), q)) or True
zero = CA.schemas.DocAssignment(scores=[CA.schemas.ChapterScore(chapter_idx=i, confidence=0.0) for i in range(3)])
good = CA.schemas.DocAssignment(scores=[CA.schemas.ChapterScore(chapter_idx=0, confidence=0.9)])
CA.service._report_vector_quality({"rotator_request_id": "r0"}, zero, False)
CA.service._report_vector_quality({"rotator_request_id": "r1"}, good, False)                 # a normal first answer: nothing to say
CA.service._report_vector_quality({"rotator_request_id": "r2"}, good, True)                  # the re-ask rescued it
assert reported == [("r0", CA.params.FEEDBACK_EMPTY_QUALITY), ("r2", CA.params.FEEDBACK_RESCUE_QUALITY)], reported
print("13 Rotator quality feedback ok")
# 14) the named Rotator task reaches the request body (Rotator endpoints only) -------------------------------------------------
import dataclasses
orig_ep = CS.ENDPOINT
try:
    CS.ENDPOINT = dataclasses.replace(orig_ep, base_url="https://coelho-llm-rotator.example.ts.net/api/v1/llm/openai/v1")
    m_named = CS.build_chat_model(timeout_s=60, rotator_task="dd.assign")
    assert (m_named.extra_body or {}).get("rotator_task") == "dd.assign", m_named.extra_body
    assert "rotator_task" not in (CS.build_chat_model(timeout_s=60).extra_body or {})
    CS.ENDPOINT = dataclasses.replace(orig_ep, base_url="https://api.example.com/v1")
    assert "rotator_task" not in (CS.build_chat_model(timeout_s=60, rotator_task="dd.assign").extra_body or {})
finally:
    CS.ENDPOINT = orig_ep
print("14 rotator_task reaches the request body ok")
print("ALL OK")
