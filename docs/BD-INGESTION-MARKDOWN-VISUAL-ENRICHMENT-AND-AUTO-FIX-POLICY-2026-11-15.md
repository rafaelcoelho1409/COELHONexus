# BD Ingestion: Markdown Structures, Visual Enrichment, and Automatic Quality Fixes

**Status:** Product and implementation reference, updated 2026-11-15. Records the automatic quality-fix policy implemented for new runs, current repair behavior, and a safe design for converting useful page visuals into report-ready structures.

**Scope:** Book Distiller (BD) only. This document covers extracted-page content and quality repair. It does not change code or execute an Ingestion run.

## 1. Decisions and goals

BD should retain the uploaded source and its page images, while providing a clean, navigable, searchable processed-book view. The processed text should correct extraction damage and, when the correction is strongly supported by language and context, obvious source typos too. The user should not have to approve or reject each quality finding.

Useful visual content should be represented in structured, renderable forms when conversion is reliable. The original image remains available as evidence. Agents return validated data; application code renders it. A generated chart or diagram should never be accepted merely because it looks plausible.

The automatic policy is **automatic resolution, not unconditional acceptance of every model string**. Clear, validated corrections should be applied without a user click. Ambiguous, overlapping, or structurally unsafe edits should be recorded and left unchanged rather than silently corrupting the book. The product should make this distinction visible in run results.

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
| Charts | Typed chart specification plus a typed dataset | BD Distill currently models bar, line, scatter, and table charts. A custom renderer is needed; a fenced JSON/CSV block alone is data, not a rendered chart. |
| Citations / source evidence | Typed source references in BD report objects | Keep page and evidence references in the report schema; do not rely on unvalidated free-form footnote syntax. |

The shared renderer uses `remark-gfm`, `remark-math`, KaTeX, Mermaid, syntax highlighting, and sanitization. Raw HTML is dropped by default and sanitized when enabled; scripts and arbitrary model-generated code are not evaluated. See [`markdown.tsx`](../apps/web/src/shared/markdown/markdown.tsx).

Markdown syntax is extensible, so this is the inventory of structures BD currently renders, not a claim that every Markdown dialect or plugin is supported. JSON, YAML, CSV, SVG source, and programming languages can be preserved in fenced code blocks, but they need a dedicated parser/renderer to become interactive data structures. Plotly figures are not currently rendered by the shared Markdown component.

The BD Learning Guide already has typed diagram nodes and edges with evidence, LaTeX equation blocks, and datasets/chart specifications. That is the preferred pattern for generated report content: validated records with source links, rendered by trusted frontend components. See [`schemas.py`](../apps/server/domains/bd/distill/schemas.py).

## 3. Should page images be sent to a vision model?

Yes, selectively. A page image or figure crop can contain a chart, diagram, equation, table, code sample, or layout that plain extracted text cannot faithfully describe. A vision-capable model can classify and transcribe that content, but its result must be treated as a proposal and checked against the image and schema.

Recommended flow:

1. Keep the original page image/crop, page number, bounding box when available, and source hash.
2. Classify the visual: chart, table, flow/relationship diagram, equation, code, illustration, or not safely convertible.
3. Ask for the smallest useful typed result. For example: chart type plus column definitions and rows; diagram nodes and labeled edges; a LaTeX expression plus symbol definitions; or code transcription with uncertain spans identified.
4. Validate the response deterministically: required fields, row/column agreement, numeric types, known diagram endpoints, bounded output size, and source-page references.
5. Render with trusted code. Keep a link to the original visual and a short accessible description. If the extraction is uncertain, show the original image with a description instead of fabricating a structured replacement.
6. Record provenance per value/edge/claim where possible. Distinguish what the book states from an agent's interpretation.

For charts, value extraction needs special care: research documents that chart-language models can produce plausible but incorrect values. Prefer a table-first result, check that plotted fields reference declared numeric columns, retain the source crop, and mark values that cannot be read rather than estimating them. For diagrams, validate topology and labels; a syntactically valid Mermaid graph can still misstate the source.

Do not execute code recovered from a book image. Code blocks are for reading and copying. Do not ask an agent to return executable chart-building code when a constrained dataset and trusted renderer can produce the chart.

### Current BD implementation

- Docling is configured for table structure, heading hierarchy, and figure extraction. OCR is off by default, and Docling vision enrichments are explicitly deferred in [`extract/config.py`](../apps/server/domains/bd/ingestion/extract/config.py).
- Extracted figure crops are persisted, but the current Ingestion flow does not semantically turn every figure into Mermaid, LaTeX, a chart, or a table.
- The page-repair visual verifier is a different use: it sends a rendered source page image and a patch proposal together to the configured LLM rotator to check a text correction. It is not a general figure-understanding pipeline.
- The repository is pinned to Docling 2.145.0 in `apps/server/uv.lock`; current Docling documentation may describe newer releases. Check version compatibility before enabling newer enrichment options.

## 4. Automatic quality fixes: current behavior and the gap

The requested product behavior is no per-finding approval for any book. The pipeline already required no user approval to finish or publish a run; the change below also makes validated proposed corrections apply automatically.

Current repair behavior in [`repair_pages/domain.py`](../apps/server/ai/bd/nodes/ingestion/repair_pages/domain.py) and [`repair_pages/service.py`](../apps/server/ai/bd/nodes/ingestion/repair_pages/service.py):

- Validated text and structure proposals are applied automatically without user approval, regardless of the source-image verifier's verdict.
- The proposal's risk and confidence labels are retained for reporting, but do not block automatic application after source, scope, protection, and conflict checks pass.
- The visual verifier remains an evidence source for a bounded set of text proposals. Its result is advisory: an image reading that matches the extracted text cannot veto a strong contextual correction.
- Source-span, issue-scope, protected technical content, structural word/number preservation, and overlapping-patch checks still apply. Invalid, protected, or conflicting patches are reported instead of being blindly written.
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

Apply a correction automatically when the proposal is within the specialist's assigned issue, has an exact source span, passes patch and overlap checks, and does not modify protected technical/code spans. Structural proposals must preserve the source's words and numbers. The repair specialist remains responsible for proposing only corrections supported by the finding and context.

Keep VLM calls where they add evidence: OCR ambiguity, line-break hyphenation, visual punctuation, and page layout. They are **not a substitute for contextual language review**, and an image reading equal to the extracted text does not veto a contextual correction. The current implementation still makes these selective verifier calls for evidence and usage reporting; its verdict is advisory. General extraction of figures/tables is a separate future enrichment stage.

For auditability, retain the original text/image, proposed and applied text, issue type, decision reason, verifier result (if called), model/prompt versions, and source references. The UI can report counts such as “applied automatically,” “retained as uncertain,” and “verification unavailable”; it should not imply that an unresolved suggestion is waiting for the user when the run has already completed.

## 5. Implementation sequence

1. **Record the output contract:** raw source remains immutable; processed Markdown is the corrected reading edition; each automated edit has provenance and an explicit correction category (`ocr_correction`, `source_typo_correction`, `mechanical_normalization`, or `structure_repair`).
2. **Change the repair decision model:** separate literal source transcription from whether to normalize the processed reading. Remove the current rule that a literal source match alone vetoes a high-confidence contextual correction.
3. **Define per-issue acceptance rules:** deterministic checks for whitespace/noise; contextual thresholds and independent validation for lexical repairs; hierarchy and boundary checks for structural repairs; protected spans for code and identifiers.
4. **Keep image verification selective:** route only issues where the source image adds evidence. A vision failure should be recorded distinctly from a correction rejected by evidence; avoid silently treating unavailable verification as proof that the original is correct.
5. **Persist decisions and provenance:** store proposal, source reading, policy, applied replacement, and rationale in the page QA artifact so the Pages view and later Distill runs use the same final text.
6. **Add the general visual-enrichment stage separately:** classify extracted figures, return typed chart/diagram/equation/table records, validate them, and render them alongside the preserved original. Do not combine figure extraction with text patch verification.
7. **Evaluate on a labeled book set before claiming quality:** include the supplied cases (`diem/them`, `saver/saved`, misplaced heading) plus genuine names, technical terms, code, line-wrap hyphens, source-printed errors, and ambiguous scans. Measure false corrections as well as missed corrections.

## 6. References

- [GitHub Flavored Markdown specification](https://github.github.com/gfm/) — GFM tables, task lists, and Markdown behavior.
- [GitHub diagram support](https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/creating-diagrams) and [Mermaid examples](https://mermaid.js.org/syntax/all-examples) — Mermaid and other diagram formats.
- [Docling enrichments](https://docling-project.github.io/docling/usage/enrichments/) and [vision models](https://docling-project.github.io/docling/usage/vision_models/) — optional picture, formula, and chart enrichment paths; check these against the repository's pinned Docling version.
- [Microsoft Research: table-first guarded reasoning for chart QA](https://www.microsoft.com/en-us/research/publication/table-first-guarded-reasoning-outperforming-larger-mllms-in-chart-question-answering/) — evidence that chart extraction should use guarded, data-first validation.
- [WACV 2026: START, spatial and textual learning for chart understanding](https://openaccess.thecvf.com/content/WACV2026/papers/Liu_START_Spatial_and_Textual_Learning_for_Chart_Understanding_WACV_2026_paper.pdf) — chart understanding requires both spatial and textual evidence.
- [Docling 2.145.0 release](https://github.com/docling-project/docling/releases/tag/v2.145.0) — version currently pinned by this repository at the time of writing.
