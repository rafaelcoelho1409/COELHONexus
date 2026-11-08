# DD Agentic AI — DeepAgents run assessment (2026-11-07)

Run `80dc906e582741fe80d891913b580c37`, slug `deepagents`, Tier 3 (`https://docs.langchain.com/sitemap.xml`, markdown twins), namespace `coelhonexus-dev`. The first live run of the agentic stage after the 2026-11-06 changes (`check_patch`, tool allowlists, harness-profile fix). Sources: the run record, LLM counters and URL records from the API, and every stored assessment, input page and output page read from MinIO through the server pod. Nothing was changed on the cluster.

## Verdict

**The stage changed 3 of 99 pages and all 3 changes were wrong or unneeded, while the furniture that does need removing stayed on every page.** One of the three (page 19) was corrupted. The cause of the missed furniture is not the model: since commit `813ae9b8` nothing runs the deterministic normalizer on Tier 2–4 pages (Tier 1 normalizes its bundle itself), and the agentic stage was expected to do that work. The fixes below restore it and close the two ways the stage damaged pages. They are offline-tested and not yet run live.

## Run

| Measure | Value |
| --- | --- |
| Pages | 70 fetched, 99 after oversize pages were split; 96 audited, 3 audits unavailable (pages 35, 49, 72) |
| LLM calls | 122 (97 audit, 25 repair); 1,196,554 tokens in, 120,842 out |
| Provider errors | 19 in the audit (9 rate limit, 10 504); page 75's repair gave no structured answer twice |
| Findings kept | 34 (26 markdown formatting, 3 duplicate section, 2 other, 2 malformed heading, 1 encoding artifact) |
| Patches | 4 accepted, 0 rejected; pages 19, 28 and 93 changed |
| Audit models | 8 answered; `poolside/laguna-xs-2.1` took 46 calls, `gemma-4-31b-it` 38 |

The three unavailable audits are two 504 time-outs and one 429 ("rotator busy: 16 concurrent chat…"). Their pages kept the extracted text, as designed.

## Findings

### 1. Furniture on every page, none removed

The extracted pages are Mintlify's markdown twins. On the 70 fetched pages (the 29 split children carry parts of them):

| Residue | Pages | Removed by the stage |
| --- | ---: | --- |
| 3-line `> ## Documentation Index … llms.txt` banner at the top | 70 | 0 (4 pages got a finding, 66 none) |
| `***` rule, "Connect these docs … via MCP", "Edit this page on GitHub or file an issue" at the bottom | 70 | 0 |
| Code-fence attributes (`theme={…}`, `icon=`, …) | 1,508 | 0 |
| Code annotations | 159 | 0 |

`normalize_doc` already strips the banner, the attributes and the annotations (run on the stored inputs: 70 banners, 1,508 attributes, 159 annotations). `Store.add_page` stopped calling it in `813ae9b8`, and `ensure_framework_normalized`, which would have caught up, skips agentic manifests and has no caller. Every Tier 2–4 doc ingested since then carries this residue into the Planner and Synth.

### 2. The three changed pages

| Page | Change | Verdict |
| --- | --- | --- |
| 19 *Customization › Backends* | Removed `>` from part of a quoted `[tab]` block and of a `[!WARNING]` callout | **Wrong.** The quote was left half-open (`>` lines under the edit, `> > Use with caution` cut from its warning). Both are the site's export of a tab and a callout. |
| 28 *Dynamic subagents* | Un-quoted the banner | **Worse than before.** The banner became a real `## Documentation Index` heading ahead of the H1. |
| 93 *Tool integrations* | `\| :- \| :- \|` → `\| - \| - \|` | Unneeded. `:-` is a valid GitHub-flavoured alignment row; the change drops the left alignment. |

### 3. What the 34 findings were

29 of the 34 are conventions or nitpicks read as defects: tabs, cards and callouts as block quotes (4), a code-fence attribute (1), the banner itself (4 pages), alignment rows called "non-standard" or "not aligned" (8, pages 12, 69, 70, 89, 90, 93, 94), HTML `<table>`/`<th />` (2), six "Table header 'Backend' is not properly linked" on page 74, three more table-cell nitpicks (pages 69, 70, 90), and page 8's Mermaid `#43;`, which Mermaid reads as the entity code for `+`. The other 5 are the source's own defects: a typo ("agenet", "laets") on page 27, a repeated `Cisco AI Defense` row and three empty headings on page 75, repeated rows in the tables on pages 70 and 90. None needs an automatic edit.

### 4. Scope

`/oss/python/releases/` is in the catalog's `include` list, but Tier 3 also applies the default noise filter (`*/releases/*`), so `releases/langchain-v1` and `releases/langgraph-v1` (release notes) and `releases/changelog` were never fetched. Tier 1 and 2 use only the filter the catalog declares.

## Fixes applied

Offline-tested; nothing deployed.

1. **The audit reads normalized pages** — `domains/dd/ingestion/ai/service.py`: `normalize_source` runs `normalize_doc` on each page as its input is frozen. A run that is resumed keeps the exact bytes its failed run froze (the freeze is immutable), and a page the normalizer cannot read is audited as it came.
2. **Mintlify footer** — `corpus_normalize`: a new pass drops the trailing "Connect these docs" and "Edit this page on GitHub" lines, and a trailing `***` or `___` rule goes like a `---` one. This bumped `NORMALIZER_VERSION` 11 → 12. Over the 99 stored pages: 140 link lines and 70 rules removed, no "Connect these docs" or "Edit this page" left, a second pass changes nothing, 7.7% fewer bytes.
3. **More export conventions** — `ai/dd/agents/ingestion/page_auditor/patterns.py`: block-quote tabs, cards, steps and callouts, and a sound table head (header row, alignment row, equal widths), join the table the audit filter and the auditor prompt both render. A finding that quotes one is dropped; a ragged table, a quote that is only a quote, or a data row is not.
4. **Quote-cutting patches are refused** — `ai/dd/nodes/ingestion/page_repair/domain.py`: `cuts_block_quote`, part of `patch_rejection`, so the repairer's `check_patch` tool and the final gate both refuse a patch that takes `>` off part of a quote that goes on above or below it, with a hint. A quote unquoted whole is not a cut.
5. **Catalog** — `sources.yaml`: DeepAgents, LangChain and LangGraph set `disable_defaults: true` and exclude only `/oss/python/releases/changelog`. DeepAgents is now 72 pages in scope (was 70 fetched), with the two release notes.
6. **Control-character cleanup and report counts** — `corpus_normalize` now removes non-whitespace Unicode `Cc` characters before the audit and reports per-page counts to the Pages quality view. This bumped `NORMALIZER_VERSION` 12 → 13. It preserves Markdown whitespace and makes the cleanup auditable instead of silently changing the source.

Replay on this run: 15 of 34 findings are no longer raised (5 quoted text the normalizer removes, 10 are conventions); the 19 left are mostly table nitpicks and source defects. The gate now refuses both page 19 patches. Pages 28 and 93 no longer arise: the banner is gone before the audit and the sound-table finding is dropped.

Tests: `test_dd_ingestion_agentic_inputs.py` (new), additions to `test_dd_page_agents_retry.py` and `test_ingestion_polish.py`. Nine of ten DD tests pass; `test_infra_boundary` fails on `ai/bd/graphs/distill/entities.py:8` importing `infra`. `test_ingestion_polish.py` ran stale on a fake store without `run_id`; I fixed that and the storage key prefix, and it passes through the normalizer checks but still stops at a later stale check (`framework_parts`), which I left.

## Not changed

- **Unavailable audits (3 of 99).** Provider time-outs and one concurrency limit; the pages are kept as extracted. A second attempt for unavailable pages after the batch would cover them; not built, because with the furniture gone an unaudited page loses little.
- **Fence attribute on page 44.** One of 1,508 survives: a `theme=` fence indented under a quote with extra spaces (`>   ```bash`). Not worth a wider pattern.
- **Table nitpicks.** Nineteen findings remain, mostly table commentary with confidence under 0.90. They sit in review and edit nothing.

## Next run

Redeploy the server and worker, delete the `deepagents` slug, ingest again. Expect: no `Documentation Index` banner or footer link on any page (page 45 shows the banner inside a code sample, which stays); at most one `theme=`; 72 URLs fetched and two release pages in the manifest; page 19 unchanged; cleanup counts visible in the Pages report; patches accepted at or near 0 (a patch is worth keeping only if a diff shows a real fix); total input tokens below this run's 1.2M for a similar page count (part of it went on furniture), and fewer than the 19 provider errors. Record the audit model pool next to the counts.
