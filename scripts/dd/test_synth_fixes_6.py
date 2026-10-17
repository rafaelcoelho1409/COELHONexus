"""Offline check for the outline block count (2026-10-16): the section floor and the block-aware cap read the chapter's code-block count, which was
always 0 because it looked for a sentinel tag the raw bodies do not carry yet.

    cd apps/fastapi && .venv/bin/python ../../scripts/dd/test_synth_fixes_6.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "fastapi"))
import domains  # noqa: E402

OL = domains.dd.synth.nodes.outline
count = OL.domain.count_code_blocks


def page(*blocks):
    return "# Page\n\nSome prose.\n\n" + "\n\n".join(f"```python\n{b}\n```" for b in blocks) + "\n"


# 1) raw bodies: distinct fenced blocks count once, however many pages repeat them -----------------------------------------------------------
a, b, c = "print('alpha')\nx = 1", "print('beta')\ny = 2", "print('gamma')\nz = 3"
assert count([page(a, b), page(b, c)]) == 3
assert count([]) == 0 and count(["", "no code here"]) == 0

# 2) sentinelized bodies count their sentinels -----------------------------------------------------------------------------------------------
sent, _ = domains.dd.synth.nodes.vault.domain.sentinelize_doc(page(a, b))
assert count([sent]) == 2, count([sent])
assert count([sent, page(c)]) == 3

# 3) the count drives the floor and the cap: 135 blocks over 18 sources -> floor 7, cap 12 --------------------------------------------------
P = OL.params
cap = P.max_h2_for_n_sources(18, 135)
assert (cap, P.min_h2_for_n_blocks(135, cap)) == (12, 7), (cap, P.min_h2_for_n_blocks(135, cap))
assert P.min_h2_for_n_blocks(count([page(a)]), P.max_h2_for_n_sources(1, 1)) == 0        # a prose-light chapter has no floor
print("test_synth_fixes_6: 3/3 checks ok")

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
print("test_synth_fixes_6: 4/4 checks ok")
