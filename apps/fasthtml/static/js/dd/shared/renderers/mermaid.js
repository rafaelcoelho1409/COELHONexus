// shared/renderers/mermaid.js — Mermaid diagram-block detection +
// `<div class="mermaid">` conversion. Lazy-rendered to SVG by
// `lazyRenderBlocks` in content_renderer.js (the IntersectionObserver
// hooks live there because they coordinate with KaTeX + hljs which
// share the same observer). Extracted from content_renderer.js Step 6
// (2026-06-05) — fully self-contained, no cross-refs back.

// ```mermaid fences → <div class="mermaid"> holding the RAW graph text (safe);
// rendered to SVG lazily, on-visible, by lazyRenderBlocks.
//
// TWO detection paths:
//   1. Explicit `language-mermaid` class — clean publisher source.
//   2. Fenceless code blocks whose first non-blank line opens with an
//      unmistakable Mermaid diagram-type keyword. This catches the
//      HTML→markdown round-trip where the source carries the diagram
//      inside `<pre>` (e.g. Jekyll GitHub Pages, Docusaurus, sphinx-
//      mermaid) and `markdownify` strips the language class to a plain
//      ``` fence with no info string. Metasploit's
//      `attacking-ad-cs-esc-vulnerabilities.html` is the canonical
//      failing case: 58 fences on the page, ALL info-string-less, two
//      of which are flowcharts that previously rendered as raw code.
//
// To minimize false positives, fenceless detection requires:
//   - `flowchart`/`graph` followed by an explicit direction
//     (TD/TB/BT/LR/RL) — guards against Python like `graph = ...`.
//   - other diagram keywords (sequenceDiagram, classDiagram, gantt,
//     etc.) are distinctive enough to match alone.
// Each diagram keyword MUST be followed by whitespace or end-of-string
// (NOT by punctuation like `.`, `=`, `(`). The `\b` word-boundary alone
// is too loose — it accepts `timeline.append(...)`. Using `(?:\s|$)`
// requires the keyword to be the first token on its own line, which is
// the Mermaid contract.
const _MERMAID_HEAD_RE = new RegExp(
  '^\\s*(?:' +
    '(?:flowchart|graph)\\s+(?:TD|TB|BT|LR|RL)(?:\\s|$)' +
    '|sequenceDiagram(?:\\s|$)' +
    '|classDiagram(?:-v2)?(?:\\s|$)' +
    '|stateDiagram(?:-v2)?(?:\\s|$)' +
    '|erDiagram(?:\\s|$)' +
    '|gantt(?:\\s|$)' +
    '|journey(?:\\s|$)' +
    '|pie(?:\\s|$)' +
    '|mindmap(?:\\s|$)' +
    '|timeline(?:\\s|$)' +
    '|quadrantChart(?:\\s|$)' +
    '|requirementDiagram(?:\\s|$)' +
    '|gitGraph(?:\\s|$)' +
    '|C4(?:Context|Container|Component|Dynamic|Deployment)(?:\\s|$)' +
    '|block-beta(?:\\s|$)' +
    '|xychart-beta(?:\\s|$)' +
    '|sankey-beta(?:\\s|$)' +
  ')',
);

function _convertCodeBlockToMermaid(code) {
  const div = document.createElement('div');
  div.className = 'mermaid fw-mermaid';
  div.textContent = code.textContent || '';      // text, never HTML
  (code.parentElement || code).replaceWith(div);
}

export function renderMermaidBlocks(root) {
  if (!root) return;
  // Path 1 — explicit language-mermaid fence.
  root.querySelectorAll('pre > code.language-mermaid')
    .forEach(_convertCodeBlockToMermaid);
  // Path 2 — info-less fences whose first line is mermaid syntax.
  // Skip blocks that already carry a `language-*` class (we don't want
  // to override a publisher's explicit `language-python` etc.).
  root.querySelectorAll('pre > code').forEach((code) => {
    const cls = code.className || '';
    if (/language-/.test(cls)) return;
    if (_MERMAID_HEAD_RE.test(code.textContent || '')) {
      _convertCodeBlockToMermaid(code);
    }
  });
}

// Terminal/console output → dark terminal block. ANSI colors arrive as escaped
// <font color>/<b> HTML (FastAPI-docs style); re-render that presentational
// subset safely (DOMPurify allow-list) so colors show instead of literal tags.
// Plain console blocks just get terminal styling + skip syntax highlighting.
