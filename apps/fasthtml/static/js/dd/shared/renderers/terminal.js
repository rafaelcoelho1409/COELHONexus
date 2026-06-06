// shared/renderers/terminal.js — terminal/console code-block styling
// (`<pre>` flagged with `.fw-terminal` for monospace + dark theme).
// ANSI-escape colors arrive as <font color>/<b> HTML (FastAPI-docs
// style); DOMPurify allow-list re-renders that subset safely.
// Extracted from content_renderer.js Step 6 (2026-06-05).

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
