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
print("ALL OK")
