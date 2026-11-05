# Book Distiller Agentic Ingestion Assessment — workflow 1.6 run, 2026-11-05

> Historical report: the latest completed run is workflow 1.7; see the [workflow 1.7 assessment](BD-INGESTION-AGENTIC-AI-ASSESSMENT-2026-11-05-WORKFLOW-1.7.md). Its fixes shipped as 1.7 and were measured there (36 → 21 review pages, as replayed).

Latest run for *The Art of Cyberwarfare*, finished 19:43 UTC, the first run on the workflow 1.6 changes from the [workflow 1.5 assessment](BD-INGESTION-AGENTIC-AI-ASSESSMENT-2026-11-05-WORKFLOW-1.5.md). Read-only evidence from the live `coelhonexus-dev` API (status, LLM counters, all 275 per-page quality records); every one of the 63 model repairs that were auto-applied was read by hand. The fixes below are workflow **1.7**: offline-tested and statically replayed against this run, **not deployed and not measured by a new run**.

## Verdict

The 1.6 changes did what the replay predicted: **59 → 36 review pages, exactly the static estimate**, and auto-applied issues rose from 17 to 77. All 63 auto-applied model repairs I read are correct (space joins, `NOT E` → `NOTE`, broken URLs, `highvalue` → `high-value`). What remains is mostly model noise on `structure` findings, plus one-bad-proposal-loses-the-batch errors that cost real URL repairs. The Book Distiller is at the point where the next gain is a better model pool or a human pass, not more rules; 1.7 closes the cheap remaining gaps.

## Run examined

| Field | Value |
| --- | --- |
| Book | *The Art of Cyberwarfare* (`4c6c6327360f7cf5`) |
| Docling extraction | `c94a1d6883c04bb0a9b9d339f85348f4` |
| Agentic run | `53876f1e194d4df7b5e19cadae04eb9b`, workflow `1.6` |
| Status | `done`, `complete_with_review`, no run error |
| Time | 2026-11-05 19:31:46–19:43:38 UTC (11m 52s) |
| Pages | 275 processed · 262 delivered · 13 blank discarded · page 229 image-only |
| Review | 36 pages (all with unresolved issues; 5 also with repair errors) · 44 unresolved issues · 2 unverified (143, 251) |

## Comparison with workflow 1.5

| Metric | 1.5 | 1.6 | Change |
| --- | ---: | ---: | ---: |
| LLM calls | 489 | 579 | +18% |
| Input / output tokens | 1,143,513 / 91,673 | 1,452,222 / 103,367 | +27% / +13% |
| Wall time | 11m 16s | 11m 52s | +5% |
| Review pages | 59 | 36 | −39% |
| Unresolved issue instances | 69 | 44 | −36% |
| Auto-applied issues | 17 | 77 | +60 (62 are model space-joins) |
| Unverified pages | 3 | 2 | page 270 and 11/266 now audited; 143, 251 new |
| Pages with auditor warnings | 84 | 39 | −54% |
| Auditor findings discarded | 77 | 30 | −61% |
| Page-level repair errors | 5 | 5 | different causes |

Cost went up because the prompt change makes the auditor report broken words and URLs it used to skip (`split_word` findings 35 → 81); each is a specialist call. Per fixed issue that is cheaper than a review page. Different model pools ran each time (auditor over seven models), so token counts are not attributable to code alone. Zero provider-level LLM errors.

## What was verified

All 63 auto-applied model repairs were compared target→replacement with a character diff. Every one changes only spaces, plus 13 hyphen insertions in plain prose (`firststage` → `first-stage`, `Ukrainebased` → `Ukraine-based`, `up-todate` → `up-to-date`) and one dangling-hyphen join (`spe- cific` → `specific`). No letter changed, no URL character other than a stray space, no digit. The gate behaved as designed; the 19 `split_word` issues that stayed in review did so for the right reasons (letters change, URL with a hyphen insertion, or the proposal failed validation).

## What the 44 unresolved issues are

| Class | Issues | Cause |
| --- | ---: | --- |
| `structure`, mostly "figure with a caption" (43, 84, 114, 172, 186, 209, 222, 163, 166) or a mid-sentence page end (90) | 17 | Weak models report normal Docling output; two proposals invent an image path or a `<!-- pagebreak -->` marker |
| `split_word` | 19 | 7 are the page-245 URL batch lost to one ambiguous target; 2 are page 258's batch; the rest are `NOT E` at 0.8 confidence, a page-21 heading at 0.8, and cases needing a letter change |
| `text_corruption` | 4 | CVE hyphen (175), stray `1` in code (189 ×2), duplicated folio (269): judgement calls |
| `table_or_index_layout` | 2 | 211 (padded table), 274 (fused index lines) |
| `metadata`, `other` | 2 | A WHOIS record flagged as "metadata" (220), a folio on a caption (196) |

## Findings

### 1. One bad proposal discards the whole specialist batch

`validate_proposals` raised on an absent or ambiguous target, so the model's other proposals died with it and the retry usually repeated the mistake. Page 245 lost seven URL repairs this way (all `high` severity, all space-only fixes the gate would have applied), page 258 two, page 219 one, page 17 its `I NTRODUC T I ON` heading. Four of this run's five page errors are this.

### 2. Risk label and confidence gated a proof

Pages 56 (`timerequired` → `time required`), 136, 260 and 21/138 were correct space-only edits left in review because the model labelled them `medium` risk or 0.8 confidence. For an edit verified by the diff, the model's own labels carry no safety information.

### 3. Figure captions are still reported as layout defects

The auditor prompt now says picture markers and captions are normal, and findings discarded fell by 61%, but weak models still file "the page contains a figure and its caption, which is typical for mixed media" as `structure` low (and one as `metadata`). Two repairs are fabrications: page 163/166 replace `<!-- image -->` with `![Figure …](image_path.png)`; page 155 inserts `<!-- pagebreak -->` at confidence 1.0.

### 4. A fifth call-limit loss remained

Two attempts still ended in a model-call limit or an invalid response on pages 143, 251 (audit) and 160 (specialist). Attempt failures are roughly independent across Rotator models, so a third attempt removes most of them.

### 5. Soft hyphen followed by a stray space stayed in review

`trade\xad craft` (page 127) is a space-only join once the invisible soft hyphen is ignored, but the gate compared it literally.

## Fixes applied (workflow 1.7)

1. **Per-proposal validation.** An absent, ambiguous or out-of-context target now drops only its own proposal (its issue stays unresolved); the batch is rejected for a retry only when nothing usable survives. Contract violations (wrong page, hash, issue, scope) still reject the batch.
2. **Space-only gate relaxed to the proof.** Risk may be low or medium (not high/critical) and confidence ≥ 0.8 (`SPACING_REPAIR_CONFIDENCE`, was 0.9/low-only). Soft hyphens are ignored in the comparison. Hyphens may be added, or dropped where the source left them dangling before a space; an existing in-word hyphen is never removed (`state-of-the-art` → `stateoftheart` stays review).
3. **No new links, images or comments.** `_invents_text` also drops a proposal whose replacement introduces a Markdown link/image destination or an HTML comment that the target does not contain (whitespace-insensitive, so URL repairs still pass).
4. **Evidence screen.** A `structure` finding whose only evidence is caption lines (`Figure 3-1: …`, `Table …`, `Listing …`) is rejected; `metadata` findings from the page auditor are rejected (book metadata has its own node and no specialist route).
5. **Attempts.** `PAGE_AUDIT_ATTEMPTS` and `SPECIALIST_ATTEMPTS` 2 → 3.
6. `WORKFLOW_VERSION` → `1.7`; architecture-doc policy paragraph updated.

Replay against this run: **36 → 21 review pages** (15 cleared), with 6 more issues auto-applied, 9 findings dropped by the evidence screen and 1 fabricated proposal dropped. The estimate does not count the extra repairs per-proposal validation and the third attempt should recover (page 245 alone is 7 issues).

### Tests

`scripts/server/test_bd_ingestion_full_workflow.py` adds: medium-risk and 0.8-confidence space joins, soft-hyphen join, dangling-hyphen join, a refused in-word hyphen removal, a refused hyphen drop without a dangling space, made-up image path and page-break marker dropped while a URL repair inside a real link passes, a batch keeping its valid proposal when another target is ambiguous (and still raising when none is usable), caption-only and `metadata` evidence rejected. All BD offline tests pass.

## Not changed

- The remaining 15 `structure` findings are judgement calls (`BitPaymer` caption deleted at 0.9, flattened code block on page 169, WHOIS fields fused on page 205). Page 205's newline insertion is probably right but is not provable by diff; review stays.
- Mid-sentence page ends (90) still get reported despite the prompt.
- The auditor model pool decides how much of this noise exists; no rule substitutes for it.
- Cross-page hyphenation and folio-on-caption (196) remain review items.

## Next run: what to check

Review pages ≤ 21, page 245's URLs repaired, pages 143/251/160 no longer degraded, no `structure` finding whose evidence is only a figure caption, no `image_path.png` or `pagebreak` replacement, and a sample of the new medium-risk space joins against the source.
