# Book Distiller Agentic Ingestion Assessment — workflow 1.12 run, 2026-11-07

First live run of workflows 1.11 + 1.12 on *The Art of Cyberwarfare*. Read-only evidence from the live API (LLM counters) and MinIO (`quality-report.json`, all 275 assessments, patch proposals, final pages). Every auto-applied model repair was classified by character diff. The fixes below are workflow **1.13**: offline-tested and replayed against this run's stored proposals, **not deployed and not measured by a new run**.

## Verdict

**16 review pages, down from 22** (1.11's replay target was ≤ 19), no wrong auto-applied repair, no unverified page, publication check `passed`. But 1.12 introduced one regression that this count hides: **six URL-heavy reference pages (247, 250, 252, 253, 257, 259) lost every specialist repair** because the `check_patch` tool, called once per patch, used up the specialist's 8 model turns. Three of them (252, 253, 257) were clean in 1.10. Fixing that (one batched check) is the main change in 1.13, together with two smaller gate fixes.

## Run examined

| Field | Value |
| --- | --- |
| Book | *The Art of Cyberwarfare* (`4c6c6327360f7cf5`), Docling extraction `c94a1d6883c04bb0a9b9d339f85348f4` |
| Agentic run | `511c2e4a38a44d41b8db4b30557dad43`, workflow `1.12` |
| Status | `done`, `complete_with_review`, no run error (an earlier run `a89d5a2e…` of the same workflow was cancelled at 248/275 pages by something other than this assessment) |
| Time | 21m 10s |
| Pages | 275 processed · 262 delivered · 13 blank discarded · page 229 image-only |
| Review | 16 pages · 27 unresolved issues · 0 unverified · 6 pages with a repair error |
| Verification | `passed`: 262 pages checked, 0 integrity failures, 0 text-loss pages |

Models that answered (the pool changes every run, so compare counts with care): page auditor, 513 calls on 11 models (`poolside/laguna-xs-2.1` 232, `google/gemma-4-31b-it` 162, `meta/muse-glimmer-30b` 62, `nvidia/nemotron-3-ultra-550b-a55b` 13, `stepfun-ai/step-5-preview` 11, `codestral-2508` 10, `minimax-m3.1-flash` 8, `qwen3.8-27b` 6, `codestral-latest` 4, `gpt-oss-20b` 3, `xing4.0-29b-a4b:free` 2); text repair, 366 calls on 9 models (`laguna-xs-2.1` 273, `gemma-4-31b-it` 38, `minimax-m3.1-flash` 17, `muse-glimmer-30b` 14, `codestral-2508` 13, `codestral-latest` 4, `qwen3.8-27b` 3, `gpt-oss-120b` 2, `xing4.0` 2); structure repair, 2 calls on `ising-calibration-1.5-31b`; metadata, 1 call on `gpt-oss-120b`. One provider 504, nothing else.

## Comparison with workflow 1.10

| Metric | 1.10 | 1.12 | Change |
| --- | ---: | ---: | ---: |
| Review pages | 22 | 16 | −6 |
| Unresolved issue instances | 26 | 27 | +1 |
| Auto-applied issues | 114 | 109 | −5 |
| Pages with a repair error | 0 | 6 | +6 |
| LLM calls | 589 | 882 | +50% |
| Text-repair calls | 107 | 366 | 3.4× |
| Input / output tokens | 1.58M / 137k | 2.52M / 166k | +59% / +21% |
| Wall time | 16m 09s | 21m 10s | +31% |
| Unverified pages | 0 | 0 | |

## The checks the 1.12 hand-over asked for

| Check | Result |
| --- | --- |
| Review pages ≤ 19 | **16** ✔ |
| No `structure`/`other` finding about a picture | ✔ — the stored assessments hold **no** `structure` or `other` finding at all (the auditor prompt change was enough) |
| Page 37 reads `cyberattacks` or stays in review, never `cyber-attacks` | ✔ for the rule, ✘ for the text: it reads `cyber attacks` (the soft hyphen was removed, its space stayed). Fixed in 1.13 |
| Pages 240, 247, 250 repaired or still flagged | 240: four URL findings, two repaired (`regime -backed`, the Chinese Wikipedia link), two still flagged with no suggestion. 247 and 250: flagged, because their specialist ran out of turns |
| Text-repair calls below 107 | **✘ 366.** Each `check_patch` call is a model turn, and the six failed pages were also retried |
| No rejected `check_patch` patch left in an answer | Not measurable: rejected patches are not stored. Every stored proposal passed `validate_proposals` |
| `finding_verifier` calls and dismissals | **0 calls, 0 dismissals.** It ran on nothing: there were no `structure`/`other` findings and none under 0.8 confidence, and no page carries a "verifier unavailable" warning. It is wired (the factory builds it) but this run says nothing about whether it works |
| `quality-report.json` `verification.status` | `passed` ✔ |

## What was verified

95 model proposals were auto-applied; the rest of the 109 applied issues are mechanical fixes (5 control characters, 1 soft hyphen, table clean-ups). Character diff of every model proposal:

| Kind | Repairs | Verdict |
| --- | ---: | --- |
| Spaces only (stray space in a word, a URL, a heading) | 73 | all correct |
| Plain-prose hyphen inserted (`highprofile` → `high-profile`, `Asiabased` → `Asia-based`, `nationstate` → `nation-state`, `commandand-control` → `command-and-control` on pages 81 and 163, …) | 21 | all correct |
| Dangling hyphen joined (`spe- cific`, `moni- tors`, …) | 5 | all correct |
| Soft hyphen + space joined (`trade­ craft` → `tradecraft`, page 127) | 1 | correct |

No letter or digit changed outside a hyphen, no URL got a space. The publication check agrees (`passed`).

## What the 27 unresolved issues are

| Class | Pages | Cause |
| --- | --- | --- |
| **Specialist ran out of turns** (regression of 1.12) | 247, 250, 252, 253, 257, 259 | Reference pages with 2–8 URL stray spaces each. The model checked patches one by one; 8 turns were not enough, the answer was lost, the retry did the same. These are space-only edits that would have been applied |
| **The gate judged the neighbours of the edit** | 221 | The model proposed `commandand-control domain software-update.org`; the unchanged domain made the target "not plain prose" and the hyphen repair went to review. Pages 81 and 163 had a narrow target and were applied |
| Real defect, no suggestion | 240 (2) | URLs with a stray space and `\_` (`edu/ ~jain`, `Titan \_Rain`) — the specialist returned nothing for them |
| Real defect, gate refuses by design | 175, 264 | `CVE2010-0249` → `CVE-2010-0249` (an identifier with digits); `C C&C` → `C&C` drops a letter |
| Auditor noise | 165 | "Registrar split into two lines" — the evidence shows no split |
| Judgement calls | 189 (2), 261, 268, 271, 272 | OCR digits in a code block and a URL; a fused `[9` marker; two index tables and a row split across lines |

## Findings

### 1. `check_patch` one patch per call costs more turns than the specialist has

`SPECIALIST_MODEL_CALL_LIMIT` is 8, and each tool call is one model turn. A page with eight URL repairs needs ten or more. The middleware ends the run, no structured answer exists, and the retry repeats the loop. Six pages lost all their proposals; the call count tripled even on pages that worked.

### 2. The spacing gate looked at the whole target, not at what changes

`_is_spacing_repair` required the whole target to be plain prose. A model that copies the evidence line (which holds a domain name) gets a different verdict from one that copies the word. Run to run, the same defect lands in auto-apply or in review.

### 3. A soft hyphen followed by a space leaves a stray space

The mechanical fix removed the soft hyphen only. `cyber­ attacks` became `cyber attacks`, while the model-driven path joined `trade­ craft` into `tradecraft`. The book spells `cyberattack` 57 times.

## Fixes applied (workflow 1.13)

1. **One batched `check_patches` tool** replaces `check_patch` (`ai/bd/tools/patch/tool.py`; both repair specialists and their prompts): all patches of a page are checked in one turn, with a line of verdict per patch. A specialist that still hits the limit is told on its retry to check once and answer.
2. **The gate judges the changed words** (`repair_pages/domain.py:_changed_words`): the words around the edit that are identical in target and replacement no longer count. A hyphen repair beside a domain applies; a digit, a URL token or a letter that changes still goes to review (tests for `CVE2010-0249`, a hyphen inside a URL, `C C&C`).
3. **A soft hyphen + space + lowercase word is joined** (`patterns._SOFT_HYPHEN_BREAK_RE`, in the mechanical pass): `cyber­ attacks` → `cyberattacks`.
4. `WORKFLOW_VERSION` → `1.13`.

Replay of this run's 102 stored proposals through the new gate: exactly one outcome changes — page 221's hyphen repair is now applied (16 → 15 review pages); nothing that was applied is refused, nothing wrong is newly applied. Pages 37 and 127 now read `cyberattacks` and `tradecraft`. The six out-of-turn pages cannot be replayed (no proposals were stored); if their space-only URL repairs all apply, the next run should land near 9–10 review pages. That is a prediction, not a measurement.

### Tests

`test_bd_specialist_tools.py` (a scripted specialist checks two patches in one call and answers in two turns), `test_bd_ingestion_full_workflow.py` (wide target applies; digit, URL and letter changes refused; soft-hyphen join), `test_bd_replay_eval.py` and the other BD offline tests pass.

## Not changed

- **`SELF_DISMISSED_RE` and `IMAGE_PLACEMENT_RE` stay.** Review pages did not rise, but neither regex fired (no page carries a "described as normal" warning) and the verifier saw no candidate, so this run proves nothing about replacing them.
- **Pages 175, 264 and the index tables stay in review**: they change digits or letters or reconstruct a layout.
- **Page 240's two un-suggested URLs**: the prompt already says to remove a stray space in a URL; whether the batched flow now catches them is for the next run.

## Next run: what to check

Pages 247/250/252/253/257/259 no longer carry a `model-call limit` error and their URL spaces are repaired; text-repair calls well under 366 (each specialist run takes about two turns, a check and the answer, so the 1.10 count of 107 runs gives roughly 200); review pages ≈ 9–15; page 37 `cyberattacks`; verifier calls (still likely 0 — to exercise it, run the auditor evaluation on a page that has a `structure` finding). Record the model pool next to the counts.
