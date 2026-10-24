"""ycs/rag — loose tunables shared by both the standard and adaptive
graphs (message-history cap, resilient-call retry classification, and
the Parallel web-search fallback's timeouts/limits)."""
from __future__ import annotations


# Cap on the prior turns we materialize into the prompt. Each turn = 2
# messages (Human + AI), so 8 turns = 16 messages. Big enough to keep
# multi-turn coherence; small enough that a 5-turn back-and-forth
# doesn't eat the LLM context budget IN THE COMMON CASE — this is a row
# count, not a size bound, so it doesn't actually guarantee the budget
# claim if those 8 rows happen to carry unusually long answers (e.g.
# ones quoting long transcript excerpts). HISTORY_MAX_TOKENS below is
# the real enforcement.
HISTORY_MESSAGES_CAP = 8

# Token ceiling for history actually enforced via `trim_messages()` in
# `domain.history_to_messages()` — a real safety net on top of
# HISTORY_MESSAGES_CAP, not a replacement for it (row cap still applies
# first, for the normal case). 800 ≈ 1/4 of GENERATE_TOTAL_CHARS's
# ~3000-token document budget (`ycs/rag/standard/params.py`) — history
# is secondary context, not the primary grounding material, so it gets
# a meaningfully smaller share. Confirmed 2026-09-26: unlike
# `contextualize`'s flattened-text history (capped per-answer via
# MAX_HISTORY_ANSWER_CHARS), this path had no length bound at all
# before — 8 long answers could genuinely crowd out the current
# question's retrieved documents.
HISTORY_MAX_TOKENS = 800

# Per-answer char cap applied BEFORE trim_messages(), so a single
# oversized answer can't exceed HISTORY_MAX_TOKENS on its own and
# trigger trim_messages()'s all-or-nothing `allow_partial=False`
# behavior (verified live 2026-09-26: an ~850-token single answer made
# trim_messages() return an EMPTY list rather than a truncated one —
# losing the whole history instead of degrading gracefully). Matches
# the same defensive idea as adaptive/params.py's
# MAX_HISTORY_ANSWER_CHARS (300) for the flattened-text contextualize
# path, sized larger here since this path's aggregate budget is bigger.
HISTORY_ANSWER_CHARS_CAP = 800


# DD parity: these mean "try again" — same rationale as
# `doc_distill._TRANSIENT_REASONS` (timeout/connection only).
TRANSIENT_SUBSTRINGS: tuple[str, ...] = (
    "timeout",
    "timed out",
    "connection",
    "connect",
    "reset by peer",
    "connection closed",
    "connection refused",
    "unreachable",
    "temporarily unavailable",
    "service unavailable",
    "internal error",
    "overloaded",
)

# Checked FIRST — these must never be retried: the provider side already
# exhausted its own cascade (retrying burns seconds for an identical
# outcome — see doc_distill's removal of rate_limit from retryables).
NON_TRANSIENT_SUBSTRINGS: tuple[str, ...] = (
    "429",
    "rate limit",
    "ratelimit",
    "quota",
    "throttle",
    "resource exhausted",
    "permission",
    "unauthorized",
    "forbidden",
    "invalid api key",
    "not found",
    "context length",
    "maximum context",
    "content filter",
    "moderation",
)


# Parallel MCP web-search fallback (`service.py::search_web`).
PARALLEL_MCP_URL = "https://search.parallel.ai/mcp"

# Short and separate from `fallback_answer`'s own 60s generation
# budget — this call happens BEFORE that one, on the pipeline's
# already-slowest, worst-UX path. A failed/slow search must not turn
# a 60s rescue into a 90s+ one; see that node's docstring for why its
# own timeout was tightened 90 -> 60s for the same reason.
SEARCH_TIMEOUT_S = 15.0
MAX_RESULTS = 5
EXCERPT_CHAR_CAP = 400
