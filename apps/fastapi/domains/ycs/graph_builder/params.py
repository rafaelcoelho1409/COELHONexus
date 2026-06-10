"""ycs/graph_builder — LLM batching + entity-resolution tunables.

Direct port of deprecated `services/youtube/graph_builder.py` defaults
(`L61, L150, L220-226, L245`)."""
from __future__ import annotations


# Concurrent LLM calls — deprecated default, now the streaming-pool
# width (2026-06-10 rework; see EXTRACT_CONCURRENCY).
DEFAULT_BATCH_SIZE = 3

# Streaming-pool concurrency for `extract_and_store_graph` (2026-06-10).
# Replaces the barrier-batch loop (batch of N → wait for ALL → sleep 2s
# → next batch): a semaphore keeps this many single-transcript LLM
# calls in flight and results are consumed in completion order, so one
# slow video never stalls the others and per-video progress/failure
# attribution is preserved at ANY width. Empirical motivation: NIM
# reasoning arms (glm-5.1 etc.) run ~180 s/transcript — sequential
# batch_size=1 made a 4-video run ~12 min and a 500-video run ~25 h.
# 3 concurrent ≈ 3× throughput while staying far under the 40 RPM
# free-tier ceiling (3 in-flight × ~1-3 min/call ≈ 1-3 RPM).
import os as _os

EXTRACT_CONCURRENCY = max(
    1, int(_os.environ.get("YCS_NEO4J_CONCURRENCY", "3") or "3"),
)

# Per-batch wall-clock watchdog (2026-06-09). Hard ceiling on ONE
# `aconvert_to_graph_documents` call so a hanging arm can never burn
# the whole run. Sized above the worst LEGIT inner stack — per-
# deployment request timeout (180s for dd-synth arms) + the pinned
# router's RateLimitErrorRetries backoff — so it only fires when the
# request stack itself wedges (connection-level hang, runaway provider
# queue). On expiry the batch is counted failed and the run moves on;
# the run-level reward then lands within minutes and the bandit demotes
# the arm — instead of the pre-watchdog behavior where step-3.5-flash
# burned 36 min/transcript in nested timeout-retries.
#
# MUST stay above the per-call LLM timeout (YCS_NEO4J_EXTRACT_TIMEOUT_S,
# default 300s) or it fires before the call's own deadline. Env-tunable
# so it can rise with the per-call timeout: if you push extraction to
# 600s, set this to ~900s.
GRAPH_BATCH_TIMEOUT_S = max(
    300.0, float(_os.environ.get("YCS_NEO4J_BATCH_WATCHDOG_S", "600") or "600"),
)

# Circuit breaker: consecutive NON-PRODUCTIVE batches (raised OR wrote
# 0 nodes + 0 rels) before `extract_and_store_graph` aborts so the
# caller can swap the pinned arm mid-run (2026-06-09). Without this a
# dead arm pinned at batch 1 rides ALL batches — on a 500-video
# overnight run that is 500 x ~3 min of guaranteed-futile burn (~25h)
# before the bandit even hears about it. Three in a row is decisive:
# real YouTube transcripts are entity-dense, so a WORKING arm
# producing 0 nodes on 3 consecutive videos is ~impossible, while a
# broken arm (step-3.5-flash timeouts, minimax-m2.7 silent zeros)
# fails every batch. Cost per trip ≈ 3 batches x 180s worst case
# ≈ 9-10 min, then the run continues on a different arm.
MAX_CONSECUTIVE_NONPRODUCTIVE = 3

# rapidfuzz token-ratio cutoff for the fuzzy-merge step. 75 was tuned
# empirically on the deprecated corpus — high enough to dodge false
# positives like "Cancun" vs "Canada", low enough to catch "St Kitts"
# vs "Saint Kitts". Kept as the FAST PRE-FILTER: candidates that pass
# get a semantic embedding-similarity check before merging (see below).
FUZZ_MERGE_CUTOFF = 75

# Semantic entity-resolution tunables (Option 2, 2026-06-07).
# ----------
# `fuzz.ratio` is character-Levenshtein; it can't tell semantic
# similarity from surface similarity. Empirical false merges from the
# deprecated 75-cutoff: `Astronomia`↔`Gastronomia` (85.7%),
# `segunda guerra mundial`↔`terceira guerra mundial` (75.6%),
# `comandante alemão`↔`comandante americano` (75.7%).
#
# Fix: after the fuzz pre-filter, embed both candidate IDs via NIM
# BGE-M3 (multilingual, key for Brazilian Portuguese entities) and
# cosine-compare. Merge only when cosine ≥ EMBED_COSINE_CUTOFF.
#
# Empirical results on real entity pairs from the corpus (2026-06-07):
#   Ufólogo↔ufólogo                       0.891  TRUE  merge ✓
#   Brasil↔Brazil                         0.955  TRUE  merge ✓
#   St Kitts↔Saint Kitts and Nevis        0.855  TRUE  merge ✓
#   sensor infravermelho↔câmera infrav.   0.822  bordl skip
#   Goldman Sachs↔Goldman                 0.811  TRUE  skip (safe miss)
#   comandante alemão↔comandante americ.  0.800  FALSE skip ✓
#   segunda guerra↔terceira guerra        0.766  FALSE skip ✓
#   Astronomia↔Gastronomia                0.597  FALSE skip ✓
#
# 0.85 is the clean separator: every false merge is below, every
# obvious true merge is above. Borderline truncations get
# conservatively skipped — the LLM tends to emit consistent forms
# across batches, so missing the occasional abbreviation merge is
# acceptable vs. risking semantic conflation.
RESOLVE_EMBED_MODEL = "baai/bge-m3"
EMBED_COSINE_CUTOFF = 0.85

# Entity labels whose IDs are numeric or otherwise lexically-similar
# in ways that DON'T mean "same thing" — e.g. "$100,000" vs "$1,000,000"
# both have a high rapidfuzz ratio but are distinct values. Mirror of
# deprecated `SKIP_FUZZY_LABELS` (`L220-226`).
NUMERIC_LABELS_SKIP: frozenset[str] = frozenset({
    "Money",
    "Money amount",
    "Cost",
    "Number",
    "Date",
    "Currency",
})

# Schema-discovery sample size (deprecated `L282, L287`).
SCHEMA_DISCOVERY_SAMPLE_COUNT = 3
SCHEMA_DISCOVERY_SAMPLE_CHAR_CAP = 10000
