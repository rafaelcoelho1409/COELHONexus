# BD Ingestion: Markdown Structures, Visual Enrichment, and Automatic Quality Fixes

**Status:** BD implementation reference, updated 2026-11-16. The first complete visual-enrichment run has been inspected; the follow-up fixes add text and timeline schemas, one schema-correction retry, and better failure details. They have not yet been deployed or replayed against the book.

**Scope:** Book Distiller (BD) only. This document covers extracted-page content, visual enrichment, and quality repair. Implementation does not execute an Ingestion run.

## 1. Decisions and goals

BD should retain the uploaded source and its page images, while providing a clean, navigable, searchable processed-book view. The processed text should correct extraction damage and, when the correction is strongly supported by language and context, obvious source typos too. The user should not have to approve or reject each quality finding.

Useful visual content should be represented in structured, renderable forms when conversion is reliable. The original image remains available as evidence. Agents return validated data; application code renders it. A generated chart or diagram should never be accepted merely because it looks plausible.

The user's explicit policy is to automatically apply every proposal that passes exact-source, issue-scope, and no-invented-content validation. The visual verifier is advisory and cannot veto a validated contextual correction. If two accepted proposals overlap the same source span, they conflict and cannot both be applied; record that conflict rather than silently picking one.

## 2. Markdown structures BD can render

Markdown can represent a useful subset of book structures directly. Some richer objects need a typed data model and a dedicated renderer rather than being squeezed into prose or an arbitrary code block.

| Content | Markdown representation | BD rendering / handling |
|---|---|---|
| Headings and prose | ATX headings, paragraphs, emphasis, links, blockquotes | Rendered by the shared Markdown component; headings receive anchors. |
| Ordered and unordered steps | Nested Markdown lists | Rendered as normal lists. |
| Separators | Thematic breaks (`---`, `***`) | Rendered as section separators. |
| Tables | GitHub Flavored Markdown (GFM) tables | Rendered in a keyboard-scrollable table container. Good for source tables and compact datasets. |
| Task lists and emphasis | GFM task-list items, strikethrough, autolinks; Markdown bold/italic | Rendered by the shared GFM plugin. |
| Code and pseudocode | Fenced code blocks with a language label | Syntax highlighted and copyable. It is displayed as text; it is not executed. |
| Flowcharts and relationship diagrams | Fenced `mermaid` blocks | Mermaid is rendered by the shared component. Mermaid diagram families include flowcharts, sequence, class, state, entity-relation, journey, and Gantt diagrams; use only a form supported by the installed renderer. |
| Equations | Inline or display LaTeX math | Parsed with KaTeX. Preserve assumptions and define symbols in nearby prose. |
| Images | Markdown image with meaningful alt text and a stable asset reference | Lazy-loaded. Keep the original crop when conversion is incomplete or uncertain. |
| Terminal examples | Fenced `ansi` or `terminal` blocks | Rendered with the terminal component. |
| Callouts | Supported alert/callout syntax | Rendered by BD's custom remark-alerts plugin. |
| Charts | GFM data table plus a fenced `vega-lite` JSON specification | The shared renderer validates inline-only specifications and renders supported bar, line, area, scatter, pie, and heatmap charts. |
| Maps | GFM feature/coordinate table plus a fenced `vega-lite` geoshape specification | Inline GeoJSON point, line, and polygon features render locally. Coordinates must be explicit in source; maps without exact coordinates remain images. |
| Citations / source evidence | Typed source references in BD report objects | Keep page and evidence references in the report schema; do not rely on unvalidated free-form footnote syntax. |

The shared renderer uses `remark-gfm`, `remark-math`, KaTeX, Mermaid, Vega-Lite/Vega Embed, syntax highlighting, and sanitization. Raw HTML is dropped by default and sanitized when enabled; scripts, remote data sources, and arbitrary model-generated code are not evaluated. See [`markdown.tsx`](../apps/web/src/shared/markdown/markdown.tsx).

Markdown syntax is extensible, so this is the inventory of structures BD currently renders, not a claim that every Markdown dialect or plugin is supported. JSON, YAML, CSV, SVG source, and programming languages can be preserved in fenced code blocks, but they need a dedicated parser/renderer to become interactive data structures. Vega-Lite blocks are restricted to bounded inline data and allowlisted fields; arbitrary URLs, expressions, transforms, HTML, or executable code are rejected.

The BD Learning Guide already has typed diagram nodes and edges with evidence, LaTeX equation blocks, and datasets/chart specifications. That is the preferred pattern for generated report content: validated records with source links, rendered by trusted frontend components. See [`schemas.py`](../apps/server/domains/bd/distill/schemas.py).

## 3. Should page images be sent to a vision model?

Yes, selectively. A figure crop can contain a chart, diagram, equation, table, or code sample that plain extracted text cannot faithfully describe. A vision-capable model can classify and transcribe it, but its result is treated as a proposal and checked against the response schema, confidence threshold, structural constraints, and deterministic serializer.

Recommended flow:

1. Keep the original page image/crop, page number, bounding box when available, and source hash.
2. Classify the visual: chart, table, flow/relationship diagram, equation, code, illustration, or not safely convertible.
3. Ask for the smallest useful typed result. For example: chart type plus column definitions and rows; diagram nodes and labeled edges; a LaTeX expression plus symbol definitions; or code transcription with uncertain spans identified.
4. Validate the response deterministically: required fields, row/column agreement, numeric types, known diagram endpoints, bounded output size, and source-page references.
5. Render with trusted code. Keep the original visual available from the figure gallery. If the extraction is uncertain, show the original image rather than fabricating a structured replacement.
6. Record provenance per value/edge/claim where possible. Distinguish what the book states from an agent's interpretation.

For charts, value extraction needs special care: research documents that chart-language models can produce plausible but incorrect values. Prefer a table-first result, check that plotted fields reference declared numeric columns, retain the source crop, and mark values that cannot be read rather than estimating them. For diagrams, validate topology and labels; a syntactically valid Mermaid graph can still misstate the source.

Do not execute code recovered from a book image. Code blocks are for reading and copying. Do not ask an agent to return executable chart-building code when a constrained dataset and trusted renderer can produce the chart.

### Current BD implementation

- Docling is configured for table structure, heading hierarchy, and figure extraction. OCR and Docling-native vision enrichments remain off; the separate `enrich_visuals` graph node performs application-level figure transcription from saved crops.
- The `enrich_visuals` graph stage reads extracted figure crops from selected, repaired pages, asks the configured vision rotator for a constrained transcription, and converts supported results to Mermaid, GFM, Vega-Lite, KaTeX, or inert code blocks.
- The visual schema supports legible text-bearing notes/document screenshots and clear timelines, in addition to diagrams, tables, exact-value charts, explicit-coordinate maps, equations, and code. Decorative artwork, photos, ambiguous mixed screenshots, maps without exact coordinates, and charts whose values cannot be read exactly remain source images.
- Low-confidence, unsupported, oversized, missing, or invalid images stay as their original figures. Raw crops remain in the figure gallery.
- The inspected run for *The Art of Cyberwarfare* (`4c6c6327360f7cf5`, Agentic run `8e6b9ac1442641e88ce84735dae137f9`) processed 90 figure candidates: 41 converted, 47 were retained, and 2 failed. Source page 84 failed because the DeepAgent envelope was mistaken for the visual result; source page 116 was retained without a recorded reason. The response parser now searches nested envelopes, and the visual sidecar records disposition reasons.
- The new extractor retries one schema-invalid response with concise validation feedback and the same source image, parses structured output from supported response envelopes or the final assistant JSON, and persists safe field/type diagnostics when validation still fails. This makes a single-node replay from `enrich_visuals` useful after deployment without rerunning page audits or text repairs.
- The page-repair visual verifier is a different use: it sends a rendered source page image and a patch proposal together to the configured LLM rotator to check a text correction. It is not a general figure-understanding pipeline.
- The repository is pinned to Docling 2.145.0 in `apps/server/uv.lock`; current Docling documentation may describe newer releases. Check version compatibility before enabling newer enrichment options.

## 4. Automatic quality fixes: current behavior and the gap

The requested product behavior is no per-finding approval for any book. The pipeline already required no user approval to finish or publish a run; the change below also makes validated proposed corrections apply automatically.

Current repair behavior in [`repair_pages/domain.py`](../apps/server/ai/bd/nodes/ingestion/repair_pages/domain.py) and [`repair_pages/service.py`](../apps/server/ai/bd/nodes/ingestion/repair_pages/service.py):

- Every validated text and structure proposal is applied automatically without user approval, regardless of the source-image verifier's verdict or the proposal's risk/confidence labels.
- Exact source span, assigned issue/specialist scope, and no-invented-content validation still apply. They prevent applying a suggestion that does not correspond to the supplied page or adds unsupported links/markers.
- The visual verifier remains an evidence source for a bounded set of text proposals. Its result is advisory: an image reading that matches the extracted text cannot veto a strong contextual correction.
- Overlapping proposals are the one case where all suggestions cannot be applied simultaneously; the conflict is reported rather than silently choosing a replacement.
- New runs use this behavior once the updated server and Celery worker code is deployed. Existing completed page artifacts do not change retroactively; replay the repair stage to regenerate them.

The examples expose a mismatch between **faithful transcription** and the desired **clean corrected reading**:

- `how to play diem` → `how to play them`: if the page image really prints “diem,” the current verifier treats that literal reading as evidence against the correction. But the user wants an obvious contextual typo corrected in the processed book.
- `was saver from ...` → `was saved from ...`: the same issue. A literal reading can establish that the source printed “saver,” but cannot by itself establish that the book should retain the typo in a normalized reading.
- A misplaced `##` heading is a structural-repair problem. It should be resolved from heading hierarchy and surrounding content, with deterministic structural validation and non-overlapping edits; a text-only VLM verdict is not the right approval rule.

### Reported quality-panel example

The UI showed:

> Quality — No approval needed · original retained where uncertain  
> 3 issues · 0 fixed automatically · 3 left unchanged

1. **Damaged text, low:** `how to play diem` proposed `how to play them`. The report said the source image disagreed because its literal reading matched the extracted text, so it retained the original.
2. **Damaged text, low:** `was saver from such a hypochondriacal career` proposed `was saved from such a hypochondriacal career`. It was retained for the same source-image reason.
3. **Structure, medium:** `## From the present point of view, child rearing may be regarded as an educational process` appeared before the plain-text chapter heading `3 THE GENESIS OF GAMES`. The proposal moved the heading marker to the chapter title, but the application reported that safety checks prevented applying the combined structural edit.

The first two cases are not merely image-reading failures: if the source really prints `diem` or `saver`, the image can correctly report those spellings while the processed reading still benefits from correcting an obvious typo. The third case needs a structural patch that can move a marker without merging or damaging either heading. All three should be resolved automatically when the relevant semantic and structural checks pass; the run should preserve the raw source for comparison.

### Applied decision policy

Treat BD processed text as an **automatically corrected reading edition**, while preserving raw extraction and original page images. This honors the requested automatic approval and keeps source evidence available.

For each finding, separate two questions instead of collapsing them into a single VLM verdict:

1. **What does the page visibly contain?** Use the image verifier when it can disambiguate OCR, line wrapping, punctuation, or layout. Its literal reading is source evidence.
2. **What should the processed reading say?** Use the assigned issue, exact target span, surrounding sentence, book-wide terminology, and bounded correction rules. A clear language/context correction may be applied even when the image proves the typo is printed in the source. Record it as a source-level typo correction, rather than falsely labeling it an OCR correction.

Apply every correction automatically when the proposal matches its exact source span, stays within the assigned issue and specialist scope, and passes the no-invented-content checks. Risk/confidence labels and image-verifier results do not require user approval. Only overlapping proposals conflict because they cannot both replace the same source span; report those as a conflict. The repair specialist remains responsible for proposing corrections supported by the finding and context.

Keep VLM calls where they add evidence: OCR ambiguity, line-break hyphenation, visual punctuation, and page layout. They are **not a substitute for contextual language review**, and an image reading equal to the extracted text does not veto a contextual correction. The current implementation still makes these selective verifier calls for evidence and usage reporting; its verdict is advisory. General figure extraction is implemented separately in the `enrich_visuals` stage described below.

For auditability, retain the original text/image, proposed and applied text, issue type, decision reason, verifier result (if called), model/prompt versions, and source references. The UI can report counts such as “applied automatically,” “retained as uncertain,” and “verification unavailable”; it should not imply that an unresolved suggestion is waiting for the user when the run has already completed.

## 5. Current text-repair behavior and evaluation

1. Raw extraction and source images remain immutable; processed Markdown is the corrected reading used by BD Pages and BD Distill.
2. Exact-source, assigned-issue/specialist-scope, and no-invented-content checks validate proposals before application.
3. Every validated proposal is applied automatically. The VLM verifier records evidence but cannot veto a contextual text correction. Conflicting overlapping proposals are surfaced instead of silently choosing one.
4. Figure understanding stays in `enrich_visuals`; page-image patch verification remains a separate, selective text-repair operation.
5. Continue measuring false corrections and missed corrections on labeled examples, including `diem/them`, `saver/saved`, malformed headings, genuine technical names, code, line-wrap hyphens, source-printed typos, and ambiguous scans.

## 6. References

- [GitHub Flavored Markdown specification](https://github.github.com/gfm/) — GFM tables, task lists, and Markdown behavior.
- [GitHub diagram support](https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/creating-diagrams) and [Mermaid examples](https://mermaid.js.org/syntax/all-examples) — Mermaid and other diagram formats.
- [Docling enrichments](https://docling-project.github.io/docling/usage/enrichments/) and [vision models](https://docling-project.github.io/docling/usage/vision_models/) — optional picture, formula, and chart enrichment paths; check these against the repository's pinned Docling version.
- [Microsoft Research: table-first guarded reasoning for chart QA](https://www.microsoft.com/en-us/research/publication/table-first-guarded-reasoning-outperforming-larger-mllms-in-chart-question-answering/) — evidence that chart extraction should use guarded, data-first validation.
- [WACV 2026: START, spatial and textual learning for chart understanding](https://openaccess.thecvf.com/content/WACV2026/papers/Liu_START_Spatial_and_Textual_Learning_for_Chart_Understanding_WACV_2026_paper.pdf) — chart understanding requires both spatial and textual evidence.
- [Docling 2.145.0 release](https://github.com/docling-project/docling/releases/tag/v2.145.0) — version currently pinned by this repository at the time of writing.

## 7. VLM-to-page visual conversion plan

### Renderer decision

Keep the existing shared React Markdown renderer. The Pages reader uses `react-markdown` 10.1.0 with the unified `remark`/`rehype` pipeline, GFM, KaTeX, syntax highlighting, sanitization, custom callouts, Mermaid 12.2.0, and Vega-Lite/Vega Embed. It renders Markdown into React components and supports custom rendering per element. Replacing it with MDX would add executable JSX behavior that is unnecessary and inappropriate for book content. Replacing it with an HTML-string renderer would weaken the current React component and sanitization model.

Do not expand the renderer to accept model-authored raw HTML, SVG, JavaScript, Plotly scripts, or executable code. Models return typed data; trusted application code serializes that data to a constrained Markdown form or to an explicitly supported page artifact component.

### Data forms the current renderer can display

This inventory is for `apps/web/src/shared/markdown/markdown.tsx` and its current pinned dependencies, not for every Markdown dialect:

| Form | Current representation | Rendering behavior and limit |
|---|---|---|
| Prose and document structure | CommonMark headings, paragraphs, emphasis, links, blockquotes, lists, thematic breaks, inline code, fenced code, and images | Rendered directly. Links and image URLs use the renderer's URL handling. |
| Tables and lightweight annotations | GFM pipe tables, task lists, strikethrough, autolinks, and footnotes | Tables render in a keyboard-scrollable container. |
| Diagrams and timelines | Fenced `mermaid` blocks | Rendered by the installed Mermaid library with `securityLevel: "strict"`. The source is a diagram language, not executable application code. |
| Data charts | Markdown data table plus fenced `vega-lite` JSON | Rendered from validated inline records. The data table and JSON source remain in Markdown for Distill. |
| Geographic features | Markdown coordinate table plus fenced `vega-lite` GeoJSON | Local geoshape projection; no external tile requests, geocoding, or inferred coordinates. |
| Equations | `remark-math` delimiters and KaTeX-supported TeX | Rendered with KaTeX. Unsupported TeX must remain visible as source or retain the original figure. |
| Code samples and pseudocode | Fenced code with a language label | Syntax-highlighted and copyable; never run. |
| Terminal output | Fenced `ansi` or `terminal` blocks | Styled as inert text; never run. |
| Callouts | The custom GitHub-style alert syntax supported by `remarkAlerts` | Rendered using BD's alert components. |
| Arbitrary JSON, YAML, CSV, or source code | Ordinary fenced code block | Preserved as text only. It does not become a table, chart, or interactive object by itself. |
| Arbitrary Vega, Plotly, or other visualization specifications | Not supported | Only a constrained inline `vega-lite` subset is rendered; other specs remain inert code. |

Use Mermaid for conceptual diagrams and timelines. Use Vega-Lite for general data charts and inline GeoJSON geoshapes; retain the exact data as Markdown tables so Distill can read it independently of the visual renderer. GFM tables remain the fallback for exact tabular data.

### Where the stage belongs

The current BD graph is:

```text
audit_batch → classify_content → plan_repairs → repair_pages → normalize_metadata
→ enrich_visuals → materialize_processed_book → reconcile_contents → publish
```

This placement means that only pages retained by content classification are candidates, and visual conversion runs after text repair but before the processed page set and its standardized contents are built. The metadata normalizer currently reads immutable source pages, so its output remains independent of this placement.

### Per-figure extraction contract

Process extracted figure crops on included pages. If a crop is missing or too large, retain the image marker; this first implementation does not fall back to sending a full source-page image. Pair each crop with its physical page, stable figure name, section path, book title, and a bounded excerpt of repaired page text. The extraction manifest orders figure names against the page's image markers. Persisted conversion artifacts and the Pages API use stable figure names; the reader filters converted names so remaining image markers continue to map to remaining source figures.

Use one constrained VLM response to both classify and extract each candidate figure. This avoids a separate classifier call followed by an extraction call. The response chooses `convert` or `retain`; for `convert` it returns one validated artifact type:

| Visual | Typed extraction | Deterministic Markdown output |
|---|---|---|
| Flowchart or relationship diagram | Bounded node IDs, visible node labels, direction, and labeled edges | Application-generated Mermaid `flowchart` block. The model must not supply raw HTML or arbitrary Mermaid configuration. |
| Table | Ordered headers and complete rows | GFM table. Do not fill illegible cells by guessing. |
| Data chart | Exact visible rows, labels, optional series, and chart type (`bar`, `line`, `area`, `scatter`, `pie`, or `heatmap`) | A readable Markdown data table plus a trusted `vega-lite` block; never estimate values from pixels. |
| Map | Points, routes, or closed polygon rings with explicit source longitude/latitude coordinates | A readable feature/coordinate table plus inline GeoJSON rendered with Vega-Lite `geoshape`; never geocode, infer coordinates, or trace boundaries from pixels. |
| Equation | Exact LaTeX transcription | KaTeX math block, only when transcription is reliable. |
| Code sample | Exact visible code and optional language label | Fenced code block for reading/copying only. Never execute it. |
| Legible note/document screenshot | Verbatim transcription, in reading order; use prose or code format | Escaped Markdown blockquote or fenced code block. Do not OCR every screenshot. |
| Clear timeline | Ordered visible periods and event labels | Mermaid `timeline` block; retain if a period or event is uncertain. |
| Photograph, illustration, decorative art, dense mixed screenshot, or unsupported chart | Retain decision | Keep the original figure. Do not infer chart values from pixels. |

For diagrams, store canonical typed nodes and edges, then serialize them with trusted code. Validate unique node IDs, edge endpoints, output size, safe labels, and graph completeness before emitting Mermaid. For charts, require numeric cells to be visibly supported by the image; never infer plotted values from a trend line. KaTeX rendering and Mermaid parsing must be checked against the pinned frontend versions before replacement is committed.

### Storage, rendering, and Distill input

Keep raw extraction Markdown, uploaded files, page images, and figure crops immutable. The visual node writes:

1. A resumable per-figure cache keyed by run, source page, and a digest of the figure name. It stores a versioned input fingerprint, image hash, and the validated typed response; the original crop remains at its existing immutable extraction key.
2. A derived per-page Markdown artifact that starts from the repaired normalized page and replaces only successfully converted figure markers with deterministic Markdown output.
3. A per-page provenance sidecar with figure names, conversion decisions, kinds, confidence, and rationale. Counts and artifact references are published without placing image bytes or full page text in LangGraph state.

Keep the repaired `normalized_key` intact for audit history. Add an explicit visual-enriched page reference (for example, `enriched_key` plus hash) and make processed-book materialization prefer that validated reference, falling back to the repaired page when no visual was converted. The candidate page's `content_key` must point to the exact enriched Markdown shown in BD Pages. BD Distill then consumes that same content key, so it receives the converted structure rather than a separate or stale text version.

When a figure is converted, show the generated Mermaid/table/math/code/chart/map in its place in the rendered Pages view. Mermaid and Vega-Lite visualizations can be expanded in a modal. Chart rows and map features remain represented as Markdown tables so Distill can read the facts as text as well as process the visualization specification. The original crop remains available in the existing figure gallery with its source page and stable name. If extraction is uncertain, invalid, unsupported, or unavailable, leave the original image in place.

### Reliability, cost, and observability

- The extractor uses the configured LLM rotator with image content blocks; it does not pin a specific vision provider or model.
- Concurrency is bounded to four figures, and crops over 8 MiB are retained without a model call. The UI shows candidate, reviewed, and converted counts; the run quality report records retained totals and the LLM usage view includes `visual_extractor` calls.
- Per-figure results are cached by image hash, repaired-context fingerprint, workflow version, and visual schema/prompt version. Replaying `enrich_visuals` clears derived page Markdown and downstream artifacts but preserves successful conversions; previous non-conversions are retried after the visual version changes. Version `1.3` accepts compatible `1.2`, `1.1`, and `1.0` entries.
- Provider or schema failures retain the original figure and do not fail the whole book. The extractor makes one schema-correction retry before retaining a figure, then logs safe field/type diagnostics.
- The workflow version remains stable for this node-logic update so the existing run can replay from `enrich_visuals`; visual cache version `1.3` invalidates incompatible outcomes without discarding successful compatible figure results. This does not require replaying audits or text repair.
- The per-figure cache preserves source image hashes and typed responses; the page provenance sidecar records figure names, decisions, confidence, and rationale. Markdown is regenerated deterministically from the cached typed response. The page manifest stores enriched-page and provenance keys with hashes.

### Implemented code paths

- Graph node and workflow wiring: [`graphs.py`](../apps/server/ai/bd/graphs/ingestion/graphs.py), [`visual_enrichment/service.py`](../apps/server/ai/bd/nodes/ingestion/visual_enrichment/service.py).
- Typed vision response and DeepAgent: [`agents/schemas.py`](../apps/server/ai/bd/agents/schemas.py), [`visual_extractor/agent.py`](../apps/server/ai/bd/agents/ingestion/visual_extractor/agent.py), and its [system prompt](../apps/server/ai/bd/prompts/ingestion/visual_extractor/SYSTEM.md).
- Trusted Markdown serialization: [`visual_enrichment/domain.py`](../apps/server/ai/bd/nodes/ingestion/visual_enrichment/domain.py).
- Versioned figure/page artifacts, source-crop access, replay cleanup, and graph version: [`keys.py`](../apps/server/domains/bd/ingestion/storage/keys.py), [`versions.py`](../apps/server/domains/bd/ingestion/ai/versions.py), and [`ai/service.py`](../apps/server/domains/bd/ingestion/ai/service.py).
- Enriched content flows into the processed-book candidate and BD Distill through [`processed_book/service.py`](../apps/server/domains/bd/ingestion/ai/processed_book/service.py). The Pages API returns converted figure IDs and renders the same enriched Markdown while keeping source crops in the gallery.
- Graph progress and LLM usage are wired in [`agentic-progress.ts`](../apps/web/src/features/bd/domain/agentic-progress.ts) and [`agentic-usage.ts`](../apps/web/src/features/bd/domain/agentic-usage.ts).

The first end-to-end book run confirmed multimodal calls and figure pairing, but exposed a response-validation failure affecting most crops. After deploying these fixes, replay `enrich_visuals` for the existing completed run to validate the corrected schema, text/timeline extraction, and figure conversion. The replay preserves the 22 compatible per-figure results and retries the 68 uncached failures. No run or deployment was started during this code update.

## 8. Research references for the renderer and structured extraction

- [react-markdown documentation and security notes](https://github.com/remarkjs/react-markdown) — CommonMark parsing, React component rendering, plugin pipeline, and secure-by-default behavior.
- [CommonMark specification](https://spec.commonmark.org/current/) — interoperable Markdown core syntax.
- [remark-gfm](https://github.com/remarkjs/remark-gfm) — GFM tables, task lists, strikethrough, autolinks, and footnotes.
- [Mermaid diagram syntax catalog](https://mermaid.js.org/syntax/all-examples.html), [XY chart syntax](https://mermaid.js.org/syntax/xyChart.html), and [Mermaid 12.2.0 release](https://github.com/mermaid-js/mermaid/releases) — check every generated diagram against the installed package version.
- [KaTeX supported functions and environments](https://katex.org/docs/supported.html) — valid TeX subset for equation extraction.
- [LangChain structured output](https://docs.langchain.com/oss/python/langchain/structured-output) and [multimodal inputs](https://docs.langchain.com/oss/python/langchain/messages) — schema-constrained results and image content blocks. Provider support varies, so application-side Pydantic validation remains mandatory.
