// shared/renderers/init.js — Vendor warm-up + getters for the
// late-bound vendor handles. initContentRenderers() loads the
// Mermaid + KaTeX UMD scripts and stores the references in
// module-private state; lazy_observer.js reads them via the
// getters (deferred resolution because they're null at module
// load and only populated once the user navigates to a chapter).
//
// Extracted from content_renderer.js Step 1 (2026-06-05 follow-up).

let _renderersPromise = null;
let _mermaid = null;
let _renderMathInElement = null;

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


// Getters — lazy_observer.js calls these on each render rather
// than capturing module-load values (which would always be null).
export function getMermaid() { return _mermaid; }
export function getRenderMathInElement() { return _renderMathInElement; }
