"""book_harmonize tunables — prose budgets + LLM caps + concurrency."""
from __future__ import annotations


MAX_CLAIMS_PER_CHAPTER = 20
PROSE_CHARS_FOR_CLAIMS = 10000
PROSE_CHARS_FOR_PATCH = 16000
DETECT_MAX_TOKENS = 800
PATCH_MAX_TOKENS = 14000
# was 1000 (issue #16, 2026-09-06/07): confirmed live that some Rotator-
# pool models spend their whole budget restating/reasoning through the
# task instructions before ever emitting JSON — a 3,659-char response
# (~the old 1000-token ceiling) was 100% preamble, cut off mid-word,
# containing no '{' at all. Raised well past any observed preamble +
# full 20-claim payload, paired with an explicit no-preamble instruction
# in EXTRACT_CLAIMS_PROMPT (prompts.py).
EXTRACT_MAX_TOKENS = 3000   # 2000 -> 3000 (2026-10-15: ch-03 / ch-05 of the asyncio study hit 2000)
CANONICALIZE_MAX_TOKENS = 3000   # 1500 -> 3000 (2026-10-15: hit 1500 on the asyncio study)
PER_CHAPTER_CONCURRENCY = 2   # 4 -> 2 (2026-10-15: detect calls timed out / 504'd under load on a slow Rotator)

# chat_text_async's own default (30s) was undersized — same fix
# applied across the rest of Synth (2026-09-06/07). book_harmonize's
# _call_with_retry already retries twice against a transient timeout,
# but that only helps if 30s wasn't just structurally too short for the
# call in the first place. Scaled to each call's max_tokens.
DETECT_TIMEOUT_S = 100.0
PATCH_TIMEOUT_S = 150.0
EXTRACT_TIMEOUT_S = 90.0
CANONICALIZE_TIMEOUT_S = 60.0

# A call cut at its token budget is retried once with double the budget, up to this cap.
LENGTH_RETRY_CAP = 8000
