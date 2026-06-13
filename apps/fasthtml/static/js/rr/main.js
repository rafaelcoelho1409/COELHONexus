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
const $ = (id) => document.getElementById(id);

const form        = $('rr-scan-form');
const statusText  = $('rr-status-text');
const statusDot   = document.querySelector('#rr-status .rr-status-dot');
const statusInfo  = $('rr-status-detail');
const digestArea  = $('rr-digest-items');
const digestEmpty = $('rr-digest-empty');

const PHASE_LABELS = {
  pending:     'Pending',
  running:     'Running — orchestrator starting',
  discovery:   'Discovery — fetching from arxiv · s2 · hf · hn',
  triage:      'Triage — scoring + cross-source dedup',
  deep_read:   'Deep read — extracting fields per paper',
  graph_build: 'Graph build — Neo4j + Qdrant',
  synthesis:   'Synthesis — finding themes',
  report:      'Report — assembling digest',
  persisting:  'Persisting findings + digest.json',
  done:        'Done',
  error:       'Error',
  cancelled:   'Cancelled',
};

let activeScanId = null;
let evtSrc       = null;
let pollTimer    = null;

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"]/g, c => ({
    '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;',
  }[c]));
}

function setStatus(phase, message) {
  statusText.textContent = PHASE_LABELS[phase] || phase || 'Idle';
  statusDot.dataset.phase = phase || '';
  if (message) statusInfo.textContent = message;
  else statusInfo.textContent = '';
}

function clearDigest() {
  digestArea.innerHTML = '';
  digestEmpty.style.display = '';
}

function renderDigest(findings) {
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

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  teardown();
  clearDigest();
  setStatus('pending', 'submitting...');

  const verticals = (form.verticals.value || '')
    .split(',')
    .map(s => s.trim())
    .filter(Boolean);

  const body = {
    profile_id: 'default',
    topic:      form.topic.value || '',
    verticals,
    top_n:      parseInt(form.top_n.value || '12', 10),
  };

  let resp;
  try {
    resp = await fetch('/api/v1/rr/scan', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(body),
    });
  } catch (err) {
    setStatus('error', `network: ${err}`);
    return;
  }
  if (!resp.ok) {
    setStatus('error', `POST /scan returned ${resp.status}`);
    return;
  }
  const data = await resp.json();
  activeScanId = data.scan_id;
  setScanIdInUrl(activeScanId);
  setStatus('pending', `scan ${activeScanId} queued (task ${data.task_id})`);
  startSSE(activeScanId);
  startPoll(activeScanId);
});


// --------------------------------------------------------------------------- //
// On page load — if ?scan=<id> is in the URL, resume it. Otherwise, idle.
// --------------------------------------------------------------------------- //
{
  const urlScan = getScanIdFromUrl();
  if (urlScan) resumeScan(urlScan);
}
