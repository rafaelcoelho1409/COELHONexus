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
PROMPT_VERSION = "v9-fallback-title-dedup-2026-09-09"
