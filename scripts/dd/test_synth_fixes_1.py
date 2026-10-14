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
print("ALL OK")
