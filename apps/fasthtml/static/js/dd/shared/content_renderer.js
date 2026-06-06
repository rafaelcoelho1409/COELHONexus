// ============================================================
// content_renderer.js — shared rich-content renderer (markdown →
//   sanitized HTML + lazy hljs + mermaid + KaTeX + ANSI terminal
//   blocks + language badges + copy buttons). Single source of
//   truth used by both the Study page (study.js) and the
//   per-page drawer that the Ingestion / Planner / Synth pages
//   open from `.fw-page-card` clicks (ui.js).
//
// PUBLIC API
//   await renderMarkdownInto(rootEl, rawMd, opts?)
//     opts.postProcess(rootEl)  — sync hook between sanitize-write
//                                 and the block-level renderers (used
//                                 by study.js to fold "Sources for
//                                 this section" into <details>, strip
//                                 the inline ## Contents list, etc.)
//     opts.addCopyButtons       — default true; pass false to skip
//
//   await initContentRenderers()  — manual warm-up (idempotent,
//                                   cached promise). Optional; called
//                                   automatically by renderMarkdownInto.
//
// Loading model
//   - marked / DOMPurify / hljs are global (loaded via shell.py HEAD).
//   - marked-katex-extension, marked-alert, mermaid are dynamic +esm
//     imports — off the initial critical path; if their CDNs fail the
//     chapter still renders (markdown + code + terminal degrade
//     gracefully).
// ============================================================

// initContentRenderers + module-private vendor state extracted to
// ./renderers/init.js (Step 1, 2026-06-05 follow-up). lazyRenderBlocks
// + _blockRenderObserver extracted to ./renderers/lazy_observer.js.
// renderMermaidBlocks / renderTerminalBlocks / addCodeCopyButtons were
// ALSO extracted (to ./renderers/{mermaid,terminal,code_copy}.js) but
// the imports never landed — calls to renderMermaidBlocks(rootEl) at
// line ~262 of renderMarkdownInto threw `ReferenceError`, which surfaced
// as the "ReferenceError: renderMermaidBlocks is not defined" message
// inside any .md drawer the user opened from the Ingestion file list.
// OLD reference (commit f5bff8e) defined all three in this file
// directly; this restores the call chain by importing them.
export { initContentRenderers, getMermaid, getRenderMathInElement } from './renderers/init.js';
export { lazyRenderBlocks } from './renderers/lazy_observer.js';
import { initContentRenderers, getRenderMathInElement } from './renderers/init.js';
import { lazyRenderBlocks } from './renderers/lazy_observer.js';
import { renderMermaidBlocks } from './renderers/mermaid.js';
import { renderTerminalBlocks } from './renderers/terminal.js';
import { addCodeCopyButtons } from './renderers/code_copy.js';

function _escapeHtml(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
                  .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// =====================================================================
// Math protect / restore — the load-bearing piece for LaTeX rendering.
//
// PROBLEM marked-katex-extension couldn't handle:
//   - `x\_1` (GitBook escapes `_` to prevent italic interpretation) —
//     marked's `\_` → `_` rewrite fires BEFORE the math extension
//     sees the span, so KaTeX gets `x_1` (correct) OR `x⟨italic⟩1`
//     depending on engine version. Either way, the original author
//     intent is silently corrupted.
//   - Bare `\begin{align}…\end{align}` blocks WITHOUT `$$..$$` wrappers
//     (the Alibi Explain ALE page pattern) — marked-katex-extension
//     only recognizes `$..$` / `$$..$$` delimiters.
//
// SOLUTION (Pandoc / markdown-it-texmath / mkdocs-arithmatex pattern):
// extract every math region to opaque `@@FWMATH{n}@@` placeholders
// BEFORE running marked.parse, so marked never sees the math content
// and never applies its escape rules to it. After DOMPurify, restore
// the math text (HTML-escaped so anything that LOOKS like HTML is
// neutralized), then let KaTeX `auto-render` scan the live DOM and
// produce its own sanitized HTML.
//
// Inline-math regex: starts with `$` then non-whitespace non-`$`, ends
// with non-whitespace before closing `$`. Skips currency in prose
// (`$5 and $10` — the closing `$` is preceded by `0`/`5` so it would
// match `$5 and $10` as one span — BUT the opening `$` of `$5` is
// followed by `5` which IS non-whitespace, so we'd incorrectly grab
// `$5 and $10$` as a single span if `10` ends with `$`; in practice
// `$5 and $10` ends without `$` so no match. For two-dollar-amount
// prose, escape with `\$`.). Inline math is single-line (no `\n`).
// =====================================================================
const _MATH_RE = new RegExp(
  '\\$\\$[\\s\\S]+?\\$\\$' +                                       // $$..$$
  '|\\\\\\[[\\s\\S]+?\\\\\\]' +                                    // \[..\]
  '|\\\\\\([\\s\\S]+?\\\\\\)' +                                    // \(..\)
  '|\\\\begin\\{([a-zA-Z*]+)\\}[\\s\\S]+?\\\\end\\{\\1\\}' +       // \begin{X}..\end{X}
  '|\\$[^\\s$][^$\\n]*?(?<=\\S)\\$',                               // $..$ inline (no ws-adjacent)
  'g',
);

function _protectMath(raw) {
  const slots = [];
  const safe = raw.replace(_MATH_RE, (m) => {
    slots.push(m);
    return `@@FWMATH${slots.length - 1}@@`;
  });
  return { safe, slots };
}

function _restoreMath(html, slots) {
  if (!slots.length) return html;
  return html.replace(/@@FWMATH(\d+)@@/g, (_, i) => {
    let s = slots[+i];
    // KaTeX does NOT implement \begin{align} (it's LaTeX-only); the
    // documented equivalent is \begin{aligned}. Same for align*.
    // (https://github.com/KaTeX/KaTeX/issues/1007)
    s = s.replace(/\\begin\{align\*?\}/g, '\\begin{aligned}')
         .replace(/\\end\{align\*?\}/g, '\\end{aligned}');
    // HTML-escape so the restored math text lands as TEXT in the DOM,
    // not as HTML. KaTeX auto-render reads textContent (decoded) so it
    // still sees the original delimiters and renders correctly.
    return _escapeHtml(s);
  });
}

// =====================================================================
// Raw-HTML block protect / restore — the load-bearing piece for TABLES.
//
// PROBLEM (Dask / pandas / xarray / any Jupyter-derived docs):
//   Tier-1 llms-full bundles embed Jupyter's HTML repr verbatim — nested
//   `<table>` blocks (often with an inline `<svg>` chunk-diagram) that
//   contain BLANK LINES and deep indentation, e.g.
//       <table>
//           <tbody>
//                                  <-- blank line
//               <tr> <th> Bytes </th> ... </tr>
//   CommonMark terminates a type-6 HTML block at the FIRST blank line, so
//   marked closes the block at `<tbody>` and then re-reads the 16-space-
//   indented `<tr>` continuation as an INDENTED CODE BLOCK — emitting
//   `<pre><code>&lt;tr&gt;…</code></pre>`. The table shatters into a
//   half-rendered header + a wall of escaped tag soup.
//
// SOLUTION (same shape as _protectMath): pull every balanced top-level
// raw-HTML block (`<table>/<svg>/<figure>`) out to an opaque
// `@@FWHTML{n}@@` placeholder BEFORE marked.parse, then splice the
// untouched HTML back into marked's OUTPUT — crucially BEFORE DOMPurify,
// so the restored table/svg is still sanitized (DOMPurify's default
// profile already allows table + svg elements + their attrs). Fenced
// code regions are skipped so a documented ```html <table> example is
// shown as code, not rendered as a live table.
// =====================================================================
const _HTML_BLOCK_OPEN_RE = /^\s*<(table|svg|figure)(?:[\s/>]|$)/i;

function _protectHtmlBlocks(raw) {
  const lines = raw.split('\n');
  const out = [];
  const slots = [];
  let i = 0;
  let inFence = false;
  let fenceChar = '';
  while (i < lines.length) {
    const line = lines[i];
    // Track fenced-code regions (``` or ~~~) so we never protect HTML
    // that's being shown AS an example inside a code block.
    const fm = line.match(/^\s*(`{3,}|~{3,})/);
    if (fm) {
      const c = fm[1][0];
      if (!inFence) { inFence = true; fenceChar = c; }
      else if (c === fenceChar) { inFence = false; fenceChar = ''; }
      out.push(line); i++; continue;
    }
    if (inFence) { out.push(line); i++; continue; }
    const m = line.match(_HTML_BLOCK_OPEN_RE);
    if (!m) { out.push(line); i++; continue; }
    // Capture from this line until the matching close tag, counting
    // same-name opens/closes so a nested `<table>` inside `<table>`
    // (Dask's array repr) is balanced correctly.
    const tag = m[1].toLowerCase();
    const openRe = new RegExp('<' + tag + '\\b', 'gi');
    const closeRe = new RegExp('</' + tag + '\\s*>', 'gi');
    let depth = 0;
    let j = i;
    let closed = false;
    const cap = [];
    for (; j < lines.length; j++) {
      const L = lines[j];
      cap.push(L);
      depth += (L.match(openRe) || []).length;
      depth -= (L.match(closeRe) || []).length;
      if (depth <= 0) { closed = true; break; }
    }
    if (!closed) {
      // Unbalanced (truncated doc / malformed) — leave the line as-is and
      // let marked handle it however it would have.
      out.push(line); i++; continue;
    }
    slots.push(cap.join('\n'));
    // Blank lines around the placeholder so marked treats it as its own
    // standalone paragraph (and doesn't glue it to adjacent prose).
    out.push('');
    out.push(`@@FWHTML${slots.length - 1}@@`);
    out.push('');
    i = j + 1;
  }
  return { safe: out.join('\n'), slots };
}

function _restoreHtmlBlocks(html, slots) {
  if (!slots.length) return html;
  // marked wraps a lone placeholder line in <p>…</p>; strip that wrapper
  // when splicing the block back so we don't nest block HTML inside <p>.
  return html.replace(
    /(?:<p>\s*)?@@FWHTML(\d+)@@(?:\s*<\/p>)?/g,
    (_, i) => slots[+i] || '',
  );
}

// KaTeX auto-render delimiter list — matches what we extract above plus
// the additional LaTeX environments KaTeX supports natively. Bare
// `\begin{aligned}` works because protect/restore already rewrote
// `\begin{align}` to it.
const _KATEX_DELIMS = [
  { left: '$$', right: '$$', display: true },
  { left: '$',  right: '$',  display: false },
  { left: '\\[', right: '\\]', display: true },
  { left: '\\(', right: '\\)', display: false },
  { left: '\\begin{equation}', right: '\\end{equation}', display: true },
  { left: '\\begin{aligned}',  right: '\\end{aligned}',  display: true },
  { left: '\\begin{alignat}',  right: '\\end{alignat}',  display: true },
  { left: '\\begin{gather}',   right: '\\end{gather}',   display: true },
  { left: '\\begin{matrix}',   right: '\\end{matrix}',   display: true },
  { left: '\\begin{pmatrix}',  right: '\\end{pmatrix}',  display: true },
  { left: '\\begin{bmatrix}',  right: '\\end{bmatrix}',  display: true },
  { left: '\\begin{vmatrix}',  right: '\\end{vmatrix}',  display: true },
  { left: '\\begin{Bmatrix}',  right: '\\end{Bmatrix}',  display: true },
  { left: '\\begin{Vmatrix}',  right: '\\end{Vmatrix}',  display: true },
  { left: '\\begin{cases}',    right: '\\end{cases}',    display: true },
  { left: '\\begin{CD}',       right: '\\end{CD}',       display: true },
];

// ============================================================
// Orchestrator — the only function callers should normally need.
// ============================================================
export async function renderMarkdownInto(rootEl, rawMd, opts = {}) {
  if (!rootEl) return;
  await initContentRenderers();
  let html;
  if (typeof marked !== 'undefined' && typeof DOMPurify !== 'undefined') {
    // Protect raw HTML blocks (tables/svg/figure) FIRST — their internal
    // blank lines would otherwise terminate the CommonMark HTML block and
    // shove the indented continuation into an escaped <pre><code>.
    const { safe: htmlSafe, slots: htmlSlots } = _protectHtmlBlocks(rawMd);
    // Then protect math regions so marked's `\_` / `*` / `_` escape
    // rules never touch the math content.
    const { safe, slots } = _protectMath(htmlSafe);
    // Splice the untouched HTML blocks back into marked's output BEFORE
    // sanitizing, so the restored table/svg is run through DOMPurify too.
    let parsed = marked.parse(safe);
    parsed = _restoreHtmlBlocks(parsed, htmlSlots);
    // SANITIZE marked's OUTPUT — page bodies are untrusted (LLM-emitted
    // chapter markdown, or third-party doc HTML converted to markdown).
    // Keep presentational <font>/<b> (terminal colors) + table/link attrs;
    // DOMPurify strips <script>, on*-handlers, javascript: URLs, etc.
    html = DOMPurify.sanitize(parsed, {
      ADD_TAGS: ['font'],
      ADD_ATTR: ['color', 'target', 'align'],
    });
    // Restore math text (HTML-escaped) into the post-sanitize HTML.
    // The escape neutralizes any HTML-looking payload inside the math
    // span; KaTeX auto-render then reads textContent (decoded) and
    // produces its own trusted SVG/HTML output.
    html = _restoreMath(html, slots);
  } else {
    // If either lib failed to load, fall back to safe escaped <pre> —
    // never inject raw HTML.
    html = '<pre>' + _escapeHtml(rawMd) + '</pre>';
  }
  rootEl.innerHTML = html;
  if (opts.postProcess) { try { opts.postProcess(rootEl); } catch (_) {} }
  renderMermaidBlocks(rootEl);
  renderTerminalBlocks(rootEl);
  if (opts.addCopyButtons !== false) addCodeCopyButtons(rootEl);
  lazyRenderBlocks(rootEl);
  // Math last — auto-render walks the live DOM looking for delimiters
  // (including bare \begin{aligned}…\end{aligned}) and replaces each
  // text node with KaTeX HTML in place. `ignoredTags: pre,code` keeps
  // currency-in-prose and shell variables inside fenced blocks safe.
  // `throwOnError: false` + `strict: 'ignore'` make malformed math
  // degrade to its literal source rather than crashing the page.
  //
  // `_renderMathInElement` used to be a module-private `let` here (OLD
  // commit f5bff8e). It was extracted to `./renderers/init.js` but the
  // call sites still referenced the old bare name → ReferenceError when
  // any .md drawer rendered. `getRenderMathInElement()` returns the
  // latest cached value (null until KaTeX auto-render finishes loading,
  // then the function itself). Same lazy pattern, just routed through
  // the accessor.
  const renderMath = getRenderMathInElement();
  if (renderMath) {
    try {
      renderMath(rootEl, {
        delimiters: _KATEX_DELIMS,
        ignoredTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code'],
        throwOnError: false,
        trust: false,
        strict: 'ignore',
      });
    } catch (_) { /* malformed math leaves the page intact */ }
  }
}
