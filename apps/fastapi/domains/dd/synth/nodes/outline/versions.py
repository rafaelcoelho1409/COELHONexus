"""outline_sdp — bump when prompt or schema changes."""
from __future__ import annotations


OUTLINE_SCHEMA_VERSION = "1.0"
# v7 (2026-10-16): the section cap also follows the chapter's code-block count (ceil(blocks / 12), at most 12), not only its source count (fastmcp ch-01:
# 135 blocks and 18 sources gave 4 sections that could print 29 blocks).
# v8 (2026-10-16): a code-heavy chapter needs at least ceil(blocks / 20) sections (validation issue -> repair loop), and the USC picker's failure
# fallback is the best-validated candidate, not always the first.
OUTLINE_PROMPT_VERSION = "v8-section-floor-2026-10-16"
