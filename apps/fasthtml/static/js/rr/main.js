/* Research Radar — scan trigger + SSE phase stream + digest render.
 *
 * Wiring:
 *   POST /api/v1/rr/scan                  → enqueue scan
 *   GET  /api/v1/rr/scan/{id}/events      → SSE phase events
 *   GET  /api/v1/rr/scan/{id}             → final result (poll fallback)
 *
 * The SSE stream terminates on phase=done|error; we also poll every 5s
 * as a belt-and-suspenders so a dropped SSE connection still surfaces
 * the final result.
 *
 * URL-state persistence: the active scan_id is encoded as `?scan=<uuid>`
 * via `history.replaceState`. On page load we read it and resume the
 * appropriate UI state from `GET /scan/{id}`:
 *
 *   pending / running   → reattach SSE + poll (live updates)
 *   done                → render the digest directly (no SSE needed)
 *   error / cancelled   → show the terminal status + error string
 *   404                 → silently drop the stale id, show empty form
 *
 * This makes scans bookmarkable, refresh-safe, and shareable.
 */
import { showConfirm } from '/static/js/dd/shared/ui/overlays.js';

const $ = (id) => document.getElementById(id);

const form        = $('rr-scan-form');
const statusText  = $('rr-status-text');
// Pill root carries `data-status` — `setStatus()` writes the lifecycle
// value (idle / working / done / failed / cancelled / cancelling) here
// and the CSS does the rest (border + glyph + bg).
const statusDot   = $('rr-status');
const statusInfo  = $('rr-status-detail');
const statusTopic = $('rr-status-topic');     // Pipeline page only
const digestTopic = $('rr-digest-topic');     // Digest page only
const digestArea  = $('rr-digest-items');
const digestEmpty = $('rr-digest-empty');

// Short phase labels — the graph below is the verbose "what's running"
// surface; the pill is a one-liner lifecycle badge. Long descriptive
// labels were dropped after the Pipeline page split.
const PHASE_LABELS = {
  pending:     'Pending',
  running:     'Running',
  discovery:   'Discovery',
  triage:      'Triage',
  deep_read:   'Deep read',
  graph_build: 'Graph build',
  synthesis:   'Synthesis',
  report:      'Report',
  persisting:  'Persisting',
  done:        'Done',
  error:       'Failed',
  cancelled:   'Cancelled',
  cancelling:  'Cancelling',
};

// SSE phase → pill `data-status` — collapses the 13-phase ladder onto the
// 5 status values the CSS knows how to render (mirrors DD Planner/Synth).
const PHASE_TO_STATUS = {
  pending:     'working',
  running:     'working',
  discovery:   'working',
  triage:      'working',
  deep_read:   'working',
  graph_build: 'working',
  synthesis:   'working',
  report:      'working',
  persisting:  'working',
  done:        'done',
  error:       'failed',
  cancelled:   'cancelled',
  cancelling:  'cancelling',
};

let activeScanId = null;
let evtSrc       = null;
let pollTimer    = null;

/* ────────────────────────────────────────────────────────────────────────── *
 * Button state machine — keeps Start / Stop in sync with the scan lifecycle.
 *
 *   idle        Start enabled · Stop hidden
 *   running     Start disabled · Stop visible+enabled
 *   cancelling  Start disabled · Stop visible+disabled (busy spinner)
 *
 * Pre-terminal phases (pending, running, discovery, …, persisting) map to
 * `running`. Terminal phases (done, error, cancelled) map to `idle` so the
 * operator can immediately fire another scan.
 * ────────────────────────────────────────────────────────────────────────── */
const startBtn = document.getElementById('rr-start-btn');
const stopBtn  = document.getElementById('rr-stop-btn');

const PHASES_TERMINAL    = new Set(['done', 'error', 'cancelled']);
const PHASES_PRE_TERMINAL = new Set([
  'pending', 'running', 'discovery', 'triage', 'deep_read',
  'graph_build', 'synthesis', 'report', 'persisting',
]);

function setButtonsForPhase(phase) {
  if (PHASES_TERMINAL.has(phase) || !phase || phase === 'idle') {
    setButtonsState('idle');
  } else if (PHASES_PRE_TERMINAL.has(phase)) {
    setButtonsState('running');
  }
  // Any other phase (custom) — leave state untouched.
}

function setButtonsState(state) {
  if (!startBtn || !stopBtn) return;
  switch (state) {
    case 'idle':
      startBtn.disabled = false;
      stopBtn.hidden    = true;
      stopBtn.disabled  = true;
      stopBtn.dataset.busy = 'false';
      break;
    case 'running':
      startBtn.disabled = true;
      stopBtn.hidden    = false;
      stopBtn.disabled  = false;
      stopBtn.dataset.busy = 'false';
      break;
    case 'cancelling':
      startBtn.disabled = true;
      stopBtn.hidden    = false;
      stopBtn.disabled  = true;
      stopBtn.dataset.busy = 'true';
      break;
  }
}

/* ────────────────────────────────────────────────────────────────────────── *
 * Verticals multi-select — checkbox panel + custom-add field.
 *
 *   checkbox change → rebuild #verticals from checked rows
 *   custom add      → validate against the inlined arXiv taxonomy:
 *                       valid + new   → inject a checked row, sync
 *                       valid + dup   → just check the existing row, sync
 *                       invalid       → inline error + shake
 *
 * #verticals is a hidden input — the comma-separated string the API expects.
 * Server-side `domains/rr/schemas.py` re-validates every code (defense-in-depth).
 * ────────────────────────────────────────────────────────────────────────── */
const verticalsInput     = form?.querySelector('#verticals');
const verticalMultiselect = $('rr-multiselect');
const verticalSummary    = $('rr-vertical-summary');
const verticalOptions    = $('rr-vertical-options');
const verticalCustom     = $('rr-vertical-custom');
const verticalAddBtn     = $('rr-vertical-add-btn');
const verticalError      = $('rr-vertical-error');
const verticalTaxonomyEl = $('rr-vertical-taxonomy');

const VERTICAL_TAXONOMY = (() => {
  try { return new Set(JSON.parse(verticalTaxonomyEl?.textContent || '[]')); }
  catch { return new Set(); }
})();

function _parseCodes(text) {
  return (text || '')
    .split(',')
    .map(s => s.trim())
    .filter(Boolean);
}

function _syncVerticals() {
  if (!verticalsInput || !verticalOptions) return;
  const codes = [...verticalOptions.querySelectorAll('input.rr-multiselect-checkbox')]
    .filter(cb => cb.checked)
    .map(cb => cb.value);
  verticalsInput.value     = codes.join(', ');
  if (verticalSummary) {
    verticalSummary.textContent = codes.length ? codes.join(', ') : 'Pick verticals…';
  }
}

function _showVerticalError(msg) {
  if (!verticalError) return;
  verticalError.textContent = msg;
  verticalError.hidden = false;
  if (verticalCustom) {
    verticalCustom.classList.remove('rr-shake');
    // Reflow so the animation re-runs even if the class was already set
    void verticalCustom.offsetWidth;
    verticalCustom.classList.add('rr-shake');
  }
}

function _clearVerticalError() {
  if (verticalError) verticalError.hidden = true;
}

function _findRowByCode(code) {
  if (!verticalOptions) return null;
  const cb = verticalOptions.querySelector(
    `input.rr-multiselect-checkbox[data-vertical-code="${CSS.escape(code)}"]`,
  );
  return cb ? cb.closest('.rr-multiselect-row') : null;
}

function _addCustomRow(code) {
  if (!verticalOptions) return;
  const row = document.createElement('label');
  row.className = 'rr-multiselect-row rr-multiselect-row-custom';
  row.dataset.curated = 'false';
  row.innerHTML = `
    <input type="checkbox"
           class="rr-multiselect-checkbox"
           data-vertical-code="${code}"
           value="${code}"
           checked>
    <span class="rr-multiselect-code">${code}</span>
    <span class="rr-multiselect-dash">—</span>
    <span class="rr-multiselect-label">custom</span>
  `;
  verticalOptions.appendChild(row);
}

function _handleAddCustom() {
  if (!verticalCustom) return;
  const code = verticalCustom.value.trim();
  if (!code) {
    _showVerticalError('Type an arXiv subject code (e.g. eess.SP).');
    return;
  }
  if (!VERTICAL_TAXONOMY.has(code)) {
    _showVerticalError(
      `${code} is not a valid arXiv subject code. ` +
      'See arxiv.org/category_taxonomy for the full list.',
    );
    return;
  }
  const existing = _findRowByCode(code);
  if (existing) {
    const cb = existing.querySelector('input.rr-multiselect-checkbox');
    if (cb) cb.checked = true;
  } else {
    _addCustomRow(code);
  }
  verticalCustom.value = '';
  _clearVerticalError();
  _syncVerticals();
}

if (verticalOptions) {
  verticalOptions.addEventListener('change', (e) => {
    if (e.target.classList?.contains('rr-multiselect-checkbox')) _syncVerticals();
  });
}
if (verticalAddBtn) verticalAddBtn.addEventListener('click', _handleAddCustom);
if (verticalCustom) {
  verticalCustom.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); _handleAddCustom(); }
  });
  verticalCustom.addEventListener('input', _clearVerticalError);
}
// Close panel on outside click — native <details> doesn't do this for free.
document.addEventListener('click', (e) => {
  if (!verticalMultiselect?.open) return;
  if (!verticalMultiselect.contains(e.target)) verticalMultiselect.open = false;
});

/* Wipe seen-set UI removed 2026-06-17. Backend endpoint
 * POST /api/v1/rr/profile/{id}/reset-seen stays live for direct API use. */

/* Recent-scans picker — lazy-load on first open so the row 2 paint stays
 * fast (no hit to Postgres unless the operator clicks the dropdown). The
 * panel is the standard `<details>` body so toggling is native. */
const scansPicker = document.getElementById('rr-scans-picker');
const scansList   = document.getElementById('rr-scans-list');
let _scansLoaded  = false;

function _fmtScanTime(iso) {
  if (!iso) return '—';
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return iso;
  const s = (Date.now() - t) / 1000;
  if (s < 60)      return `${Math.floor(s)}s ago`;
  if (s < 3600)    return `${Math.floor(s / 60)}m ago`;
  if (s < 86400)   return `${Math.floor(s / 3600)}h ago`;
  if (s < 2592000) return `${Math.floor(s / 86400)}d ago`;
  return new Date(t).toISOString().slice(0, 16).replace('T', ' ');
}

function _fmtAbsoluteTime(iso) {
  if (!iso) return '—';
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return iso;
  const d = new Date(t);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  if (sameDay) return `today ${hh}:${mm}`;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (d.toDateString() === yesterday.toDateString()) return `yesterday ${hh}:${mm}`;
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  return `${months[d.getMonth()]} ${d.getDate()}, ${hh}:${mm}`;
}

function _fmtDuration(startedIso, finishedIso) {
  if (!startedIso || !finishedIso) return '';
  const t0 = Date.parse(startedIso), t1 = Date.parse(finishedIso);
  if (!Number.isFinite(t0) || !Number.isFinite(t1)) return '';
  const s = Math.floor((t1 - t0) / 1000);
  if (s < 60)  return `${s}s`;
  const m = Math.floor(s / 60), sr = s - m * 60;
  if (m < 60)  return sr ? `${m}m ${sr}s` : `${m}m`;
  const h = Math.floor(m / 60), mr = m - h * 60;
  return mr ? `${h}h ${mr}m` : `${h}h`;
}

function _esc(s) {
  return String(s ?? '').replace(/[&<>"]/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

async function _loadRecentScans() {
  if (!scansList) return;
  scansList.innerHTML = '<div class="rr-scans-empty">Loading…</div>';
  try {
    const r = await fetch('/api/v1/rr/scans/recent?profile_id=default&limit=20');
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data  = await r.json();
    const items = data.items || [];
    if (!items.length) {
      scansList.innerHTML =
        '<div class="rr-scans-empty">No scans yet — fire one from the toolbar.</div>';
      return;
    }
    scansList.innerHTML = items.map(s => {
      const href      = `/research-radar/digest?scan=${encodeURIComponent(s.scan_id)}`;
      const topic     = s.topic || '(no topic)';
      const startedAt = _fmtAbsoluteTime(s.started_at);
      const duration  = _fmtDuration(s.started_at, s.finished_at);
      // Secondary line: verticals · findings · duration · themes preview
      const meta = [];
      if (s.verticals && s.verticals.length) meta.push(s.verticals.slice(0, 3).join(' · '));
      if (s.status === 'done') meta.push(`${s.total_in_digest} finding${s.total_in_digest === 1 ? '' : 's'}`);
      else                     meta.push(s.status);
      if (duration) meta.push(duration);
      if (s.themes && s.themes.length) meta.push(s.themes.slice(0, 2).join(', '));
      const topicShort = topic.length > 40 ? `${topic.slice(0, 40)}…` : topic;
      return (
        `<div class="rr-scans-row-wrap" data-scan-id="${_esc(s.scan_id)}">` +
          `<a class="rr-scans-row" data-status="${s.status}" href="${href}">` +
            `<div class="rr-scans-row-main">` +
              `<div class="rr-scans-row-topic">${_esc(topic)}</div>` +
              `<div class="rr-scans-row-meta">${_esc(meta.join(' · '))}</div>` +
            `</div>` +
            `<div class="rr-scans-row-time">${_esc(startedAt)}</div>` +
          `</a>` +
          `<button type="button" class="rr-scans-row-trash" ` +
                  `data-scan-id="${_esc(s.scan_id)}" ` +
                  `data-scan-topic="${_esc(topicShort)}" ` +
                  `title="Delete this scan (digest + findings; Neo4j graph stays)" ` +
                  `aria-label="Delete scan">🗑</button>` +
        `</div>`
      );
    }).join('');
  } catch (err) {
    scansList.innerHTML =
      `<div class="rr-scans-empty">Failed: ${err.message || err}</div>`;
  }
}

if (scansPicker) {
  // 2026-06-17: refresh on EVERY open instead of just the first. Picks up
  // new scans (just-completed runs, deletes from other tabs) without
  // requiring a page reload. _scansLoaded stays as a one-shot flag for
  // "we've fetched at least once" semantics elsewhere (kept for back-compat).
  scansPicker.addEventListener('toggle', () => {
    if (scansPicker.open) {
      _scansLoaded = true;
      _loadRecentScans();
    }
  });
}

/* In-place resume on EVERY RR page (2026-06-17). The picker's row hrefs
 * are hardcoded to `/research-radar/digest?scan=...` (server-side
 * rendering doesn't know which page is mounting the picker). Without
 * intercept:
 *   - Click on Pipeline → browser navigates to Digest (wrong page!)
 *   - Click on Digest   → browser does a full page reload (wasteful)
 *
 * With intercept on both pages: URL updates via history.replaceState,
 * resumeScan() reloads the scan state in-place — on Pipeline that
 * repaints the graph + drawer + totals; on Digest that re-renders the
 * findings via renderDigest(). Either way the operator stays on the
 * page they're on.
 *
 * Registered BEFORE the trash handler so the trash button's
 * `e.stopPropagation()` still wins for delete clicks. */
const _IS_RR_PAGE = (
  window.location.pathname === '/research-radar'
  || window.location.pathname === '/research-radar/'
  || window.location.pathname === '/research-radar/digest'
  || window.location.pathname === '/research-radar/digest/'
);
if (scansList && _IS_RR_PAGE) {
  scansList.addEventListener('click', (e) => {
    // Skip if it's a delete click — trash handler below owns those.
    if (e.target?.closest?.('.rr-scans-row-trash')) return;
    const row = e.target?.closest?.('.rr-scans-row');
    if (!row) return;
    const wrap   = row.closest('.rr-scans-row-wrap');
    const scanId = wrap?.dataset?.scanId;
    if (!scanId) return;
    e.preventDefault();
    e.stopPropagation();
    // Update URL + load scan state into whichever surfaces this page
    // hosts (Pipeline: graph/drawer/totals; Digest: findings list).
    try {
      if (typeof setScanIdInUrl === 'function') setScanIdInUrl(scanId);
    } catch (_) { /* setScanIdInUrl is defined below in this file */ }
    try {
      if (typeof resumeScan === 'function') {
        Promise.resolve().then(() => resumeScan(scanId)).catch(err =>
          console.warn('resumeScan from picker threw', err));
      }
    } catch (_) { /* same */ }
    // Close the picker dropdown — the operator just made their choice.
    if (scansPicker && scansPicker.open) scansPicker.open = false;
  });
}

/* Per-row trash — click → DD's shared `showConfirm()` modal → DELETE →
 * refetch list. Same modal family DD's framework picker and YCS's library
 * trash use, so the chrome reads as one feature family across the app.
 * Button is a sibling of the row's `<a>` so the click never bubbles to
 * the link. */
if (scansList) {
  scansList.addEventListener('click', async (e) => {
    const trash = e.target?.closest?.('.rr-scans-row-trash');
    if (!trash) return;
    e.preventDefault();
    e.stopPropagation();
    const scanId = trash.dataset.scanId;
    const topic  = trash.dataset.scanTopic || '(no topic)';
    if (!scanId) return;
    const ok = await showConfirm(
      'Delete this scan?',
      `“${topic}” — the digest and findings will be removed permanently. ` +
      `Past scans of other topics and the accumulated Neo4j paper graph are not touched.`,
      'Delete',
    );
    if (!ok) return;
    trash.disabled = true;
    try {
      const r = await fetch(`/api/v1/rr/scan/${encodeURIComponent(scanId)}`, {
        method: 'DELETE',
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await _loadRecentScans();
    } catch (err) {
      trash.disabled = false;
      await showConfirm('Delete failed', err.message || String(err), 'OK');
    }
  });
}

/* Browse-all dialog — click any code to add + close. Codes already selected
 * are highlighted (red fill) when the dialog opens so the operator sees
 * what's in their list at a glance. */
const verticalBrowseBtn    = $('rr-vertical-browse-btn');
const verticalBrowseDialog = $('rr-vertical-browse-dialog');

function _markBrowseSelected() {
  if (!verticalBrowseDialog || !verticalsInput) return;
  const selected = new Set(_parseCodes(verticalsInput.value));
  verticalBrowseDialog.querySelectorAll('.rr-browse-code').forEach(btn => {
    btn.dataset.alreadySelected = selected.has(btn.dataset.verticalCode) ? 'true' : 'false';
  });
}

function _openBrowseDialog() {
  if (!verticalBrowseDialog) return;
  _markBrowseSelected();
  if (typeof verticalBrowseDialog.showModal === 'function') {
    verticalBrowseDialog.showModal();
  } else {
    verticalBrowseDialog.setAttribute('open', '');  // pre-2022 fallback
  }
}

function _closeBrowseDialog() {
  if (!verticalBrowseDialog) return;
  if (typeof verticalBrowseDialog.close === 'function') {
    verticalBrowseDialog.close();
  } else {
    verticalBrowseDialog.removeAttribute('open');
  }
}

if (verticalBrowseBtn) verticalBrowseBtn.addEventListener('click', _openBrowseDialog);
if (verticalBrowseDialog) {
  verticalBrowseDialog.addEventListener('click', (e) => {
    if (e.target.dataset?.rrCloseDialog === 'true') {
      _closeBrowseDialog();
      return;
    }
    const codeBtn = e.target.closest('.rr-browse-code');
    if (!codeBtn) return;
    // Toggle: re-clicking an already-selected code removes it.
    const code     = codeBtn.dataset.verticalCode || '';
    const existing = _findRowByCode(code);
    if (existing) {
      const cb = existing.querySelector('input.rr-multiselect-checkbox');
      if (cb) cb.checked = !cb.checked;
    } else {
      _addCustomRow(code);
    }
    _syncVerticals();
    _markBrowseSelected();
  });
  // Native backdrop-click → close.
  verticalBrowseDialog.addEventListener('click', (e) => {
    if (e.target === verticalBrowseDialog) _closeBrowseDialog();
  });
}

_syncVerticals();

/* ────────────────────────────────────────────────────────────────────────── *
 * Top N input — localStorage cache + min/max clamp.
 *
 * 2026-06-17: switched from range slider to number input. The redundant
 * #rr-top-n-value readout span is gone (the input IS its own readout);
 * localStorage persistence + HTML-attribute mirror stay so the typed
 * value survives page refresh / re-render. Clamp restored values to
 * the declared min/max so operator-edited localStorage can't bypass
 * the form's guards.
 * ────────────────────────────────────────────────────────────────────────── */
const topNInput     = form?.querySelector('#top_n');
const _TOP_N_LS_KEY = 'rr.top_n';

function _syncTopN() {
  if (!topNInput) return;
  try { topNInput.setAttribute('value', topNInput.value); } catch {}
  try { localStorage.setItem(_TOP_N_LS_KEY, topNInput.value); } catch {}
}

if (topNInput) {
  // Restore BEFORE wiring listeners so the initial state reflects the
  // persisted value. Clamp to declared min/max — operator-edited LS
  // shouldn't bypass server-side Pydantic ge/le guards.
  try {
    const saved = localStorage.getItem(_TOP_N_LS_KEY);
    if (saved !== null && saved !== '') {
      const min  = parseInt(topNInput.min  || '4',   10);
      const max  = parseInt(topNInput.max  || '100', 10);
      const n    = parseInt(saved, 10);
      if (Number.isFinite(n)) {
        topNInput.value = String(Math.min(Math.max(n, min), max));
      }
    }
  } catch {}
  topNInput.addEventListener('input',  _syncTopN);
  topNInput.addEventListener('change', _syncTopN);
  _syncTopN();
}

/* ────────────────────────────────────────────────────────────────────────── *
 * Topic input — localStorage cache + status-pill mirror (2026-06-17).
 *
 * Same pattern as the top_n slider: persist the operator's typed topic so
 * it survives page refresh / re-render. Additionally, mirror the live
 * topic into the status pill's #rr-status-topic span so the operator
 * always sees what they're scanning without scrolling up to the form.
 *
 * When a scan resumes from `?scan=<id>`, `resumeScan()` overrides the
 * pill topic from the server-side scan record (it's the source of truth
 * for that historical scan). When idle, the pill follows the form's
 * current topic. _setPillTopic centralises the write so both paths share
 * the same DOM contract.
 * ────────────────────────────────────────────────────────────────────────── */
const topicInput   = form?.querySelector('#topic');
const _TOPIC_LS_KEY = 'rr.topic';

function _setPillTopic(text) {
  // Updates BOTH topic surfaces — the Pipeline-page status-pill span and
  // the Digest-page title-row span. Each is null on the other page; the
  // null-guards mean callers don't need to know which page they're on.
  const t = (text || '').trim();
  for (const el of [statusTopic, digestTopic]) {
    if (!el) continue;
    el.textContent = t;
    // data-empty="true" lets CSS collapse the element entirely so the
    // layout doesn't reserve space for an empty bracket / dash.
    el.dataset.empty = t ? 'false' : 'true';
    el.title = t ? `Scan topic: ${t}` : 'Scan topic';
  }
}

function _syncTopicFromInput() {
  if (!topicInput) return;
  try { localStorage.setItem(_TOPIC_LS_KEY, topicInput.value); } catch {}
  try { topicInput.setAttribute('value', topicInput.value); } catch {}
  // Only update the pill from the input when there's no active scan_id —
  // a live/recent scan owns the pill topic and shouldn't get clobbered
  // by the operator typing a fresh topic.
  if (!activeScanId) _setPillTopic(topicInput.value);
}

if (topicInput) {
  try {
    const saved = localStorage.getItem(_TOPIC_LS_KEY);
    if (saved !== null && saved !== '') {
      topicInput.value = saved;
      topicInput.setAttribute('value', saved);
    }
  } catch {}
  topicInput.addEventListener('input',  _syncTopicFromInput);
  topicInput.addEventListener('change', _syncTopicFromInput);
  // Initial pill hydrate from the (possibly restored) input value.
  _syncTopicFromInput();
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"]/g, c => ({
    '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;',
  }[c]));
}

function setStatus(phase, message) {
  if (statusText) statusText.textContent = PHASE_LABELS[phase] || (phase ? phase : 'Idle');
  // Drive the DD-style stage pill's visual state via data-status. Empty
  // phase resets to idle. statusDot is the pill root element itself
  // (carries the `.rr-stage-pill` class).
  const pill = statusDot;
  if (pill) {
    const st = phase ? (PHASE_TO_STATUS[phase] || 'working') : 'idle';
    pill.dataset.status = st;
  }
  // Error / network / resume strings land in the detail slot; counts are
  // surfaced on the graph nodes, so we don't echo them in the pill.
  if (statusInfo) statusInfo.textContent = _pillDetail(phase, message);
  syncElapsedTimer(phase);
  setButtonsForPhase(phase);
  if (typeof window._rrSetPipelineState === 'function') {
    window._rrSetPipelineState(phase, message);
  }
  maybeAutoNavigateToDigest(phase);
}

// Only show the detail message when it carries information the graph
// can't (error reasons, queueing tokens, resume notes). Per-phase progress
// counts (`3/4 sources stashed`, `6/8 extractions written`) are already
// rendered as KPIs inside the active node — re-rendering them in the pill
// is the noise the redesign aims to remove.
function _pillDetail(phase, message) {
  if (!message) return '';
  if (phase === 'error' || phase === 'cancelled' || phase === 'cancelling') {
    return message;
  }
  // Drop pure "N/M …" updates — they show up on the graph.
  if (/^\s*\d+\s*\/\s*\d+/.test(message)) return '';
  return message;
}

// ───── Elapsed timer — ticks while the scan is in any working state ─────
//
// Persistence across refreshes:
//   _elapsedStartMs   = absolute epoch-ms of the scan's started_at, seeded
//                       from `GET /scan/{id}.started_at` in resumeScan().
//                       Falls back to Date.now() only when a brand-new scan
//                       is fired client-side (POST /scan response handler).
//   _elapsedFrozenMs  = absolute epoch-ms duration for terminal scans
//                       (finished_at - started_at). When non-null, the
//                       timer renders this value directly instead of
//                       ticking against Date.now(), so a refresh on a
//                       completed scan shows the FINAL wall time, not
//                       "time since I reloaded".
let _elapsedStartMs  = null;
let _elapsedFrozenMs = null;
let _elapsedTick     = null;
const statusElapsed  = document.getElementById('rr-status-elapsed');

function _fmtElapsed(ms) {
  const s = Math.floor(ms / 1000);
  if (s < 60)    return `${s}s`;
  const m = Math.floor(s / 60);
  const sr = s - m * 60;
  if (m < 60)    return sr ? `${m}m ${sr}s` : `${m}m`;
  const h = Math.floor(m / 60);
  const mr = m - h * 60;
  return mr ? `${h}h ${mr}m` : `${h}h`;
}

function _renderElapsed() {
  if (!statusElapsed) return;
  if (_elapsedFrozenMs != null) {
    statusElapsed.textContent = _fmtElapsed(_elapsedFrozenMs);
    return;
  }
  if (_elapsedStartMs == null) return;
  statusElapsed.textContent = _fmtElapsed(Date.now() - _elapsedStartMs);
}

// Hydrate the elapsed timer from a ScanResult shape (POST /scan response
// OR GET /scan/{id}). started_at + finished_at are ISO 8601 UTC strings.
function _seedElapsedFromScan(d) {
  if (!d) return;
  const t0 = d.started_at  ? Date.parse(d.started_at)  : NaN;
  const t1 = d.finished_at ? Date.parse(d.finished_at) : NaN;
  if (Number.isFinite(t0)) _elapsedStartMs = t0;
  if (Number.isFinite(t0) && Number.isFinite(t1)) {
    _elapsedFrozenMs = Math.max(0, t1 - t0);
  } else {
    _elapsedFrozenMs = null;
  }
}

function syncElapsedTimer(phase) {
  const isWorking = phase && PHASE_TO_STATUS[phase] &&
    (PHASE_TO_STATUS[phase] === 'working' || PHASE_TO_STATUS[phase] === 'cancelling');
  const isTerminal = phase && (phase === 'done' || phase === 'error' || phase === 'cancelled');
  if (isWorking) {
    // Live tick. Keep any pre-seeded _elapsedStartMs (recovered from the
    // server on resume); only fall back to Date.now() for a brand-new
    // scan whose POST response hasn't landed yet.
    if (_elapsedStartMs == null) _elapsedStartMs = Date.now();
    _elapsedFrozenMs = null;
    if (!_elapsedTick) _elapsedTick = setInterval(_renderElapsed, 1000);
    _renderElapsed();
  } else if (isTerminal) {
    // Freeze whatever the timer reads at this moment (live → frozen).
    // If a frozen value was seeded from the server it wins; otherwise
    // we snapshot `Date.now() - _elapsedStartMs` as the final.
    if (_elapsedFrozenMs == null && _elapsedStartMs != null) {
      _elapsedFrozenMs = Date.now() - _elapsedStartMs;
    }
    if (_elapsedTick) { clearInterval(_elapsedTick); _elapsedTick = null; }
    _renderElapsed();
  } else {
    if (_elapsedTick) { clearInterval(_elapsedTick); _elapsedTick = null; }
    _elapsedStartMs  = null;
    _elapsedFrozenMs = null;
    if (statusElapsed) statusElapsed.textContent = '';
  }
}

/* ────────────────────────────────────────────────────────────────────────── *
 * Auto-navigate to Digest the moment the scan finishes successfully.
 * Only triggers on the Pipeline page (presence of .rr-pipeline-graph),
 * keeps the active scan_id in the query string so the digest page hydrates.
 * ────────────────────────────────────────────────────────────────────────── */
let _autoNavigated = false;
function maybeAutoNavigateToDigest(phase) {
  if (_autoNavigated || phase !== 'done') return;
  if (!document.getElementById('rr-pipeline-graph')) return;  // already on digest
  if (!activeScanId) return;
  _autoNavigated = true;
  const url = `/research-radar/digest?scan=${encodeURIComponent(activeScanId)}`;
  // Small delay so the operator sees the final "Done" state for a beat
  // before the navigation.
  setTimeout(() => { window.location.href = url; }, 600);
}

// Both helpers are no-ops on the Pipeline page (digest DOM only exists on
// the Digest page after the row-2 stage split). Null-guarded so calls
// from startScan / resumeScan / SSE done branches don't blow up the
// scan trigger when the operator is staring at the Pipeline canvas.
function clearDigest() {
  if (digestArea)  digestArea.innerHTML = '';
  if (digestEmpty) digestEmpty.style.display = '';
}

function renderDigest(findings) {
  if (!digestArea || !digestEmpty) return;
  digestEmpty.style.display = 'none';
  digestArea.innerHTML = '';
  if (!findings || !findings.length) {
    digestEmpty.style.display = '';
    digestEmpty.textContent = 'Scan completed but no findings — the orchestrator returned an empty digest.';
    return;
  }
  for (const f of findings) {
    const card = document.createElement('div');
    card.className = 'rr-finding';
    const ex = f.extraction || {};
    card.innerHTML = `
      <div class="rr-finding-head">
        <span class="rr-rank">#${f.rank ?? '?'}</span>
        <span class="rr-signal">${Number(f.signal ?? 0).toFixed(3)}</span>
        ${f.is_new ? '<span class="rr-new">NEW</span>' : ''}
        <span class="rr-arxiv">${escapeHtml(f.arxiv_id || '')}</span>
      </div>
      <h4 class="rr-title">${escapeHtml(f.title || '(untitled)')}</h4>
      ${(f.authors || []).length ? `<p class="rr-authors">${escapeHtml((f.authors || []).join(', '))}</p>` : ''}
      ${f.summary ? `<p class="rr-summary">${escapeHtml(f.summary)}</p>` : ''}
      ${ex.problem || ex.method || ex.money_angle ? `
        <div class="rr-extraction">
          ${ex.problem      ? `<p><b>Problem:</b> ${escapeHtml(ex.problem)}</p>` : ''}
          ${ex.method       ? `<p><b>Method:</b> ${escapeHtml(ex.method)}</p>`   : ''}
          ${ex.how_to_build ? `<p><b>How to build:</b> ${escapeHtml(ex.how_to_build)}</p>` : ''}
          ${ex.money_angle  ? `<p><b>Money angle:</b> ${escapeHtml(ex.money_angle)}</p>` : ''}
          ${ex.math         ? `<p><b>Math:</b> <code>${escapeHtml(ex.math)}</code></p>` : ''}
        </div>` : ''}
      ${(f.themes || []).length ? `<div class="rr-themes">${(f.themes || []).map(t => `<span class="rr-theme">${escapeHtml(t)}</span>`).join('')}</div>` : ''}
      <div class="rr-sources">${(f.sources || []).map(s => `<span class="rr-source">${escapeHtml(s)}</span>`).join('')}</div>
    `;
    digestArea.appendChild(card);
  }
}

function teardown() {
  if (evtSrc)    { try { evtSrc.close(); } catch {} evtSrc = null; }
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

function startSSE(scanId) {
  evtSrc = new EventSource(`/api/v1/rr/scan/${scanId}/events`);
  evtSrc.onmessage = (e) => {
    let ev;
    try { ev = JSON.parse(e.data); } catch { return; }
    // Retry events (2026-06-16) augment the pipeline graph (back-edge +
    // per-node ↻N badge) without driving the lifecycle pill state — we
    // don't want a retry to flicker the pill back to "deep_read working"
    // mid-synthesis. Hand off to pipeline.js directly.
    if (ev.phase === 'retry' && ev.summary
        && typeof window._rrHandleRetry === 'function') {
      try { window._rrHandleRetry(ev.summary, ev.message); }
      catch (err) { console.warn('_rrHandleRetry threw', err); }
      return;
    }
    setStatus(ev.phase, ev.message || (ev.summary ? `summary: ${JSON.stringify(ev.summary)}` : ''));
    if (['done', 'error', 'cancelled'].includes(ev.phase)) {
      try { evtSrc.close(); } catch {}
      evtSrc = null;
      // SSE close triggers the polling-driven final render below
    }
  };
  evtSrc.onerror = () => {
    // Don't tear down on transient connection issues — the poll loop
    // will surface the terminal state.
  };
}

function startPoll(scanId) {
  pollTimer = setInterval(async () => {
    try {
      const r = await fetch(`/api/v1/rr/scan/${scanId}`);
      if (!r.ok) return;
      const d = await r.json();
      if (['done', 'error', 'cancelled'].includes(d.status)) {
        clearInterval(pollTimer); pollTimer = null;
        if (evtSrc) { try { evtSrc.close(); } catch {} evtSrc = null; }
        if (d.status === 'done') {
          setStatus('done', `${(d.findings || []).length} finding(s) · digest at ${d.digest_minio_key || 'MinIO'}`);
          renderDigest(d.findings || []);
        } else if (d.status === 'error') {
          setStatus('error', d.error || '(no error message)');
        } else {
          setStatus(d.status, '');
        }
      }
    } catch (e) {
      console.warn('rr poll failed', e);
    }
  }, 5000);
}

// --------------------------------------------------------------------------- //
// URL-state — encode active scan_id as ?scan=<uuid> so refresh / share works.
// --------------------------------------------------------------------------- //
function getScanIdFromUrl() {
  try {
    const sp = new URLSearchParams(window.location.search);
    const id = sp.get('scan');
    // Loose UUID-shape sanity check (avoid pinging the API with garbage).
    return id && /^[0-9a-f-]{32,}$/i.test(id) ? id : null;
  } catch {
    return null;
  }
}

function setScanIdInUrl(scanId) {
  const url = new URL(window.location.href);
  if (scanId) url.searchParams.set('scan', scanId);
  else        url.searchParams.delete('scan');
  // `replaceState` (not pushState): a fresh scan shouldn't bloat the back-stack.
  history.replaceState(null, '', url);
  // Stage tabs in row 2 (`Pipeline` / `Digest`) are server-rendered with
  // whatever scan_id was in the URL when the page loaded. Without this
  // rewrite, clicking Digest after starting a NEW scan would navigate to
  // the PREVIOUS scan's digest (stale href). Walk every `[data-substage]`
  // anchor and patch its `?scan=` param to match the live activeScanId.
  document.querySelectorAll('[data-substage]').forEach(link => {
    if (!link.href) return;
    try {
      const lu = new URL(link.href);
      if (scanId) lu.searchParams.set('scan', scanId);
      else        lu.searchParams.delete('scan');
      link.href = lu.toString();
    } catch { /* malformed href — skip */ }
  });
}

// --------------------------------------------------------------------------- //
// Resume — called on page load when ?scan=<id> is present. Fetches the
// current status and reattaches live updates OR renders the terminal state.
// --------------------------------------------------------------------------- //
async function resumeScan(scanId) {
  setStatus('pending', `restoring scan ${scanId}…`);
  let r;
  try {
    r = await fetch(`/api/v1/rr/scan/${scanId}`);
  } catch (err) {
    // Network blip — leave the form usable and drop the param so a refresh resets cleanly.
    setStatus('error', `network: ${err}`);
    setScanIdInUrl(null);
    return;
  }
  if (r.status === 404) {
    // Stale id (e.g. radar_scans was truncated). Forget it silently.
    setScanIdInUrl(null);
    setStatus('pending', 'fill the form and click Start Scan');
    return;
  }
  if (!r.ok) {
    setStatus('error', `GET /scan/${scanId} returned ${r.status}`);
    return;
  }
  const d = await r.json();
  activeScanId = scanId;

  // Recover the original scan start (and, for finished scans, the end)
  // from the server so the elapsed timer in the pill shows true wall
  // time — not "time since this tab loaded". Without this, every page
  // refresh resets the counter to 0s.
  _seedElapsedFromScan(d);

  // 2026-06-17: pill topic mirrors the resumed scan's topic, NOT whatever
  // the operator may have since typed into the form. Falls back to the
  // form value if the server response omits topic (defensive — the field
  // is present on every scan response since 2026-06-12).
  _setPillTopic(d.topic || topicInput?.value || '');

  if (['done', 'error', 'cancelled'].includes(d.status)) {
    // Terminal — render the snapshot, no live attachments.
    if (d.status === 'done') {
      setStatus('done', `${(d.findings || []).length} finding(s) · digest at ${d.digest_minio_key || 'MinIO'}`);
      renderDigest(d.findings || []);
    } else if (d.status === 'error') {
      setStatus('error', d.error || '(no error message)');
    } else {
      setStatus('cancelled', '');
    }
    return;
  }

  // Live — attach both SSE (replay catches up to current phase) and the poll fallback.
  setStatus(d.status, `resumed scan ${scanId}`);
  startSSE(scanId);
  startPoll(scanId);
}

/* ────────────────────────────────────────────────────────────────────────── *
 * Stop button — revokes the running scan. While the POST is in flight we
 * stay in `cancelling` (Start disabled, Stop disabled with spinner). The
 * server emits a final phase=cancelled SSE event which setStatus() then
 * resolves back to `idle`. As a belt-and-suspenders we also flip to idle
 * after a 12s timeout if the SSE event never arrives.
 * ────────────────────────────────────────────────────────────────────────── */
async function handleStop() {
  if (!activeScanId) { setButtonsState('idle'); return; }
  // `cancelling` is not in PHASES_TERMINAL nor PHASES_PRE_TERMINAL, so
  // setStatus' built-in button sync leaves whatever we set here untouched.
  setStatus('cancelling', 'revoking task…');
  setButtonsState('cancelling');
  let resp;
  try {
    resp = await fetch(`/api/v1/rr/scan/${activeScanId}/cancel`, { method: 'POST' });
  } catch (err) {
    setButtonsState('idle');
    setStatus('error', `cancel failed: ${err}`);
    return;
  }
  if (resp.status === 404) {
    // The task already finished — pull the latest snapshot to render.
    setButtonsState('idle');
    setStatus('done', 'scan had already finished');
    return;
  }
  if (!resp.ok) {
    setButtonsState('idle');
    setStatus('error', `cancel returned ${resp.status}`);
    return;
  }
  // Backstop timer in case the SSE phase=cancelled event never lands
  // (e.g. SSE dropped while waiting for the cancel to ripple through).
  setTimeout(() => {
    if (stopBtn?.dataset.busy === 'true') {
      setButtonsState('idle');
      setStatus('cancelled', '');
    }
  }, 12000);
}

if (stopBtn) stopBtn.addEventListener('click', handleStop);

/* Scan trigger — wired to BOTH the Start button click and the form submit
 * event so it fires regardless of which path the browser dispatches first
 * (Enter in an input → submit only; pointer click on the button → click
 * + submit). The `_inflight` guard prevents a double-fire when both
 * events bubble through. Form-element lookups go via `form.elements.…`
 * for the most defensive shape — `form.verticals` worked but failed
 * silently when a sibling control had a colliding name; .elements is
 * the spec-blessed accessor. */
let _scanInflight = false;

async function startScan() {
  if (_scanInflight) return;
  _scanInflight = true;
  try {
    if (!form) {
      console.error('[rr-main] startScan aborted: form #rr-scan-form not found');
      return;
    }
    teardown();
    clearDigest();
    setStatus('pending', 'submitting...');
    setButtonsState('running');

    const verticalsRaw = form.elements?.verticals?.value || '';
    const topicRaw     = form.elements?.topic?.value     || '';
    const topNRaw      = form.elements?.top_n?.value     || '12';

    const verticals = verticalsRaw
      .split(',')
      .map(s => s.trim())
      .filter(Boolean);

    const body = {
      profile_id: 'default',
      topic:      topicRaw,
      verticals,
      top_n:      parseInt(topNRaw, 10),
    };

    let resp;
    try {
      resp = await fetch('/api/v1/rr/scan', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(body),
      });
    } catch (err) {
      console.error('[rr-main] POST /scan threw', err);
      setStatus('error', `network: ${err}`);
      return;
    }
    if (!resp.ok) {
      const txt = await resp.text().catch(() => '');
      console.error('[rr-main] POST /scan returned', resp.status, txt);
      setStatus('error', `POST /scan returned ${resp.status}`);
      return;
    }
    const data = await resp.json();
    activeScanId = data.scan_id;
    setScanIdInUrl(activeScanId);
    // Seed the elapsed timer from the server-emitted started_at so the
    // pill reads true wall-clock time (the 200-300ms POST latency would
    // otherwise show up as drift on every fresh scan).
    _seedElapsedFromScan(data);
    // 2026-06-17: lock the pill topic to what the operator submitted —
    // covers the case where they type a new topic while the scan runs.
    _setPillTopic(topicRaw);
    setStatus('pending', `scan ${activeScanId} queued (task ${data.task_id})`);
    startSSE(activeScanId);
    startPoll(activeScanId);
  } finally {
    _scanInflight = false;
  }
}

if (startBtn) {
  startBtn.addEventListener('click', (e) => {
    e.preventDefault();   // type="submit" inside a form — block default
    // Surface immediate proof-of-life to the pill detail BEFORE anything
    // async runs. If startScan() throws before its own setStatus call,
    // this still tells the operator the click was received (browser
    // DevTools may not be available on mobile).
    if (statusInfo) statusInfo.textContent = '[click heard]';
    Promise.resolve().then(() => startScan()).catch(err => {
      const msg = `startScan threw: ${err && err.message || err}`;
      console.error('[rr-main]', msg, err);
      setStatus('error', msg);
    });
  });
}
if (form) {
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    Promise.resolve().then(() => startScan()).catch(err => {
      setStatus('error', `submit threw: ${err && err.message || err}`);
    });
  });
}

// Visible boot marker — proves main.js executed end-to-end. Lands in the
// pill detail so a mobile operator (no DevTools) can confirm load. Overwritten
// by the first real setStatus() call.
if (statusInfo && !statusInfo.textContent) {
  statusInfo.textContent = `(ready ${new Date().toISOString().slice(11,19)})`;
}


// --------------------------------------------------------------------------- //
// On page load:
//   - If `?scan=<id>` is in the URL → resume it (covers refresh / share /
//     deep-link).
//   - Else, on the DIGEST page specifically → load the MOST RECENT scan
//     for the current profile so the user lands on something useful
//     instead of the empty "fill the form…" hint. If there are no scans
//     yet, the empty-state copy stays visible.
//   - Else (Pipeline page, no URL scan_id) → idle, fill-the-form state.
// --------------------------------------------------------------------------- //
async function _bootDigestLatest() {
  if (!window.location.pathname.endsWith('/digest')) return;
  try {
    const r = await fetch(
      '/api/v1/rr/scans/recent?profile_id=default&limit=1',
    );
    if (!r.ok) return;
    const data   = await r.json();
    const latest = (data?.items || [])[0];
    if (!latest?.scan_id) return;
    setScanIdInUrl(latest.scan_id);
    await resumeScan(latest.scan_id);
  } catch { /* non-fatal — leave empty state alone */ }
}

{
  const urlScan = getScanIdFromUrl();
  if (urlScan) {
    resumeScan(urlScan);
  } else {
    _bootDigestLatest();  // fire-and-forget; only acts on /digest
  }
  // Pipeline graph paints itself on mount (pipeline.js); no work needed here.
}
