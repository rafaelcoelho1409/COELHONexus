# Book Distiller Agentic Ingestion Assessment — 2026-11-05

Second run, after the quality-disposition, deterministic-check, bounded-repair-context and metadata changes. Compared with [the 2026-11-04 run](BD-INGESTION-AGENTIC-AI-ASSESSMENT-2026-11-04.md). Read-only: data came from the `coelhonexus-dev` server API and the run's MinIO artifacts.

## Verdict

The workflow is now **honest and ~5.6× cheaper in input tokens**, but it still **repairs almost nothing**: 0 patches applied, 85 pages left in review, the known defects on pages 3, 17 and 21 are untouched, and the book title is still the raw filename. The run is correctly reported as `done` / `complete_with_review`, which is why the Agentic AI box shows no green check (the check required `quality_status == "complete"`).

## Run

| Field | Value |
| --- | --- |
| Book | *The Art of Cyberwarfare* (`4c6c6327360f7cf5`), 275 pages |
| Docling run | `c94a1d6883c04bb0a9b9d339f85348f4` |
| Agentic run | `f229be1d2c73422da353551f95c6d4b1`, workflow `1.1` |
| Status | `done`, no run error, all 5 graph nodes `done` |
| Quality status | `complete_with_review` |
| Duration | 23.2 min |
| Pages | 275 audited/processed · 262 delivered · 13 blank discarded · 130 flagged for review (85 with issues) |

## What improved

| | 2026-11-04 | 2026-11-05 |
| --- | ---: | ---: |
| LLM calls | 676 | 596 |
| Input tokens | 8,954,083 | 1,592,893 (−82%) |
| Output tokens | 218,950 | 154,101 |
| Text-repair input tokens | 7,735,410 | 252,820 (−97%) |
| Structured-output fallbacks | 39 | 21 |
| Audit warnings reported | 0 (misleading) | 73 |
| Blank pages | published empty | 13 discarded, numbers preserved |
| LLM errors in counters | 0 | 0 |

- Blank pages (2, 4, 8, 10, 16, 78, 106, 126, 178, 200, 232, 236, 262) are dispositioned deterministically with no auditor call; page 229 is classified `image_only`.
- Repair context is bounded: 107,889 chars sent vs ~240,113 unbounded (132,224 saved), which is where the token drop comes from.
- The quality report now separates processing from quality and lists fallbacks, unresolved issues and review pages.
- Detection now finds what the last run missed: 36 `toc_noise`, 32 `split_word`, 12 `table_or_index_layout`, 3 `control_characters`, 3 `soft_hyphen`, 5 `text_corruption`, 9 `structure`.

## What is still wrong

### 1. Repairs almost never land (0 applied, 20 proposals)

| Cause | Count |
| --- | ---: |
| Specialist returned a non-object (`PatchProposalBatch: Input should be a valid dictionary`) — 24 structure, 6 text | 30 |
| `context_before` / `context_after` anchor does not match the page | 16 |
| `target_text` not present in the page (pages 3 and 17) | 2 |
| Valid proposals | 20 (7 at confidence ≥ 0.98) |

- The 30 schema failures and 16 anchor mismatches mean the free models either don't honour the structured-output contract or paraphrase the quoted anchors. Requiring the model to quote anchors verbatim is the fragile part.
- Visible result: page 3 still contains the `\x07` control character, page 17 still reads `I NTRODUC T I ON`, page 21 still reads `L ANDSCAPE`.
- Control characters and soft hyphens are mechanical. They should be fixed **deterministically in code**, not by asking a model for a patch. They are 6 issues here and all stayed open.

### 2. Book title is still the raw filename

`book-metadata.json` holds a validated, evidence-backed proposal — title "The Art of Cyberwarfare", subtitle, author Jon DiMaggio, confidence **0.9** — but the published title is `The Art of Cyberwarfare An Investigators Guide … (z-library.sk, 1lib.sk, z-lib.sk)` with no authors or subtitle. Two defects stack:

1. The proposal is gated by `AUTO_APPLY_CONFIDENCE = 0.98`, the page-patch threshold. A grounded 0.9 proposal is discarded (`review_required: true`).
2. The fallback then fails: `_matches_filename_label` strips an "extension" from the title with `PurePath(...).stem`, and `.sk)` in `z-lib.sk)` looks like one, so the filename-derived title is treated as a genuine supplied title and the page-1 heading fallback is never reached.

### 3. Auditor reliability

- 21 pages (9, 14, 18, 74, 82, 86, 98, 107, 117, 122, 128, 132, 134, 139, 143, 166, 172, 196, 215, 237, 249) fell back to deterministic-only audits after structured-output validation failures. Page 9, the contents page, is one of them, so its TOC problems are unexamined.
- About 40 pages carry a warning that auditor evidence or a whole finding was discarded as not exact source text (`Discarded … evidence excerpt(s)`), and 5 page-kind labels conflicted with Docling's picture counts. Discarding is correct, but each one is a wasted call.
- 364 auditor calls for 261 pages that needed one: ~100 extra calls are retries and provider failover.

### 4. Issue accounting is inflated and noisy

The same defect is reported twice when the deterministic check and the LLM auditor both see it (page 3: two `control_characters`; page 17: two `split_word`; page 21: two `split_word`). The 106 "unresolved issues" overstate distinct problems. `toc_noise` (36) is the largest class, but dotted leaders in a contents page are normal formatting; it should be a page-level hint, not 36 review items.

### 5. Review volume is too high to be useful

`review_page_count` is 130 but only 85 pages have any issue; the other 45 are pages whose audit only carried warnings (discarded evidence, kind conflicts). A reviewer cannot act on 130 pages. Warnings and issues should be separate counts.

## Fixes, in order

1. **Fix the title** — own threshold for metadata (a grounded proposal at ≥ 0.85 is accepted), and fix the filename-label match (compare against the full filename stem, not `PurePath(title).stem`) so the page-1 heading fallback works. Also strip bracketed source-site tags from filename-derived titles.
2. **Apply mechanical fixes in code** — control characters and soft hyphens, with the original preserved in the raw run; no model call.
3. **Make repair proposals robust** — anchor by character offset or by a code-computed window instead of model-quoted `context_before/after`; on a non-object response retry once with the error text, then fall back to the next model, not to review.
4. **Dedupe issues** — merge a deterministic and an LLM issue with the same code and overlapping evidence; keep `toc_noise` at page level.
5. **Separate warnings from issues** in the quality report and in the review count.
6. **Cut auditor retries** — route the 21 fallback pages and any page with a TOC to a model that honours structured output reliably; re-audit only those.
7. **Raise concurrency** (see below) only after 3 and 6, since more parallel calls will increase provider failures on the current models.

## Shipped after this assessment (workflow `1.2`, offline-tested, needs server redeploy)

| Fix | Change |
| --- | --- |
| 1. Title | Metadata uses its own `METADATA_ACCEPT_CONFIDENCE = 0.85`; the filename-label match no longer treats `.sk)` as an extension; filename-derived titles drop download-site tags. |
| 2. Mechanical noise | Control characters and soft hyphens are removed in code after model patches, are no longer sent to a specialist, are counted as auto-applied, and the raw page is untouched. |
| 3. Repair robustness | A wrong `context_before/after` is dropped instead of rejecting a valid unique target; a rejected specialist response is retried once (`SPECIALIST_ATTEMPTS = 2`) with the rejection reason in the request; prompts say anchors may be empty. |
| 4. Dedupe | Auditor findings overlapping a deterministic finding of the same code are merged; contents noise collapses to one issue per page with all excerpts. |
| 5. Warnings vs issues | Handled audit warnings no longer force review or `complete_with_review`; a page needs review for unresolved issues, repair errors or a degraded audit. The report adds `review_reasons` and `mechanical_fixes`. |
| 6. Auditor retries | One retry (`PAGE_AUDIT_ATTEMPTS = 2`) before falling back to deterministic findings. |

Not changed: concurrency (still 4 audit / 4×2 repair), and the page-patch auto-apply policy for `split_word` and structure fixes, which stays review-only.

## Concurrency headroom

Today: audit = 4 pages × 1 call = 4 concurrent calls; repair = 4 pages × up to 2 specialists = 8; metadata = 1. Docs Distiller's ceiling is 16 (Planner `off_topic` 16, Synth `digest` 16, `chapter_assign` 12, `doc_distill` 10, `sawc` 8); the Rotator's total capacity was documented as ~19 slots (2026-09-07), and DD lowered 24 → 16 after burst timeouts. So BD can go to **16 concurrent calls** at most: audit `PAGE_AUDIT_CONCURRENCY = 16`, repair `PAGE_REPAIR_CONCURRENCY = 8`.

## Basis

Run manifest, `quality-report.json`, `book-metadata.json`, per-page `assessments/` and `patch-proposals/`, and the run's LLM counters (596 calls, 0 recorded errors). Pages 3, 17 and 21 were read through the page API (`content_source: agentic`). Counts are from the artifacts, not estimates; no page was proofread end to end.
