// shared/renderers/lazy_observer.js — IntersectionObserver-driven
// lazy rendering of Mermaid + KaTeX + hljs blocks. Extracted from
// content_renderer.js Step 1 (2026-06-05 follow-up). Vendor handles
// are pulled via getters on init.js — they're null at module load
// and populated by initContentRenderers() the first time a chapter
// renders.
import { getMermaid, getRenderMathInElement } from './init.js';
import { _findScrollRoot } from './code_copy.js';

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
      if (getMermaid()) { try { getMermaid().run({ nodes: [el] }); } catch (_) {} }
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
