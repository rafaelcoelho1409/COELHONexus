// Shared timing-display helpers for DD Planner/Synth (2026-05-29).
//
// Per-node times already render in the NodeDrawer (each "done" event shows
// its wall_ms). This module owns the navbar row-3 "total" indicators: a live
// wall-clock that ticks while a run is in flight, falling back to the
// server-persisted total on load / for already-finished (cached) runs.
//
// Design note: totals are WALL-CLOCK (elapsed), never a sum of per-node
// wall_ms — synth/planner fan out concurrently, so summing overcounts.

export function fmtMs(ms) {
  ms = Math.max(0, Math.round(Number(ms) || 0));
  if (ms < 1000) return ms + ' ms';
  const s = Math.floor(ms / 1000);
  if (s < 60) return (ms / 1000).toFixed(1) + 's';
  const m = Math.floor(s / 60), rs = s % 60;
  if (m < 60) return m + 'm ' + String(rs).padStart(2, '0') + 's';
  const h = Math.floor(m / 60), rm = m % 60;
  return h + 'h ' + String(rm).padStart(2, '0') + 'm';
}

const _tickers = {};   // kind -> {t0, baseMs, intervalId}

function _el(kind) { return document.getElementById('fw-' + kind + '-elapsed'); }

// Static set — load / cached / final authoritative value. ms<=0 clears it.
export function showElapsed(kind, ms) {
  const el = _el(kind);
  if (!el) return;
  el.textContent = (ms > 0) ? ('⏱ ' + fmtMs(ms)) : '';
  el.dataset.running = '0';
}

export function isElapsedRunning(kind) {
  return !!_tickers[kind];
}

// Begin a live wall-clock ticker. `baseMs` (optional) is prior accumulated
// time to add to (e.g. a resume that already has finished chapters).
// Idempotent: a no-op if a ticker for `kind` is already running, so callers
// can fire it on every live event without resetting t0.
export function startElapsed(kind, baseMs) {
  if (_tickers[kind]) return;
  const t = { t0: Date.now(), baseMs: Math.max(0, baseMs || 0) };
  _tickers[kind] = t;
  const paint = () => {
    const el = _el(kind);
    if (!el) return;
    el.textContent = '⏱ ' + fmtMs(t.baseMs + (Date.now() - t.t0));
    el.dataset.running = '1';
  };
  paint();
  t.intervalId = setInterval(paint, 1000);
}

// Stop ticking. If `finalMs` is a number, show it (authoritative); otherwise
// freeze the last live value.
export function stopElapsed(kind, finalMs) {
  const t = _tickers[kind];
  if (t && t.intervalId) clearInterval(t.intervalId);
  delete _tickers[kind];
  if (typeof finalMs === 'number' && finalMs >= 0) {
    showElapsed(kind, finalMs);
  } else {
    const el = _el(kind);
    if (el) el.dataset.running = '0';
  }
}
