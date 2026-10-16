"""outline_sdp — bump when prompt or schema changes."""
from __future__ import annotations


OUTLINE_SCHEMA_VERSION = "1.0"
# v7 (2026-10-16): the section cap also follows the chapter's code-block count (ceil(blocks / 12), at most 12), not only its source count (fastmcp ch-01:
# 135 blocks and 18 sources gave 4 sections that could print 29 blocks).
OUTLINE_PROMPT_VERSION = "v7-block-aware-cap-2026-10-16"
