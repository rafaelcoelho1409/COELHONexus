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

let _renderersPromise = null;
let _mermaid = null;
let _renderMathInElement = null;

// Dynamic <script> loader for UMD bundles (KaTeX core + auto-render
// expose globals on window, not ES exports). Cached so the second
// call resolves instantly. Async=false preserves load order; reject
// on `error` so failures are caught by the try/except wrapping the call.
function _loadUmdScript(src) {
  return new Promise((resolve, reject) => {
    // Already loaded? `data-fw-src` lets us dedup across calls without
    // re-checking every existing <script src=…>.
    if (document.querySelector(`script[data-fw-src="${src}"]`)) return resolve();
    const s = document.createElement('script');
    s.src = src;
    s.async = false;
    s.dataset.fwSrc = src;
    s.onload = () => resolve();
    s.onerror = (e) => reject(e);
    document.head.appendChild(s);
  });
}

export function initContentRenderers() {
  if (_renderersPromise) return _renderersPromise;
  _renderersPromise = (async () => {
    // GitHub-style alert callouts — registered on the global `marked`
    // (marked is loaded as a UMD in shell.py HEAD; marked-alert is a
    // small +esm import).
    try {
      const alertExt = await import('https://cdn.jsdelivr.net/npm/marked-alert@2/+esm');
      if (typeof marked !== 'undefined') {
        try { marked.use(alertExt.default()); } catch (_) {}
      }
    } catch (_) { /* callouts optional — degrade to plain markdown */ }

    // KaTeX core + auto-render (UMD, both attach globals to window).
    // We DROPPED marked-katex-extension here (2026-06-01) because it
    // only recognized $..$ / $$..$$, missing the two patterns that
    // dominate real-world docs markdown:
    //   1. Bare \begin{aligned}…\end{aligned} environments with no $$
    //      wrapper (Alibi Explain, Jupyter-converted notebooks)
    //   2. Backslash-escaped underscores inside inline math (`x\_1`,
    //      GitBook convention) — marked mangles `\_` BEFORE the math
    //      extension sees the content, so KaTeX gets the wrong string.
    // The new pipeline (see renderMarkdownInto): protect math BEFORE
    // marked, restore AFTER DOMPurify, then auto-render sweeps the live
    // DOM. Auto-render natively supports \begin{X}…\end{X} delimiters
    // and the protect-before-parse approach defangs marked's escape rules.
    try {
      await _loadUmdScript('https://cdn.jsdelivr.net/npm/katex@0.16.22/dist/katex.min.js');
      await _loadUmdScript('https://cdn.jsdelivr.net/npm/katex@0.16.22/dist/contrib/auto-render.min.js');
      _renderMathInElement = (typeof window !== 'undefined')
        ? (window.renderMathInElement || null) : null;
    } catch (_) { _renderMathInElement = null; }

    // Mermaid — diagrams. securityLevel:'strict' is load-bearing (untrusted
    // LLM-emitted diagram source). theme:'base' recolored to the
    // warm-paper/burgundy palette. v11.15+ sanitizes its own SVG output.
    try {
      const m = await import('https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs');
      _mermaid = m.default;
      _mermaid.initialize({
        startOnLoad: false,
        securityLevel: 'strict',
        theme: 'base',
        themeVariables: {
          background: '#fffdf9', primaryColor: '#fef7e0',
          primaryBorderColor: '#c41230', primaryTextColor: '#1f1d1b',
          lineColor: '#c41230', secondaryColor: '#f7f5f1', tertiaryColor: '#fffdf9',
        },
      });
    } catch (_) { _mermaid = null; }
  })();
  return _renderersPromise;
}

// ```mermaid fences → <div class="mermaid"> holding the RAW graph text (safe);
// rendered to SVG lazily, on-visible, by lazyRenderBlocks.
export function renderMermaidBlocks(root) {
  if (!root) return;
  root.querySelectorAll('pre > code.language-mermaid').forEach((code) => {
    const div = document.createElement('div');
    div.className = 'mermaid fw-mermaid';
    div.textContent = code.textContent || '';      // text, never HTML
    (code.parentElement || code).replaceWith(div);
  });
}

// Terminal/console output → dark terminal block. ANSI colors arrive as escaped
// <font color>/<b> HTML (FastAPI-docs style); re-render that presentational
// subset safely (DOMPurify allow-list) so colors show instead of literal tags.
// Plain console blocks just get terminal styling + skip syntax highlighting.
const _TERM_LANG_RE = /\blanguage-(console|shell|shell-session|shellsession|bash|sh|zsh|terminal|ansi)\b/i;
export function renderTerminalBlocks(root) {
  if (!root) return;
  root.querySelectorAll('pre > code').forEach((code) => {
    const txt = code.textContent || '';
    // `<font color=` is the ANSI-to-HTML signature (terminal converters emit it;
    // real code almost never does). Don't match bare <b> — that would mangle
    // HTML/markup code examples. <b> is still allowed in the re-render below.
    const ansiHtml = /<font\s+color=/i.test(txt);
    if (!(_TERM_LANG_RE.test(code.className || '') || ansiHtml)) return;
    code.parentElement.classList.add('fw-terminal');
    code.dataset.noHighlight = '1';               // never hljs terminal output
    if (ansiHtml && typeof DOMPurify !== 'undefined') {
      code.innerHTML = DOMPurify.sanitize(txt, {
        ALLOWED_TAGS: ['font', 'b', 'strong', 'i', 'em', 'u', 'span', 'br'],
        ALLOWED_ATTR: ['color', 'style', 'class'],
      });
    }
  });
}

// Add a language badge + "Copy" button to every code/terminal block.
export function addCodeCopyButtons(root) {
  if (!root) return;
  root.querySelectorAll('pre').forEach(pre => {
    if (pre.querySelector('.fw-code-copy')) return;
    const code = pre.querySelector('code');
    let lang = '';
    if (pre.classList.contains('fw-terminal')) lang = 'Terminal';
    else if (code) {
      const m = (code.className || '').match(/language-([\w+#.-]+)/);
      if (m && m[1] && m[1] !== 'mermaid') lang = m[1];
    }
    if (lang) {
      const badge = document.createElement('span');
      badge.className = 'fw-code-lang';
      badge.textContent = lang;
      pre.appendChild(badge);
    }
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'fw-code-copy';
    btn.textContent = 'Copy';
    btn.addEventListener('click', async () => {
      const c = pre.querySelector('code')?.innerText ?? pre.innerText;
      try {
        await navigator.clipboard.writeText(c);
        btn.textContent = 'Copied';
      } catch (_) { btn.textContent = 'Copy failed'; }
      setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
    });
    pre.appendChild(btn);
  });
}

// Find the nearest scrollable ancestor — the IntersectionObserver root for
// lazy code/diagram rendering. Falls back to null (viewport) for pages
// where the scroll happens at the document level (Ingestion page) vs an
// inner overflow-scroll container (Study page `.page`, drawer body).
function _findScrollRoot(el) {
  let node = el?.parentElement || null;
  while (node) {
    const oy = getComputedStyle(node).overflowY;
    if (oy === 'auto' || oy === 'scroll') return node;
    node = node.parentElement;
  }
  return null;
}

// Lazily highlight code + render mermaid as each block nears the viewport. One
// IntersectionObserver per render call — disconnected on the next call to
// avoid leaks when the drawer flips between pages or the chapter changes.
let _blockRenderObserver = null;
export function lazyRenderBlocks(root) {
  if (!root) return;
  if (_blockRenderObserver) { _blockRenderObserver.disconnect(); _blockRenderObserver = null; }
  const targets = [
    ...root.querySelectorAll('pre > code:not([data-no-highlight])'),
    ...root.querySelectorAll('.mermaid'),
  ];
  if (!targets.length) return;
  const render = (el) => {
    if (el.classList.contains('mermaid')) {
      if (_mermaid) { try { _mermaid.run({ nodes: [el] }); } catch (_) {} }
    } else if (typeof hljs !== 'undefined') {
      try { hljs.highlightElement(el); } catch (_) {}
    }
  };
  if (!('IntersectionObserver' in window)) { targets.forEach(render); return; }
  const scrollRoot = _findScrollRoot(root);
  _blockRenderObserver = new IntersectionObserver((entries, obs) => {
    entries.forEach((en) => {
      if (!en.isIntersecting) return;
      obs.unobserve(en.target);
      render(en.target);
    });
  }, { root: scrollRoot, rootMargin: '300px 0px 300px 0px', threshold: 0 });
  targets.forEach((t) => _blockRenderObserver.observe(t));
}

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
    // Protect math regions FIRST so marked's `\_` / `*` / `_` escape
    // rules never touch the math content.
    const { safe, slots } = _protectMath(rawMd);
    // SANITIZE marked's OUTPUT — page bodies are untrusted (LLM-emitted
    // chapter markdown, or third-party doc HTML converted to markdown).
    // Keep presentational <font>/<b> (terminal colors) + table/link attrs;
    // DOMPurify strips <script>, on*-handlers, javascript: URLs, etc.
    html = DOMPurify.sanitize(marked.parse(safe), {
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
  if (_renderMathInElement) {
    try {
      _renderMathInElement(rootEl, {
        delimiters: _KATEX_DELIMS,
        ignoredTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code'],
        throwOnError: false,
        trust: false,
        strict: 'ignore',
      });
    } catch (_) { /* malformed math leaves the page intact */ }
  }
}
