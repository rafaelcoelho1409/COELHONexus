"""book_harmonize tunables — prose budgets and concurrency."""
from __future__ import annotations


# Rendered chapters a study needs for the pass to mean anything: one chapter has nothing to agree with.
MIN_CHAPTERS = 2
MAX_CLAIMS_PER_CHAPTER = 20
PROSE_CHARS_FOR_CLAIMS = 10000
# Chapters worked on at once. 4 -> 2 (2026-10-15): detect calls timed out / 504'd under load on a slow Rotator.
PER_CHAPTER_CONCURRENCY = 2
# A model call that fails for a transient provider reason is asked again this many times in all, waiting `RETRY_WAIT_S` x attempt between.
ASK_ATTEMPTS = 3
RETRY_WAIT_S = 4.0

# A violation is patched only when it quotes this chapter verbatim (at least MIN_QUOTE_WORDS words) and points at a sibling claim or
# a canonical term that exists (asyncio study: both "corrections" came from the model's own knowledge — a documented
# "subclass of AbstractEventLoop" rewritten, a true InvalidStateError condition replaced). A sibling claim must share
# CLAIM_MATCH_SHARE of its words with that sibling chapter's extracted claims.
MIN_QUOTE_WORDS = 3
CLAIM_MATCH_SHARE = 0.6
# The edit is made on the paragraph that holds the quote, never on the whole chapter (the whole-chapter rewrite changed
# unrelated words and added an ungrounded sentence); the edited paragraph must stay within this length ratio.
PATCH_PARAGRAPH_MIN_RATIO = 0.6
PATCH_PARAGRAPH_MAX_RATIO = 1.6
# Violations patched per chapter, and the share of its length a patched chapter must keep.
MAX_VIOLATIONS_PER_CHAPTER = 12
PATCH_MIN_KEPT_SHARE = 0.9
# Characters of a chapter the detector is shown, and of the terms block the canonicalizer is shown.
PROSE_CHARS_FOR_DETECT = 10000
TERMS_BLOCK_CHARS = 8000
TERMS_PER_CHAPTER = 12
