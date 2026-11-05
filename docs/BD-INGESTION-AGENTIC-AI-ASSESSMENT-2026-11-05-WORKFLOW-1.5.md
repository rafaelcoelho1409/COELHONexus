# Book Distiller Agentic Ingestion Assessment — workflow 1.5 run, 2026-11-05

> Historical report: the latest completed run is workflow 1.6; see the [workflow 1.6 assessment](BD-INGESTION-AGENTIC-AI-ASSESSMENT-2026-11-05-WORKFLOW-1.6.md). Its fixes shipped as 1.6 and were measured there.

Latest run for *The Art of Cyberwarfare*. Read-only evidence from the live `coelhonexus-dev` API (status, LLM counters, all 275 per-page quality records) and the MinIO `quality-report.json`. The fixes below are local workflow **1.6** changes: offline-tested and statically replayed against this run's persisted records, **not deployed and not measured by a new run**.

## Verdict

Workflow 1.5's evidence checks worked: review pages fell from 127 to 59 and unresolved issues from 150 to 69. The remaining review load is mostly **fixable by code, not by a better model**: 23 of the 69 issues are proposals that only add or remove spaces and were still sent to a human, 5 are findings whose only evidence is a picture marker or a bare page number, and three "high-confidence" proposals are invented text. Three pages were left unverified, one of them (page 270) only because table padding inflated its size 13×.

## Run examined

| Field | Value |
| --- | --- |
| Book | *The Art of Cyberwarfare* (`4c6c6327360f7cf5`) |
| Docling extraction | `c94a1d6883c04bb0a9b9d339f85348f4` |
| Agentic run | `ed928851eaf44bde88c2ed0772df3110`, workflow `1.5` |
| Status | `done`, `complete_with_review`, no run error |
| Time | 2026-11-05 18:50:35–19:01:51 UTC (11m 16s) |
| Pages | 275 processed · 262 delivered · 13 blank discarded · page 229 image-only |
| Review | 59 pages (58 with unresolved issues, 5 with repair errors) · 69 unresolved issues · 3 unverified (11, 266, 270) |
| Metadata | accepted, `review_required: false` |

## Comparison with workflow 1.4

| Metric | 1.4 | 1.5 | Change |
| --- | ---: | ---: | ---: |
| LLM calls | 513 | 489 | −4.7% |
| Input / output tokens | 1,132,182 / 99,484 | 1,143,513 / 91,673 | +1.0% / −7.9% |
| Wall time | 10m 01s | 11m 16s | +12% |
| Findings kept | 171 | 86 | −50% |
| Review pages | 127 | 59 | −54% |
| Unresolved issue instances | 150 | 69 | −54% |
| `toc_noise` / `control_characters` findings | 50 / 6 | 3 / 1 | all remaining are genuine |
| Auto-applied issues | 21 | 17 | all deterministic (table cleanup, soft hyphen, control chars) |
| Page-level repair errors | 12 | 5 | −58% |
| Unverified pages | 1 | 3 | +2 |

Different model pools ran each time (this run's auditor spread over eight models, led by `codestral-latest` with 146 of 386 calls), so cost and time differences are not attributable to code alone. Zero provider-level LLM errors in both.

## What the 69 unresolved issues are

| Class | Issues | Treatment |
| --- | ---: | --- |
| Proposal changes only spaces (`I NTRODUC T I ON`, `NOT E`, `https://www .site.com`, `denial-ofservice`) | 23 | **Auto-apply** (fix 1) |
| Evidence is only `<!-- image -->` or a bare page number | 5 | **Drop** (fix 2) |
| Model-invented text at confidence 0.8–1.0 (pages 90, 115, 234) | 3 | **Drop the proposal** (fix 3) |
| Intact headings reported as "suspiciously split" (25, 107, 155, 233, 237, 247) | 6 | Prompt fix (fix 4); unmeasured |
| Specialist hit its model-call limit or broke its schema (88, 240, 252, 269) | 4 pages | **Retry** (fix 5) |
| Needs a human: figure caption → heading (84, 169), index tables (267, 269), `avail-`/`ture` across a page break (88, 179), folio fused to prose (38, 41, 222), duplicated back-cover bullets (275), `&amp;` (81) | rest | Review stays |

Replaying the persisted records through fixes 1–2 clears **23 of the 59 review pages (59 → 36)** statically: pages 5, 15, 17, 31, 32, 55, 79, 109, 110, 129, 134, 144, 157, 163, 188, 206, 224, 239, 243, 249, 255, 261, 263.

## Findings

### 1. Space-only repairs were never applied

`apply_proposals` auto-applied only soft-hyphen and control-character removal, so every model proposal went to review, however trivial. All of these were proposed at 0.9–1.0 confidence, low risk, and differ from the source only by spaces: `ACKNOWL EDGMENT S` → `ACKNOWLEDGMENTS`, `AT TACKS`, `DoDaffiliated` → `DoD affiliated`, and broken URLs (`research .checkpoint.com`). Such an edit cannot change a word, digit or character, so the model's confidence is irrelevant to its safety; what matters is whether the edit stays inside the finding and leaves Markdown structure alone.

### 2. Picture markers and folios still count as evidence

Five findings cite only `<!-- image -->` (pages 109, 188, 206, 224) or a lone page number (110). Page 109's "fix" replaces the marker with `![Figure 4-1: …](image_path.png)`, a made-up image path. Docling's `<!-- image -->` is the normal marker for an extracted picture.

### 3. High-confidence proposals can be fabrications

Page 234 (confidence 1.0, low risk) appends `[Content placeholder — to be filled by analyst]` under a heading. Page 115 (0.85, "high" severity) extends `In the private industry, cyber` into a full invented sentence. Page 90 does the same. A page ending mid-sentence is normal: its paragraph continues on the next page. These stayed review-only only because of the confidence gate, and they would mislead a reviewer.

### 4. Models report intact headings as split

Six `split_word` findings carry the text "Suspiciously split heading word(s)" on headings that are fine (`Hidden Lynx Espionage Campaigns`, `ELECTION HACKING`, `## Chapter 2`). The wording is the auditor prompt's own phrase and its `L ANDSCAPE` example. Deterministic inventory already catches letter-spaced uppercase headings, so the LLM does not need that priming. The auditor discarded 77 findings and 97 evidence excerpts in this run (164 warnings over 84 pages) — the weaker pool models produce a lot of noise that validation absorbs at the cost of calls.

### 5. A model-call limit ended an attempt for good

Both retry loops treated "model-call limit reached" as terminal (`break`), though the Rotator can route the next attempt to another model. Three of this run's five page-level errors (88, 240, 252) and one unverified page (266) are call-limit endings; page 11 was an invalid-assessment fallback after both attempts.

### 6. Padded tables tripped the size limit

Page 270's raw Markdown is 43,064 bytes against a 30,000-character audit/repair limit, but its content (an index table, 3,363 characters after cleanup) is small: the bulk is column padding. It was skipped by the auditor and every specialist and is still unverified. Pages 263, 265, 267 and 272 (11–27 KB) are the same shape and sit near the limit.

## Fixes applied (workflow 1.6)

1. **Space-only auto-apply.** `repair_pages/domain.py:_is_spacing_repair` + `apply_proposals`. A `split_word`, `fused_marker` or `text_corruption` proposal is applied at confidence ≥ 0.9 (`SPACING_REPAIR_CONFIDENCE`) and low risk when: the target overlaps the issue's own evidence; only spaces differ (plus intra-word hyphens, only when both strings are plain letters/apostrophes/hyphens — never in strings with digits, URLs' punctuation or code); leading/trailing whitespace is unchanged; and list, heading and quote markers are unchanged. Joining a line break (`cyber-\nwarfare`), changing a letter, `SHA-256` → `SHA256`, gluing `- item` and structure edits stay review-only.
2. **Evidence screen.** `page_audit/domain.py:_has_page_content` rejects an auditor excerpt that is only picture markers, whitespace and bare page numbers, for every code except the three with their own checks.
3. **Invented-text guard.** `validate_proposals` drops a proposal whose replacement is the target plus more than one new word. The issue stays in review without the fabricated patch.
4. **Auditor wording.** System prompt and `audit-docling-page` skill (1.2): `split_word` needs a word visibly broken by a space or line break; intact headings, `<!-- image -->` and its caption, edge page numbers, `&amp;` and mid-sentence page edges are never findings. The text-repair skill (1.1) now tells specialists that space-only joins are safe to propose and are applied automatically.
5. **Call-limit retry.** The `break` on `ModelCallLimitReachedError` is removed in `audit_batch/service.py` and `repair_pages/service.py`; the existing two-attempt loop covers it.
6. **Size gate on effective length.** `inventory/domain.py:effective_length` collapses space runs before the 30,000-character comparison in the audit and repair services; the model still receives the page unchanged.
7. `WORKFLOW_VERSION` → `1.6`; `SPACING_REPAIR_CONFIDENCE = 0.9` in `domains/bd/ingestion/ai/params.py`; architecture doc policy paragraph updated.

### Tests

`scripts/server/test_bd_ingestion_full_workflow.py` gains: replayed run proposals (8 applied, 10 refused cases), picture-marker/folio evidence, effective length, invented-text drop, call-limit retry for auditor and specialist, and a confident letter-changing repair staying review-only. Two assertions in it were already stale and are corrected (the retry fake predated the slimmed `PatchDraft` payload; the page-quality case now uses a letter-changing proposal for the review path). Also fixed: `test_bd_ingestion_run_contract.py` (schema version `1.2`) and `test_bd_page_auditor.py` (missing `domains.bd.ingestion.ai` import). All BD offline tests pass.

## Not changed

- **Heading hallucinations (fix 4) are prompt-only**; effect unmeasured. A deterministic check is not reliable: `ELECTION HACKING` and `DRI VEN` are structurally identical without a dictionary.
- Page 21's `L ANDSCAPE` is a deterministic finding the specialist returned no proposal for; fix 1's skill wording should help, but a deterministic single-capital join was not added (lone `A`/`I` are real words).
- Cross-page hyphenation (`avail-` → `available`, page 88) needs the next page and stays a human call.
- Trailing folios fused to prose (38, 41, 222) and `C&amp;C` (81) remain review items.
- Auditor model mix is a Rotator pool decision.

## Next run: what to check

Review pages ≪ 59 (static estimate 36 or fewer), `patches_auto_applied` rising from 17, unverified pages 11/266 back to audited, page 270 audited, no split-heading finding on an intact heading, and a sample of auto-applied space joins on pages 255/258/261/263 against the source.
