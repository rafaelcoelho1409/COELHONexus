"""sawc — bump when prompt or schema changes."""
from __future__ import annotations


SAWC_SCHEMA_VERSION = "2.0-cookbook"
# v13 (2026-10-15): coverage top-up (extra subtopics for routed-but-unused code blocks), misaligned code -> prose, prose-only
# section capped by key facts, repeated blocks printed once (asyncio full study: 77 of 118 blocks used, ch-01 14 of 35).
# v14 (2026-10-15): the source top-up writes ONE connected subtopic per page (>= 18 words); fragments of one page are folded together.
SAWC_PROMPT_VERSION = "v14-source-topup-merged-2026-10-15"
