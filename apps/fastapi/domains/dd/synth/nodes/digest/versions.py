"""digest_construct — bump when prompt or schema changes."""
from __future__ import annotations


DIGEST_SCHEMA_VERSION = "1.1"
# v7 (2026-10-15): the prompt says never to return an empty contributes_to, and a source still routed to no section is placed by
# title/summary overlap with all of its code blocks (asyncio ch-05: 3 sources and 22 code blocks lost to a weak digest model).
# v8 (2026-10-15): sections are not merged when the result would hold more than MERGE_MAX_BLOCKS routed code blocks.
# v9 (2026-10-16): a contribution with more than 5 key facts (or one over/under the length bounds) is trimmed, not rejected (fastmcp ch-01: a source lost to "got 6").
# v10 (2026-10-16): a source whose digest failed every attempt is kept as a stub (title + opening text) and routed with all its code blocks; a 45 s pause before
# the second wave.
DIGEST_PROMPT_VERSION = "v10-failed-source-stubs-2026-10-16"
