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
OUTLINE_PROMPT_VERSION = "v10-flatten-chain-2026-10-17"
