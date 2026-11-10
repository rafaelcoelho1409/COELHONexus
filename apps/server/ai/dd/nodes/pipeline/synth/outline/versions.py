"""outline_sdp — bump when prompt or schema changes."""
from __future__ import annotations


OUTLINE_SCHEMA_VERSION = "1.0"
# v7 (2026-10-16): the section cap also follows the chapter's code-block count (ceil(blocks / 12), at most 12), not only its source count (fastmcp ch-01:
# 135 blocks and 18 sources gave 4 sections that could print 29 blocks).
# v8 (2026-10-16): a code-heavy chapter needs at least ceil(blocks / 20) sections (validation issue -> repair loop), and the USC picker's failure
# fallback is the best-validated candidate, not always the first.
# v9 (2026-10-17): an unmet section floor gets up to 2 extra repair attempts, and its repair issue names the source titles to split along (fastmcp ch-01:
# two repairs left 4 sections against a floor of 7 and the outline shipped with the violation).
# v10 (2026-10-17): a prerequisite chain deeper than MAX_STAGE_DEPTH is flattened deterministically, before and after the repair loop (fastmcp ch-01: a 12-section
# chain, depth 11, made the writer 25 min).
# v11 (2026-10-17): the floor issue and its hint say a section must be backed by the sources (fastmcp ch-01: 12 sections for 18 client pages, 4 invented).
# v12 (2026-10-20): a repair that clears the section floor is kept even when it overshoots the cap (qdrant ch-10 shipped 5 sections for 160 blocks after four rejected/identical repairs).
# v13 (2026-11-06): the outline is drafted by a chapter_outliner agent that validates it with a tool and reads the sources on demand (was 3 samples + a vote + repair calls).
OUTLINE_PROMPT_VERSION = "v14-source-digests-learning-contract-2026-11-08"
OUTLINE_V2_SCHEMA_VERSION = "1.2"
OUTLINE_V2_PROMPT_VERSION = "v16-grouped-code-learning-contract-2026-11-10"


def schema_version(tutorial: bool = False) -> str:
    return OUTLINE_V2_SCHEMA_VERSION if tutorial else OUTLINE_SCHEMA_VERSION


def prompt_version(tutorial: bool = False) -> str:
    return OUTLINE_V2_PROMPT_VERSION if tutorial else OUTLINE_PROMPT_VERSION
