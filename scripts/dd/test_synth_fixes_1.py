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
print("ALL OK")
