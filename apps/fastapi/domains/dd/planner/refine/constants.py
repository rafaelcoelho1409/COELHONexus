from __future__ import annotations

import re


# Boundary threshold — slightly more generous than cluster's 0.5 floor.
# Tokens are free per project policy; refine more docs.
_BOUNDARY_FLOOR = 0.60
# Top-K candidate clusters offered per boundary doc. Research: >10 hurts
# accuracy due to position bias + prompt length; top-5 is the sweet spot.
_TOP_K = 5
# c-TF-IDF keyword count per cluster — research: 5-8.
_KEYWORDS_PER_CLUSTER = 7
# Representative-doc snippet length per cluster (~80 tokens).
_SNIPPET_CHARS = 320
# Body of the doc being judged (truncated to bound prompt size, ~600 tokens).
_DOC_BODY_CHARS = 2400
# Per-cluster doc-text cap when building c-TF-IDF corpus (keeps TF-IDF fast).
_CTFIDF_DOC_CHARS = 1200
# Concurrency — 8 in-flight. ParetoBandit + LiteLLM cooldowns handle
# rate-limit pressure within the dd-all rotator.
_REFINE_CONCURRENCY = 8
# Per-call LLM budget for the JSON output (chosen, confidence, rationale).
_REFINE_MAX_TOKENS = 200
# Cache version — bump on prompt redesign / hyperparam tweaks so old
# blobs invalidate cleanly.
_PROMPT_VERSION = "v1-2026-05-18"
_BLOB_PREFIX = "planner"

# Letter labels A-E for the top-5 candidates.
_LABELS = list("ABCDE")

_JSON_RE = re.compile(r"\{.*?\}", re.DOTALL)
