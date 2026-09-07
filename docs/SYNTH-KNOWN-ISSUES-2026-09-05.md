# Synth — known issues from the claude-code study run (2026-09-05)

Found by direct log/artifact inspection of all 11 chapters of the
`claude-code` study run
(`docs-distiller/study/claude-code/59353d70-e1ef-4d06-9708-947b1273017f`),
now complete.

## ✅ 17. Nearly every Synth LLM call used a bare 30s timeout — the likely dominant cause of "too many chapters fail" — FIXED 2026-09-07

**Severity: high — plausibly the single biggest lever on chapter quality across all 5 study runs.**

Prompted directly by the user asking why so many chapters keep coming
back `needs_review`. Every prior run in this doc treated `APITimeoutError`
as ambient, unfixable-from-Synth's-side Rotator/provider capacity —
issue #4's framing. That framing was incomplete: `chat_judge_bandit_async`
(the Rotator function nearly every Synth node calls) has a **default
`timeout_s` of 30.0 seconds**, passed straight through to
`litellm.acompletion(timeout=...)` as a hard per-call deadline. Audited
every call site across `outline_sdp`, `digest_construct`, `sawc_write`,
`sawc_derive`, `checklist_eval` (+ CoCoA + atomic-claim grounding),
`mgsr_replan`, and `book_harmonize` — **only `render/service.py`
overrode it (to 60s); every other call site relied on the bare 30s
default.** `sawc_derive/params.py` even had an unused `REQUEST_TIMEOUT_S
= 60.0` constant sitting right next to the call sites that needed it,
defined but never actually imported or passed — dead config, same class
of bug in a third form.

This is suspicious on its own terms: the Rotator's own deployment
entries give several of its heaviest models (NIM-hosted 120b+/675b
models — `glm-5.1`, `minimax-m2.7`, `kimi-k2.6`, `nemotron-3-super-120b`,
etc.) individually-configured **90-120s** timeouts elsewhere in the same
Rotator codebase — meaning the codebase already "knows" these models
need that much headroom. Synth's bandit-routed calls never inherited
that judgment. A flat 30s ceiling very plausibly cut off completions
that would have succeeded given 30-60 more seconds — especially
`sawc_write`'s drafting calls (writing a full section is the single
heaviest generation task in the pipeline, and by far the most
consistently timeout-hit call across every chapter in every run this
week).

**Fixed:** added an explicit `timeout_s` override at every Synth call
site that lacked one, scoped per-call to its `max_tokens` (45s for small
outputs like votes/critics, up to 120-150s for the heaviest generation
calls like `sawc_write`'s drafts and `book_harmonize`'s chapter patch) —
same pattern `render/service.py` already established. Wired up
`sawc_derive`'s orphaned `REQUEST_TIMEOUT_S` constant. Scoped to Synth's
own call sites only, not a change to `chat_judge_bandit_async`'s shared
default, since other Rotator consumers may depend on the current 30s
fast-fail behavior for their own reasons.

**Validated live, 2026-09-07** via the single-chapter escape hatch on a
cache-cleared `ch-01`: `HALT success` at **100% pass rate**, `audit_passed
= True`, 23/23 code refs resolved — the best result this chapter has
ever produced across 5+ prior attempts, every one of which ended
`needs_review` near-empty. The fix works. Cost: 42m35s for one chapter
(2 full `sawc_write` iterations, no premature cutoffs) — which
surfaced issue #18 below.

---

## ✅ 18. Genuine per-chapter speed levers, without trading away quality — 20 min is an aspirational target, not an enforced cap — FIXED 2026-09-07

**Severity: high — one chapter took 42m35s; a full 11-chapter study could run for hours.**

Direct consequence of #17: raising `chat_judge_bandit_async`'s per-call
timeouts (30s → 90-150s depending on node) fixed the false-timeout
epidemic, confirmed live — but those short timeouts had also been
acting as an *unintentional* ceiling on total chapter time. The
validation run for #17 (`ch-01`, cache cleared) took 42m35s for a
single chapter.

**First attempt, reverted same day:** a `chapter_deadline_ts` /
`CHAPTER_TIME_BUDGET_S` (20 min) mechanism that skipped `outline_sdp`'s
repair attempt, `digest_construct`'s second retry wave, and forced an
early `HALT time-budget` in `_route_after_mgsr` once 20 minutes had
elapsed. **Explicitly corrected on user feedback**: the actual goal is
"highest quality possible, under 20 minutes *if possible*, lowest time
possible" — not a forced cap that trades completeness for a clock. All
three skip points and the new halt reason were fully reverted
(`chapter_deadline_ts`/`CHAPTER_TIME_BUDGET_S` removed from
`state.py`/`params.py`/`graph.py`/`outline/service.py`/
`digest/service.py` — no orphaned references left behind). 20 minutes
remains the *target* to work toward via genuine speed improvements and
manual per-chapter timing (the single-chapter escape hatch + reading
Celery's own task-duration log), not something Synth enforces on itself.

**What actually shipped — a genuine, no-quality-tradeoff speed fix,
root-caused via the old-commit-vs-current comparison** (`4eac11a`, July
5, vs current): the entire LLM-serving substrate under Synth was
swapped out on **Sept 4** (`36d435a`, three days before this whole
investigation started) — from an in-process, ~2000-line multi-provider
bandit router to a thin HTTP adapter calling a brand-new,
still-actively-iterated standalone Rotator microservice (this session's
own home repo, 15 commits, all since Sept 4). Per-provider caps
(`_PROVIDER_CAPS`, ~19 total concurrent slots across every provider
combined) existed at the July commit too — not new — but Planner's own
call sites never exceed 16 concurrent per node, while
`digest_construct._CONCURRENCY = 24` was the one value in either
codebase exceeding total system capacity on its own, guaranteeing
self-inflicted queueing regardless of anything else running. **Lowered
to 16**, matching Planner's own ceiling — this reduces contention (and
therefore the frequency of timeout-retry cascades) with zero cost to
completeness or quality, unlike the reverted mechanism above.

**Going forward:** per the user's stated approach, further speed work
should be measured manually (single-chapter runs + Celery's own
`succeeded in Ns` log, exactly as done for `ch-01`'s 42m35s
measurement) and pursued only via genuine efficiency levers — reducing
real contention/waste — never via skipping work that would improve the
outcome.

---

| # | Fix | Files |
|---|---|---|
| **18** | Reverted a first-attempt 20-min forced cap (skipped repairs/retries/iterations) per user correction — 20 min is a target, not an enforced ceiling. What actually shipped: `digest_construct._CONCURRENCY` lowered 24→16, matching Planner's own ceiling and no longer exceeding the Rotator's total ~19-slot cross-provider capacity on its own — a genuine contention reduction with zero quality tradeoff. | `digest/service.py` |
| **17** | Added explicit `timeout_s` overrides (scaled to each call's `max_tokens`, 45-150s) at every Synth `chat_judge_bandit_async`/`_call_with_retry` call site that relied on the bare 30s default — `outline_sdp`, `digest_construct`, `sawc_write`, `sawc_derive` (wired up an orphaned unused constant), `checklist_eval`, CoCoA, atomic-claim grounding, `mgsr_replan`, `book_harmonize`. | `outline/service.py`, `digest/service.py`, `sawc/service.py`, `sawc_derive/service.py`, `checklist/service.py`, `checklist/cocoa.py`, `checklist/faithfulness.py`, `mgsr/service.py`, `mgsr/params.py`, `book_harmonize/service.py`, `book_harmonize/params.py` |

## 🆕 Fifth study run, 2026-09-06/07 (`38282f1e-...`) — book_harmonize's real root cause finally visible

Retrieved entirely via **Loki** (`{namespace="coelhonexus-dev", container="coelhonexus-celery"}` over the run's time range), not `kubectl logs` — confirmed durable past pod restarts, see the Loki-retention memory note. Ran overnight, 00:19-02:43 UTC, 9,500s total. **3/11 done** (5, 9, 10), **8/11 needs_review** — worse completion rate than run 4 (4/11), consistent with this being ambient variance, not a regression (same code, same corpus, no new failures).

**Issue #11 — cleanest confirmation yet, a genuine 4-way comparison.**
Chapter 9 ran 4 iterations: 71% → **79%** → 64% → 29% (`HALT sustained-outage`). The winner is iteration 2 — neither the first nor the last — the first time this session a *middle* iteration was the true best. Checkpoint confirms `best_seen_score = 0.7857` (iteration 2's exact score) with a matching `sawc_manifest_hash`, correctly surviving two subsequent *worse* iterations without being overwritten. This is a strictly harder test than any prior confirmation (which were all 2-iteration accept/reject/tie cases) and it passed cleanly. Issue #11 is about as thoroughly verified as this doc can make it.

**Issue #15's logging fix (round 2) just paid for itself — the real root causes of `book_harmonize`'s failures are now directly visible, and they're not what round 1 assumed.** `book_harmonize` again extracted 0 claims from all 3 `done` chapters (`skipped: "no_claims_extracted"`), but this time the `raw[:200]` snippet fix shows exactly why:

- **`ch-05` — the model's entire token budget went to restating the task, never reaching JSON.** Raw response (3,659 chars, right at `EXTRACT_MAX_TOKENS=1000`'s rough token ceiling): `"We need to extract atomic factual claims (single verifiable assertions about the technology) from the chapter. Cap at 20. Skip motivational/structural/transitional sentences. Also extract key terminol"` — cut off mid-word, **no `{` anywhere**. This isn't a malformed-JSON problem at all — some model in the Rotator's pool is echoing/reasoning through the instructions as its primary output channel, and 1000 tokens isn't enough for both that preamble *and* a 20-claim JSON payload. No repair library fixes a response that never contains JSON.
- **`ch-10` — a genuinely new malformation shape: a duplicated leading brace.** Raw response: `'{\n{"claims": ["The environment variable CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 enables...'` — note the `{` then a bare newline then a *second* `{` starting the real object. Round 2's brace-depth-aware scanner (correctly) still failed here: starting from the first `{`, depth never returns to 0 because that outer brace has no matching close. Verified the fix directly: retrying `_extract_first_json_object` from the position just after the first `{` (i.e., trying the *second* one) recovers the object cleanly and it parses as valid JSON with real claims. This is a distinct, previously-unseen-in-this-doc malformation pattern — not single quotes, not trailing commas, not a greedy-regex overreach — an actual extra brace the model itself emitted.
- **`ch-09` — plain `APITimeoutError`.** Ambient, already tracked under #4, not new.

**Not fixed this turn — this was an assess-only request, not a fix request.** Two concrete, evidence-backed directions for whenever code changes are next authorized: (1) either raise `EXTRACT_MAX_TOKENS` well past 1000 or reinforce the prompt to suppress reasoning/preamble output (a known mitigation for models that bleed chain-of-thought into the primary answer channel) — `ch-05`'s case is a token-budget problem, not a parsing problem, and no amount of repair logic touches it; (2) make `_extract_first_json_object` retry from each subsequent `{` position in the text when the first attempt fails to close, not just the first one — verified standalone that this recovers `ch-10`'s exact malformation shape. Both are refinements to #15's underlying mechanism, filed as **#16** below since they're genuinely new failure modes, only now diagnosable because of round 2's own logging fix.

## 📊 Fourth study run, 2026-09-06 (`e4ffbee9-...`) — verifying #14/#15 live

Fresh pods (16m old at time of check). **Chapter 1: code freshness
confirmed immediately** — `checklist_stats.prompt_version =
"v5-small-sample-infra-floor-2026-09-06"` (#14's fix) is live on the
very first chapter.

Chapter 1 itself: severe ambient Rotator degradation from the start
(`outline_sdp` 2 repair timeouts + HARD-TRIM 8→3; `digest_construct`
8/15 digested with `APITimeoutError×7`; `sawc_write` **0/1 sections
both iterations**, exact tie at 29% (`pre=4/9, llm=0/5` both times) →
`HALT sustained-outage`). Chapter ships fully empty (294 bytes, 0
subtopics) — same severity class as run 3's chapter 11, not a new
issue.

This chapter doesn't specifically exercise #14's fix — `judge_call_
failed=True` and `sawc_writer_degraded=True` (2 genuine
`APITimeoutError`s) are independently sufficient to set
`infra_degraded=True` on their own, so the small-sample-fraction branch
never needed to fire either way. It does, however, confirm the
exact-tie edge case behaves correctly: iteration 2 tied iteration 1
exactly (`pass_rate` and `n_pregate_passed` both identical), and
`best_seen_score`/`best_seen_pregate`/`best_seen_sawc_path` all stayed
consistent with a single winner rather than churning — the strict `>`
comparison correctly does not "promote" on an exact tie. No practical
difference in outcome either way (both iterations are equally empty).

No new issues on chapter 1. #15 needed `book_harmonize` to actually run
(end of study) to verify live — the full run is now complete; see below.

**Chapters 2-11, full run assessed:**

| Ch | outline | digest | iter1 | iter2 | halt | best-seen | final |
|---|---|---|---|---|---|---|---|
| 2 | clean | 8/16 (50%) | 0/4, 29% | 2/4, 43% (better, last) | sustained-outage | ✓ iter2 shipped | needs_review, 8/8 refs |
| 3 | trim | 6/15 (40%) | 0/1, 43% | 0/1, 43% (exact tie) | sustained-outage | tie stable | needs_review, empty |
| 4 | trim | 4/8 (50%) | 2/2, 0 fail, 64% | 0/2, 43% (worse, last) | sustained-outage | ✓ **rejected** worse iter2, kept iter1 | done, audit_passed |
| 5 | trim | 6/11 (55%) | 2/2, 0 fail, **100%** | — (`HALT success`) | success | n/a | done, perfect |
| 6 | trim | 7/11 (64%) | 1/2, 57% | 0/2, 36% (worse, last) | sustained-outage | ✓ rejected worse iter2, kept iter1 | needs_review |
| 7 | clean | 5/12 (42%) | 0/2, 29% | 2/2, 0 fail, 64% (better, last) | sustained-outage | ✓ iter2 shipped | done, audit_passed |
| 8 | trim | 11/19 (58%) | 0/3, 29% | 1/3, 43% (better, last) | sustained-outage | ✓ iter2 shipped | needs_review |
| 9 | trim | 4/8 (50%) | 0/2, 29% | 1/2, 64% (better, last) | sustained-outage | ✓ iter2 shipped | needs_review |
| 10 | trim | 5/9 (56%) | 0/1, 29% | 1/1, 64% (better, last) | sustained-outage | ✓ iter2 shipped | done, audit_passed |
| 11 | trim | 4/10 (40%) | 1/2, 50% | 1/2, 50% (exact tie, last) | sustained-outage | tie stable | needs_review |

Every `best_seen_sawc_path` hash was checked against the checkpoint state
and matches the shipped `render-latest.json`'s `sawc_manifest_hash`
byte-for-byte, no exceptions. **Issue #11 now has 12 clean confirmations
across two full study runs** — accept-better-last (2, 7, 8, 9, 10),
reject-worse-last (4, 6, and run 3's chapter 11), and exact-tie-stable
(1, 3, 11) all covered with zero misses. No further verification needed;
this is closed with high confidence.

Final: 4/11 `done` (4, 5, 7, 10), 7/11 `needs_review` (1, 2, 3, 6, 8, 9,
11) — same 4-good/7-degraded shape as run 3, consistent with ambient
Rotator load rather than a run-specific regression.

**`book_harmonize`: issue #15's fix is live but insufficient — still
0 claims extracted, `skipped: "no_claims_extracted"` again.** The new
`json_repair` fallback is confirmed running (log wording
`"(json.loads and json_repair both failed)"` is this session's own new
message), but of the 4 `done` chapters (4, 5, 7, 10):
- `ch-07`: failed — response was only **92 characters**, too short to
  plausibly hold real claims/terms JSON; `json_repair` correctly gave up
  on it because there's likely nothing recoverable in 92 chars, not
  because the repair layer itself is weak.
- `ch-10`: failed — response was **4,692 characters** (a substantial,
  plausible response) and `json_repair` *still* couldn't recover it.
  This is the more interesting failure: a large response defeating both
  parsers points at `_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)` itself
  — it's greedy across the *entire* response, so if the model's claims
  text contains any stray `{`/`}` (a code snippet or JSON example inside
  a claim's own text is very plausible for this content), the regex
  spans from the first real `{` to a much later, unrelated `}` and hands
  both parsers a malformed hybrid neither can fix. Not confirmed without
  the raw text (not currently logged past its length), but a greedy,
  DOTALL, whole-response regex is inherently exposed to this failure
  mode regardless of how good the repair step is downstream.
- `ch-04`, `ch-05`: **no log line at all**, same silent-outcome shape as
  run 3's `ch-03` — except this time the "silent branch" (`if not m`)
  really shouldn't be silent anymore, since that fix shipped. Two
  readings: either the JSON parsed validly but under a shape my fix
  didn't anticipate (e.g. missing `"claims"`/`"terms"` keys entirely, so
  `.get("claims", [])` legitimately and silently returns `[]` with no
  error raised at all — a schema-shape mismatch, not a parse failure),
  or something upstream (`_call_with_retry`) is swallowing a case my
  instrumentation doesn't cover. Real technical prose from two
  substantial, `audit_passed=True` chapters producing genuinely zero
  atomic claims is implausible, so this reads as a second, distinct gap
  from the one issue #15 fixed, not evidence #15 didn't work.

**Not fixed this turn — flagging for whenever code changes are next
authorized.** Two concrete follow-ups: (1) log a `raw[:200]` snippet
(not just `len(raw)`) on every extraction failure/anomaly, so a case
like `ch-10`'s can actually be diagnosed instead of just detected; (2)
make `_JSON_RE` non-greedy or brace-depth-aware instead of
`\{.*\}` DOTALL, since a whole-response greedy match is structurally
exposed to exactly this failure whenever claim text itself contains
braces. Both are refinements to #15, not a new issue number — the fix
that shipped is real and confirmed running, just not sufficient to
reach 100% recovery on its own.

## ✅ Third study run, 2026-09-06 (`37bf66a1-...`) — code freshness confirmed for #10/#11/#12/#13

Fresh pods (33m old at time of check), fresh study run, and this time
**directly verified via the LangGraph checkpoint state** that all four
pending fixes are genuinely active — not just present on disk:
`sawc_stats.prompt_version = "v12-rethink-feedback-loop-2026-09-05"`,
`digest_stats.prompt_version = "v6-contribution-source-key-2026-09-05"`,
`chapter_stats.template_version = "v5-fence-info-sanitize-2026-09-06"`
all match exactly, and `checklist_stats.sawc_writer_degraded` (#10) and
`best_seen_pregate` (#12) — fields that didn't exist before today — are
present and correctly populated (`True` and `4` respectively) on chapter
1. This settles the "is this run even using the fix" question
independent of any single chapter's specific outcome.

**Chapter 2 of this same run provided the discriminating case — issue #11
is now confirmed fixed, live, not just code-verified.** Iteration 2
scored better than iteration 1 (64% vs 43%) *and* was the last iteration
before the sustained-outage halt — exactly the scenario that silently
shipped the worse draft in every prior observation (chapters 3, 5, 8 of
the previous run). Read the actual shipped content: it's iteration 2's
real, 6-subtopic section, not iteration 1's empty one. First unambiguous
positive confirmation the fix changes the outcome correctly, not just
that the code is present.

**Chapter 8, same run — cleanest confirmation yet, checkpoint-verified.**
Same discriminating shape as chapter 2 (iteration 2 scored better, 64%
vs 57%, and was the last iteration before `HALT sustained-outage`), but
this time confirmed by reading the LangGraph checkpoint state directly
rather than inferring from README content: `best_seen_score = 0.6429`
(iteration 2's exact score) and `best_seen_sawc_path` embeds manifest
hash `b849af9dbf51e39d`, which matches `sawc_manifest_hash` in the
shipped `render-latest.json` byte-for-byte. `best_seen_pregate = 6`
also correctly reflects iteration 2's own pregate count (not iteration
1's), confirming issue #12's tie-breaker field is populated correctly
too, independent of any actual tie occurring this chapter. Third
consecutive clean data point for #11.

**Chapter 9, same run — 4th consecutive #11 confirmation.** Identical
shape again: iteration 2 (43%) beat iteration 1 (36%) and was the last
iteration before `HALT sustained-outage`. Checkpoint: `best_seen_score =
0.4286` (iteration 2's exact score), `best_seen_sawc_path` hash
`c0f49faee76fe966` matches the shipped `render-latest.json`'s
`sawc_manifest_hash` byte-for-byte. Issue #11 has now been discriminated
correctly on 4 separate chapters in a row (2, 8, 9, and originally
confirmed on 2) with zero misses — consider it settled.

**Chapter 10, same run — new observation, not yet a numbered issue: the
sustained-outage counter can mark a genuinely clean, successful iteration
as infra-degraded on small chapters.** iteration 1 (0/1 sections, 29%,
below `NO_RECOVERY_FLOOR`) correctly got the infra-aware RETHINK
exception instead of an immediate no-recovery halt (real APITimeoutErrors
in `sawc_write`, so the exception fired as designed) → iteration 2 (1/1
sections, 0 fallbacks, 86%) hit `HALT success` cleanly. But the
checkpoint shows `consecutive_infra_degraded = 2` even after iteration
2's clean pass — meaning iteration 2 was *also* internally flagged
`infra_degraded = True`, harmlessly, only because `HALT success` is
checked before the sustained-outage counter in `_route_after_mgsr`. Root
cause: `atomic_claim_grounding`'s `resolved=False` fires whenever
`evaluated_fraction < 0.5` (`faithfulness.py`), and with a 1-section
chapter producing very few atomic claims, a single incidental judge-call
failure among them is enough to trip that floor — folding "too few
claims to survive one blip" into the same `infra_degraded` signal as a
genuine sustained Rotator outage. Harmless here (score-first routing
protected it), but on a small chapter that *doesn't* clear 0.80, this
could inflate `consecutive_infra_degraded` and trigger `HALT
sustained-outage` prematurely, on data sparsity rather than a real
outage. Not yet observed causing an actual bad halt — recorded as issue
**#14** below since the mechanism is confirmed live, only the failure
consequence isn't (yet).

**Chapter 11, same run — 5th #11 confirmation, and the first to test the
"reject a worse last iteration" direction.** The worst chapter of this
entire run: both iterations' `sawc_write` wrote 0/2 sections (every
single draft attempt across both iterations timed out — `4 drafts fired`
→ 0 written, twice), so the chapter ships fully empty (0 subtopics, 0
citations, 0 code refs, 773 bytes). iteration 1 scored 36% (pregate 4/9,
llm 1/5), iteration 2 scored **worse**, 29% (same pregate 4/9, llm 0/5)
→ `HALT sustained-outage`. Checkpoint: `best_seen_score = 0.3571`
(iteration 1's, correctly the higher of the two) and
`best_seen_sawc_path` hash `04063fc942db3dbc` matches the shipped
`sawc_manifest_hash` exactly — the immediate-comparison fix correctly
*declined* to overwrite best-seen with the worse, later iteration. Every
prior confirmation (2, 8, 9) tested "last iteration is better, must be
promoted"; this one tests the complementary case, "last iteration is
worse, must be rejected" — both directions of the comparison are now
live-verified. Consider issue #11 fully closed.

No new issues from chapter 11 — the empty-both-iterations outcome is
severe ambient Rotator degradation (digest 5/10 sources, sawc_write 8/8
draft attempts timing out across both iterations), the same
unfixable-from-Synth's-side pattern tracked under issue #4, at its worst
observed intensity this run.

**Third study run complete: 11/11 chapters rendered, 4 `done` (3, 4, 6,
10), 7 `needs_review` (1, 2, 5, 7, 8, 9, 11).**

**`book_harmonize`: correctly ran against only the 4 `done` chapters, but
extracted zero usable claims from all of them — new issue #15.** Log
shows explicit claim/term-extraction failures for 3 of the 4 chapters:
`ch-06` — `JSONDecodeError: Expecting property name enclosed in double
quotes: line 1 column 2 (char 1)` (the model's response wasn't valid
JSON despite `response_format={"type":"json_object"}`); `ch-04` and
`ch-10` — `APITimeoutError` (ambient, already tracked under #4). The 4th
chapter, `ch-03`, has **no log line at all**, success or failure — see
issue #15 for why that's itself a bug. Final result:
`n_atomic_claims: 0, skipped: "no_claims_extracted"` — canonicalization,
violation detection, and patching never ran; the entire harmonization
pass produced nothing for a run that actually had 4 solid `done`
chapters to work with.

## 📊 Second study run, 2026-09-06 (`c70d63fc-...`) — 0/11 clean, book_harmonize correctly skipped

A second full study run completed the same day this doc's issues were
addressed in code. Headline result: **0 of 11 chapters reached a clean
`done` status** — every chapter ended `needs_review`, the worst overall
outcome observed this session. `book_harmonize` correctly did not run
(`n_completed=0 < BOOK_HARMONIZE_MIN_CHAPTERS`), consistent with the
harmonize-exclusion fix from the earlier session working as designed —
nothing to harmonize, so it skipped at the outer gate rather than
attempting anything.

This run also surfaced the operational finding under issue #11 (this
run's Celery worker predates the #10/#11 fixes — a long-running task
doesn't hot-reload) and two new issues (#12, #13) from reading chapter
11's actual rendered content closely. See the per-chapter notes
throughout this doc for the full trace.

## ✅ Status: all 9 issues addressed (2026-09-05) — 3 confirmed live in production the same day

8 of 9 got real code fixes, applied and syntax-verified across 12 files
in `apps/fastapi/domains/dd/synth/`. A fresh `claude-code` study run
(post-redeploy) directly confirmed 3 of them working as designed on
chapter 1:

- **#7** — `[sawc_write] ... 1 fallbacks (failed = {'APITimeoutError': 2})`
  now appears in the log (never visible before this fix); confirms the
  chapter's section failures are plain timeouts, not context-overflow or
  schema issues.
- **#8** — `[render_audit_write] sawc source = best-seen, audit_passed = False...`.
  Verified against content: iteration 1 scored best (64%) and had 1/2
  real sections; iterations 2-5 all regressed (50%→43%→50%→29%,
  ending 0/2 empty). The shipped README is iteration 1's content, not
  iteration 5's — before this fix, it would always have been iteration 5's
  (the worst one).
- **#1** — `0 citation-fallback (chapter-wide)` across all 5 iterations,
  and the rendered section's citations are genuinely section-scoped (3
  sources, not the chapter-wide pool). Every prior run this whole session
  showed 100% citation-fallback, every section, every time.

The chapter still ended `needs_review` — content quality is still capped
by a live, sustained Rotator-side timeout pattern that's outside Synth's
control — but the *system's behavior around that* is now verifiably
correct: honest status reporting, best-draft preservation, and correct
per-section routing, instead of silently shipping the worst draft under a
"done" label with no diagnostic trail.

## ✅ 10. Sustained-outage detector watches the wrong node's health — FIXED 2026-09-06

**Severity: high — directly caused a 33-minute chapter run that should have taken ~16.**

Found by comparing two consecutive chapter 1 runs on 2026-09-06:

- **Run A (33 min):** `sawc_write`'s writer calls timed out in every
  iteration (`(failed = {'APITimeoutError': 4})`, all 5 iterations) — but
  `checklist_eval`'s bundled judge mostly got real responses anyway
  (`llm=3/5, 3/5, 2/5, 3/5`, only iteration 5 was `0/5`). Since
  `consecutive_infra_degraded` (issue from the prior fix round) only
  increments on **checklist's own** call failure — not sawc_write's — the
  counter kept resetting to 0 and never reached the halt threshold. The
  loop ran the full `MAX_REFINE_ITER=5` budget, burning ~20 of the 33
  minutes inside `sawc_write` alone.
- **Run B (16 min):** the judge itself also failed genuinely both
  iterations (`llm=0/5` both times) → `infra_degraded=True` both times →
  counter hit 2 → correctly halted early.

The detector isn't broken — it's watching the wrong node. `sawc_write` is
just as often the one independently timing out, and its own
`error_breakdown` (from issue #7's fix) already carries exactly the
signal needed. **Fix:** fold `sawc_stats.error_breakdown` into the
`infra_degraded` computation — e.g. treat a section with an
`APITimeoutError`/`context_overflow` tag in `error_breakdown` as
infra-degraded evidence in its own right, independent of whether
checklist's judge happened to get a response that round. This directly
determines whether a stuck chapter costs ~16 minutes or ~33.

**Reinforced by chapter 2, same day, a third distinct case:** `sawc_write`
failed **all 6** draft attempts across all 3 sections with
`error_breakdown: {'APITimeoutError': 6}` — a total, chapter-wide writer
outage. But `checklist_eval`'s judge call itself succeeded genuinely
(`llm=1/5`), so `infra_degraded=False`, and the chapter went straight to
`HALT no-recovery` — no RETHINK attempted at all. The fix above would
have flipped this decision too: a chapter whose entire content is the
product of infra failure got zero chances to recover, not because the
no-recovery logic is wrong, but because it was fed the wrong verdict.
Three runs, three halt paths (`sustained-outage`, `budget`, `no-recovery`)
all downstream of the same root gap — worth prioritizing accordingly.

**Reinforced again by chapter 6, same day, same run (`c70d63fc-...`) — a
4th instance:** `sawc_write` failed its only surviving section outright
(`error_breakdown: {'APITimeoutError': 2}`), `infra_degraded` still read
`False` (no "infra_degraded=True" message logged), chapter went straight
to `HALT no-recovery` with zero RETHINK attempts. Consistent with the
operational finding in issue #11 below — this run's worker process
predates the fix, so this is expected, not a new failure of the fix
itself. Recorded here as further evidence for when the fix *is* verified
live.

**5th instance, chapter 7, same run — pattern now fully established,
noting briefly rather than repeating the full writeup:** identical shape
(`error_breakdown: {'APITimeoutError': 2}`, `infra_degraded=False`,
`HALT no-recovery`, zero RETHINK). Chapters 2, 5 (partially — see #11),
6, and 7 of this one study run all hit this same gap.

**6th instance, chapter 10, same run, same shape.** Tally for this one
study run (`c70d63fc-...`): chapters 2, 6, 7, 10 hit this exact gap via
`HALT no-recovery`; chapters 5 and 8 hit the related #11 bug via
`HALT sustained-outage`. 6 of 10 chapters processed so far affected by
one or the other — this pair of fixes is the single highest-leverage
unverified change pending a fresh run.

**Fixed 2026-09-06**, in `checklist/service.py`: `infra_degraded` now also
checks `sawc_stats.error_breakdown` for any tag other than
`parse_failed`/`pydantic_fail` (those are genuine model-output-quality
issues; everything else — `APITimeoutError`, `context_overflow`,
`RateLimitError`, etc. — is by construction an infra-side call failure).
Also added `sawc_writer_degraded` to `checklist_stats` for direct
visibility into which signal fired.

**⚠️ Correction, same day:** chapter 1's earlier "confirmed working" note
above over-attributed. That chapter's correct early halt is fully
explained by the **pre-existing** `judge_call_failed` check (both
iterations' bundled judge calls genuinely failed) — it doesn't require
this fix at all, and per the operational note in issue #11 below, this
fix has not actually been exercised by any chapter yet. Status: code
written and reasoned through, **not yet observed live**.

## ✅ 11. Issue #8's original fix had a real bug — corrected same day, VERIFIED LIVE 2026-09-06 (chapter 2, third study run)

**Severity: high — found on the very next chapter after #8 shipped.**

Chapter 3, 2026-09-06: iteration 2 scored 57% (genuinely better than
iteration 1's 29%, real content improvement), but the graph halted right
after iteration 2's own `mgsr_replan` call (`HALT sustained-outage`) — no
iteration 3 ever ran. The final rendered chapter shipped **iteration 1's
content** (2/2 empty placeholders), not iteration 2's (1/2 real
sections).

Root cause: the original #8 fix put the best-seen comparison inside
`sawc_write`, at the *start of the next* iteration — comparing the
PREVIOUS iteration's score against the running record. That's one step
behind by construction: if the LAST iteration before a halt happens to be
the best one, there is no "next" `sawc_write` call to do the comparison,
so its score never gets a chance to be promoted, and the stale
(potentially worse) prior best-seen pointer ships instead.

**Fixed:** moved the comparison into `checklist_eval_run`, immediately
after `pass_rate` is computed for the current iteration — using
`sawc_manifest_hash` (already read there) plus sawc's own
`versioned_blob_key` convention (imported from `..sawc.keys`) to
reconstruct the current iteration's sawc path directly, with zero lag.
`sawc_write`'s original comparison logic is left in place as a harmless
redundant backup (comparing already-consistent values is a no-op).
**Verify on the next multi-iteration chapter that regresses on its last
attempt** — this is exactly the failure mode chapter 3 exposed, and the
fix hasn't yet been observed working live (only reasoned through and
code-verified).

**⚠️ Operational finding, chapter 5, same day — not a new code bug:**
directly inspected the LangGraph checkpoint state for chapter 5 (via
`graph.aget_state` at the exact step right after `checklist_eval`
finished). Result: `checklist_stats.pass_rate` was correctly fresh
(0.5714, iteration 2's real score) but `best_seen_score` was still
0.357 (iteration 1's, stale) — meaning this fix's comparison code did
*not* run, despite being present and correct on disk (verified line by
line, and the persisted checklist blob's own embedded `sawc_manifest_hash`
confirms the right value was in scope at that exact point in execution).

The likely explanation: **the currently-running study's Celery worker
process loaded the pre-fix code before this fix was written, and a
long-running background task doesn't hot-reload mid-flight** — rebuilding
and redeploying pods doesn't retroactively update a task that's already
executing in an old process's memory. This means every chapter analyzed
in this specific study run (`c70d63fc-...`) may be running on stale code
for #10 and #11 specifically, regardless of what's on disk. **To verify
these two fixes, start a genuinely fresh study run after confirming the
celery deployment has actually cycled** (new pod, not just a rebuilt
image sitting alongside an old still-running task).

**2nd confirmation, chapter 8, same run:** iteration 2 scored 36% (better
than iteration 1's 29%, with 1/3 sections genuinely written vs 0/3), halt
fired right after iteration 2's own `mgsr_replan`. Final chapter shipped
all 3 sections empty — iteration 1's shape, not iteration 2's. Same bug,
same explanation, no new information — recorded as a second data point
for whenever a fresh run allows this to actually be verified fixed.

**Related nuance from chapter 11:** its 5-iteration run oscillated
(57%→29%→57%→29%→57%) rather than degrading in a straight run, so
`consecutive_infra_degraded` never got 2 in a row even though the writer
was clearly unstable across the *entire* chapter. "N consecutive" doesn't
catch an oscillating instability pattern, only a sustained one — worth
keeping in mind alongside #10's fix, though not urgent enough for its own
issue number yet (the eventual budget halt still applied, so no chapter
shipped worse than intended because of this specific gap — just less
efficiently than it could have).

---

## ✅ 12. Best-seen tie-breaking doesn't account for infra-masked quality differences — FIXED 2026-09-06

**Severity: medium — distinct from issue #11, found on chapter 11 (2026-09-06).**

Chapter 11's iteration 5 was the *only* iteration all chapter to write
both sections (2/2, zero draft errors) and had the best structural score
(`pre=8/9`, best in the chapter) — but its bundled judge call happened to
fail outright (`llm=0/5`, full ~62s timeout), landing its overall
`pass_rate` at exactly the same 57.14% as two earlier, less-complete
iterations (1/2 sections each). Even a fully-working best-seen mechanism
(#11's fix, strict `>` comparison) would not promote iteration 5 here —
a tie keeps the earlier entry — so the *less* complete draft shipped,
this time correctly per the comparison's own logic, but not per what a
reasonable person would call "the best iteration."

The root issue: comparing on blended `pass_rate` alone can't distinguish
"genuinely equal quality" from "tied only because a judge call failure
happened to erase a real structural advantage." A secondary tie-breaker
using a purely structural signal — e.g. `sawc_stats.n_completed`
(sections actually written) or `n_pregate_passed` (deterministic checks,
unaffected by judge-call health) — would correctly prefer iteration 5 in
this exact case.

**Applied:** the best-seen comparison in `checklist_eval_run` now compares
`(pass_rate, n_pregate_passed) > (incoming_best_score,
incoming_best_pregate or 0)` instead of `pass_rate` alone, in both the
fresh-compute and cache-hit paths. New `best_seen_pregate` field added to
`SynthState` alongside `best_seen_score`, recomputed from the cached
blob's `criteria` list (`kind == "deterministic"`) on a cache hit since
it isn't a top-level persisted field. **Not yet verified live** — same
caveat as #10/#11, this needs a fresh study run on a cycled worker.

---

## ✅ 13. Mintlify/MDX code-fence attributes leak into rendered output — FIXED 2026-09-06

**Severity: medium — systematic (appeared twice in one chapter), not a fluke.**

Chapter 11 shipped two code blocks with corrupted info-strings:

````
```text Claude Code theme={null}
> /autofix-pr
```
````

`theme={null}` and the stray "Claude Code" text are Mintlify-style JSX
code-fence attributes from the original source page's markdown — valid
in a Mintlify-rendered docs site, meaningless (and ugly) as a plain
markdown fence info-string. Since Visible Vault substitutes code
verbatim, this was captured as-is at ingestion/sentinelization time and
reproduced unchanged by `render` — appearing twice in this one chapter
means it's a systematic gap in how fence info-strings get parsed, not an
isolated glitch.

**Applied, at the render layer only — deliberately not touching
ingestion/vault storage.** Checked first: `vault/schemas.py` explicitly
documents preserving `info_string` "byte-exactly... carries Mintlify
attrs" — a deliberate fidelity choice, and `render/service.py`'s own
normalize pass already comments "byte-preserve info-string + markers
regardless of LLM output." Hash/drift audits key on the code *body*, not
the fence header, so sanitizing the header for display doesn't touch
audit fidelity. Added `_sanitize_fence_info` in `render/domain.py`
(reuses the exact reconstruction already built for issue #6's stray-slash
fix, in the same `dedupe_and_align_sections` pass): keeps only the first
whitespace token after the fence markers, dropping any
JSX-attribute-looking trailer. The underlying vault entry, its hash, and
`info_string` field are all untouched — only the copy that reaches the
rendered chapter is sanitized.

**Also observed, likely out of Synth's scope:** a `curl` command in the
same chapter is truncated mid-argument (`-d` with nothing after it).
Since Visible Vault substitutes verbatim, this must already be truncated
in the source vault entry — an ingestion-time artifact (a page-split or
max-length cut mid-code-block), not something `sawc_write` or `render`
introduced. Flagging for awareness; not actionable within this doc's
Synth-only scope.

## ✅ 14. Sustained-outage counter conflates "real infra outage" with "too few claims to survive one blip" — FIXED 2026-09-06 (SOTA research applied)

**Severity: low-medium — confirmed happening live, but not yet observed to cause a bad halt.**

Found on chapter 10 of the third study run (2026-09-06): iteration 2
wrote 1/1 sections cleanly (0 fallbacks) and scored 86% — a genuine,
unambiguous success — yet the checkpoint state still showed
`consecutive_infra_degraded = 2` immediately after it, meaning
iteration 2 was internally marked `infra_degraded = True` despite having
no writer failures, no judge-call failure, and no CoCoA/atomic-claim call
outage in the ordinary sense.

Root cause: `atomic_claim_grounding` (`checklist/faithfulness.py`)
returns `resolved=False` whenever `evaluated_fraction < 0.5`
(`_MIN_EVALUATED_FRACTION`), i.e. fewer than half of extracted claims got
a real judge verdict. That threshold is a *fraction*, so its sensitivity
scales inversely with claim count: a chapter with 20 claims tolerates
several genuine call failures before tripping it, but a small chapter
(1 section, few claims — exactly this case) can trip it from a *single*
incidental judge-call failure. `checklist/service.py`'s `infra_degraded`
computation treats that identically to a real sustained Rotator outage,
feeding the same `consecutive_infra_degraded` counter that drives `HALT
sustained-outage` in `graph.py::_route_after_mgsr`.

This run's chapter 10 was saved by routing order: `_route_after_mgsr`
checks `score >= CHECKLIST_THRESHOLD` (HALT success) *before* it checks
the sustained-outage counter, so the false infra-degraded flag on a
genuinely passing iteration never mattered. But the risk case is real and
distinct: a small chapter that scores in the 50-79% range (not a clean
pass, not a no-recovery-floor miss either) for two iterations in a row,
where the *only* reason each iteration reads as infra-degraded is claim
sparsity rather than actual model/API failure, would hit `HALT
sustained-outage` and best-seen-rescue a chapter that a further genuine
content RETHINK might have actually fixed.

**Fixed, explicitly requested via `/sota-search`.** Research (Wilson-
score-interval literature for small-sample proportion estimates;
triangulated across statistics references and 2025-2026 LLM-judge
evaluation papers) confirms the general principle: a raw fraction is not
a reliable signal below some minimum trial count, and the correct fix is
either widening the interval or requiring an absolute floor of evidence
— not tightening it. Applied the simplest defensible version of that,
and one already idiomatically consistent with this codebase's own
`SUSTAINED_INFRA_OUTAGE_LIMIT = 2` convention ("one blip isn't a
pattern, two is"): both `faithfulness.py`'s `evaluated_fraction` check
and `cocoa.py`'s `llm_evaluated_fraction` check now require an
**absolute** minimum failure/gap count (`_MIN_ABSOLUTE_FAILURES_FOR_
UNRESOLVED = 2` / `_MIN_ABSOLUTE_GAP_FOR_UNRESOLVED = 2`) in addition to
the existing fraction floor, before flagging `resolved=False`. A single
incidental call failure on a small-claim/small-pair chapter (exactly
chapter 10's case) now falls through to a genuine pass instead of
reading as an infra outage; a real outage (≥2 actual failures) is
unaffected. `CHECKLIST_PROMPT_VERSION` bumped to invalidate any cached
checklist blob computed under the old logic. Verified with a standalone
logic test across 6 cases (1/1, 2/1, 3/2, 10/4, 10/6, 20/15
claims/failures) — behaves exactly as intended in each. Not yet
re-observed live (requires a fresh study run to hit this code path
again).

---

## ✅ 15. `book_harmonize`'s claim extraction has no repair path and one silent failure branch — FIXED 2026-09-06, follow-up round applied

**Severity: medium — observed causing total functional failure on a real run with usable input.**

Third study run, 2026-09-06: `book_harmonize` correctly selected only
the 4 `done` chapters (`ch-03`, `ch-04`, `ch-06`, `ch-10`) and correctly
ran (not skipped at the outer gate — see the second run's confirmed-fine
`less_than_2_chapters` skip path for contrast). But
`_extract_claims_and_terms` (`book_harmonize/service.py`) failed for
**all 4** chapters, and the final result was `n_atomic_claims: 0,
skipped: "no_claims_extracted"` — canonicalization, violation detection,
and patching never ran at all.

Two distinct problems, both in `_extract_claims_and_terms`:

1. **No repair path on a JSON parse failure.** The function does
   `m = _JSON_RE.search(raw or "")` then `json.loads(m.group(0))` inside
   a bare `try/except` — if the model's response isn't valid JSON (even
   with `response_format={"type": "json_object"}` requested), the whole
   chapter's extraction is abandoned with zero recovery attempt. Every
   other structured-output node in this codebase (`outline_sdp`,
   `sawc_write`) has a repair-prompt loop for exactly this failure mode;
   this one doesn't. Confirmed live: `ch-06` failed with
   `JSONDecodeError: Expecting property name enclosed in double quotes:
   line 1 column 2 (char 1)` — the model returned *something*, just not
   parseable JSON, and that's precisely the case a repair call is for.
2. **A second failure branch that logs nothing at all.** `if not m:
   return {}` (no JSON-shaped substring found anywhere in the response)
   returns silently — no warning, no trace of any kind. `ch-03` shows
   zero log output for this chapter's extraction, success or failure,
   yet contributed 0 claims to the total. It cannot currently be told
   apart from "this chapter genuinely has zero atomic claims" (implausible
   for real chapter prose) without instrumenting the code — the silent
   branch is indistinguishable from success in the logs as they exist
   today.

The two explicit `APITimeoutError`s (`ch-04`, `ch-10`) are the same
ambient Rotator degradation already tracked under issue #4 — not new.
What's new is that claim extraction's all-or-nothing, no-repair,
partially-silent design means that under exactly the kind of degraded
conditions this whole doc has been tracking all run, a handful of
unlucky per-chapter failures — plausible on any given run, not a rare
edge case — zeroes out the *entire* harmonization pass for the whole
book, even when most of its input chapters are genuinely `done` and
harmonization-worthy.

**Fixed, explicitly requested via `/sota-search`.** Research confirmed
the 2026 consensus for LLM JSON-output robustness is a layered approach —
constrained decoding where available, schema validation, and a bounded
deterministic repair step — and that `response_format={"type":
"json_object"}` alone does not guarantee valid JSON in practice (5-20%
failure rates are reported in production even with it). Rather than add
a brand-new mechanism, found the SOTA fix was **already a dependency in
this exact codebase**: `json-repair` (`pyproject.toml`) is already used
for precisely this pattern in `domains/dd/planner/nodes/*/domain.py` and
`domains/ycs/rag/domain.py` — try strict `json.loads`, fall back to
`json_repair.loads` (handles single-quoted dicts, trailing commas,
markdown fences, preamble) before giving up. Added a shared
`_parse_json_block` helper in `book_harmonize/service.py` using that
exact idiom, wired into all three JSON-parsing call sites
(`_extract_claims_and_terms`, `_canonicalize_terms`, `_detect_violations`
— the latter two shared the identical bare-`json.loads` bug, unfixed
until now). The previously-silent `if not m` / parse-failure branch in
all three now logs a warning naming the chapter and response length, so
a future `ch-03`-style case is diagnosable. Deliberately did **not**
add a repair-*reprompt* (an extra LLM call) — `json_repair` is
deterministic, free, and directly matches the observed failure signature
(`ch-06`'s error is the classic single-quoted-dict shape `json_repair`
is built to fix), so it's the more targeted fix per the research rather
than the heavier alternative floated when this issue was first written.
Left the all-or-nothing `total_claims == 0` aggregation gate as-is — it
now only fires for genuine no-recovery cases once the parse-failure root
cause is fixed, so restructuring it wasn't necessary. `BOOK_HARMONIZE_
PROMPT_VERSION` bumped to invalidate any cached harmonize blob computed
under the old logic. Verified: `_parse_json_block` recovers a
markdown-fenced+preamble response in a standalone test; `json_repair`
itself could not be exercised live in this session (the dev pods were
already down when this fix landed) but the fix is code-identical to the
already-proven, already-in-production pattern used elsewhere in this
same codebase.

**Follow-up round, 2026-09-06 (4th study run's findings, fixed on
request):** the first round's fix shipped correctly (confirmed running
live — its own new log wording appeared in the 4th run) but the 4th run
proved it wasn't sufficient: `book_harmonize` still extracted 0 claims
from all 4 `done` chapters. Root-caused two further gaps and fixed both:

1. **`ch-10`'s 4,692-char response defeated `json.loads` AND
   `json_repair`.** The suspected cause: `_JSON_RE = re.compile(r"\{.*\}",
   re.DOTALL)` is greedy across the *entire* response — if claim/term
   text itself quotes a code snippet or JSON example containing a stray
   brace, the match spans from the first real `{` to a much later,
   unrelated `}`, handing both parsers an unrecoverable hybrid no repair
   step can fix, because it isn't one coherent structure. Replaced it
   with `_extract_first_json_object`, a brace-depth-aware scanner that
   tracks nesting and skips over quoted-string content, stopping at the
   first point depth returns to 0 — the precise substring, not a
   over-greedy span. `_parse_json_block` now tries this first (both
   strict and `json_repair`'d), falling back to the old greedy regex
   only as a last resort so no previously-working case regresses.
   Verified with 5 standalone cases: a stray trailing brace after a
   valid object, a brace embedded *inside* a claim string (the adversarial
   case this targets), a truncated response (correctly still returns
   `None`, falls through to the old fallback), a markdown-fenced+preamble
   response, and a large 50-claim response with trailing commentary
   braces matching `ch-10`'s actual shape — all extract cleanly and
   `json.loads` validates every one.
2. **`ch-04`/`ch-05` logged nothing at all**, the same undiagnosable-
   silence shape as run 3's `ch-03` even after round 1's fix — meaning
   the JSON most likely parsed *successfully* but under a shape the code
   didn't expect (e.g. missing `"claims"`/`"terms"` keys entirely, so
   `.get("claims", [])` legitimately and silently returns `[]`). Added a
   check right after a successful parse: if the result has neither key,
   log a warning with the actual keys seen and a response prefix. This
   doesn't retroactively explain what `ch-04`/`ch-05` returned (the raw
   text wasn't persisted anywhere and is gone), but the next occurrence
   of this exact shape will now be diagnosable instead of a silent zero.
3. Every failure/anomaly warning (`_extract_claims_and_terms`,
   `_canonicalize_terms`, `_detect_violations`) now logs a
   `raw[:200]` response prefix, not just its length — `len(raw)` alone
   told us `ch-10`'s response was substantial but nothing about *why* it
   failed; a snippet would have made the stray-brace hypothesis a
   confirmed fact instead of an inference.

`BOOK_HARMONIZE_PROMPT_VERSION` bumped again to invalidate stale cached
blobs. **Re-observed live on the 5th study run (2026-09-06/07):** the
round-2 fixes did their job exactly as intended — every failure is now
individually diagnosable via a real `raw[:200]` prefix instead of a bare
length. That diagnosability immediately paid off: it surfaced two
genuinely new, previously-invisible root causes, filed as issue #16.

---

## ✅ 16. `book_harmonize`'s real failure modes, now diagnosed and fixed: reasoning-preamble token exhaustion and a duplicated-leading-brace malformation — FIXED 2026-09-07

**Severity: medium — root cause now concretely known, not fixed yet (assess-only request).**

Found on the 5th study run (2026-09-06/07) via issue #15's own round-2
logging fix — the first time `book_harmonize`'s actual raw responses
(not just their length) have been visible in this doc. Two distinct,
previously-undiagnosed failure modes, both confirmed with verbatim
evidence:

1. **Reasoning/preamble token exhaustion (`ch-05`).** Raw response
   (3,659 chars — right at `EXTRACT_MAX_TOKENS = 1000`'s rough token
   ceiling): `"We need to extract atomic factual claims (single
   verifiable assertions about the technology) from the chapter. Cap at
   20. Skip motivational/structural/transitional sentences. Also extract
   key terminol"` — cut off mid-word ("terminology"), **containing no
   `{` at all**. Some model in the Rotator's bandit pool is restating/
   reasoning through the task instructions as its primary output, and
   1000 tokens isn't enough headroom for both that preamble and an
   actual 20-claim JSON payload. This was never a parsing problem —
   round 1 and round 2's fixes (json_repair, balanced-brace extraction)
   were solving a problem that, for this failure mode, doesn't exist:
   there is no JSON anywhere in the response to recover.
2. **Duplicated leading brace (`ch-10`) — a malformation shape not seen
   anywhere else in this doc.** Raw response: `'{\n{"claims":
   ["The environment variable CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
   enables the experimental agent teams feature.", ...'` — a bare `{`,
   a newline, then a *second* `{` opening the real object. Issue #15's
   brace-depth-aware `_extract_first_json_object` correctly still failed
   here: starting from the first `{`, depth never returns to 0 because
   that outer brace has no matching close anywhere in the response.
   Verified directly (standalone test): retrying the same scanner
   starting just *after* the first `{` (i.e. the second one) recovers
   the real object cleanly, and it parses as genuine, valid JSON with
   real claims. Not single quotes, not trailing commas, not a
   greedy-regex overreach — an actual extra brace the model itself
   emitted, a new pattern round 2's fix didn't anticipate.

(A third failure this run, `ch-09`, was a plain `APITimeoutError` —
already tracked under #4, not new.)

**Fixed, explicitly requested (2026-09-07).** Both directions applied:

1. **Token-budget + prompt reinforcement, for `ch-05`'s case.**
   `EXTRACT_MAX_TOKENS` raised `1000 → 2000` (comfortable headroom for a
   preamble *and* a full 20-claim payload, rather than assuming the
   preamble away). All three JSON-returning prompts (extract,
   canonicalize, detect — canonicalize/detect share the exact same call
   idiom and are structurally exposed to the same mechanism even though
   only extract has been observed failing this way) now explicitly
   instruct: *"Output ONLY the JSON object below — no preamble, no
   restating these instructions, no reasoning or explanation before or
   after it."*
2. **Multi-start brace matching, for `ch-10`'s case.** Added
   `_all_balanced_json_candidates`: retries `_extract_first_json_object`
   from each successive `{` in the response (capped at 5 attempts) so a
   stray/duplicated leading brace with no closing partner of its own no
   longer blocks recovery of the real object that follows it.
   `_parse_json_block` now tries every candidate found this way (strict
   `json.loads`, then `json_repair`) before falling back to the original
   greedy regex.

Verified with 5 standalone cases: the exact real `ch-10` raw response
(now recovers the real claims correctly), the exact real `ch-05` raw
response (correctly still returns `None` — there genuinely is no JSON
in a pure-reasoning response, and no parser change should paper over
that), the previously-fixed large-response-with-trailing-braces case
(no regression), a markdown-fenced+preamble response (no regression),
and a bonus triple-duplicated-brace edge case (recovered too, though
unobserved in practice — the bounded retry handles it for free).
`BOOK_HARMONIZE_PROMPT_VERSION` bumped to invalidate stale cached
harmonize blobs. Not yet re-observed live — needs a 6th study run.

---

| # | Fix | Files |
|---|---|---|
| **16** | `EXTRACT_MAX_TOKENS` raised 1000→2000; all 3 JSON-returning prompts now explicitly instruct no preamble/reasoning output. `_all_balanced_json_candidates` retries the brace scan from each successive `{` (capped at 5) so a duplicated leading brace no longer blocks recovery of the real object after it. | `book_harmonize/service.py`, `book_harmonize/params.py`, `book_harmonize/prompts.py`, `book_harmonize/versions.py` |
| **15** | All 3 JSON-parsing call sites in `book_harmonize` fall back to `json_repair.loads` on a bare `json.loads` failure (round 1). Round 2 (4th-run findings): replaced the greedy whole-response `\{.*\}` regex with a brace-depth-aware `_extract_first_json_object` scanner (fixes a large-response-with-stray-braces failure both parsers missed), added a warning when parsed JSON lacks both `"claims"`/`"terms"` keys, and every failure log now includes a `raw[:200]` response prefix. | `book_harmonize/service.py`, `book_harmonize/versions.py` |
| **14** | `faithfulness.py`'s and `cocoa.py`'s evaluated-fraction floor now also requires an absolute minimum failure/gap count (≥2, matching the codebase's own `SUSTAINED_INFRA_OUTAGE_LIMIT` convention) before flagging `resolved=False` — a single incidental call failure on a small-claim chapter no longer reads as an infra outage. | `checklist/faithfulness.py`, `checklist/cocoa.py`, `checklist/versions.py` |
| **12** | Best-seen comparison now tie-breaks on `n_pregate_passed` (deterministic, judge-independent) when `pass_rate` ties — a tie can be an artifact of a judge call failing that round rather than genuinely equal quality. New `best_seen_pregate` field in `SynthState`. | `checklist/service.py`, `state.py` |
| **13** | Added `_sanitize_fence_info` in render's `dedupe_and_align_sections` — strips leaked Mintlify/MDX JSX fence attributes (`theme={null}`, stray framework-name tokens) from the *displayed* fence header only. Vault storage, hashing, and audit fidelity untouched by design (both are explicitly byte-preserved elsewhere in this codebase). | `render/domain.py` |
| **10** | `infra_degraded` now also checks `sawc_stats.error_breakdown` for any tag other than `parse_failed`/`pydantic_fail` — those are genuine model-output issues; everything else is by construction an infra-side call failure. **Not yet verified live — see #11's operational finding.** | `checklist/service.py` |
| **11** | Best-seen comparison moved from `sawc_write` (one-step-behind, missed the last iteration before a halt) into `checklist_eval_run` (immediate, zero-lag). **Confirmed correct on disk; not yet verified live** — the study run used to test it was running on a Celery worker process that predates the fix. | `checklist/service.py` |
| **8** | mgsr's own comment claimed it tracked best-seen; it never did. `sawc_write` now reconstructs the just-finished iteration's versioned key and promotes it to `best_seen_sawc_path`/`best_seen_score` only if its score beats the running record. `render_audit_write` now reads `best_seen_sawc_path` first, falling back to the latest pointer only if unset. **See issue #11 — this had a real bug, corrected 2026-09-06.** | `sawc/service.py`, `render/service.py`, `checklist/service.py` |
| **7** | `_draft_one_section`'s failure paths now return an `error_reason` (5th tuple element) and log at `WARNING`/`INFO`; total section failures aggregate into a per-chapter `error_breakdown` in the summary log, mirroring `digest_construct`'s existing pattern. | `sawc/service.py` |
| **1** | Root cause found: `SectionContribution` had no `source_key` field at all — `sawc_write`'s routing read a field that never existed, 100% of the time, regardless of digest's success rate. Added the field (backfilled programmatically after LLM parsing, never asked of the model) and bumped the schema version. | `digest/schemas.py`, `digest/service.py` |
| **3** | `"file"`/`"path"` weren't in the misroute detector's `NOISE_IDENTS` — too generic to signal real relevance, which is exactly why `rm/mv/cp` under a "Checkpoint State Recovery" subtopic slipped through (shared only the word "file"). Added to the noise set. | `render/params.py` |
| **2** | Added a `_normalize_outline_dict` pre-validation pass — remaps descriptive-slug `section_id`s to `s<N>` (and fixes up `prerequisites` refs accordingly) and truncates over-long headings to their leading clause, *before* Pydantic ever sees them, instead of reject-and-hope-the-repair-call-fixes-it. Also strengthened both the draft and repair prompts with explicit WRONG/RIGHT examples for both fields. | `outline/service.py` |
| **5** | Prose-mode writer prompt now explicitly demands ≥1 concrete artifact (path/command/config key) per subtopic and varied openings across subtopics — the exact gap behind chapter 3's repetitive, ungrounded filler. | `sawc/service.py` |
| **6** | Added a render-time regex normalization (`_fix_stray_slash_command_space`) that fixes `"/ command"` → `"/command"` on the first line of a fenced code block — scoped tight (leading-slash-plus-whitespace only) so real code/division is untouched. Fixes the bug regardless of whether it originated in LLM generation or the ingested source itself. | `render/domain.py` |
| **9** | Scoped version (not the full "v2 loop" — see below): closed the actual self-refine loop. `checklist_stats` now carries the real `failed_feedback` strings (previously only a count was passed through state); `sawc_write` reads them on a RETHINK iteration and threads them into the writer prompt as "PRIOR ATTEMPT FEEDBACK — FIX THESE," so a rewrite has real signal instead of rerunning blind. | `checklist/service.py`, `sawc/service.py` |
| **4** | Investigated, no code fix applied. No new evidence pointed to a specific, fixable root cause beyond what's already mitigated (second-wave retry) — the doc's own framing ("may be a non-issue once provider capacity recovers") held up. Left as a watch item, not a speculative change. |  |

**Final study result:** `5/11 completed, 0 failed, 6 needs_review,
final_status = "needs_review"`. Done: chapters 1, 2, 4, 10, 11.
Needs-review: chapters 3, 5, 6, 7, 8, 9 — every one of them due to
`sawc_write` failing completely on at least one section despite the
underlying source material generally being adequate (see issue #7). Not a
single chapter failed for lack of source coverage or a genuine,
un-recoverable content-quality problem — chapters 4 and 11 both proved a
healthy RETHINK loop reliably fixes real quality issues (50%→86% and
64%→100% respectively). `book_harmonize` correctly ran against only the
5 `done` chapters (confirms this session's exclusion fix worked
end-to-end), though it extracted zero atomic claims across all 5 —
plausibly one more sustained-outage casualty, unconfirmed.

**Chapter 10: no new Synth issues — a clean confirmation run** (14/14
checklist, single iteration, audit passed). Worth noting separately
(out of this doc's scope, it's a Rotator/Groq config matter, not a Synth
code issue): `outline_sdp`'s repair attempts surfaced a raw
`"model \`qwen/qwen3.8-27b\`/\`qwen/qwen3.6-27b\` is blocked at the
project level"` Groq error, the same class of issue from earlier in this
conversation — now hitting two distinct qwen variants, after 40 retries
each. Worth checking the Groq console directly.

**Chapter 9 disambiguates two things:** issue #7 (writer failure) is
confirmed independent of issue #1 (citation routing) — this chapter had
working routing (0 fallback triggers, both sections got primary sources)
yet the writer still failed completely both iterations. And chapter 8's
size-correlation hypothesis for #7 is weakened, not confirmed — chapter 8
failed totally with the *largest* vault this session (313 entries),
chapter 9 failed totally with a *small* one (43 entries) — total writer
failure isn't exclusive to large chapters, which points back toward a
general sustained-outage explanation. Both readings stay open until #7's
logging fix actually reveals the error type.

**⚠️ Issue #8 (below) is the most severe finding in this document — read
it first.** It affects every chapter that regresses across RETHINK
iterations, which per this run's own logs is common. (Chapter 7's
iterations genuinely improved 1/3 → 2/3 sections, so "always render
latest" happened to coincide with "best" there — a useful reminder that
issue #8 doesn't cause visible harm on every chapter, only when a later
iteration regresses, as chapters 3 and 6 did.)

**Issues #1 and #7 reconfirmed in chapter 7** — every section again shows
`citation-fallback (chapter-wide)`, and the one section that failed
completely (0 subtopics) again has no error-reason in the logs, same as
chapter 5.

**Confirmed working in production during chapter 5's run** (this session's
earlier fixes, not a new issue): the `infra_degraded` discrimination logic
correctly fired in *both* directions now observed — chapter 3 gave a
genuinely infra-degraded low score one extra RETHINK attempt; chapter 5's
low score (`36%`, genuine judge review, no call failures) correctly
triggered `HALT no-recovery` immediately instead of wasting an iteration.
That's the intended behavior in both cases.

**Confirmed working in production during chapter 4's run** (this session's
earlier fixes, not a new issue): the CoRefine RETHINK loop delivered a
genuine content-quality recovery, not just an infra-driven retry — iter 1
scored 50% (1/2 sections written), iter 2 scored 86% (2/2 sections,
cleanly passing). Both iterations show real `pre`/`llm` structural review
with no call failures, confirming the loop can and does improve real
output quality when the underlying issue is actually fixable content, not
just an outage.

**Confirmed working in production during chapter 3's run** (this session's
earlier fixes, not new issues — recorded here for completeness): the
sustained-outage halt fired correctly — `"HALT sustained-outage (2
consecutive infra-degraded iterations >= 2); best-seen-rescue applies"` —
stopping after 2 RETHINK iterations instead of grinding to
`MAX_REFINE_ITER=5`. The fail-closed gate also correctly reported
`needs_review (3/11)` instead of `done` for the resulting broken chapter.

---

## 1. digest_construct's per-section citation routing has never once worked

**Severity: highest — 100% reproducible, independent of provider health.**

Every section, in every chapter, in every run observed this session —
including chapter 1's clean 8/10 pass with zero infra failures — ends up
logging:

```
[sawc_write] <section>: digest routed 0 sources to this section; falling back to chapter-wide (N sources) for citations
```

This is not a symptom of digest's failure rate (the second-wave retry
already added this session addresses that). Sections whose sources *did*
digest successfully still get zero section-specific routing. The routing
logic itself — matching `contributes_to.section_id` from
`digest/schemas.py`'s `SectionContribution` back to a section's citation
pool in `sawc/service.py`'s `_run_section` — has a bug independent of call
success rate.

**Now confirmed across 4 consecutive chapters, every section, regardless
of outcome quality** — including chapter 4's clean 86%-passing, 2/2-section
result. This is the single most reproducible issue in this document.

**Where to look:** `digest_construct`'s `SourceDigest.contributes_to`
population (`digest/service.py::_digest_one_source`,
`digest/domain.py::build_per_section_index`) vs. how `sawc/service.py`
reads `per_section_index.get(sid)` — the two sides may be keying on
different identifiers, or the LLM-side `contributes_to.section_id` values
may not be validated/coerced to match `valid_section_ids` before indexing.

---

## 2. outline_sdp's initial 3-sample draft fails to parse 100% of the time (this chapter)

**Severity: high — 100% reproducible for `ch-01-installation-and-authentication`
across every run this session.**

Every single run logs:

```
[outline_sdp] ...: ALL 3 samples failed to parse; emitting heuristic fallback outline
```

...followed by a repair loop (often itself timing out) and a programmatic
HARD-TRIM. The fallback path works, so output ships — but a 100%
reproduction rate at the *first* stage of every run for this specific
chapter is not random noise.

**Root cause now has real evidence, from chapter 3's run** — the pydantic
rejection was captured in full for the first time, and it's specific and
actionable:

```
pydantic-reject — sections.0.section_id: Value error, section_id 'config-hierarchy-scopes'
must match /^s\d+$/ (e.g. 's1', 's12'); sections.1.section_id: Value error,
section_id 'file-level-customization' must match /^s\d+$/ ...
```

The model is consistently emitting **descriptive slugs** as `section_id`
(`config-hierarchy-scopes`, `file-level-customization`) instead of the
required `s1`/`s2`/`s3` format the schema demands. This reads like the
model finds a human-readable slug more natural to generate than an
arbitrary `s<N>` token, and the prompt isn't constraining it strongly
enough (few-shot examples using literal `s1`/`s2` might not be salient
enough, or the field description alone isn't sufficient). This is now a
scoped, fixable prompt-engineering issue, not an unknown — check
`outline/prompts.py`'s section_id instructions and consider adding an
explicit "NOT a descriptive name" negative example, or relaxing the
schema to accept a slug and normalize it to `s<N>` programmatically instead
of rejecting outright.

**A third, distinct validation failure mode, from chapter 9** — this time
not a parse failure or section_id format issue, but a heading word-count
violation, rejected twice in a row:

```
sections.0.heading: Value error, heading must be 2-8 words; got 11
('Extending Claude Code with Skills, Hooks, System Prompts, and Project Context')
```

The model keeps trying to cram a full comma-separated topic list into one
heading instead of a short 2-8 word title. Same underlying pattern as the
section_id issue — the model reaches for the most natural/descriptive
phrasing, and the schema's constraint isn't salient enough in the prompt
to override that instinct. Three distinct schema fields now confirmed
hit by this same class of problem (`section_id` format, heading length,
plus whatever caused the pure parse failures in other chapters) suggests
the fix should be structural — e.g. a shared "constraint-compliance"
few-shot block reused across all of outline's field validators — rather
than patching each field's instructions separately.

---

## 3. Code-misroute detector has a confirmed false negative

**Severity: medium — concrete content-quality defect, single confirmed instance.**

Chapter 2's rendered README shipped this under "Checkpoint State Recovery"
(prose about `/rewind` restoring file/conversation state):

```bash
rm file.txt
mv old.txt new.txt
cp source.txt dest.txt
```

— a code block with no relation to the subtopic. In the **same chapter**,
a different mismatch was correctly caught and self-omitted:

```
> _(Code example omitted — it did not match this subtopic and was likely misrouted.)_
```

So the detector works but isn't catching every case. Likely only checks an
explicit signal (hash/id mismatch) rather than semantic relevance between
the subtopic's prose and the chosen code body. Worth checking
`render`'s dedup/misroute logic (the "1 misrouted block(s) omitted" /
"1 recycled code block(s) cross-referenced" log line's source) against
what specifically it validates.

---

## 4. digest_construct's raw failure rate is persistently high even outside the confirmed outage window

**Severity: medium — likely mostly provider-side, but unverified.**

Observed failure rates this session: 53%, 73%, 80% (best), 47% (85% at one
point during the confirmed sustained Rotator outage), and 75% in chapter 2
(worst, unrelated to that outage window). Some of this is genuinely
provider-side rate-limiting (out of Synth's control — see the Rotator's
own arm-pool health, tracked separately). But it's worth checking whether
`digest_construct`'s own per-call payload size (`_MAX_SOURCE_CHARS =
100_000`) or timeout budget is disproportionately unfavorable compared to
`sawc_write`/`checklist_eval`, which recovered better under the same
conditions in the same runs. If digest's calls are simply larger/slower
per-request than other nodes', that alone would explain a higher timeout
rate under identical arm-pool stress.

---

## 5. Prose-mode (no-code) sections produce vague, repetitive filler text

**Severity: medium — concrete content-quality defect, confirmed in chapter 3.**

When a section has no routable code hashes, `sawc_write` falls to
`prose_mode` (see `n_routed_hashes == 0` gate in `sawc/service.py`). The
resulting prose in chapter 3's "Explore the directory" section is thin and
repetitive — all 3 subtopics open with near-identical phrasing
("Exploring the directory reveals...", "Exploring the directory
clarifies...", "Exploring the directory helps locate...") and contain zero
concrete artifacts (no directory listing, no example config file, no
command) despite the topic being inherently structural — a `tree
~/.claude` or a sample `settings.json` would ground this immediately.

This is distinct from issue #1 (routing) — this section's citations were
present and correct, it just had no code to show. The gap is in the
prose-mode writer prompt itself not pushing hard enough for concrete,
non-generic detail when there's no code block to anchor around. Worth
checking `sawc/prompts.py`'s prose-mode branch for whether it asks for
specifics (file paths, example structures) or just asks for an
"explanation," which invites this kind of filler.

---

## 6. Stray-space slash command syntax in generated inline commands

**Severity: low — single confirmed instance, but a real functional accuracy defect.**

Chapter 4's "Add Marketplace via HTTPS URL" subtopic renders:

```
/ plugin marketplace add https://github.example.com/platform/claude-plugins.git
```

`/ plugin` (with a space) is not a valid Claude Code slash command — it
should be `/plugin` with no space. A reader who copy-pastes this verbatim
gets a failure. Every other inline command in this chapter (and the prior
three) is clean, so this reads as an isolated writer-generation slip
rather than a systemic normalize-pass bug — but nothing in the pipeline
(writer validation, render's normalize pass, the checklist judge) caught
it. Worth a light-touch fix: a post-generation regex check for `` `/ ``
followed by a known command word inside inline/fenced code, since slash
commands are a well-known, enumerable vocabulary for this framework.

---

## 7. sawc_write's total per-section draft failure is invisible in logs

**Severity: medium-high — a diagnosability gap, now confirmed in chapters 5 and 8
(the latter a total-failure case that raises a real size-correlation hypothesis).**

**Chapter 8 update:** the worst instance yet — `0/3 sections written` in
370 seconds (the longest `sawc_write` run this session) on the chapter
with the *most* source material observed (19 sources, 313 vault entries
pre-fold, 134 post-fold). Richest inputs, worst outcome, longest runtime
before giving up. That combination suggests a real hypothesis distinct
from generic Rotator flakiness: **a context-overflow failure specific to
large chapters** — `sawc_write` does have a context-overflow retry
(halved vault budget) added earlier this session, but if the *initial*
prompt for a heavily-sourced section is large enough, the halved retry
might still not fit. This can't be confirmed without the logging fix
below — it's exactly the kind of thing `error_breakdown` would surface
(a spike in `context_overflow`-tagged failures correlating with
`n_sources`/vault size).

Chapter 5's one surviving section (`s1`, 12 vault entries across 2
sources — not source-starved) failed **all** of its draft attempts and
fell to a placeholder:

```
[sawc_write] claude-code/ch-05-cloud-and-enterprise-deployment: 0/1 sections written, 1 fallbacks, ...
[render_audit_write] ...: 1/1 section(s) are EMPTY placeholders (writer produced 0 subtopics) — failing audit.
```

Neither line says *why* the drafts failed. Checked `_draft_one_section`
directly (`sawc/service.py`): its call-failure and parse-failure paths
only call `emit_progress` (an internal SSE event), never
`logger.warning` — so nothing lands in celery pod logs, unlike
`digest_construct`'s per-source failures, which got an `error_breakdown`
dict + `logger.warning` lines earlier this session specifically to make
this diagnosable. `sawc_write` never got the equivalent treatment. When a
section fails completely despite having real source material, there's
currently no way to tell from logs alone whether it was a call timeout, a
parse failure, or a schema rejection.

**Fix:** mirror `digest_construct`'s pattern — add `logger.warning` at
`_draft_one_section`'s failure points and surface an aggregate
`error_breakdown` per chapter in `sawc_write`'s summary log line, the same
way `digest_construct` already does.

---

## 8. "Best-seen-rescue" never actually rescues anything — confirmed dead machinery

**Severity: CRITICAL — silently defeats the safety net for every chapter that regresses across RETHINK iterations.**

`graph.py` logs `"best-seen-rescue applies"` on every no-recovery/budget/
sustained-outage halt, implying render falls back to whichever iteration
scored highest. **It doesn't.** Confirmed by direct code inspection of all
three nodes involved:

- `render_audit_write_run` (`render/service.py`) computes its sawc input
  as `sawc_key = sawc_latest_key(slug, chapter_id)` — always the **most
  recently written** sawc blob. It never references
  `state.get("best_seen_sawc_path")` anywhere in the file (checked via
  grep — zero occurrences).
- `sawc/service.py`'s own comment says *"best-seen iteration tracking —
  checklist score updated in mgsr_replan after sawc returns"* — but
  `mgsr/service.py` has **zero references** to `best_seen_sawc_path` or
  `best_seen_score` anywhere. The node that's supposed to do the score
  comparison and update the pointer never does.
- `sawc_write` itself only ever *forwards* `best_seen_score` when
  `incoming_best_score is not None` — but nothing ever sets it to a real
  value in the first place (iteration 1 has no incoming score to
  forward), so it stays `None` for the entire graph run, on every
  chapter, every time. Same dead-field pattern as the
  `prev_checklist_score` bug fixed earlier this session — except this one
  is still live.

**Confirmed live impact, chapter 6:** iteration 1 wrote 1/2 sections
successfully; iteration 2 regressed to 0/2 (worse, under the same
sustained-outage conditions). The halt fired
`"HALT sustained-outage... best-seen-rescue applies"`, but the final
shipped chapter has **both** sections empty — iteration 2's worse result,
not iteration 1's better one. This is not a one-off: any chapter whose
RETHINK loop makes things worse (a real, observed pattern this session —
chapter 3 went 43%→36%, chapter 6 went 43%→29%) silently ships its worst
attempt instead of its best, with logs actively claiming the opposite
happened.

**Fix:** requires wiring across three nodes:
1. `mgsr_replan` (or `checklist_eval`, which already has the fresh score)
   must actually compare the current iteration's score against
   `state.get("best_seen_score")` and update both
   `best_seen_sawc_path`/`best_seen_score` in its state patch when the
   current iteration is better.
2. `render_audit_write_run` must read `state.get("best_seen_sawc_path")`
   (falling back to `sawc_latest_key` only when it's unset) instead of
   unconditionally reading the latest pointer.
3. Verify with a synthetic case: force iteration 2 to score worse than
   iteration 1, confirm the rendered chapter reflects iteration 1's
   content.

---

## 9. mgsr_replan's corrective actions are computed but never applied

**Severity: medium — a known, longstanding design gap (not new this session), reconfirmed concretely in chapter 8.**

Chapter 8: `mgsr_replan` computed 4 real corrective actions at `conf=0.92`
(high confidence) — but because the chapter hit `HALT no-recovery`
immediately (genuinely low score, no RETHINK attempt), those actions were
discarded entirely. This isn't new — mgsr's own schema documents this
directly (`HaltReason` literals tagged `"(v2 only)"`, `iteration: int = 0
# v1 always 0; v2 loop bumps`, fallback literal `"v1_no_loop"`) — the
graph's real CoRefine loop only ever uses checklist's raw `pass_rate` to
decide whether to loop, never mgsr's own analysis of *what specifically*
to fix. mgsr is currently pure audit telemetry, not an active participant
in repair. Chapter 8 is a concrete example of the cost: 4 well-reasoned,
high-confidence actions (likely "retarget this section," "merge into
adjacent," matching the placeholder text's own suggestion) that could
plausibly have fixed the chapter, computed and thrown away in the same
breath.

**Fix:** this is the "v2 loop" already scoped in mgsr's own schema
comments — wire `_route_after_mgsr` to route mgsr's `actions` back into
`sawc_write`'s next iteration (targeted re-draft of just the flagged
sections) instead of a blind full re-run, and bump `iteration` per the
v2 semantics already reserved for this. Larger scope than the other
issues here — treat as a follow-up project, not a quick fix.

---

## Fix order recommendation (once the study run completes)

1. **#8** (best-seen-rescue is dead) — do this first, above everything
   else. It's the highest-severity, highest-blast-radius finding: it
   silently ships the *worst* iteration instead of the best one on every
   chapter that regresses, and the logs actively claim otherwise, which
   would mislead any future debugging that trusts them.
2. **#7** (sawc_write failure observability) — raised above #1 after
   chapter 8's total-failure (0/3 sections, richest chapter this session).
   Cheap, mechanical port of an already-proven `digest_construct` pattern,
   and now the fastest path to confirming or ruling out the size/
   context-overflow hypothesis before investing in anything downstream of it.
3. **#1** (citation routing) — purely a code bug, no dependency on
   provider health, confirmed across every chapter regardless of outcome
   quality.
4. **#3** (misroute false negative) — concrete, scoped, low risk.
5. **#2** (outline parse failure) — now has a specific, actionable lead
   (descriptive slugs instead of `s<N>` format) — a prompt/schema fix, not
   a fresh repro investigation.
6. **#5** (prose-mode filler quality) — scoped to one prompt branch, worth
   pairing with #1 since fixing routing may reduce how often prose_mode
   triggers at all.
7. **#6** (stray-space slash commands) — cheap, low-risk regex check;
   bundle with whichever writer-prompt work touches #5.
8. **#4** (digest failure rate) — may turn out to be a non-issue once
   provider-side capacity recovers, but check the payload-size hypothesis
   regardless — likely shares a root cause with #7's new hypothesis.
9. **#9** (mgsr actions unused) — largest scope of anything here, a real
   "v2 loop" project rather than a fix; do last, after everything above
   has stabilized the pipeline enough to make wiring a new active-repair
   path safe to build on.
