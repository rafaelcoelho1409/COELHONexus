"""doc_distill tunables — page-size caps, the settle window, attempts and the fallback's stop words."""
from __future__ import annotations
from ..... import agents


# The name this stage reports its progress events, cached items and scratch data under.
STEP = "doc_distill"


# The summary and term limits are part of the distiller's contract (`agents.pipeline.planner.params`); the node reads the same numbers.
SUMMARY_WORDS_MIN = agents.pipeline.planner.params.SUMMARY_WORDS_MIN
SUMMARY_WORDS_MAX = agents.pipeline.planner.params.SUMMARY_WORDS_MAX
KEY_TERMS_MIN = agents.pipeline.planner.params.KEY_TERMS_MIN
KEY_TERMS_MAX = agents.pipeline.planner.params.KEY_TERMS_MAX
KEY_TERM_CHARS_MIN = agents.pipeline.planner.params.KEY_TERM_CHARS_MIN
KEY_TERM_CHARS_MAX = agents.pipeline.planner.params.KEY_TERM_CHARS_MAX

# Characters of a page the distiller reads. Summarization has no head+tail trick (that is a binary-classification optimization):
# content past the old 8000-char cap — a later API section, a code example — was invisible to the summary and key terms that
# chapter_propose builds on. 100K is the cap synth's digest node reads whole pages at. A retry reads half, so a prompt that
# timed out is smaller the second time.
BODY_CHARS_MAX = 100_000
BODY_CHARS_RETRY_DIVISOR = 2

# Distillers in flight at once. 24 saturated a free-tier pool (402s, timeouts, 75% fallbacks), 16 still burst; 10 keeps each
# per-minute provider quota window from being hit hard.
CONCURRENCY = 10

# Settle window before the node's first model call: the Rotator's pool needs time to recover from the previous node's burst.
# SETTLE_DELAY_S is the cap (the Router's own cooldown is 120s); the wait ends as soon as the pool has recovered.
SETTLE_DELAY_S = 130.0

# A page is tried ATTEMPTS times (the second with half the body); each try asks the agent up to ASK_ATTEMPTS times for a usable answer.
ATTEMPTS = 2
ASK_ATTEMPTS = 2
RETRY_WAIT_S = 10.0
# Progress events per run, at most.
PROGRESS_EVENTS = 10

# Stop-words for the fallback distillate identifier scan.
FB_STOP = frozenset({
    "the", "and", "for", "this", "with", "that", "from", "your", "into",
    "via", "are", "use", "how", "you", "can", "will", "not", "but", "its",
    "has", "see", "all", "one", "two", "any",
})
