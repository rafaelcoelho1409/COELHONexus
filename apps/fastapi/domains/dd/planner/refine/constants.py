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

# ─── Phase D (2026-05-23) — Soft-membership resolver fast-path ───────────────
# When KD_REFINE_USE_GMM=1, refine first runs a deterministic boundary
# resolver on the soft membership matrix (sharpened via temperature softmax)
# and only escalates to the bandit LLM-judge for the genuinely-uncertain tail
# (sharpened_max_posterior < _GMM_POSTERIOR_THRESHOLD).
#
# Research (Wiley 2025, Brenndoerfer 2026):
# - Deterministic boundary resolution lands at 92-94% accuracy vs LLM-judge's
#   ~97% on technical doc corpora — 3-5pp regression but ~85% LLM-cost cut.
# - Softmax sharpening on HDBSCAN's persistence-based soft membership reclassifies
#   40-60% of "boundary" docs as confident (HDBSCAN issue #246).
# - The user's free-tier rule weights compute cost; the bandit's per-call
#   reward already encodes quality — the 3-5pp loss is largely absorbed by
#   FGTS-VA picking the best LLM-judge model for the residual tail.
#
# Configured to gate on env so we can A/B against pure-LLM-judge in shadow runs.
_GMM_POSTERIOR_THRESHOLD = 0.60     # sharpened argmax confidence to take det path
_GMM_SOFTMAX_TEMPERATURE = 0.30     # T < 1 sharpens the distribution; calibrate
                                     # from a few runs' sharpened_max_posterior
                                     # histograms in OTel

_JSON_RE = re.compile(r"\{.*?\}", re.DOTALL)
