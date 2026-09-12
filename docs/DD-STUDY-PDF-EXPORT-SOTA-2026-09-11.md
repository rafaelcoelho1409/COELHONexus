# Docs Distiller Study page — "download all chapters as PDF" SOTA (2026-09-11)

⬜ **Research only — nothing in this doc is implemented yet.** Captures the
`/sota-search` decision for when the Study page should let the user download
one PDF containing every synthesized chapter, so it can be built without
re-deriving the trade-offs.

## Problem

Once every chapter in a framework's plan has `rendered = true` (i.e. Synth
has finished the whole book), let the user download one PDF with all
chapters — as close as possible to what they already read in the Study
reader (code highlighting, Mermaid diagrams, KaTeX math, tables).

Sub-questions this doc answers:
1. What generates the PDF — a headless browser, a self-hosted HTTP service,
   or a Python/LaTeX pipeline?
2. How does it handle content that only renders via JS today (Mermaid,
   KaTeX auto-render, hljs)?
3. If a LaTeX pipeline is used instead (better typography, real searchable
   text, page numbers/TOC for free), what's the current (Sept 2026) best way
   to keep code-block quality high?

## Current Study-page rendering (context)

`apps/fasthtml/static/js/dd/shared/content_renderer.js`'s `renderMarkdownInto()`
is the single source of truth for how a chapter's `README.md` becomes what
the user sees: `marked` → `DOMPurify` → Mermaid (`renderMermaidBlocks`) →
terminal-ANSI blocks → hljs copy buttons → KaTeX `auto-render`. All of this
is **JS-driven and runs in the browser** — there is no server-side HTML
snapshot of a fully-rendered chapter anywhere today.

The gate for "all chapters synthesized" already exists client-side:
`_renderStudySidebar()` (`static/js/dd/study/sidebar.js`) computes
`synthesized = studyChapters.filter(ch => ch.rendered).length` against
`studyChapters.length` for the sidebar's progress line — the same count a
"Download PDF" button's enabled-state would gate on.

## Recommendation: Playwright `page.pdf()` against a server-rendered print view

Reuse the **existing** `content_renderer.js` pipeline inside a dedicated
print-view page, then let headless Chromium (via Playwright — already a
Nexus dependency for YCS transcript scraping) print it to PDF. This is the
only approach that can execute the same Mermaid/KaTeX/hljs JS the reader
already relies on, so the PDF matches the reader exactly, with zero new
runtime dependency.

### How to apply

1. **Gate the button** in the Study toolbar: enabled only when
   `synthesized === studyChapters.length`. Disabled tooltip: "Finish Synth
   on all chapters first."
2. **Print-view route** (FastHTML, e.g. `GET /study/{slug}/print`):
   server-concatenates every chapter's `README.md` in plan order behind a
   cover page (framework name + linked chapter TOC), reusing
   `renderMarkdownInto()` per chapter. Print-only CSS hides the rail / TOC /
   search / focus-toggle / progress-bar / prev-next nav, adds
   `page-break-before: always` per chapter, and sets `@page` margins +
   numbering.
3. **Generate as a Celery task** (matches the existing pattern of pushing
   browser-driven work off the ASGI workers, per YCS's Playwright scraping):
   navigate to the internal print-view URL, wait for a `data-render-done`
   marker set once Mermaid + KaTeX finish, then
   `page.pdf({format: 'A4', printBackground: true, outline: true, displayHeaderFooter: true, margin: {...}})`.
   `outline: true` gives the PDF real clickable bookmarks per chapter — a
   free upgrade for a multi-chapter book.
4. **Cache in MinIO** at `synth/{slug}/book.pdf`, keyed by a hash of every
   chapter's `manifest_hash` (mirrors the existing chapter-blob caching
   convention). `GET /synth/{slug}/study/pdf` downloads the cached artifact;
   `POST .../pdf/generate` (re)builds it.

### Comparison

| Approach | Maturity | Handles JS (Mermaid/KaTeX) | Trade-off | Verdict |
|---|---|---|---|---|
| **Playwright `page.pdf()`** | Mature — `mkdocs-exporter` (PyPI, 2.0.0) uses exactly this pattern | Yes — real Chromium | ~300MB Chromium image, but already a Nexus dependency | **Adopt** |
| Gotenberg (self-hosted HTTP service wrapping Chromium) | Actively maintained | Yes | New service/pod/Helm chart for zero extra capability over Playwright, which we already run | Skip — redundant |
| WeasyPrint (pure Python, no JS) | Mature, smallest files, fastest cold | **No** — can't execute Mermaid or KaTeX auto-render | Needs Mermaid pre-rendered to SVG (mermaid-cli) + KaTeX server-side rendered separately — two pipelines to keep in sync with the reader | Skip — wrong tool for JS-rendered content |
| Pandoc + LaTeX | Highest print typography | No (same JS gap as WeasyPrint) | See the deep dive below | Alternative path, not the default |

## Alternative path: Pandoc + LaTeX, if typography/searchable-text matters more than pixel-for-pixel reader fidelity

Skipped above because neither TeX engine executes JS — Mermaid diagrams
would need pre-conversion to images (mermaid-cli) and there's no "read what
you see" guarantee. But if the LaTeX route is ever wanted (real vector
text, best page-break/TOC control, smaller files), the current (Sept 2026)
best configuration for **code blocks specifically** is:

**Tectonic engine + Pandoc's built-in `skylighting` highlighter** (i.e.
`pandoc --pdf-engine=tectonic`, no extra flags). Do **not** use `minted`
with Tectonic.

- **Why not minted despite it being the tempting best-quality answer:**
  TeX Live 2025+ fixed minted's old shell-escape security problem
  (`latexminted`, a restricted helper script — no more raw `-shell-escape`
  needed), so on **full TeX Live** minted is genuinely safe and the
  highest-quality option (real Pygments, 500+ languages, closest visual
  match to the browser's hljs). But on **Tectonic** specifically, fenced
  code blocks hit an open, unresolved bug: Tectonic runs shell-escape from
  a different working directory than classic engines, so
  `\inputminted`/minted can't find the files it needs to highlight
  ([tectonic-typesetting/tectonic#835](https://github.com/tectonic-typesetting/tectonic/issues/835)).
  The only workarounds are fragile (pre-run Pygments yourself, feed
  pre-highlighted output instead of relying on Tectonic's shell-escape).
  Not worth it just for code blocks.

| Approach | Image cost | Code-block quality | Risk | Verdict |
|---|---|---|---|---|
| **Tectonic + skylighting (default)** | Small — Tectonic fetches only needed packages on demand (~95% smaller reported vs full TeX Live in migration write-ups) | Good — Pandoc's built-in highlighter, zero config | None — no shell-escape, no known bugs | **Adopt if LaTeX path is chosen** |
| Full TeX Live + minted (`latexminted`) | Heavy — TeX Live images run 2–4GB | Best — real Pygments, 500+ languages | Low now (2025+ fixed the shell-escape security issue) | Fallback, only if code fidelity must exactly match Pygments and image size is a non-issue |
| Tectonic + minted | Small | Best on paper | **Broken** — `\inputminted` fails, open GitHub issue | Ruled out |
| `--listings` (LaTeX `listings` package) | Small | Weakest — fewer languages, manual per-language lexer config | Low | Skip — no reason to pick over skylighting |

**Caveat:** Tectonic downloads packages on first use and caches them —
pre-warm that cache into the Docker image at build time (a dry `tectonic`
run against the template during `docker build`), or the first PDF
generation in a fresh pod stalls on package fetches.

## Evidence

- Playwright fastest-in-2026 HTML→PDF benchmark; WeasyPrint's no-JS
  limitation — [PDF4.dev, "Playwright vs WeasyPrint: PDF generation in
  Python (2026 comparison)"](https://pdf4.dev/blog/playwright-vs-weasyprint)
- Playwright PDF bookmarks (`outline`) + print-CSS-as-product guidance —
  [screenshotapi.net, "Playwright PDF Generation: URL & HTML to PDF Guide
  (2026)"](https://www.screenshotapi.net/blog/every-detail-about-generating-pdf-of-a-website-using-playwright)
- Playwright-driven static-docs-to-PDF precedent — `mkdocs-exporter` 2.0.0
  on PyPI
- Gotenberg as the self-hosted-service alternative, actively maintained —
  2026 HTML-to-PDF comparison roundups (PDF4.dev, ironsoftware.com)
- TeX Live image size (2.3–4GB) vs Tectonic's on-demand fetch model, ~95%
  size reduction reported — [Josh Finnie, "Why I Switched From Tex Live to
  Tectonic"](https://www.joshfinnie.com/blog/why-i-switched-to-tectonic/);
  [FormaTeX, "Why TeX Live Docker Images Are 4GB"](https://formatex.io/blog/why-texlive-docker-images-are-4gb)
- Tectonic + minted working-directory bug —
  [tectonic-typesetting/tectonic#835](https://github.com/tectonic-typesetting/tectonic/issues/835)
- minted's TeX Live 2025 shell-escape fix (`latexminted` restricted helper)
  — TeX Live 2025 release notes / minted package docs (via search)

## Open product decisions (not yet answered)

- **Audit-failed chapters:** should a chapter that's `rendered = true` but
  `audit_passed = false` block the whole PDF, or be included with a visible
  "⚠ audit failed" watermark on its pages? Leaning toward including it
  watermarked rather than blocking the export.
- Whether the LaTeX path is ever worth building at all, given it trades
  reader-visual-fidelity (no Mermaid without a pre-render step) for
  typography/searchability — no decision made; the Playwright path above is
  the one to build first.
