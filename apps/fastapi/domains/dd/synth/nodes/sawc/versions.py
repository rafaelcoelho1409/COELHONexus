"""sawc — bump when prompt or schema changes."""
from __future__ import annotations


SAWC_SCHEMA_VERSION = "2.0-cookbook"
# v13 (2026-10-15): coverage top-up (extra subtopics for routed-but-unused code blocks), misaligned code -> prose, prose-only
# section capped by key facts, repeated blocks printed once (asyncio full study: 77 of 118 blocks used, ch-01 14 of 35).
# v14 (2026-10-15): the source top-up writes ONE connected subtopic per page (>= 18 words); fragments of one page are folded together.
# v15 (2026-10-15): the coverage top-up grows a section to TOPUP_SECTION_MAX (not 12) with 8 new per call; a bank below the writer minimum borrows
# other sections' blocks only when it would hold < 3; an over-long intro is trimmed instead of rejecting the draft.
# v16 (2026-10-15): the intro field asks for substance, not "what this section covers" (asyncio study: 15 of 24 intros opened "This section
# covers/explores/explains...", failed prose_code_first_not_meta_framing on ch-03).
# v17 (2026-10-15): writer output is typography-normalized (U+2011 non-breaking hyphens in 8 of 28 ch-05 headings/explanations).
# v18 (2026-10-16): a draft over the citation / subtopic caps is trimmed, not rejected (fastmcp ch-01: 16-citation drafts failed the 12 cap, repairs timed out, placeholder).
# v19 (2026-10-16): a copy of a printed block that restates its winner is dropped (not kept as a prose echo) before the top-ups refill, a prose
# subtopic repeating another is dropped, and the source top-up also teaches the key facts a CITED source leaves unsaid (asyncio ch-04: 10 of 31 facts
# absent, 2 echoes), with a prompt that asks for the listed facts.
# v20 (2026-10-16): the top-ups ask again once when a call fails or holds nothing valid, a block that defines a helper a printed block calls is
# REQUIRED in the coverage top-up and printed before its callers (asyncio ch-04: gather(run(...)) shown without run()), and the source top-up sees the
# covered text, states only new facts and drops "the documentation provides ..." filler.
# v21 (2026-10-16): the coverage top-up runs up to 2 rounds (a section that gained subtopics and still has unused routed blocks and room is asked again).
SAWC_PROMPT_VERSION = "v21-top-up-rounds-2026-10-16"
