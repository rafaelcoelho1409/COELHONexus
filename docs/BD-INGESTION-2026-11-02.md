# Book Distiller — ingestion (built 2026-11-02, uncommitted)

First stage of the Book Distiller (`domains/bd/`): a PDF in, its pages out as markdown plus structure, page by page, with live progress. The decision behind the feature is `docs/BOOK-RESEARCH-FEATURE-SOTA-2026-10-30.md`. Extractor: Docling 2.145.0 on CPU torch (SOTA search 2026-11-02).

State: the Docling vs VLM sample is complete; no candidate VLM meets the predeclared gates, so Docling stays. The implementation is still uncommitted and **has not been deployed to the cluster**; the shared Celery worker will consume the `bd` queue after deployment.

## Flow

```
POST /api/v1/bd/ingestion (multipart PDF)         API pod (Docling dependencies installed, not imported)
  → book_id = sha256(bytes)[:16]; PDF + queued manifest → MinIO `books/{id}/`; start gate; registered Celery task
existing Celery worker (Docling + CPU torch, `bd` queue)
  → inspect_pdf: page count, title, PDF outline → manifest (outline, chapters)
  → slices of SLICE_PAGES=5: convert_slice → pages + figures + structured → MinIO → manifest → events
GET  …/{id}/events  (SSE)   started · page · slice_done · done | failed | cancelled   (replays history to late subscribers)
```

## MinIO layout (`books/{book_id}/`)

`source.pdf` · `manifest.json` · `pages/0001.md` … · `structured/0001-0005.json` (Docling document per slice, pixels stripped) · `figures/p0003-1.png`

## API (`/api/v1/bd/ingestion`, tag "Book Distiller"; typed in the exported OpenAPI)

| | |
|---|---|
| `POST ""` (form `file`, `refresh`) | `queued` (task sent; `resumed` when it continues a stopped book) · `cached` · `locked`; 400 not a PDF, 413 over 300 MB |
| `GET ""` | library rows, newest first |
| `GET /active` | runs in flight |
| `GET /{id}` | `{manifest, progress}`: outline, chapters, pages so far (each: key, chars, headings, tables, pictures, formulas, figures, `section_path`) |
| `GET /{id}/pages/{n}` · `GET /{id}/figures/{name}` | page markdown · figure PNG |
| `GET /{id}/events` | SSE, see below |
| `POST /{id}/run?refresh=` | start or continue a stored book from its stored PDF, no upload (resume a cancelled/failed book, or extract again); same answers as the upload, 404 unknown |
| `POST /{id}/cancel` · `DELETE /{id}` | cancel lands between slices · wipe (refused while a run holds the book) |

Events (JSON in `data:`): `started {title, n_pages, outline, chapters, resumed, completed}` · `page {page, markdown, truncated, stats{chars,headings,tables,pictures,formulas}, figures[], section_path[], completed, total}` · `slice_done {first, last, seconds}` · `done|failed|cancelled {pages, total, error}`. Every event also has `kind` and `ts`.

## Decisions

- **The same file is the same book** (content hash): re-upload of a finished book is `cached`; of a stopped one resumes at the pages it lacks (`extract.domain.pending_slices`).
- **Slices, not one call:** Docling's progress events carry no content and pages finish out of order; a slice gives the UI real page text as it goes. Page numbers stay the book's own across slices (checked on a real PDF).
- **Heading levels:** `section_path` comes from the PDF outline (`outline.domain`), not from Docling's per-slice levels; Docling's hierarchy stage is on (bookmark > numbering > style) and its levels are kept per page only as counts/`headings`.
- **Structured output is persisted** so the later enrichment stage (figure descriptions, formulas as LaTeX, scanned pages through a vision model via the Rotator) reads it without re-converting.
- **No OCR** (`extract.config.DOCLING.ocr = False`): born-digital books first.
- **API startup stays light:** Docling and pypdfium2 are runtime dependencies in the shared image, but their modules stay outside the eager import chain; only the book task loads them when extraction begins. Linux torch uses the CPU-only index.

## Deployment (decision after the A/B; not yet applied to the cluster)

The shared server image includes Docling and CPU torch. The existing Celery deployment consumes the `bd-{env}` queue and includes `domains.bd.ingestion.task`; the API dispatches through the registered task. The Docling and PDF reader modules remain lazy so API requests and unrelated worker tasks do not load them at process startup. `OMP_NUM_THREADS=1` limits extraction threads to the shared worker's CPU budget. The Hugging Face model cache is local to the worker container, so the first extraction after a pod replacement downloads the models again. The earlier single-task worker used 2.5–2.7 GiB within its 6 GiB limit; monitor memory after it shares the existing worker at concurrency 2. The separate `bd` image, Deployment, PVC, Skaffold artifact and ArgoCD image updater entries are removed.

## Previously verified end to end (scratch Redis + MinIO + dedicated image; not in the cluster)

Upload → `bd` queue → worker container → MinIO → SSE → `done`; `cached` on re-upload; cancel lands between slices and resume continues at the next missing page (314-page book: 181 outline entries, 9 chapters). Image 2.75 GB. Worker memory flat at 2.5–2.7 GiB over 80 pages; ≈ 4 s/page overall, but a slice of table-of-contents pages took 96 s (≈ 19 s/page). First slice includes the model download and load (≈ 20 s).

## Known issues

- Figures are `<!-- image -->` placeholders in the page markdown; the crops are stored (`figures/…png`, listed per page) but the markdown does not link them.
- Some headings carry a private-use glyph in front (a box before "Summary", p. 80 of the Git book): a PDF font artifact that reaches the markdown. Strip Unicode category `Co` in the extractor as a follow-up.
- Cancel is checked between slices only: 15–95 s on real books.
- A page whose conversion raises fails the whole slice and the book (`failed`); pages done before it are kept and a resume retries from there.

## A/B result: Docling vs vision models (`scripts/bd/ab_ingestion.py`)

The fixed rule required a VLM to stay within 0.01 median F1 of Docling on born-digital pages, fail on no more than 5% of them, and win at least 60% of decided born-digital judge pairs. The 54-page sample covered prose, code, figures, tables and scans from three books.

| arm | born-digital F1 delta vs Docling | born-digital failures | all-page F1 | all-page failures | median seconds/page | outcome |
|---|---:|---:|---:|---:|---:|---|
| Docling | — | 0/42 | 0.978 | 0/54 | 4.6 | baseline |
| Nemotron 3.5 Super VL 120B | -0.013 | 1/42 | 0.976 | 1/54 | 18.2 | misses F1 floor |
| Llama 3.2 90B Vision | -0.035 | 1/42 | 0.964 | 1/54 | 52.1 | misses F1 floor |
| Mistral Large 4 | -0.006 | 18/42 | 0.975 | 27/54 | 23.3 | fails reliability gate |
| Vision pool | -0.017 | 1/42 | 0.969 | 2/54 | 21.3 | misses F1 floor |

All-page F1 is the median over successful outputs; failures are reported separately. No VLM qualified even before the judge condition, so the judge was not run: it could not change the result under the conjunctive rule. Docling therefore stays, and the code now routes it to the shared server image and Celery worker. Every VLM's successful scan outputs had higher text F1 than Docling's 0.975, but 12 scan pages are too few to establish a reliable scan-only fallback, especially with failures in some arms; it needs a separate reliability and structure evaluation.

The per-stratum scorecard and exact verdict logic are reproducible with:

```bash
apps/server/.venv/bin/python scripts/bd/ab_ingestion.py --out /tmp/bd_ab2 report docling \
  vlm-nemotron-3.5-super-vl-120b-a12b vlm-llama-3.2-90b-vision-instruct \
  vlm-mistral-large-4 vlm-vision
```

## Not done

1. Deploy and smoke-test the shared Celery worker path. The earlier end-to-end run used the now-retired dedicated image.
2. Observability spans (`gen_ai`-style `traced` + metrics) on the run; the DD ingestion has them.
3. Enrichment stage, EPUB, the distill/index stages.
4. Page-level OCR/VLM fallback via the Rotator (needs a pinned / vision-capable route there: the chat route accepts only `auto`, `fgts-va`, `latency-ts` and its pool excludes VLMs).
