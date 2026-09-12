// study/progress.js — reading-progress bar for the active chapter.
//
// CSS-first: `#fw-study-progress-bar-fill` is animated purely by
// `animation-timeline: scroll(nearest block)` (tabs.css) — zero JS, zero
// scroll-listener overhead, on any browser that supports scroll-driven
// animations (Chrome/Edge since 115, Safari since 26 / Sept-2025).
//
// Firefox stable still gates the feature behind a flag as of Sept-2026
// (`layout.css.scroll-driven-animations.enabled`), so this module
// feature-detects once at load and installs a plain scroll-listener
// fallback ONLY when the CSS path isn't available — never both, so the
// bar never double-animates.
import * as Ss from '@dd/shared/state/study.js';

const _CSS_SUPPORTED = (() => {
  try { return CSS.supports('animation-timeline', 'scroll()'); }
  catch (_) { return false; }
})();

let _scrollRoot = null;

function _update() {
  if (!Ss.studyProgressFillEl || !_scrollRoot) return;
  const max = _scrollRoot.scrollHeight - _scrollRoot.clientHeight;
  const pct = max > 0
    ? Math.min(1, Math.max(0, _scrollRoot.scrollTop / max))
    : 0;
  Ss.studyProgressFillEl.style.width = (pct * 100) + '%';
}

// One-time wiring — safe to call even if the bar or `.page` isn't in the
// DOM yet (Study page not the active feature on load).
export function initStudyProgressBar() {
  if (_CSS_SUPPORTED || !Ss.studyProgressFillEl) return;
  _scrollRoot = Ss.studyProgressFillEl.closest('.page') ||
    document.querySelector('.page');
  if (!_scrollRoot) return;
  _scrollRoot.addEventListener('scroll', _update, { passive: true });
  _update();
}

// Called right after a chapter switch (new content height + reader
// scrolled back to top) so the fallback bar snaps to 0 immediately
// instead of waiting for the next manual scroll.
export function resetStudyProgressBar() {
  if (_CSS_SUPPORTED) return;
  requestAnimationFrame(_update);
}
