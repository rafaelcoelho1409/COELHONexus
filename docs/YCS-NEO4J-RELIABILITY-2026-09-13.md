# YCS Neo4j graph-build — LLM client fix, a same-day regression, the structural fix, and a streaming-architecture proposal (2026-09-13)

Context: following the Playwright speed pass (see
`YCS-PLAYWRIGHT-PERFORMANCE-2026-09-13.md`), attention turned to Neo4j —
the slowest of the four ingestion phases, running sequentially against
COELHO LLM Rotator. This doc covers the architecture question that
started it, the client-config fix that came out of it, a same-day
regression from over-tightening a timeout, the structural circuit-
breaker fix (SHIPPED), the Qdrant+Neo4j concurrency fix (SHIPPED — see
bottom section), and the remaining, deliberately-deferred piece of the
streaming-architecture proposal.

## Starting question: should DD's "timeout engine" move into COELHO LLM Rotator?

Investigated by reading `chat_judge_bandit_async` (DD Planner's
`doc_distill` node, the actual code, not assumption) side by side with
YCS's Neo4j chain builder.

**Finding: the rotation/arm-selection already lives server-side, in the
rotator.** Evidence: `chat_judge_bandit_async`'s `dd_process` and
`candidate_filter` params are explicitly commented `# ignored — no
per-process weights` / `# ignored — no heavyweight filter` — fossils of
a client-side bandit that used to exist and has since been stripped
down to a single `client.chat.completions.create(...)` call with
`model="auto"`. There is nothing left to "transfer."

**What's still client-side, and has to stay there** (this is the actual
"timeout engine," and none of it can move server-side):
1. **`timeout_s`** — how long *this caller* is willing to wait. Varies
   per consumer (a Celery task with a 3660s budget vs. a fast
   synchronous judge call) — no single server-side value serves every
   consumer.
2. **An outer `asyncio.wait_for` backstop** (`timeout_s + 15s`) — works
   around httpx's read-timeout only measuring gaps *between chunks*,
   not total duration. A blind spot in the client's own HTTP library,
   invisible to the server.
3. **`max_retries=0`** on the pooled client — a local SDK config flag
   with no wire representation; the rotator has no way to observe or
   enforce it over the protocol.

Conclusion: keep it in Nexus (nothing DD-specific to relocate — the
shared client already lives at `domains/llm/rotator/chain/service.py`,
not under `domains/dd/`), apply the same discipline to YCS's Neo4j path,
adjusted for its different interface (LangChain `ChatOpenAI`, required
by `LLMGraphTransformer`, vs. DD's raw `AsyncOpenAI` hot path — these
correctly stay separate call shapes, not a case for merging).

## Fix 1 (shipped): give Neo4j's chain the same client discipline DD has

`build_ycs_neo4j_pinned_chain()` (`domains/llm/rotator/chain/service.py`)
used to delegate to `build_reduce_label_chain()` — a bare
`ChatOpenAI(base_url=..., api_key=..., model=..., temperature=0.0)` with
**no timeout override and no `max_retries` override**, falling back to
the openai SDK's own defaults (600s timeout, `max_retries=2`). An
SDK-level retry there stacks a second, redundant retry loop on top of
the rotator's own arm-swap cascade — exactly the failure mode a
neighboring comment in the same file already warned about
("`max_retries=0,  # rotator handles cascade, SDK retries would triple
timeout`") but that this call site didn't follow.

Added a shared `_build_chat_openai(*, timeout_s, ...)` helper (also
found and fixed the same gap dormant in `_get_chat_llm()`, DD's own
"legacy" LangChain wrapper — zero live callers today, but the same bug
waiting to happen the moment something calls it) so both paths get
`max_retries=0` by construction instead of trusting each call site to
remember.

## Fix 2 → same-day regression → Fix 3: the timeout value itself

First shipped `build_ycs_neo4j_pinned_chain()` with `timeout_s=120.0`,
reasoning "comfortably above the ~20-30s observed 504 latency." **Wrong
reasoning** — that 20-30s figure was the rotator's *fast-fail* path; a
genuinely slow-but-working call for this workload (full transcripts →
large completions, entity+relationship extraction) legitimately needs
more.

Caught via live testing on a real 24-video Raiam Santos McArn batch:

1. **First run** (concurrency bumped 3→5 same day): only 10/24 videos
   ever got a real extraction attempt. Every failure was
   `APITimeoutError: Request timed out` in **simultaneous clusters of
   3, exactly 120.0s after each segment started** — the client-side
   timeout firing, not server behavior.
2. **Misdiagnosed concurrency as the cause**, reverted 5→3, re-tested.
3. **Identical failure reproduced at concurrency=3** — 0 successes,
   same simultaneous-120s-cluster signature. This **disproved**
   concurrency as the driver and pointed straight at the timeout value.
4. **Fixed**: raised `timeout_s` to 400.0 (comfortably under the 600s
   `GRAPH_BATCH_TIMEOUT_S` outer watchdog in `graph_builder/service.py`,
   much closer to the ~600s implicit default that was actually working
   before any of today's changes). Restored concurrency to 5 — there
   was never real evidence against it.

**Caveat carried forward, not resolved**: 400s is a reasoned estimate
("~600s used to work, land safely under the 600s watchdog"), not a
measured percentile. DD's `doc_distill` timeout (70s) came from a real
LangFuse p99 pull (genuine successful calls topped out at 47.6s, max
51.7s) — Neo4j's graph-extraction calls have no equivalent telemetry
pulled yet. If timeouts recur at 400s, pulling real latency data (if
this call path is traced in LangFuse) should replace the next guess.

## Validation run — fix confirmed, but exposed a bigger, separate problem

Re-ran the same 24 videos at concurrency=5 / timeout=400s. Verified
**directly against Neo4j** (`MATCH (d:Document {video_id: vid})
-[:MENTIONS]->(e)`), not the task's self-reported result dict — see why
below.

**Confirms the timeout fix worked**: every failure this run was a
genuine server-side `InternalServerError: Error code: 504` at natural,
varied intervals (30-90s apart) — no more artificial simultaneous
120s cutoffs. Concurrency=5 caused no new problems.

**Real outcome: 11/24 videos got genuine entities, 13/24 did not**
(one of those 13, `O5pmO8qCv6s`, legitimately has no transcript —
expected). Total wall time: 1026s (~17 min), dominated by riding out
repeated 504 sequences across segments.

**The task's own self-reported result dict is misleading** — it claimed
`videos_completed: 2, videos_failed: 3` (only 5 total accounted for!),
when the real cumulative picture across all 4 segments was 11
successes / 13 gaps. The result dict only reports the *last segment's*
tally, not the run's cumulative outcome. This is a real observability
gap independent of everything else in this doc — anyone trusting the
task's own summary would badly underestimate how incomplete a run
actually is. Worth fixing (aggregate `completed_ids`/`failed_ids`
across all segments into the final result), separately from the
structural issue below.

## Structural bug (was open, now SHIPPED) — why real provider instability caused permanent gaps

Traced in `domains/ycs/graph_builder/service.py:291-362`:

```python
pool = [asyncio.create_task(_extract_one(doc)) for doc in documents]
for fut in asyncio.as_completed(pool):
    ...
    if consecutive_nonproductive >= abort_after_consecutive:
        aborted_nonproductive = True
        ...
        break   # <-- whatever's still in `pool` (started or not) is abandoned here
```

`asyncio.as_completed` yields results in *completion order*, not
dispatch order. The moment 3 consecutive extractions failed, `break`
exited the loop — any other tasks in `pool` that hadn't finished yet
(running or not yet started, gated by the concurrency semaphore) were
simply dropped from that segment's accounting. They weren't lost
forever — `neo4j_task/task.py`'s next segment re-included them via a
real DB-state check ("N videos already in Neo4j; skip") — but there
were only `MAX_ARM_SWAPS=3` segments total (4 attempts), and each
segment's breaker tended to trip after only ~5-15% of its pool got
attempted when the provider was genuinely degraded. A sustained bad
patch could burn the entire swap budget while a third or more of the
video pool never got a single real attempt — this is exactly what the
validation run above measured (11/24 succeeded, 13/24 never got a real
attempt).

### Root-cause reframe: the circuit breaker existed to enable arm-swapping, which doesn't exist

Before fixing this, a separate question forced a reframe: **"we're
using COELHO LLM Rotator as the external LLM provider, so we don't
need the old LLM Rotator codes from COELHO Nexus anymore, right?"**

Checked directly against the actual code (not assumption):
`pick_ycs_neo4j_deployment_bandit()`, `record_ycs_neo4j_reward()`, and
`release_ycs_provider_slot()` (`domains/llm/rotator/chain/service.py`)
were all confirmed to be **pure no-op shims** — the FGTS-VA bandit
(arm-selection, reward tracking, slot management) now lives entirely
**server-side**, inside the external COELHO LLM Rotator itself. Every
client-side call these three functions made resolved to the same
`model="auto"` target regardless of which "arm" the local shim thought
it was swapping to. Confirmed zero remaining callers via grep before
deleting.

This meant the entire premise of the circuit breaker — "abort this
segment's pool so the caller can swap to a different arm" — bought
**nothing**. There was no different arm to swap to. The breaker's only
real effect was the abandonment bug above: trading real work for an
arm-swap that never actually changed anything.

### The fix: delete the breaker, retry only what actually failed

Also verified (separately, via grep of actual import paths, not
assumption) that **all** YCS Ingestion LLM/embedding calls route
exclusively through COELHO LLM Rotator or its embedding-gateway
sibling — Neo4j via `chain/service.py`'s `COELHO_ROTATOR_URL`, Qdrant/
entity-resolution embeddings via the new `domains/llm/embeddings/
service.py`'s `COELHO_EMBEDDING_URL` — with zero imports of the
orphaned NVIDIA-hardcoded fallback path (`embed_via_router_sync`/
`_get_embeddings()`). That confirmed it was safe to delete the old
Nexus LLM-rotator plumbing for this path entirely, not just the three
no-op functions.

**Removed** (all confirmed dead via grep before deletion, all 5 touched
files re-verified with `py_compile` after):
- `pick_ycs_neo4j_deployment_bandit`, `record_ycs_neo4j_reward`,
  `release_ycs_provider_slot` — deleted from `chain/service.py` and
  from `chain/__init__.py`'s imports/`__all__`.
- `MAX_CONSECUTIVE_NONPRODUCTIVE` — deleted from
  `graph_builder/params.py` (zero remaining callers).
- The circuit-breaker block itself, plus its supporting state
  (`consecutive_nonproductive`, `aborted_nonproductive`, the
  per-video `productive` tracker, and the `abort_after_consecutive`
  parameter) — deleted from `extract_and_store_graph()` in
  `graph_builder/service.py`. `if run_resolution and not
  aborted_nonproductive:` simplified to `if run_resolution:`.
- Unused imports in `neo4j_task/task.py` (`classify_error`, stale
  `time`/`logging`) cleaned up alongside.

**Replaced with**, in `neo4j_task/task.py`: `MAX_ARM_SWAPS = 3` renamed
to `MAX_RETRY_PASSES = 3` (same budget, honest name), driving a real
retry-failed-only loop —

```python
pending_transcripts = transcripts
for attempt in range(MAX_RETRY_PASSES + 1):
    extraction_stats = await extract_and_store_graph(
        transcripts=pending_transcripts, ..., run_resolution=False,
    )
    failed_ids = extraction_stats.get("failed_video_ids") or []
    if not failed_ids:
        break
    if int(extraction_stats.get("videos_completed", 0) or 0) == 0:
        break   # provider fully down this pass — no point burning more passes
    if attempt < MAX_RETRY_PASSES:
        pending_transcripts = [t for t in transcripts if t["video_id"] in failed_ids]
```

Every document now gets a genuine attempt every pass — nothing is
silently abandoned mid-pool anymore. `extract_and_store_graph()` also
now returns `completed_video_ids` (new, symmetric with the pre-existing
`failed_video_ids`) so the caller has an explicit list to work from
instead of inferring it.

### Bonus fix: the misleading self-reported result dict

Also fixed while rewriting the loop: the task's final `return` used to
be `{**extraction_stats, ...}` — spreading only the **last pass's**
stats, which is exactly the bug the validation run exposed (self-report
said "2 completed / 3 failed" when Neo4j itself showed 11/24 real
successes). Changed the final return to build `completed_video_ids`/
`videos_completed` from `_ordered(completed_global)` — the same
cumulative, DB-seeded set the live progress bar already tracks
correctly — instead of re-deriving it from the last pass alone. An
initial pass at this fix added a redundant `all_completed_ids` list
accumulated via `.extend()`; self-caught on review and removed since
`completed_global` already did that job, and does it more accurately
(seeded from Neo4j's real pre-existing tagged Documents, not just
this run's in-memory passes).

**Status: shipped, `py_compile`-clean on all 5 touched files, NOT yet
redeployed or live-tested on a real batch** — per standing project
convention, skaffold redeploy + live smoke test is a separate,
explicit manual step.

## Researched, deferred: full streaming/per-item pipeline architecture

Separately, a `/sota-search` was run on whether to go further and
convert the whole 4-phase pipeline (Playwright → ES → Qdrant → Neo4j,
today 3 strictly sequential Celery tasks chained via `.si()`) into a
streaming architecture — each video flowing individually into Qdrant-
and Neo4j-consumption as soon as it's extracted, instead of waiting for
the full batch to clear one phase before the next phase starts. Idea as
proposed: keep Playwright's internal 5-way concurrency pool, but let
Qdrant embedding and Neo4j extraction start consuming individual videos
concurrently with each other and with Playwright still fetching the
rest of the batch.

**Key findings from that research, not yet acted on:**
- LangGraph + `AsyncPostgresSaver` checkpointing (DD's Planner/Synth
  resilience mechanism) is a **mismatch** for this shape of problem —
  it's built for durable resumption of a single stateful graph
  traversal, not fan-out over N independent, order-irrelevant items.
  Checkpointing alone also does not provide automatic crash detection/
  recovery — that requires an external task queue/worker pool, which
  Celery already natively provides. YCS's existing idempotent
  skip-checks (ES cache-hit, Neo4j "already exists") are already the
  right-shaped resilience primitive for this problem — same spirit as
  DD's checkpointing, different plumbing appropriate to a smaller
  per-item task shape.
- Per-item Celery task dispatch (one small task per video, `group()`/
  chord fan-out) rather than one monolithic task per phase is the
  Celery-native way to get streaming/pipelined concurrency without
  introducing a new broker.
- Redis-backed size-or-time-windowed micro-batching is the standard
  pattern to preserve Qdrant's 50-chunk batching win in a streaming
  world where chunks arrive one video at a time.

**Why not implemented yet — explicit, reasoned pause, not an
oversight:** the concrete next step (parallelizing Qdrant+Neo4j via a
Celery `group()`/chord inside `pipeline_task/service.py`'s
`dispatch_videos_pipeline()`) requires knowing exactly how Celery
represents task IDs inside a `chain(A, group(B, C), D)` (an implicit
chord) at runtime, so `_phase_ids_from_chain()` can be updated to keep
extracting the right IDs for the FastHTML progress-bar polling. The
official docs' description of this was explicitly hedged ("the exact
attribute structure depends on how Celery internally represents the
chord"), and getting it wrong would silently break the progress UI with
no easy way to verify short of a live dispatch. Given that blast
radius, this was deliberately deferred rather than guessed at — next
action is either a live Celery verification spike, or a dedicated
planning pass, before touching `pipeline_task/service.py`.

The full per-video streaming fan-out (Playwright→ES immediately
triggering per-video Qdrant/Neo4j work) is a larger, separately-deferred
item on top of this — it additionally needs a Redis-backed completion-
counter design for entity-resolution timing (today `run_resolution`
only runs once, after a whole segment), and a full progress-UI redesign
since the current 4-bar system assumes one Celery task ID per phase.

## Shipped: Qdrant + Neo4j now run concurrently (the "bifurcation" from the original proposal)

The deferred item from the previous section — live-verifying Celery's
exact chain-of-chord ID-extraction semantics before touching
`pipeline_task/service.py` — was resolved and acted on.

**Verification method**: a standalone script using `celery.chain(...).
freeze()` against an in-memory Celery app (`broker="memory://"`, no
Redis, no live worker needed — Celery assigns every canvas link's UUID
at freeze/build time, not at dispatch time). Confirmed the exact shape
of `chain(A, group(B, C), D)`:
- the returned `AsyncResult` = D (the callback / last link)
- `.parent` = a `GroupResult` (the chord header) — **not** a plain
  `AsyncResult` like a flat chain produces — whose `.children` are B's
  and C's `AsyncResult`s in the same order passed to `group(...)`
- `.parent.parent` = A's `AsyncResult` (first link, `.parent is None`)

Then double-checked this held for a real dispatch, not just a frozen
canvas: built the identical chain with realistic `.si()` args
(matching `extract_videos`/`ingest_to_qdrant`/`ingest_to_neo4j`/
`invalidate_cache`'s real signatures) and called `.apply_async()`
against the same in-memory broker — the resulting ids matched the
freeze-only ids exactly, confirming the id-extraction logic doesn't
depend on whether a real broker/worker is involved.

**Shipped**, in `pipeline_task/service.py`'s `dispatch_videos_pipeline()`
and `pipeline_task/task.py`'s `full_channel_pipeline()`:

```python
chain_sig = chain(
    extract_videos.si(video_ids, include_transcription, languages),
    group(
        ingest_to_qdrant.si(video_ids),
        ingest_to_neo4j.si(video_ids, NEO4J_BATCH_SIZE),
    ),
    invalidate_cache.si(),
)
```

Qdrant and Neo4j both only ever depended on Phase 1's ES writes, never
on each other — the old flat `chain(extract, qdrant, neo4j,
invalidate)` made Neo4j (the slow, LLM-bound phase) wait out the entire
Qdrant embedding pass first for no real reason. Now Celery dispatches
both the moment Phase 1 finishes, as an implicit chord with
`invalidate_cache` as the callback. `_phase_ids_from_chain()` was
rewritten to walk the group-aware result tree above instead of the old
flat `while cur.parent` loop.

**Confirmed this produces REAL concurrency, not just reordered
dispatch**: checked `k8s/helm/values.yaml` — the Celery worker runs
`concurrency: 2` (2 prefork slots, deliberately capped there per an
existing comment about OOM risk at higher concurrency), and
`infra/celery/params.py`'s `TASK_ROUTES` puts every `domains.ycs.*`
task on one shared queue consumed by that same worker pool. With 2
slots available, `ingest_to_qdrant` and `ingest_to_neo4j` genuinely run
in two different worker processes at once (subject to normal queueing
if another task already holds a slot) — not merely "dispatched
together but still serialized."

**No FastHTML/JS changes needed.** Read `pipeline_panel.js` end to end
looking for any sequential-order assumption between bars (e.g. "don't
poll Neo4j until Qdrant shows SUCCESS," or an elapsed-time / drawer-
table calculation that presumes one phase gates the next) — found
none. Every bar (`playwright`/`elasticsearch`/`qdrant`/`neo4j`) is
already polled independently every tick via its own task id, and its
percentage/label/hint are derived purely from that task's own
`/admin/task/{id}` response — the panel was already built to render N
independent, concurrently-progressing bars; it just never had a reason
to actually exercise that until now. `_phase_ids_from_chain()`'s
output is consumed exclusively by key (`.get("extract"/"qdrant"/
"neo4j"/"invalidate")`) everywhere in `router.py` (4 dispatch
endpoints + stop + wipe) and `pipeline_panel.js` — never by list
position — so the dict-shape change is fully backward compatible.

**One real behavioral nuance, unchanged from before (not a
regression)**: if the user hits Stop while Qdrant and Neo4j are both
mid-flight, `invalidate_cache` (the chord callback) will not run —
same as the old design, where revoking any in-chain link already
stopped the chain before reaching `invalidate_cache`. Not new; noted
for completeness.

**Status: shipped, `py_compile`-clean, the id-extraction logic verified
against real `.si()`-shaped signatures via a live (in-memory-broker)
Celery dispatch — not yet redeployed or smoke-tested against the real
k8s cluster.**

## Deferred: full per-video streaming fan-out (Playwright → per-video Qdrant/Neo4j)

What's now shipped closes the "bifurcation" the original proposal
asked for at the phase level (Qdrant ∥ Neo4j). What remains is the
finer-grained ask: starting Neo4j extraction on video 1 while
Playwright is still fetching video 20, instead of waiting for the
entire batch to clear Phase 1 first.

**Still deliberately not attempted**, for the same reason recorded
earlier: it requires rewriting `extract_videos`'s Playwright/yt-dlp
engine from "one task, internal 5-way concurrency pool over the whole
batch" into "one Celery task per video, fanned out via `group()`" —
and this exact codepath was the subject of this session's earlier
concurrency-vs-timeout misdiagnosis saga (see `YCS-PLAYWRIGHT-
PERFORMANCE-2026-09-13.md` and the top of this doc). Rewriting its
dispatch shape again, immediately after that, without a dedicated
design pass, is a real regression risk — not a guess to make casually.
It also has two real unresolved sub-problems, not just an implementation
detail:
- **Qdrant's batching would need to move to Redis.** Today's
  `ingest_to_qdrant()` batches ~50 chunks in an in-memory buffer
  across ALL videos in one task invocation (5.4× embed-latency win —
  see `ingestion/service.py`'s `_flush()`). If videos arrive one Celery
  task at a time instead, that buffer has nowhere to live between
  separate task invocations except Redis — a genuinely new piece of
  shared state, not a refactor of existing code.
- **Entity resolution timing changes.** `resolve_entities()` today
  runs exactly once, after `ingest_to_neo4j`'s entire retry-pass loop
  finishes. Per-video fan-out breaks that single well-defined "after
  all extraction" moment; it would need a completion-counter (Redis-
  backed) to know when the LAST video of a batch has landed before
  triggering resolution once.

Both were already flagged as needing a dedicated planning pass before
this session started (see `[[project_ycs_qdrant_packing_2026_06_10]]`-
style prior art); nothing found this session removes that need.
Recommended next action, when picked back up: design the Redis
completion-counter + micro-batch buffer FIRST as a standalone spec,
then implement the per-video fan-out against that design — not the
other way around.

## Current state of the tunables

- `domains/ycs/graph_builder/params.py`: `EXTRACT_CONCURRENCY = 5`
  (env override `YCS_NEO4J_CONCURRENCY`). `MAX_CONSECUTIVE_NONPRODUCTIVE`
  removed (circuit breaker deleted).
- `domains/llm/rotator/chain/service.py`:
  `build_ycs_neo4j_pinned_chain()` → `_build_chat_openai(timeout_s =
  400.0)`, `max_retries=0`. `pick_ycs_neo4j_deployment_bandit` /
  `record_ycs_neo4j_reward` / `release_ycs_provider_slot` removed
  (confirmed no-op shims — bandit lives server-side in COELHO LLM
  Rotator now).
- `neo4j_task/task.py`: `MAX_ARM_SWAPS` renamed `MAX_RETRY_PASSES = 3`,
  now drives a genuine retry-failed-only loop instead of an arm-swap
  that never changed targets. Final result dict now built from
  `_ordered(completed_global)` (cumulative, DB-seeded) instead of
  spreading the last pass's `extraction_stats`.
- `pipeline_task/service.py` / `pipeline_task/task.py`: Qdrant+Neo4j
  now dispatched via `group()` (implicit chord, `invalidate_cache` as
  callback) instead of a flat sequential chain — SHIPPED, see above.
- Next open item: the full per-video streaming fan-out (deferred, see
  above) — not a tunable, a design decision pending a dedicated
  planning pass (Redis micro-batch buffer + completion-counter design
  first).
