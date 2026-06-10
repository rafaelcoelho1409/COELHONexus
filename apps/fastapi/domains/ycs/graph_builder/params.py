"""ycs/graph_builder — LLM batching + entity-resolution tunables.

Direct port of deprecated `services/youtube/graph_builder.py` defaults
(`L61, L150, L220-226, L245`)."""
from __future__ import annotations


# Concurrent LLM calls per batch — deprecated default. Tuned for free-
# tier NIM (40 RPM) with INTER_BATCH_SLEEP_S pacing between batches.
DEFAULT_BATCH_SIZE = 3

# Pacing between batches to stay under 40 RPM (deprecated `L150`).
# NOTE: deprecated used `time.sleep` inside an `async def`; preserved
# verbatim per the port-fidelity mandate.
INTER_BATCH_SLEEP_S = 2.0

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
GRAPH_BATCH_TIMEOUT_S = 600.0

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
