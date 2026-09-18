from __future__ import annotations

import os as _os


DEFAULT_BATCH_SIZE = 3

# Overridable via YCS_NEO4J_CONCURRENCY; semaphore keeps this many transcripts in flight.
# 2026-09-13: 5 -> 3 -> 5 same day. First bump to 5 looked like it caused a
# regression (only 10/24 videos ever attempted on a Raiam Santos run,
# circuit breaker tripping almost every segment). Reverted to 3 as the
# suspected cause — but the SAME failure (every first-batch call timing
# out simultaneously at exactly 120s, 0 successes) reproduced identically
# at concurrency=3, proving concurrency was never the real driver. Actual
# cause: chain/service.py's build_ycs_neo4j_pinned_chain() had an explicit
# 120.0s timeout that was too tight for this call shape (large transcripts
# → large completions) — fixed there (raised to 400.0s). Back to 5 now
# that the real cause is fixed. The old circuit-breaker abandonment bug
# (graph_builder/service.py's `asyncio.as_completed` + `break` dropping
# whatever's still in-flight untried) is FIXED — the circuit breaker was
# removed entirely (it existed to let the caller "swap arms," which was
# confirmed to always resolve to the same target anyway) and replaced
# with neo4j_task's real retry-failed-only loop. Do not push toward
# doc_distill's CONCURRENCY=10 — these calls carry much larger prompts/
# completions than doc_distill's 600-token summaries and are more likely
# to hit free-tier rate limits sooner.
#
# 2026-09-14: 1 -> 3. The distributed Redis semaphore (see
# NEO4J_EXTRACT_SEM_KEY below) now makes this a REAL global cap, not
# just an in-process one — earlier "concurrency was never the driver"
# findings predate that fix, when concurrency=N never actually reached
# N real simultaneous rotator calls. Raised alongside GRAPH_BATCH_
# TIMEOUT_S/the rotator's max_wall_s (600s) rather than in isolation —
# per-request budget is now generous enough that queuing behind 2
# siblings (tightest provider caps) shouldn't by itself exhaust it the
# way the old 180s ceiling did.
#
# 2026-09-14: 3 -> 5, requested test now that BOTH the semaphore-limit
# bug (was silently capping at whatever the smallest concurrent chunk
# happened to be, not this value) and the wall-clock ceiling are fixed
# and confirmed live (3 genuinely concurrent holders observed). 5 still
# exceeds every single free-tier provider's own in-flight cap in the
# rotator (nvidia_nim=4 is the highest; most are 2) — some queuing
# inside the rotator's own cascade is expected and is the thing this
# test is actually checking, now that queued time has real room (600s)
# to resolve in instead of blowing the old 180s ceiling.
# 2026-09-15: 5 -> 3. Live-observed on a real chunk: 5 concurrent
# full-transcript extraction calls blew through Mistral's + Groq's
# free-tier per-minute quotas almost instantly (RateLimitError burst on
# 3 different models within the first ~5s), forcing the cascade through
# several dead providers before landing on one with headroom. Nothing
# actually failed permanently (retry passes absorbed it), but for
# whole-channel runs (many chunks back-to-back against the same scarce
# free-tier pool, not just one 5-video test) that overhead compounds.
# 3 trades a little peak throughput for meaningfully fewer 429/504
# retries at that scale.
EXTRACT_CONCURRENCY = max(
    1, int(_os.environ.get("YCS_NEO4J_CONCURRENCY", "3") or "3"),
)

# 2026-09-14: 600 -> 700. Must exceed the rotator's own max_wall_s
# (600s, see build_ycs_neo4j_pinned_chain) AND this client's own
# ChatOpenAI timeout (650s) — otherwise THIS watchdog fires first and
# cancels a call that the rotator was still legitimately working on,
# making the larger rotator/client budgets pointless. NEO4J_EXTRACT_
# SEM_LEASE_S below derives from this, so it scales automatically.
GRAPH_BATCH_TIMEOUT_S = max(
    300.0, float(_os.environ.get("YCS_NEO4J_BATCH_WATCHDOG_S", "700") or "700"),
)

# 2026-09-14: EXTRACT_CONCURRENCY only ever gated an in-process
# asyncio.Semaphore — invisible across separate Celery worker
# processes. Since neo4j_task dispatches ONE Celery task PER VIDEO,
# multiple such tasks run concurrently across the worker pool
# (confirmed live: 2 simultaneous rotator LLM calls at
# EXTRACT_CONCURRENCY=1 with the worker pool's max-concurrency=2),
# defeating the whole point of testing concurrency=1 against the
# rotator's per-provider caps. This Redis sorted-set key backs a real
# distributed semaphore (Redis in Action fair-semaphore pattern,
# self-healing via score-based eviction) shared by every worker/pod —
# EXTRACT_CONCURRENCY now caps the TRUE global in-flight call count,
# not just one process's view of it.
NEO4J_EXTRACT_SEM_KEY = "ycs:neo4j:extract:sem"

# Lease TTL for one held slot. Must exceed GRAPH_BATCH_TIMEOUT_S (the
# hard per-call watchdog) — a legitimately-still-running holder must
# never look "stale" to another worker's cleanup pass. Margin covers
# slot-acquire overhead + clock skew across pods.
NEO4J_EXTRACT_SEM_LEASE_S = GRAPH_BATCH_TIMEOUT_S + 30.0

# fuzz.ratio pre-filter; embedding cosine gate at 0.85 catches false positives like Astronomia↔Gastronomia.
FUZZ_MERGE_CUTOFF = 75

# 2026-09-14: extraction fingerprint version, stored on every
# source Document alongside `transcript_sha`. Bump when the extraction
# prompt/schema changes in a way that makes previously-extracted graphs
# stale — the skip-on-video_id check then re-extracts instead of
# trusting outdated entities (DD's manifest_hash pattern, adapted:
# DD versions prompt+inputs per node; here one version covers the
# single LLMGraphTransformer call shape).
EXTRACT_PROMPT_VERSION = 1

# Inter-retry-pass backoff (jittered): immediate retries hammer an
# already-exhausted free-tier pool (DD: 2-5s+jitter per item; scaled up
# here since one Neo4j call carries a ~20K-char transcript, not a
# 600-token summary — but far below DD's 130s inter-node settle, which
# would dominate per-chunk).
RETRY_PASS_BACKOFF_S = (10.0, 30.0)

# Consecutive all-failed INFRA passes before giving up early (DD's
# SUSTAINED_INFRA_OUTAGE_LIMIT=2, adapted: counts passes, not videos —
# with 5-video chunks a per-video streak would be trigger-happy).
MAX_CONSECUTIVE_INFRA_PASSES = 2

# 2026-09-17: a pass's "0 successes" only counts toward the streak
# above when at least this many videos were pending IN THAT PASS.
# Root-caused via a live Nomad Capitalist video (AkH_MtjFfz0) that
# failed 3 separate ingestion runs in a row — always as a lone
# `batch_size=1` streaming chunk/retry (0/1 = trivially "all failed"
# off two merely-unlucky NIM timeouts), tripping the halt at exactly
# MAX_CONSECUTIVE_INFRA_PASSES and abandoning the video after only 2
# of its 4 allotted MAX_RETRY_PASSES attempts. A manual re-run of the
# exact same transcript minutes later succeeded in 3s — the content
# was never the problem, the breaker was just evaluated on a sample
# too small to mean anything (the "5-video chunks" assumption in the
# comment above doesn't hold for single-video chunks/retries, which
# this same code path also serves). Below this threshold, infra
# failures still retry with the normal backoff — they just don't
# count toward the early-abandon streak.
MIN_PENDING_FOR_INFRA_HALT = 6
# 2026-09-17 (raised 3 -> 6, same day): a 3-5 video pass was still
# exposed to the breaker on 2 consecutive fully-unlucky passes — the
# threshold now sits above EXTRACT_CONCURRENCY's normal chunk size
# (3), so only a genuinely larger batch's all-fail streak counts as
# real outage evidence.

# Per-video Bolt write budget (DD: wait_for(write,60)). add_graph_documents
# is a SYNC driver call — run in a thread + watchdog so a wedged
# connection can't hang the task past its retry budget.
WRITE_TIMEOUT_S = 120.0

# 2026-09-13: tuned against baai/bge-m3's score distribution back when
# entity-resolution pinned that model specifically via its own
# NVIDIAEmbeddings instance. Now shares the main embedding-endpoint
# singleton (no per-call model pinning) — may need re-tuning against
# whatever the endpoint currently resolves to.
EMBED_COSINE_CUTOFF = 0.85

# Numeric/date labels: high fuzz ratio ≠ same entity (e.g. "$100k" vs "$1M").
NUMERIC_LABELS_SKIP: frozenset[str] = frozenset({
    "Money",
    "Money amount",
    "Cost",
    "Number",
    "Date",
    "Currency",
})

SCHEMA_DISCOVERY_SAMPLE_COUNT = 3
SCHEMA_DISCOVERY_SAMPLE_CHAR_CAP = 10000

# 2026-09-14: Neo4j Community Edition has no multi-database support (hard
# DBMS limit, confirmed unchanged as of Sept 2026 — Enterprise/Aura-paid
# only), so a shared instance is the only option. Every Video/Channel/
# Document/__Entity__ node this feature writes also gets these two static
# labels — app-level + feature-level — so a future second Neo4j-writing
# project can't have its nodes silently fuzzy-merged or graph-walked
# together with YCS's by resolve_entities() or anything else that scans
# by label. Neo4j nodes support multiple labels natively; no schema
# migration needed to add more.
PROJECT_LABEL = "COELHONexus"
SOURCE_LABEL = "YCS"
