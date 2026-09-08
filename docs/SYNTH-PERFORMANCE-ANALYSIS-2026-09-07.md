# Synth — why chapters are now successful but slow (2026-09-07)

Context: issues #17 (per-call timeouts raised from a bare 30s default to
90-150s depending on node) and #18 (`digest_construct` concurrency
24→16) in `SYNTH-KNOWN-ISSUES-2026-09-05.md` fixed a chapter-failure
epidemic — chapters now reliably complete with high-quality, complete
output instead of shipping near-empty `needs_review` chapters. The
tradeoff: chapters that used to fail fast now succeed slowly. This doc
captures every factor behind that slowness, so future tuning work
starts from a full picture instead of one symptom.

## Measured evidence

All three runs used the same post-#17/#18 code, on the `claude-code`
corpus, via the single-chapter escape hatch (`POST
/api/v1/docs-distiller/synth/{slug}?chapter_id=<id>&mode=quality`)
with the chapter's MinIO cache cleared first (so every run is a
genuine from-scratch computation, not a cache hit).

| Run | Chapter | Total time | Iterations | Result |
|---|---|---|---|---|
| 1 | `ch-01-installation-and-authentication` | 42m35s (2555.6s) | 2 | `HALT success`, 100% pass, audit passed |
| 2 | `ch-02-cli-commands-and-session-control` | 26m45s (1605.2s) | 2 | `HALT success`, 100% pass, audit passed |
| 3 | `ch-01-installation-and-authentication` (rerun, inside a full study) | 55m13s | 2 | `HALT sustained-outage` + best-seen-rescue, audit passed |

The headline finding is run 1 vs run 3: **identical chapter, identical
code, 42m35s vs 55m13s.** That 30% swing on a controlled rerun is the
single strongest piece of evidence that timing is not fully under
Synth's control — see the root cause at the bottom.

## What's making it slow, ranked by impact

### 1. Every failed call now costs 90-150s before the bandit tries the next candidate

The deliberate, unavoidable core of issue #17's tradeoff. Before the
fix, a stuck or slow candidate was abandoned after 30s; now it's given
90-150s (scaled to the call's `max_tokens`) before the bandit moves on.
This is the multiplier behind nearly every other item below — it's not
a bug, it's the necessary cost of not cutting off genuinely-slow-but-
working completions early (which is what caused the original failure
epidemic).

### 2. `checklist_eval`'s bundled judge timing out wastes a whole extra `sawc_write` iteration

Observed directly in both `ch-01` runs. When the bundled judge call
itself fails (times out), all 5 LLM-scored criteria default to fail
regardless of actual content quality — `pre=9/9` (perfect structural
score) paired with `llm=0/5` is the signature. That drags the overall
`pass_rate` below the 0.80 threshold and triggers `RETHINK`: a full
re-run of `sawc_write`, which alone costs 12-20+ minutes. In run 1's
first attempt, iteration 2 confirmed the content was already fine —
the judge just needed a working call to see it. The entire second
iteration was spent re-deriving output that didn't need to change.

### 3. `checklist_eval` bundles three separate judge calls per iteration, each an independent timeout risk

The bundled judge, CoCoA alignment check, and atomic-claim grounding
are three separate LLM calls per `checklist_eval` invocation. CoCoA and
atomic-claim are fail-soft by design (`"bundled judge stands"` — a
timeout there never overrides or downgrades the bundled judge's own
score), so their failing doesn't necessarily change the outcome. But
Synth still *waits* for them to time out before finalizing — 60-120s
each — even on iterations where the eventual answer doesn't depend on
them. Confirmed timing out in every single `checklist_eval` call
observed across all three runs, pass or fail.

### 4. Candidate cascading within one logical call

`chat_judge_bandit_async` tries a ranked list of candidate deployments
sequentially, not just once. Directly measured on `outline_sdp`: two
back-to-back 120s timeouts before a third candidate finally succeeded
— one "call" cost ~240s in failed attempts before its real ~15s of
useful work. This compounds #1: the same fix that lets a slow-but-good
candidate finish also lets a genuinely bad one burn its full timeout
multiple times in a row before the cascade exhausts it.

### 5. The RETHINK loop can repeat up to `MAX_REFINE_ITER=5` times

Every repeat re-runs the two most expensive nodes in full —
`sawc_write` and `checklist_eval`. Any of #1-#4 landing on a bad
iteration means paying that cost again, times however many iterations
it takes to converge (or to exhaust the budget/hit sustained-outage).
Both measured `ch-01` runs needed 2 iterations; run 3's own iteration 2
alone took 20m44s — the longest single iteration observed in this
investigation.

### 6. `digest_construct`'s two-wave retry is strictly additive

The second wave (retrying only the sources that failed the first wave)
only starts after the first wave fully completes — never overlapped.
Each wave is independently exposed to #1 and #4. In run 3, digest alone
cost 457s (7.6 min) across both waves.

### 7. `outline_sdp`'s own repair loop (up to 2 attempts) is exposed the same way

Measured taking 170-245s across the three runs on its own, purely from
repair-cycle timeouts when the initial 3-sample draft doesn't parse
cleanly.

### 8. Nothing overlaps, by design

Nodes within a chapter run strictly sequentially
(`outline_sdp → digest_construct → sawc_write → sawc_derive →
checklist_eval → mgsr_replan → render_audit_write`) — no node starts
before the previous one finishes. Chapters within a study run also
never overlap (single Celery worker, one chapter at a time). This
second point is deliberate and **should not be changed**: the shared
Rotator's total cross-provider capacity is only ~19 concurrent slots
(`_PROVIDER_CAPS` summed), and a single chapter's own `digest_construct`
(16 concurrent) or `sawc_write` (8 concurrent) calls already consume a
large fraction of that. Running multiple chapters concurrently would
oversubscribe the same shared bottleneck and very likely produce *more*
timeouts, not fewer — this was evaluated and rejected earlier in this
investigation.

## Root cause underneath all of it

Genuine, irreducible variance in the still-young standalone Rotator
microservice's real-time provider availability. The Rotator (this
session's own home repo) has existed only since **2026-09-04** — three
days before this whole investigation began — replacing what had been a
mature, in-process, multi-provider bandit router inside COELHONexus
itself since around July. The 42m35s → 55m13s swing on an identical
chapter and identical code is direct, controlled evidence that no
amount of Synth-side tuning fully controls this: some of it is real
external unreliability that only stabilizes as the Rotator itself
matures, or as its actual per-model (not per-provider-string) capacity
limits get corrected — both out of Synth's scope by design (see
`SYNTH-KNOWN-ISSUES-2026-09-05.md` issue #17 and #18's Planner-vs-Synth
comparison for the full architecture history).

## What was evaluated and deliberately not done

- **Raising `checklist_eval`'s judge timeout further.** Would reduce
  some judge-call failures (item #2 above) but directly extends the
  worst-case cost of the failures it *doesn't* fix (item #4 shows some
  failures are genuine unavailability, not "almost done"). Not pursued
  without more data — 1 wasted iteration out of 2 chapters tested is
  too small a sample to justify the tradeoff either way.
- **Parallelizing chapters within a study.** Rejected — see item #8.
  The shared Rotator capacity is the binding constraint, and Synth has
  no way to change how the Rotator gates concurrent calls from outside.
- **A hard per-chapter wall-clock cap.** Attempted, then fully reverted
  same day (see issue #18's revision history) — it traded completeness/
  quality for a time target, which doesn't match the actual goal
  (highest quality possible, 20 minutes as an aspiration, not an
  enforced ceiling).

## Full architecture review + SOTA comparison (2026-09-07)

Prompted by: analyze the entire Synth structure, check it against
current (Sept 2026) research, find dead processes wasting time, and
find genuinely fixable bottlenecks.

### The pipeline

```
outline_sdp → digest_construct → sawc_write → sawc_derive → checklist_eval → mgsr_replan
                                       ↑                                          │
                                       └──────────── RETHINK (loop) ─────────────┘
                                                                                   │
                                                                          render_audit_write
```

Plus a study-level `book_harmonize` pass across all `done` chapters,
after every chapter finishes.

### Is it SOTA?

Mixed. **Aligned:** the dual deterministic-"pregate" + LLM-judged
criteria split (with `best_seen_pregate` breaking ties deterministically,
issue #12) matches what current research recommends. [*Evaluation
Ability Does Not Imply Optimization Utility*, arXiv:2607.13347](https://arxiv.org/abs/2607.13347)
(Jul 2026) found that "iterative refinement requires, at minimum, a
verification signal that deterministically detects structural change,
rather than judge scores alone" — pure judge-score optimization raised
judge pass rates while real accuracy stagnated. Synth already avoids
that trap.

**Not aligned:** a 2026 survey on self-corrective RAG architecture
states that effective systems have "multiple independent feedback
loops... route each to the earliest upstream node that can actually fix
it," and warns that "a system that routes all three of these to a
single 'try harder' self-correction step wastes retries on unfixable
problems" ([SoK: Agentic RAG, arXiv:2603.07379](https://arxiv.org/pdf/2603.07379)).
`mgsr_replan` exists specifically to do this root-cause diagnosis (see
issue #19) — but until today its output was discarded, and every
RETHINK routed to the same single "try harder" step (a full
`sawc_write` re-draft) regardless of what was actually wrong. That's
the exact anti-pattern named. Also relevant: [*Two Calls Beat Five
Agents*, arXiv:2607.26922](https://arxiv.org/html/2607.26922v1) (Jul
2026) found leaner pipelines frequently outperform heavier multi-agent
ones on the same task, and that self-refinement "should be applied
selectively" — Synth doesn't currently distinguish "needs deep rework"
from "needs one targeted fix," treating every non-passing iteration
identically (the "v2 loop" in issue #9/#19 is exactly the missing
distinction).

### Dead process, confirmed and fixed: issue #19

See `SYNTH-KNOWN-ISSUES-2026-09-05.md` issue #19 for the full writeup.
Summary: `mgsr_replan`'s real LLM call (up to 90s x 2 attempts, observed
as long as 3.5 minutes) fired on every non-trivial-pass iteration and
produced targeted corrective `actions` that **zero code anywhere reads**
— confirmed by tracing every reference to `mgsr_stats` in the codebase.
Fixed by skipping the call entirely and reusing the existing
`fallback_decision` path (already production-tolerated when the real
call fails) — behavior-identical, zero LLM cost.

### Other candidates checked, not actioned

- **`sawc_derive`** — has logged `0/0 promoted` in every single chapter
  observed this entire investigation (its trigger condition, a
  signature-only "thin" code block, apparently never matches this
  prose-heavy CLI-docs corpus). Not flagged as waste: it short-circuits
  to ~30-300ms when there's nothing to derive, negligible against
  25-55 minute chapters. Likely corpus-specific rather than broken —
  worth re-checking against an API-reference-style corpus later.
- **CoCoA + atomic-claim grounding** (`checklist_eval`) — timed out in
  every single call observed across all three measured runs. Fail-soft
  by design (never override the bundled judge), so not incorrect, but
  under current Rotator conditions they're rarely completing at all —
  closer to pure overhead than the "external verification" they're
  meant to provide. **Flagged for a decision, not yet actioned** —
  disabling them removes an existing (if currently weak) safety net,
  a different risk profile than deleting confirmed-dead code.

### Answering directly: would raising `checklist_eval`'s timeout stop it from triggering more than once?

No, not primarily. RETHINK triggers for two different reasons that look
identical in the logs: (a) the bundled judge itself timing out, zeroing
all LLM criteria regardless of actual content (confirmed: `ch-01`'s
wasted iteration) — more timeout headroom helps this, with the same
diminishing-returns tradeoff discussed under issue #17; (b) genuine
content gaps the judge correctly identifies (confirmed: `ch-02`'s
bundled judge completed both times and scored differently, 2/5 → 5/5)
— no timeout change affects this, and you don't want it to, since the
RETHINK is doing real work. Issue #19 is the actual lever for "make
each RETHINK cheaper" — it doesn't reduce how often RETHINK fires, but
it removes a large, confirmed-wasted cost from every time it does.
