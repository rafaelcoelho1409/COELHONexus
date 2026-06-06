// shared/renderers/code_copy.js — `<pre><code>` ↦ Copy-button injector
// + the _findScrollRoot helper that finds the nearest scrollable
// ancestor (so a copy-button click that ends up inside an off-screen
// code block reveals it before flashing the success state).
// Extracted from content_renderer.js Step 3 (2026-06-05 follow-up).
// Self-contained — zero cross-refs to other content_renderer state.

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
export function _findScrollRoot(el) {
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
