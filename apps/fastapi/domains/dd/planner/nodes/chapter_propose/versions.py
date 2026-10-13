"""chapter_propose — bump when proposal logic changes so re-plans don't cache-hit the old set."""
from __future__ import annotations


# v4 (2026-09-08): heading seeds now strip the trailing permalink markdown
# link (`[¶](#slug "Permanent link")`) every static-site generator appends
# to headings. Harmless while headings were only ever LLM prompt context —
# became a directly user-visible bug once _build_fallback_proposals()
# (2026-09-04) started using them verbatim as final chapter titles.
# Confirmed live on the fastapi corpus: every fallback-path chapter title
# carried its heading's raw permalink markup.
# v5 (2026-09-08): heading extraction now strips fenced code blocks before
# scanning for `#`/`##` lines — a Python comment like
# "# Code below omitted 👇" inside a ```python fence was being misread as
# a real heading. Confirmed live on the fastapi corpus: this exact string
# became a fallback-generated chapter title.
# v6 (2026-09-08): draft/repair timeout raised 90s -> 120s using this
# node's own 14-day Langfuse percentiles (p99=86.8s, max=87.9s) — the last
# node in this cluster still below the evidence-based floor, and the only
# node that failed (fallback_used=True) on every single run this whole
# investigation.
# v7 (2026-09-09): added SETTLE_DELAY_S=130s — the last node in this
# cluster still missing a settle window against the Rotator's own
# cooldown_time=120. Confirmed live: chapter_propose failed all 3 samples
# on every numpy run, even after doc_distill/chapter_assign/order_chapters
# got their own settle-delay fix.
# v8 (2026-09-09): heading seeds now also strip a LEADING empty-text
# anchor link (`[](#slug)`) — a different static-site generator's
# permalink convention than v4's trailing `[¶](#slug "Permanent link")`.
# Confirmed live on the langchain-langgraph-deepagents corpus: every
# fallback-path title carried a leading `[](#anchor)` (e.g.
# `[](#static-runtime-context) Static runtime context`).
# v9 (2026-09-09): _build_fallback_proposals()'s title dedup is now
# case-insensitive and runs on the FINAL (post word-count-normalized)
# title instead of the raw pre-mutation heading/namespace string.
# Previously two headings differing only in case (or colliding only after
# the 2-8-word truncation/suffix step) both passed the old case-sensitive
# pre-mutation check, then crashed ChapterProposalList's case-insensitive
# uniqueness validator with no further fallback — killing the whole run.
# Confirmed live on the fastmcp corpus: duplicate 'How It Works' after all
# 3 LLM samples had already failed.
# v11 (2026-10-13): SOURCE STRUCTURE signal (the docs' own parent pages in
# navigation order) + family-cohesion and no-overview-chapter rules; the
# "split a chapter" rule tightened >40% → >30% to match chapter_select's cap.
# v12 (2026-10-13): a draft cut at max_tokens (LengthFinishReasonError) is re-asked with
# a "be concise" hint instead of being lost; Nexus now sends the Rotator its own
# timeout as the cascade budget (max_wall_s), so 504s at the Rotator's 180s default stop.
# v13 (2026-10-13): SOURCE STRUCTURE also lists URL-directory "site sections" (≥6 direct
# pages, not the site trunk) + a rule giving a section of like catalog pages its own
# chapter — deepagents' 12 provider pages were scattered over 4 topical chapters.
# v14 (2026-10-13): one-word chapter titles are valid (the schema silently dropped Streams/
# Queues/Subprocesses/Futures … — asyncio samples shrank 9-13 -> 4-5 chapters); the sample is
# picked by an evidence score (docs matching no chapter, largest share, distance from target),
# the LLM vote only breaks near-ties; first-draft token budget scales with the target and a
# length-truncated draft is re-asked with a bigger budget.
# v15 (2026-10-13): each sample after the first gets its own organizing angle (tasks / components /
# learning path / doc sections) — the 5 prompts were byte-identical and deepagents' 4 codestral
# samples were one and the same 7-chapter set.
# v16 (2026-10-13): SOURCE STRUCTURE is built from each page's `source_path` over the RELEVANT pages
# only — a single-URL dump (llms-full.txt) gave the proposer NO structure at all and counted pages
# the off-topic filter had dropped (fastmcp: a 112-page changelog family); targets >= 16 chapters
# ask for compact entries; fewer than 4 valid samples get one top-up round.
PROMPT_VERSION = "v16-source-path-structure-2026-10-13"
