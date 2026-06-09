// ============================================================
// nav.js — Page-navigation helpers for the per-stage Docs
//   Distiller routes. Replaces the wizard's showStep/jumpTo
//   in-page panel toggling with real <a href> redirects so the
//   URL stays the source of truth for "which stage am I on?".
// ============================================================

export const STAGES = ['catalog', 'ingestion', 'pipeline', 'planner', 'synth', 'study'];

// Read the active stage from the server-rendered .fw-picker wrapper.
// _DDPage stamps `data-dd-stage` on that div for every route.
export function currentStage() {
  return document.querySelector('.fw-picker')?.dataset.ddStage || 'catalog';
}

// Read the slug the server resolved for THIS page. Catalog never has
// a slug; other stages may render with a slug (route resolved it from
// the query string). Falls back to URLSearchParams for browser-back
// after a History API push (we don't push today, but cheap insurance).
export function currentSlug() {
  const fromBody = document.querySelector('.fw-picker')?.dataset.ddSlug;
  if (fromBody) return fromBody;
  try {
    return new URLSearchParams(window.location.search).get('slug') || null;
  } catch (_) { return null; }
}

// Read the run_id from URL — used by /docs-distiller/ingestion to
// auto-resume polling on page load without a separate fetch.
export function currentRunId() {
  try {
    return new URLSearchParams(window.location.search).get('run') || null;
  } catch (_) { return null; }
}

// Build a stage URL. Catalog has no slug; everything else carries the
// slug as ?slug=...&run=... (run only when set).
export function stageUrl(stage, slug, runId) {
  let url = '/docs-distiller';
  if (stage && stage !== 'catalog') url += '/' + stage;
  const qs = new URLSearchParams();
  if (slug && stage !== 'catalog') qs.set('slug', slug);
  if (runId && stage === 'ingestion') qs.set('run', runId);
  const qsStr = qs.toString();
  return qsStr ? url + '?' + qsStr : url;
}

// Hard navigation to a stage. Full page reload (per the server-side
// split plan); the destination route hydrates from the URL.
export function navigateToStage(stage, slug, runId) {
  window.location.href = stageUrl(stage, slug, runId);
}
