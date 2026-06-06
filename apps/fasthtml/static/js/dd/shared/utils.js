// ============================================================
// utils.js — Pure utility functions, no DOM dependencies
// ============================================================

export function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

export function fmtBytes(n) {
  if (!n) return '0 B';
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  return (n / (1024 * 1024)).toFixed(1) + ' MB';
}

export function fmtAge(ts) {
  if (!ts) return '';
  const s = Math.max(1, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return s + 's ago';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  return Math.floor(s / 86400) + 'd ago';
}

export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;',
    '"': '&quot;', "'": '&#39;',
  }[c]));
}

export function formatFieldValue(v) {
  if (v === null || v === undefined) return String(v);
  if (Array.isArray(v)) {
    if (v.length === 0) return '[]';
    const head = v.slice(0, 20).map(x => '  ' + JSON.stringify(x)).join(',\n');
    const tail = v.length > 20 ? '\n  … (' + (v.length - 20) + ' more)' : '';
    return '[\n' + head + tail + '\n] (' + v.length + ' items)';
  }
  return JSON.stringify(v, null, 2);
}
