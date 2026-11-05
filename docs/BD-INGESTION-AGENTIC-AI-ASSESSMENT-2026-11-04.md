# Book Distiller Agentic Ingestion Assessment — 2026-11-04

## Executive assessment

The latest Agentic AI run completed end-to-end and published all 275 page records, but the resulting material is **not yet reliable enough to treat as a clean canonical corpus**. The run status and graph nodes are green; sampled output still contains blank pages, extraction artifacts, and an unnormalized book-level title. The empty audit-warning list therefore overstates the actual content quality.

This is a read-only assessment of the COELHO Cloud dev API and Rotator logs. No ingestion data was changed.

## Run examined

| Field | Value |
| --- | --- |
| Book | *The Art of Cyberwarfare* |
| Book ID | \`4c6c6327360f7cf5\` |
| Source | 5,565,091-byte PDF |
| Docling run | \`c94a1d6883c04bb0a9b9d339f85348f4\` |
| Agentic run | \`e787d124a1b041008f52add906ebf97a\` |
| Result | \`done\`, no run-level error |
| Pages | 275/275 audited, processed, and published |
| Graph nodes | Audit pages, Plan repairs, Repair pages, Normalize metadata, and Publish results all \`done\` |
| Audit warnings | 0 reported |
| Chapters / outline | 10 chapter ranges / 163 outline entries |

## Quality findings

### Positive results

- The graph reached \`done\`, all 275 pages have an Agentic output source, and all 275 were published.
- The page-1 title is normalized to “The Art of Cyberwarfare”; representative body pages such as 50, 100, 250, and 275 contain readable text.
- Docling found 32 tables and extracted 90 figure assets across the book.
- The run returned no run-level error, and its usage endpoint recorded no LLM errors.

### Issues still present in the published pages

- **Blank pages were not removed:** pages 2, 4, 8, 10, 16, 78, 106, 126, 178, 200, 232, 236, and 262 each have an empty Agentic page body. All 13 remain in the 275-page published set.
- **Character noise remains:** page 3 contains a bell/control character before an author attribution.
- **Heading repair is incomplete:** page 17 still has “I NTRODUC T I ON”; page 21 still has “L ANDSCAPE”.
- **Table structure needs review:** page 9's contents table retains dotted-leader artifacts rather than a clean semantic table.
- **Metadata title is still the raw filename**, including the long source-site suffix, even though the first page's heading is normalized.
- Page 229's page body is only an image placeholder. The corresponding figure asset should be verified/displayed; text-only checks cannot determine whether the image itself is usable.
- The audit reported no warning pages despite these visible issues. The auditor needs stronger checks for blank pages, control characters, split-word headings, malformed table rows, and image-only pages.

These are representative spot checks plus a full manifest scan, not a human proofreading pass over every page.

## LLM usage and efficiency

| Agent | Calls | Input tokens | Output tokens |
| --- | ---: | ---: | ---: |
| Page auditor | 414 | 924,177 | 83,049 |
| Structure repair | 110 | 292,875 | 39,942 |
| Text repair | 151 | 7,735,410 | 95,781 |
| Metadata normalizer | 1 | 1,621 | 178 |
| **Total** | **676** | **8,954,083** | **218,950** |

Text repair accounts for about 86% of the run's input tokens. That is a substantial cost for a 275-page book and warrants inspecting per-page prompt sizes and limiting repair context to the relevant page and evidence. During the final part of this run, a 15-minute Rotator log sample showed 60 successful BD requests (59 text-repair and 1 metadata-normalizer); provider-level rate limits and quota cooldowns occurred, but the outer requests returned HTTP 200 through failover. This shows capacity pressure, not a Rotator outage, and does not prove this run was the only workload using the shared service.

Earlier worker logs also showed 39 page-audit structured-output validation fallbacks. The usage endpoint still reported zero errors, so its error counter does not currently represent this degradation mode.

## \`Plan repairs\` node

\`plan_repairs\` completed successfully, but this run has no usage counter for it. That is consistent with a deterministic planning node: it can produce a repair plan from audit findings without making an LLM call. The absence of a usage row is therefore not, by itself, a failure. A graph node should still show its execution status and explain that no model calls were recorded; if the design intends this node to call an LLM, that expectation is not reflected in this run's counters.

## Docling data beyond plain text

### What this Book Distiller run currently persists

The book's page manifest records page number and object key, character count, heading/table/picture/formula counts, figure names, and section path. The ingestion also has book metadata, a 163-entry outline, 10 chapter ranges, and 90 separately extracted figure assets. Tables and the text hierarchy are represented in the per-page Markdown export; the current API does not expose a complete serialized \`DoclingDocument\` for this run.

### Rich structures supported by Docling

Docling's \`DoclingDocument\` is richer than a Markdown string. Its documented top-level content collections include:

- Text items, with typed forms such as titles, section headers, list items, code, formulas, and field headings/values.
- Tables and table data/cells, including table structure and annotations.
- Pictures and image references, with captions/classification annotations where available.
- Key-value items, form items, field regions, and field items.
- Page records and page-level provenance, such as page number, bounding boxes, coordinate origin, and character spans.
- Body/furniture trees and groups that preserve reading order, sections, lists, headers/footers, and other relationships.
- Document origin/name/schema version and references between items.

The specific fields available depend on the source and conversion result. These are capabilities of Docling's native model; they must not be assumed persisted merely because BD stores Markdown and figure files. See the official [DoclingDocument concept guide](https://github.com/docling-project/docling/blob/main/docs/concepts/docling_document.md) and [DoclingDocument API reference](https://docling-project.github.io/docling/reference/docling_document/).

## Recommended next steps

1. Preserve the original Docling pages and figure assets under the raw run path; at publish time, exclude empty pages from the delivered content index or mark them explicitly as discarded while preserving original PDF page numbers.
2. Add deterministic quality checks for empty/near-empty pages, disallowed control characters, suspicious intra-word spacing, malformed Markdown tables, and image-only pages. Emit actionable page-level warnings instead of returning a clean audit summary.
3. Persist the normalized title into the canonical book/manifest metadata, not only the first page's Markdown.
4. Record structured-output validation fallbacks and agent-level retries as quality/error counters visible in the run summary.
5. Reduce text-repair token use with per-page/context budgets and repair only pages flagged by the audit or deterministic checks; measure before/after quality and tokens per page.
6. Consider retaining a versioned Docling JSON sidecar alongside Markdown and figure assets if future agents need layout, table-cell, or provenance data.

## Assessment basis

The manifest and Agentic status/counters were read from the \`k3d-coelho-cloud\` \`coelhonexus-dev\` server API. All 13 zero-character manifest pages were checked against their published page endpoints and remained empty. Representative page bodies were checked for pages 3, 9, 17, 21, and 229. Counts above are the run's reported data, not estimates.
