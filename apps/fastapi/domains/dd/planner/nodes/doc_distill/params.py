"""doc_distill tunables — pass-through threshold + LLM caps + cache tag."""
from __future__ import annotations


PASS_THROUGH_THRESHOLD = 80

BODY_CHARS_MAX = 20_000

# 16-way concurrency produced 36% rate-limit failures on NIM+Mistral; 8 keeps burst inside sustained capacity
CONCURRENCY = 8

SUMMARY_WORDS_MIN = 8
SUMMARY_WORDS_MAX = 60
KEY_TERMS_MIN = 3
KEY_TERMS_MAX = 8
KEY_TERM_CHARS_MIN = 2
KEY_TERM_CHARS_MAX = 80

MAX_TOKENS = 300
TEMPERATURE = 0.2

MAX_REPAIR_ATTEMPTS = 1

MAX_TRANSIENT_RETRIES = 2
RETRY_BACKOFF_S = (2.0, 5.0)

BLOB_PREFIX = "planner"

# Stop-words for the fallback distillate identifier scan.
FB_STOP = frozenset({
    "the", "and", "for", "this", "with", "that", "from", "your", "into",
    "via", "are", "use", "how", "you", "can", "will", "not", "but", "its",
    "has", "see", "all", "one", "two", "any",
})
