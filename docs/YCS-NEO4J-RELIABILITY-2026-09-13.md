# YCS Neo4j graph-build — LLM client fix, a same-day regression, and a real structural gap (2026-09-13)

Context: following the Playwright speed pass (see
`YCS-PLAYWRIGHT-PERFORMANCE-2026-09-13.md`), attention turned to Neo4j —
the slowest of the four ingestion phases, running sequentially against
COELHO LLM Rotator. This doc covers the architecture question that
started it, the client-config fix that came out of it, a same-day
regression from over-tightening a timeout, and a real structural bug in
the circuit-breaker/arm-swap logic that's still open. **We're returning
to this topic soon** — the structural fix at the bottom is the next
thing to decide on.

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

## Open structural bug — why real provider instability still causes permanent gaps

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
dispatch order. The moment 3 consecutive extractions fail, `break`
exits the loop — any other tasks in `pool` that hadn't finished yet
(running or not yet started, gated by the concurrency semaphore) are
simply dropped from that segment's accounting. They're not lost
forever — `neo4j_task/task.py`'s next segment re-includes them via a
real DB-state check ("N videos already in Neo4j; skip"), not via the
misleadingly-named `residual` list (which is actually just
`failed_video_ids`, populated only by videos that were awaited and
returned an error — never the silently-abandoned ones). But there are
only `MAX_ARM_SWAPS=3` segments total (4 attempts), and each segment's
breaker tends to trip after only ~5-15% of its pool gets attempted when
the provider is genuinely degraded. A sustained bad patch — like
tonight's — can burn the entire swap budget while a third or more of
the video pool never gets a single real attempt.

**This bug is independent of everything else in this doc** — it would
happen at any concurrency, any timeout value, the moment a provider has
a sustained rough patch lasting more than ~3 consecutive calls. Two
fixes discussed, neither applied yet:
- Raise `MAX_ARM_SWAPS` so more segments are available to work through
  a large pool despite frequent trips (cheap, doesn't fix the
  abandonment itself, just gives more chances).
- Fix the abandonment directly: track exactly which video IDs were
  dispatched-but-never-completed when the breaker trips, and feed that
  set explicitly into the next segment's pool (or retry them within the
  same arm after a cooldown) instead of relying on the DB-state
  skip-check to implicitly rediscover them.

## Current state of the tunables

- `domains/ycs/graph_builder/params.py`: `EXTRACT_CONCURRENCY = 5`
  (env override `YCS_NEO4J_CONCURRENCY`).
- `domains/llm/rotator/chain/service.py`:
  `build_ycs_neo4j_pinned_chain()` → `_build_chat_openai(timeout_s =
  400.0)`, `max_retries=0`.
- `MAX_ARM_SWAPS` (in `neo4j_task/task.py`) — unchanged at 3, this is
  the next thing to revisit.
