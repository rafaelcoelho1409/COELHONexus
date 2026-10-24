"""digest_construct — bump when prompt or schema changes."""
from __future__ import annotations


DIGEST_SCHEMA_VERSION = "1.1"
# v7 (2026-10-15): the prompt says never to return an empty contributes_to, and a source still routed to no section is placed by
# title/summary overlap with all of its code blocks (asyncio ch-05: 3 sources and 22 code blocks lost to a weak digest model).
# v8 (2026-10-15): sections are not merged when the result would hold more than MERGE_MAX_BLOCKS routed code blocks.
# v9 (2026-10-16): a contribution with more than 5 key facts (or one over/under the length bounds) is trimmed, not rejected (fastmcp ch-01: a source lost to "got 6").
# v10 (2026-10-16): a source whose digest failed every attempt is kept as a stub (title + opening text) and routed with all its code blocks; a 45 s pause before
# the second wave.
# v11 (2026-10-17): an outline section no source was routed to is folded away (the writer skips it) instead of being written from the whole chapter.
# v13 (2026-10-19): chunks of a dense page that fail are retried once. v12: a source with more than CHUNK_MAX_BLOCKS (30) code blocks is digested in chunks of whole blocks and merged (langgraph ch-05: 28 of 149 blocks printed).
DIGEST_PROMPT_VERSION = "v13-chunk-retry-2026-10-19"
