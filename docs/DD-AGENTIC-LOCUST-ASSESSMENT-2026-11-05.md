# DD Agentic AI — Locust run assessment (2026-11-05)

Run `3cd93054d7204ed1ae96d753bfe44988`, slug `locust`, namespace `coelhonexus-dev`, Celery task `81665b3b`.
Result: **failed** after 867 s in `audit_pages` — `OpenAIAPIError: 504 TimeoutError (attempt 3)`.

**Status:** findings were measured from the run artifacts and provider logs. Fixes 1–5 below are now implemented in the local working tree, with offline checks added; they have not been deployed, and Locust has not been rerun.

## Timeline

| Time | Event |
|---|---|
| 15:11:14 | Tier 1 fetch of `llms-full.txt` (212 KB), 8 images stored |
| 15:11:16 | `split_monolith` → h1-fallback → 24 pages |
| 15:11:18 | audit starts (4 pages at a time) |
| ~15:16 | 23 of 24 pages audited and saved |
| 15:16:37 → 15:25:41 | page 23 (`API`, 52 KB, 16.7k input tokens) alone: three requests, each ended in a Rotator 504 at its 180 s wall (15:19:38, 15:22:39, 15:25:41) |
| 15:25:41 | run fails; staging prefix cleaned (24 objects); audits stay under `agentic/{run}/audit/` |

About 9 of the 14 minutes were spent on that one page.

## Findings

### Why it failed
- Rotator saw 57 `dd_ingestion_page_auditor` requests: **52 × 200, 5 × 504**. Every 504 was a request on a large page (9.1k–16.7k tokens) that hit the Rotator's default 180 s wall.
- Successful latency is bimodal. Median 18 s; fast arms (`nemotron-3-ultra`, `minimax-m3.1-flash`) answer in 3–30 s. One arm (`ising-calibration-1.5-31b`) either fails fast with `unknown` or succeeds only after 98–139 s. In the failing cascades another arm hung (`minimax` 161 s, `nemotron` 61 s) while the rest were `rate_limit`.
- Large inputs have a tiny eligible pool (`n_eligible` 2–4), and the BD ingestion was using the same Rotator (1650 calls) until 15:13.
- The client sends no `timeout_s`, so the Rotator never gets `max_wall_s` and uses its 180 s default. The SDK then retries (`max_retries=2`), so one page can burn 3 × 180 s = 9 min, as happened.
- One failed page aborts the whole run (`_gather_cancel_on_failure`) and a new run gets a new `run_id`, so the 23 good audits are not reused.
- The largest page was scheduled last (idx order), so its slow retries became a pure tail.

### Pages
- Sizes: 105 B … 52 KB. Three pages are over 20 KB: 4 (23 KB), 5 (41 KB), 23 (52 KB). `OVERSIZED_SPLIT_BYTES` is 100 KB, so none was split further.
- Page 5 ("Configuration", 41 KB) came back with `title=""`, `title_confidence=0`.

### Audit quality (23 audits, 43 findings: 34 low, 9 medium, 0 high)
- Categories: `markdown_formatting` 30, `duplicate_boilerplate` 4, `other` 4, `malformed_heading` 3, `encoding_artifact` 2. Page kinds: 21 documentation, 1 index, 1 tutorial.
- **About 16 of the 43 findings are one split defect.** The raw file puts `---` and `<a id="section"></a>` right before each section's `# Heading`. The H1 cut falls between the anchor and its heading, so every page ends with the *next* page's anchor and a `---`, and that page starts without its own anchor (`#installation` links lose their target). Cause: `split_markdown_by_headings` in `domains/dd/ingestion/post/domain.py` cuts at `tok.map[0]` of the heading.
- **False positives:** four findings (pages 3, 5, 14, 20; two of them medium) flag `/api/v1/dd/ingestion/locust/artifacts/<id>.png` image paths. Those are the application's own stored copies of the images, not defects. The repair skill already says to preserve URLs, so no damage, but the findings are noise.
- **Weak findings:** pages 4 and 16 report "code blocks not properly formatted" with a whole valid fenced block as evidence; page 4 reports "repeated navigation" with evidence `---`.
- **Real findings worth keeping:** empty-href links `[text]()` (pages 2, 5, 22 — in the source), a mis-indented GitHub Actions YAML (page 10), `<br/>` in table cells (page 15), stray `history` line (page 1).

## Fixes (implemented locally; deployment pending)

| # | Fix | Where | Why |
|---|---|---|---|
| 1 | Pass a 120 s per-call timeout (Rotator `max_wall_s` is 117 s) and disable nested SDK retries so page-level retries own the retry budget | `ai/dd/agents/ingestion/{page_auditor,page_repair}/{config,agent}.py` | 51 of 52 successes were ≤ 117 s; a stalled call is bounded, without three hidden SDK attempts multiplying each page retry |
| 2 | Retry 5xx / 429 / timeout failures and empty structured responses up to three times, waiting 20 s then 40 s after transient failures | `domains/settings/chat/service.py` and `ai/dd/nodes/ingestion/{page_audit/node.py,page_repair/node.py,page_audit/params.py}` (+ `scripts/server/test_dd_page_agents_retry.py`) | transient failures no longer abort immediately or retry back-to-back while provider arms are rate-limited |
| 3 | Start the largest pages first in both auditing and normalization | `ai/dd/nodes/ingestion/{audit_batch/service.py,normalize_pages/service.py}` (+ `scripts/server/test_dd_page_agents_retry.py`) | the slowest pages begin early rather than becoming a pure tail |
| 4 | Keep a section's separator and adjacent `<a id="…"></a>` anchor(s) with its heading when splitting | `domains/dd/ingestion/post/{domain.py,patterns.py}` `split_markdown_by_headings` (+ case in `scripts/server/test_ingestion_tier1.py`) | keeps in-page links on their page and prevents the next section's anchor from becoming evidence on the previous page; no source text is removed |
| 5 | Tell the auditor that `/api/v1/dd/ingestion/<slug>/artifacts/<path>` image paths are valid | `ai/dd/skills/ingestion/page-auditor/audit-documentation-page/SKILL.md` | addresses the four findings that treated application-served images as broken links |

Deliberately **not** proposed:
- Stripping the trailing `---` at the split: it is a section separator, but removing it is normalization, which stays with the agent.
- Reusing audits across runs (needs a prompt/skill fingerprint to avoid serving stale audits while the prompts are being iterated). Worth doing if reruns of a failed run keep being expensive.
- Rotator changes (separate repo): a per-arm deadline for the large-input bucket would stop one hung arm from consuming the whole wall.

The offline checks were added but not run in this continuation. A Celery worker redeploy is required before the timeout, retry, scheduling, and auditor-skill changes affect a run.

## Next steps
1. Run the focused DD page-agent retry check and the Tier 1 splitter check.
2. Deploy the Celery worker so fixes #1–#5 take effect, then rerun Locust.
3. A 504 on page 23 is still possible while the large-input pool is small; if it recurs, check `/routing/health` on the Rotator before changing code.
