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
PROMPT_VERSION = "v6-timeout-120s-2026-09-08"
