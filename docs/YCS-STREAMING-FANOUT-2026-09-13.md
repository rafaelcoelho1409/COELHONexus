# YCS per-video streaming fan-out (2026-09-13)

Implements the fine-grained half of the streaming-architecture proposal
from earlier the same day (`YCS-NEO4J-RELIABILITY-2026-09-13.md`'s
"researched, deferred" section): Neo4j and Qdrant no longer wait for
Playwright/ES to finish the WHOLE video batch — each video's Neo4j
extraction and Qdrant embedding now start the instant that video's
transcript is committed to ES, while Playwright keeps fetching the
rest of the batch. This is a genuine architecture replacement of the
Videos-tab ingestion control flow, not an incremental tweak — see
"Scope and risk" at the bottom for how it was decided and verified.

## Before → after

**Before** (this morning's earlier ship): `chain(extract_videos,
group(ingest_to_qdrant, ingest_to_neo4j), invalidate_cache)` — Qdrant
and Neo4j ran concurrently with EACH OTHER, but both still waited for
`extract_videos` to finish ALL N videos first.

**After**: `extract_videos` dispatches one `ingest_to_neo4j` task and
one `stream_video_to_qdrant` task PER VIDEO, the instant that video's
transcript lands in ES — while still fetching the remaining videos.
`invalidate_cache` fires once, dynamically, when every dispatched task
for both phases has reported in.

## Why this couldn't be a static Celery chain/chord

`video_ids` is known upfront, so there's no "unknown membership" chord
problem. What actually breaks is timing: a chord's `group()` members
are ALL dispatched together when the chord itself is applied — Celery
has no primitive for "dispatch member N only when an external event
(this video's ES write) fires." Achieving true per-video streaming
therefore required moving completion-tracking OFF Celery's own canvas
machinery and onto Redis-native primitives — see `pipeline_task/
streaming.py`.

## The exactly-once finalize model

Every other piece of this pipeline (Stop, Rerun, the progress bars)
was built on "Neo4j/Qdrant are each one task whose state you can poll."
Per-video fan-out breaks that. The replacement, implemented in
`pipeline_task/streaming.py`:

1. `extract_videos` sets `phase_total_key(extract_id, phase)` to the
   exact count of videos it dispatched to that phase, once, right
   after its own dispatch loop finishes.
2. Every per-video task calls `mark_video_done` on completion, which
   does an atomic `INCR` on `phase_finished_key`. INCR is atomic and
   strictly monotonic — at most one caller's INCR can ever return any
   given integer, so "did MY increment reach the total" is a race-free
   way to detect "I am the last one," no lock required for that check.
3. Whichever caller (a per-video task, or `extract_videos` itself —
   covering the race where every video finishes before the total is
   even set) observes BOTH phases done takes `finalize_flag_key` via
   `SET NX` and is the one that dispatches `invalidate_cache`.

**Live-verified**, not just reasoned about: a standalone script against
a real local `redis-server` (not fakeredis, not mocked) fired ~20
`mark_video_done`/`maybe_finalize` calls concurrently via
`asyncio.gather`, including the "everything finishes before totals are
set" race and the "totals set concurrently with late finishers" race.
`invalidate_cache.delay()` (patched to a counter) fired **exactly
once** across all scenarios, and re-calling `maybe_finalize` 20 more
times afterward did not re-trigger it. Aggregate stats
(`nodes_created`, `points_upserted`, `completed_ids`) summed correctly
across all simulated videos.

## The Qdrant streaming buffer

`ingestion/service.py::ingest_to_qdrant`'s cross-video chunk-packing
buffer (accumulate ~50 chunks, flush in one NIM call — the 5.4× embed-
latency win from an earlier ship) only worked because one task held it
in memory for the whole batch. `ingestion/streaming.py` replaces it
with a Redis LIST (`qdrant_buffer_key`) shared across every video's
`stream_video_to_qdrant` task, with the drain-and-upsert critical
section guarded by a Redis `Lock` (non-blocking acquire — a caller
that doesn't get the lock just skips its flush attempt; the chunks
stay buffered for the next attempt or the final drain).

**Live-verified**: 30 concurrent "videos" (90 chunks total) pushed and
attempted-flushed simultaneously via `asyncio.gather` against a real
Redis, with Qdrant/embedding calls mocked to record what would have
been upserted. Result: exactly 90 points upserted (matching 90 pushed,
zero lost, zero duplicated), buffer fully drained to 0, no duplicate
point ids. One flush fired at the 50-chunk threshold, the remainder (40)
via the final-drain path — confirming both code paths work correctly
under real concurrency, not just in isolation.

## What's NOT verified — the honest gap

Both tests above validate the **novel distributed-coordination logic**
in isolation against real Redis. They do NOT exercise:
- The real Playwright → ES → per-video-dispatch path end to end.
- Real Neo4j entity extraction or real Qdrant upserts.
- Celery's actual worker-pool behavior under this dispatch pattern
  (the 2-slot prefork pool from `k8s/helm/values.yaml`).
- The FastHTML JS changes against a running poll loop.

This is a real architecture replacement of a live pipeline, shipped
without a skaffold redeploy or live smoke test this session — per
explicit instruction to build the full thing now and accept that risk
(see "Scope and risk" below), not an oversight. **Live-test on a real
batch before trusting this in production.**

## Files changed

- `transcript/service.py` — `fetch_transcriptions_batch`: ES indexing
  moved from once-per-chunk-of-10 to per-video (fire-and-forget tasks,
  gathered before return); new `on_video_indexed` hook fires once a
  video's ES write is confirmed searchable (`BULK_REFRESH=True` makes
  this a real guarantee, not best-effort). Cached videos ALSO fire the
  hook now (needed so a Rerun over mostly-cached videos still dispatches
  Neo4j/Qdrant work for them). New `stats["dispatched_streaming"]` /
  `stats["index_write_failed"]` fields.
- `extract/task.py` — `_extract_videos_async` threads `extract_id`
  through; new `_on_video_indexed` dispatches per-video
  `ingest_to_neo4j`/`stream_video_to_qdrant`; new
  `_dispatch_streaming_totals` sets both phase totals once dispatch
  finishes (or fires `invalidate_cache` directly if nothing was
  dispatched). Removed the now-redundant second bulk ES index (per-
  video indexing already happened inside `fetch_transcriptions_batch`).
- `neo4j_task/task.py` — new `skip_resolution`/`extract_id` params.
  Streaming mode reports to `mark_video_done`; the "last" video runs
  `resolve_entities()` once (unconditionally — safe/idempotent even if
  THIS video created 0 nodes) instead of the old per-call `agg_nodes >
  0` gate. Safety-net counter increment on the "no transcript in ES"
  early-return path so a missing doc can't hang the exactly-once
  finalize forever.
- `qdrant_task/task.py` — new `stream_video_to_qdrant` task: chunks one
  video, pushes to the Redis buffer, reports to `mark_video_done`,
  drains the buffer remainder if last.
- `pipeline_task/streaming.py` (new) — the exactly-once finalize model
  described above.
- `ingestion/streaming.py` (new) — the Redis-backed Qdrant buffer
  described above.
- `pipeline_task/service.py` — `dispatch_videos_pipeline` now just
  fires `extract_videos` (no more chain/group — nothing left to build
  one over). Returns `{extract: <real id>, qdrant: <extract_id
  reused>, neo4j: <extract_id reused>, invalidate: ""}` for backward
  compatibility with every consumer keyed by name.
- `pipeline_task/params.py` — removed `NEO4J_BATCH_SIZE` (orphaned;
  per-video dispatch always passes `1` directly now).
- `ingestion/keys.py` / `ingestion/params.py` — `qdrant_buffer_key`/
  `qdrant_flush_lock_key` live here (not in `pipeline_task`) — see the
  circular-import note below.
- `api/v1/ycs/admin/router.py` — new `GET /pipeline/{extract_id}/stream/
  {phase}` aggregator endpoint, synthesizing the same `{state, meta,
  result}` shape `task_status` returns for a real Celery id.
- `api/v1/ycs/content/router.py` — Stop/Wipe now also revoke every
  per-video task id tracked via `get_dispatched_task_ids` (best-effort
  — see caveat below), not just the 4 old static ids.
- `pipeline_panel.js` — `qdrant`/`neo4j` bars now poll the new
  aggregator endpoint (`pollStreamOnce`) instead of `/admin/task/{id}`
  (`pollTaskOnce`). No changes needed to `_setBar`/`_phasePct`/
  `_phaseLabel`/`_successHint` — the aggregator returns the same shape.

## Circular-import note (why buffer keys live in `ingestion`, not `pipeline_task`)

`from pipeline_task.<anything> import x` requires executing
`pipeline_task/__init__.py` first, which pulls in `.task` → every
`domains.ycs.*` Celery task module. `ingestion/streaming.py` only
needed 2 tiny key-builder functions from that chain — importing the
whole thing just for those would break `pipeline_task/service.py`'s
own documented goal ("import cleanly in test environments without
[Celery-adjacent] deps installed"). Fix: `qdrant_buffer_key`/
`qdrant_flush_lock_key` live in `ingestion/keys.py` instead, using a
locally-duplicated prefix constant (`STREAMING_KEY_PREFIX =
"ycs:pipeline:"`, documented as needing to stay in sync with
`pipeline_task.params.PIPELINE_STATE_PREFIX`'s literal value).
Verified via real import (not just reasoning) — see below.

## 2026-09-14 — live test caught a real regression, fixed same-day

Ran a real 25-video Capital Global channel batch against the shipped
code. Two real bugs surfaced, found via `kubectl logs` + a direct
Library-endpoint query (not by guessing):

**Root cause: Celery worker-slot starvation.** The worker pool is
capped at `concurrency: 2` (memory budget, shared across every domain —
crawler/llm/planner/synth/ycs/rr). `extract_videos` pins one slot for
its entire run (424s here). That left exactly **one** slot for all 21
per-video Neo4j + Qdrant tasks combined — they ran strictly one at a
time. Worse: `extract_and_store_graph`'s internal `EXTRACT_CONCURRENCY`
(5) semaphore was still being created every call, but with only 1
video ever in the pool, it bought nothing — confirmed via the log line
`processing 1 transcripts (skipped 0, concurrency=5)` repeating 21
times. Neo4j throughput was WORSE than the pre-streaming design (which
processed 5 videos concurrently per call).

**Confirmed live: fire-and-forget task-tracking bug.** The dispatched-
task-id tracking in `_on_video_indexed` (added for Stop-button revoke
coverage) used bare `asyncio.ensure_future`, not gathered — the logs
showed 4 `Task was destroyed but it is pending` warnings in one run,
confirming this "accepted, rare" risk was actually routine.

**Fix — reverted the dispatch granularity, not the streaming property.**
`extract/task.py`'s `_on_video_indexed` now accumulates video ids into
an in-process list (no new Redis queue needed — `extract_videos` is
already the one place watching every video complete, in order) and
flushes ONE `ingest_to_neo4j` call per `EXTRACT_CONCURRENCY`-sized
chunk, passing `batch_size=len(chunk)` so `extract_and_store_graph`'s
internal pool width exactly matches the chunk — full concurrency
restored. Chunks still flush well before the whole batch clears
Phase 1 (every 5 videos, not after 25), preserving the actual
streaming win. `neo4j_task/task.py`'s streaming-completion branch was
generalized from "the one video in this call" to "loop over every
video in this chunk," attaching the chunk's aggregate node/relationship
counts to only the first video in the loop (avoids inflating the
cross-video sum `get_phase_progress` computes). Also fixed a related
latent gap the chunking exposed: a transcript missing from ES for only
SOME of a chunk's videos (not all) now correctly marks just those
video(s) done-as-failed, instead of only the old single-video safety
net firing when `video_ids[0]` alone was missing. The task-tracking
fire-and-forget was also fixed — tracking coroutines are now collected
and `asyncio.gather`'d before `_extract_videos_async` returns, same
pattern `transcript/service.py`'s ES-indexing tasks already used.

Qdrant was untouched — its per-video tasks completed in ~0.1-0.5s each
in the live run, never the bottleneck.

**Live-verified again** (not just reasoned about): a live-Redis test
simulating 21 videos split into 5 chunks (4×5 + 1×1) for Neo4j,
mixed concurrently with 21 individual Qdrant reports, confirmed
exactly-once finalize still holds and the chunk-aggregate attribution
sums correctly (5 chunks × 10 nodes = 50, not 21 × 10 = 210).

## Known, accepted tradeoffs (not bugs)

- **No "Running" pill for Qdrant/Neo4j in the per-video drawer table.**
  The old model tracked one `current_item` per phase (there was only
  ever one video "active" at a time). With many videos genuinely
  in-flight at once, there's no single "current" video to highlight —
  cells now jump straight from Queued to Done/Failed, skipping the
  animated Processing state. Cosmetic only; the data itself is correct.
- **Per-video overhead.** Each per-video Neo4j task opens its own fresh
  ES + Neo4j connections (previously amortized across the whole batch).
  Inherent to the streaming tradeoff (lower latency-to-first-result,
  more per-item connection overhead) — not a regression to fix.
- **Best-effort dispatched-task-id tracking for Stop.** `extract/
  task.py`'s tracking write is fire-and-forget (`asyncio.ensure_future`,
  not gathered) — a hiccup there means Stop might not revoke the very
  last video's tasks. Documented in `extract/task.py`; not worth a sync
  Redis client just for this edge case.
- **Stop mid-Phase-1 still means `invalidate_cache` never fires** — true
  before this ship too (a revoked chain link never reached the
  callback either). Not a new gap.

## Scope and risk — why this was built in one pass

Started as "ship 5 already-scoped next steps" (Celery spike, phase-level
parallelization — already shipped earlier the same session — then
Redis buffer, per-video dispatch, progress-UI rework). Mid-implementation,
it became clear per-video fan-out isn't additive polish on top of the
phase-level chord — it structurally requires replacing the pipeline's
entire completion-tracking model (Stop/Rerun/progress-UI all assumed
fixed, known-upfront task ids). That finding was surfaced explicitly
before continuing; the choice made was to build the full thing now and
accept that correctness rests on the reasoning + the two live-Redis
verifications above, rather than a live end-to-end cluster test (not
performed this session). Live-test on a real batch is the immediate
next step before trusting this in production.
