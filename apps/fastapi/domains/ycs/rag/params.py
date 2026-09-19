"""ycs/rag — loose tunables shared by both the standard and adaptive
graphs (message-history cap, resilient-call retry classification, and
the Parallel web-search fallback's timeouts/limits)."""
from __future__ import annotations


# Cap on the prior turns we materialize into the prompt. Each turn = 2
# messages (Human + AI), so 8 turns = 16 messages. Big enough to keep
# multi-turn coherence; small enough that a 5-turn back-and-forth
# doesn't eat the LLM context budget.
HISTORY_MESSAGES_CAP = 8


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
