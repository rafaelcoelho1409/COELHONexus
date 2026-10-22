# Next fixes to ship (saved 2026-10-22)

State when this was written: planner and synth declared ready after the 2026-10-19/20 test rounds (all test chapters 17/17 or 16/17, audits pass, qdrant re-planned with 0 dropped / 0 unassigned / 0 lexical). Nexus is pinned to `fgts-va` in Settings (the `latency_ts` comparison runs were manual). Planner and synth data were wiped on 2026-10-20 so the user can run everything manually.

Order matters: each list is meant to be shipped in a row. Ship Nexus items only when no manual run is active (every deploy kills the running task).

---

## A. `latency_ts` (Rotator repo; belongs to the Rotator session, send as a proposal)

Never touch `fgts_va`. Every change below must be gated on `policy == latency_ts` and live inside `domains/llm/rotator/policies/latency_ts/` (or a challenger-only branch). Shared code in `chain/service.py` (prefilters) must not change.

### Evidence (langchain planner, one run per policy, 54 min apart, Nexus forcing `model=fgts-va` / `latency-ts`)

| | `fgts_va` | `latency_ts` |
|---|---|---|
| Wall time | 12:52 | 13:45 (+53 s, 7%) |
| Plan | 15 chapters, 100 sources, 0 dropped | 15 chapters, 99 sources (1 off-topic drop), 0 unassigned |
| Median / p95 of successful calls | 5.9 s / 39.7 s | 5.1 s / 46.6 s |
| Failed requests | 2 (spread out, large prompts) | 5 (one 23 s burst, small prompts, each 117.5 s) |
| Decisions with one eligible arm | 32 of 417 (all during chapter_assign) | 69 of 266, every one with `p_ok=0.000` |
| Top model's share of traffic | 28% | 53% (`ising-calibration`) |

- The extra 53 s sits in `chapter_assign` (3:12 vs 1:49, +83 s) and `chapter_propose` (+32 s). Other stages were faster under `latency_ts`.
- The 5 failures all happened between 13:08:22 and 13:08:46, tried the same arms in sequence (`poolside/laguna-xs-2.1`, `stepfun-ai/step-5-preview`, then `ising-calibration`), and each waited about 117.5 s (3 to 4 hops at the >=30 s attempt floor) for prompts that normally take about 5 s.
- Both policies lose eligible arms under concurrency (4 to 8 in flight), so the shrinking set comes from the shared prefilters. The difference is that `latency_ts` sends to an arm it rates as dead.
- Current pending penalty is `x(1 + 0.25 * pending)`; it is too weak to spread concurrent requests.
- Limits: n = 1 run per policy; the Rotator container log dropped the first ~3 min of the second run (so request counts are not comparable, rates are); the Rotator's own plan asks for ~900 requests per policy per bucket.
- Plain Thompson sampling already tolerates delayed feedback (Wu & Wager 2022, arXiv 2202.12431): do not rework the Bayesian model.

### Fixes, in order

0. **Persist `[ab-request]` and `[ab-decision]` lines** to a file or Loki before anything else. The container log rotated mid-experiment and lost evidence.
1. **Peak-EWMA cost + power-of-two choices.** Change the pending penalty from `0.25` to `1.0` (Envoy's `cost = RTT_ewma * (active_requests + 1)`) and pick between the two best Thompson draws by lowest cost. Sources: Envoy peak_ewma docs (v1.37 contrib extension); Linkerd 2016 (success at 1 s timeout with one slow backend: round robin ~95%, least loaded ~99%, peak EWMA ~99.9%).
2. **Dead-arm guard.** If the only eligible arm has `p_ok < 0.05`, wait 1 to 3 s and re-filter (or relax the already-tried filter) instead of firing at it. Removes the 69 sends at `p_ok=0.000`.
3. **Bucket-aware attempt deadline for small prompts.** About 2x the arm's p90 for the `s` bucket, bounded to 10..30 s, replacing the >=30 s floor. Cuts the 117 s waits.
4. **Capped hedge at p90.** Hedge when a call passes its measured p90; cap hedges at 5 to 10% of traffic; cancel the loser. Single-source guidance (LLM-gateway tail-latency article, 2026-05-02), so treat the numbers as starting values. NIM allows about 40 requests/min, so the cap matters.
5. **Validate.** ABBA, four alternating langchain planner runs (~13 min each), model forced to `latency-ts` / `fgts-va`. A change stays only if median wall time and p95 are at or below `fgts_va`'s (12:52 and 39.7 s) and no stall burst repeats.
6. Open question for the Rotator session: confirm the pending count is applied at dispatch time (not only after completion), otherwise fix 1 will not help.

---

## B. Nexus (this repo)

1. **Leftover blocks in the digest.** Re-ask, per chunk, only for the blocks the model left unrouted; one bounded extra round. Evidence: deepagents ch-05-event-subagent-streaming printed 39 of 131 blocks with 67 orphan refs (one page also failed and became a stub).
2. **Automatic re-judge at the end of a run** when the judge was unavailable, after a short pause. About 3 of every 10 chapters scored on the 12 gates only (judge 0/5 after ~300 s). Today a re-trigger without wipe does it by hand (the verdict is not cached).
3. **Coverage gate and language variants.** Count REST `http` / `json` variants of a printed example as covered. Evidence: qdrant ch-02 passed 16/17; its code_coverage gate missed 57 of 125 routed blocks, and the 79 unprinted ones were mostly `http` (37) and `json` (32) variants the writer judged redundant. Optional companion: collapse client-language tabs at ingestion.
4. **Outline floor fallback.** The repair-acceptance fix (`a5eae89`, `accept_repair`, slack 2) is covered by unit tests only; the qdrant ch-10 rerun drew a good 12-section outline, so it never ran live. If a floor violation still survives the repairs, add a deterministic fallback (group sources by title) instead of shipping 5 sections for 20 pages.
5. **Learning-quality layer** (the big item): see `docs/SYNTH-LEARNING-QUALITY-PLAN-2026-10-18.md`.

### Block-coverage reference (final code, old Rotator build)

| Chapter | Judge | Cited | Blocks printed |
|---|---|---|---|
| langgraph ch-05 | 17/17 | 3/3 | 94/149 (was 28) |
| langgraph ch-07 | 17/17 | 15/15 (was 10) | 63/144 |
| fastmcp ch-01 | 17/17 | 18/18 | 90/135 |
| deepagents ch-05 (re-planned) | 17/17 | 5/5 | 39/131 |
| qdrant ch-02 | 16/17 | 6/6 | 50/135 |
| qdrant ch-10 | 17/17 | 20/20 | 117/160 |

---

## C. Operating notes

- `setsid nohup skaffold dev & echo $!` records the setsid wrapper pid, not skaffold. Record the pid by scanning `pgrep -x skaffold` for cwd = this repo. Kill the old dev only after `skaffold delete` (its cleanup would uninstall the new release); never touch the Rotator's or EyeWitness's skaffold.
- Chapter ids change when a doc is re-planned. Take ids from `GET /synth/<slug>/study/chapters`; a queue must skip on "not in plan".
- Planner and synth cannot run at once (Redis single-flight per slug).
- A judge-only re-run is `POST /synth/<slug>?chapter_id=<id>&mode=quality` without a wipe (writer output is cached).
