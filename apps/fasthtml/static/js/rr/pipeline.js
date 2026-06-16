// rr/pipeline.js — RR Pipeline page canvas.
//
// Adopts the shared `StageGraph` helper Planner + Synth use (Cytoscape
// + Dagre, lazy-loaded). Topology comes from a `data-topology` JSON blob
// the server-rendered canvas div carries. Phase → node-status mapping
// lives in `_PHASE_PLAN` below — the single source of truth that
// `_rrSetPipelineState(phase, message)` consults to drive node statuses
// + per-node KPIs on every SSE event.
//
// Exposed on `window` (not ESM-exported) so `main.js` can call it
// without an import cycle — main.js is loaded as a module from the
// same page after this file, so the global is already wired.
import { ensureCytoscape } from '/static/js/dd/shared/cytoscape_loader.js';
import { StageGraph }      from '/static/js/dd/shared/stagegraph.js';


// ---------------------------------------------------------------------------
// Phase → topology state mapping. Each phase says which nodes are
// `running` and (implicitly via the sequence below) which are `done`
// (everything before) vs `future` (everything after). Keeping this as a
// flat list of step descriptors lets the state walker derive done/future
// trivially without scattering ordering across multiple constants.
//
// _kpiNode names the node that should host the parsed KPI counter for
// the phase (when the SSE message carries one). Discovery's count is a
// cross-source aggregate, so the aggregate goes on the orchestrator;
// deep_read's count maps to the deep_read node directly.
// ---------------------------------------------------------------------------
const _PHASE_PLAN = [
  { phase: 'running',     active: ['orchestrator'],
    _kpiNode: null },
  { phase: 'discovery',   active: [
      'discovery_arxiv', 'discovery_semantic_scholar',
      'discovery_huggingface_daily_papers', 'discovery_hn',
    ],
    _kpiNode: 'orchestrator' },
  { phase: 'triage',      active: ['triage'],
    _kpiNode: 'triage' },
  { phase: 'deep_read',   active: ['deep_read'],
    _kpiNode: 'deep_read' },
  { phase: 'graph_build', active: ['graph_build'],
    _kpiNode: 'graph_build' },
  { phase: 'synthesis',   active: ['synthesis'],
    _kpiNode: 'synthesis' },
  { phase: 'report',      active: ['report'],
    _kpiNode: 'report' },
  { phase: 'persisting',  active: ['persist'],
    _kpiNode: 'persist' },
];

const _PHASES_TERMINAL = new Set(['done', 'error', 'cancelled']);


// ---------------------------------------------------------------------------
// Topology read + label shaping. Each StageGraph node wants
// {id, label, status}; the optional second-line "sub" is folded into the
// label as `"name\nkind · sub"` so we don't have to patch StageGraph's
// stylesheet (which only knows about status). The `kind` prefix doubles
// as the visual chip from the original hand-rolled markup.
// ---------------------------------------------------------------------------
function _readTopology(containerEl) {
  const raw = containerEl?.dataset?.topology || '';
  if (!raw) return { nodes: [], edges: [] };
  try { return JSON.parse(raw); }
  catch (e) {
    console.warn('[rr-pipeline] bad data-topology JSON', e);
    return { nodes: [], edges: [] };
  }
}

function _toStageGraphNodes(rawNodes) {
  return rawNodes.map(n => ({
    id:     n.key,
    // Shape will encode the kind visually (see _applyKindShapes below);
    // keep the kind word in the second line as a redundant text channel
    // for accessibility + zoomed-out reads where shapes blur.
    label:  `${n.label}\n${n.kind} · ${n.sub}`,
    status: 'pending',
    kpi:    '',
    kind:   n.kind,
  }));
}

function _toStageGraphEdges(rawEdges) {
  return rawEdges.map(([source, target]) => ({ source, target }));
}


// ---------------------------------------------------------------------------
// KPI extraction from the SSE message field. Discovery + deep_read emit
// "X/Y …" counters; the other phases either don't, or the counts aren't
// meaningful at the per-node level (graph_build, synthesis, report).
// ---------------------------------------------------------------------------
const _COUNT_RE = /(\d+)\s*\/\s*(\d+)/;

function _extractCount(message) {
  if (!message) return null;
  const m = message.match(_COUNT_RE);
  if (!m) return null;
  return { done: Number(m[1]), total: Number(m[2]) };
}

function _kpiTextForPhase(phase, message) {
  const c = _extractCount(message);
  if (!c) return '';
  if (phase === 'discovery') return `${c.done}/${c.total} sources`;
  if (phase === 'deep_read') return `${c.done}/${c.total} extractions`;
  return `${c.done}/${c.total}`;
}


// ---------------------------------------------------------------------------
// State machine: paint every node based on the current phase. Sequencing
// is derived from `_PHASE_PLAN`'s order so done/future fall out naturally
// (nodes belonging to phases BEFORE the current phase index → done; AT
// the current index → running; AFTER → future).
// ---------------------------------------------------------------------------
function _applyPhase(graph, phase, message) {
  if (!graph) return;
  // Terminal phases collapse to a uniform paint pass.
  if (phase === 'done') {
    graph.cy.nodes().forEach(n => graph.setStatus(n.id(), 'done', ''));
    graph.cy.edges().forEach(e => e.data('active', 'false'));
    return;
  }
  if (phase === 'error') {
    graph.cy.nodes("[status = 'running']").forEach(n =>
      graph.setStatus(n.id(), 'failed', ''));
    return;
  }
  if (phase === 'cancelled' || phase === 'cancelling') {
    graph.cy.nodes("[status = 'running']").forEach(n =>
      graph.setStatus(n.id(), 'pending', ''));
    return;
  }
  // `pending` fires when a NEW scan is enqueued (POST /scan response or
  // resumeScan's "restoring scan…" preamble). Reset every node back to
  // the neutral pending state so the previous run's green ✓s + KPIs
  // don't bleed into the fresh scan. Edges clear too.
  if (phase === 'pending') {
    graph.cy.nodes().forEach(n => graph.setStatus(n.id(), 'pending', ''));
    graph.cy.edges().forEach(e => e.data('active', 'false'));
    return;
  }

  const idx = _PHASE_PLAN.findIndex(p => p.phase === phase);
  if (idx < 0) return;  // unknown phase; leave the canvas alone

  // Per-step membership walk:
  //   nodes BEFORE the current phase   → 'done'    (green, ✓)
  //   nodes AT      the current phase  → 'running' (sky-blue, marching ants)
  //   nodes AFTER   the current phase  → 'pending' (white, clean)
  //
  // We deliberately don't use 'future' here — StageGraph reserves that for
  // "this node isn't implemented in the current plan" (DD Planner/Synth use
  // it for stubbed nodes that haven't shipped yet) and renders it at 0.55
  // opacity with gray fill. Every RR node IS implemented, so the right
  // queued-but-not-yet-started visual is 'pending'.
  const seenActive = new Set();
  for (let i = 0; i < _PHASE_PLAN.length; i++) {
    const step   = _PHASE_PLAN[i];
    const status = i < idx ? 'done' : i === idx ? 'running' : 'pending';
    for (const id of step.active) {
      graph.setStatus(id, status);
      if (status === 'running') seenActive.add(id);
    }
  }

  // KPI lands on the designated node for this phase, if the message
  // carries a count. Other nodes' KPIs are cleared so stale counts from
  // previous phases don't linger.
  const kpiNode = _PHASE_PLAN[idx]._kpiNode;
  const kpiText = _kpiTextForPhase(phase, message);
  graph.cy.nodes().forEach(n => {
    if (kpiNode && n.id() === kpiNode) {
      n.data('kpi', kpiText);
    } else if (!seenActive.has(n.id())) {
      n.data('kpi', '');
    }
  });
}


// ---------------------------------------------------------------------------
// Kind → Cytoscape shape. Layered over StageGraph's default style (which
// hard-codes round-rectangle for every node) by appending kind-selectors
// AFTER create — Cytoscape resolves later style rules with higher specificity
// so the shape sticks through status transitions. We deliberately don't
// override color/border — those stay status-driven so Planner/Synth's visual
// vocabulary (running=sky-blue, done=green, failed=red) carries straight
// through. The `kind` data field is set per-node in _toStageGraphNodes().
//
// Width is bumped per shape so the inscribed text fits — hexagon and barrel
// lose more interior area than round-rectangle.
// ---------------------------------------------------------------------------
function _applyKindShapes(cy) {
  // Identity vs state encoding:
  //
  //   shape                         → kind (always — agent/subagent/tool/store)
  //   border-color (when working)   → kind tint (purple/blue/green/orange)
  //   border-color (when pending)   → light gray (neutral; matches Planner/Synth)
  //   background fill               → status (white pending → sky-blue running
  //                                   → green done → red failed)
  //
  // The dual-state border (kind tint when active, gray when pending) keeps
  // the kind identity visible during a run AND avoids the "every node looks
  // colored even at rest" problem the always-tinted border had. Status bg
  // is left to StageGraph's stylesheet — we don't override it.
  cy.style()
    .selector("node[kind = 'agent']")
    .style({
      'shape':            'hexagon',
      'width':            240,
      'height':           74,
      // Bolder palette (2026-06-15) — Tailwind-style 600/700 saturations
      // so the kind tint reads from across the canvas.
      'border-color':     '#6d28d9',   // violet-700
      'border-width':     3,
    })
    .selector("node[kind = 'subagent']")
    .style({
      'shape':            'ellipse',
      'width':            220,
      'height':           66,
      'border-color':     '#1d4ed8',   // blue-700
      'border-width':     3,
    })
    .selector("node[kind = 'tool']")
    .style({
      'shape':            'rectangle',
      'width':            210,
      'height':           60,
      'border-color':     '#15803d',   // green-700
      'border-width':     3,
    })
    .selector("node[kind = 'store']")
    .style({
      'shape':            'barrel',
      'width':            220,
      'height':           62,
      'border-color':     '#b45309',   // amber-700
      'border-width':     3,
    })
    // Pending takes the neutral palette so a fresh page (or post-Start
    // reset) reads as "queued and quiet", not "actively colored". Same
    // visual contract as Planner/Synth's pending nodes.
    .selector("node[status = 'pending']")
    .style({
      'background-color': '#ffffff',
      'border-color':     '#cccccc',
      'border-width':     1.5,
      'color':            '#666666',
    })
    // Bump status fill colors from StageGraph's 100-shades to 200-shades
    // (2026-06-15) so the fill reads alongside the bold 700-shade kind
    // borders without washing out. Hierarchy: border (700) > fill (200)
    // > white. Same hues as the 700-shade family so the visual identity
    // of running/done/failed stays consistent.
    .selector("node[status = 'running']")
    .style({
      'background-color': '#bae6fd',   // sky-200 (was sky-100 #e0f2fe)
      'color':            '#0c4a6e',
    })
    .selector("node[status = 'done']")
    .style({
      'background-color': '#bbf7d0',   // green-200 (was green-100 #e5f4e9)
      'color':            '#14532d',
    })
    .selector("node[status = 'failed']")
    .style({
      'background-color': '#fecaca',   // red-200 (was red-100 #fde7e9)
      'color':            '#7f1d1d',
      'border-width':     4,
    })
    .update();
}


// ---------------------------------------------------------------------------
// Per-node drawer — populates `#rr-drawer` with the inline node-details
// payload server-rendered in pipeline.py. One JSON parse on first open;
// drawer DOM is reused across clicks.
// ---------------------------------------------------------------------------
let _nodeDetailsCache = null;
function _readNodeDetails() {
  if (_nodeDetailsCache) return _nodeDetailsCache;
  const el = document.getElementById('rr-node-details');
  if (!el) return {};
  try { _nodeDetailsCache = JSON.parse(el.textContent || '{}'); }
  catch { _nodeDetailsCache = {}; }
  return _nodeDetailsCache;
}

function _esc(s) {
  return String(s ?? '').replace(/[&<>]/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
}

export function openNodeDrawer(nodeId) {
  const drawer = document.getElementById('rr-drawer');
  if (!drawer) return;
  const details = _readNodeDetails();
  const d = details[nodeId];
  if (!d) {
    console.warn('[rr-pipeline] no drawer entry for', nodeId);
    return;
  }
  const titleEl = document.getElementById('rr-drawer-title');
  const subEl   = document.getElementById('rr-drawer-subtitle');
  const srcEl   = document.getElementById('rr-drawer-source');
  const bodyEl  = document.getElementById('rr-drawer-body');
  if (titleEl) titleEl.textContent = d.title || nodeId;
  if (subEl)   subEl.textContent   = d.subtitle || '';
  if (srcEl)   srcEl.textContent   = d.source ? `📄 ${d.source}` : '';
  if (bodyEl) {
    const sections = (d.body || []).map(([heading, content]) => {
      const isProse = heading === 'Role' || heading === 'Output' ||
                      heading === 'Tool' || heading === 'Middleware' ||
                      heading === 'Response format' || heading === 'Stores written' ||
                      heading === 'Signal weights' || heading === 'Concurrency';
      const bodyCls = 'rr-drawer-section-body' +
                      (isProse ? ' rr-drawer-section-body--prose' : '');
      return (
        `<div class="rr-drawer-section">` +
        `<h4 class="rr-drawer-section-title">${_esc(heading)}</h4>` +
        `<div class="${bodyCls}">${_esc(content)}</div>` +
        `</div>`
      );
    });
    // Prepend a live-state placeholder section (filled by _fetchLiveState
    // below when this node has a live_fs_path AND a scan is in scope).
    if (d.live_fs_path) {
      sections.unshift(
        `<div class="rr-drawer-section" data-live-section="true">` +
        `<h4 class="rr-drawer-section-title">Last scan output</h4>` +
        `<div class="rr-drawer-section-body" id="rr-drawer-live-body">` +
        `Loading…</div></div>`
      );
    }
    // Prepend an LLM-activity placeholder section (Path A 2026-06-16).
    // Filled by _fetchLlmCounters below when the node has an llm_phase
    // assignment AND a scan_id is in scope. Goes at the very top of the
    // body so the operator sees rotator call/token KPIs immediately.
    if (d.llm_phase) {
      sections.unshift(
        `<div class="rr-drawer-section" data-llm-section="true">` +
        `<h4 class="rr-drawer-section-title">LLM activity (this scan)</h4>` +
        `<div class="rr-drawer-section-body" id="rr-drawer-llm-body">` +
        `Loading…</div></div>`
      );
    }
    bodyEl.innerHTML = sections.join('');
  }
  drawer.hidden = false;
  if (d.live_fs_path) _fetchLiveState(d.live_fs_path);
  if (d.llm_phase)    _fetchLlmCounters(d.llm_phase);
}

function _getActiveScanId() {
  try {
    const sp = new URLSearchParams(window.location.search);
    const id = sp.get('scan');
    return id && /^[0-9a-f-]{32,}$/i.test(id) ? id : null;
  } catch { return null; }
}

function _summarizeLiveValue(value) {
  // Quick at-a-glance preview — array counts, object key/value sample,
  // string snippet — followed by the raw JSON for the curious.
  if (Array.isArray(value)) {
    return `${value.length} entries\n\n` +
           JSON.stringify(value.slice(0, 3), null, 2) +
           (value.length > 3 ? `\n\n… (${value.length - 3} more elided)` : '');
  }
  if (value && typeof value === 'object') {
    return JSON.stringify(value, null, 2);
  }
  return String(value);
}

async function _fetchLiveState(path) {
  const scanId = _getActiveScanId();
  const target = document.getElementById('rr-drawer-live-body');
  if (!target) return;
  if (!scanId) {
    target.textContent = '(no scan in URL — open a past scan via ?scan=<id> or run a new one)';
    return;
  }
  try {
    const r = await fetch(`/api/v1/rr/scan/${scanId}/fs/${path}`);
    if (r.status === 404) {
      target.textContent = '(no data yet — this node hasn\'t produced output for this scan)';
      return;
    }
    if (!r.ok) {
      target.textContent = `Fetch failed: HTTP ${r.status}`;
      return;
    }
    const data = await r.json();
    target.textContent = _summarizeLiveValue(data.value);
  } catch (err) {
    target.textContent = `Fetch failed: ${err.message || err}`;
  }
}

// ---------------------------------------------------------------------------
// LLM counters — Path A (2026-06-16). One fetch per drawer-open; renders
// total calls + tokens + per-model breakdown for the node's phase bucket.
// ---------------------------------------------------------------------------
function _fmtNumber(n) {
  // Compact thousands separators: 1234567 → "1,234,567".
  if (n == null) return '—';
  try { return Number(n).toLocaleString('en-US'); }
  catch { return String(n); }
}

function _renderLlmCounters(phase, payload) {
  // Payload shape comes from GET /scan/{id}/llm-counters; see backend doc.
  // Returns an HTML string suitable for the rr-drawer-llm-body container.
  if (!payload || !payload.by_phase) {
    return '(no LLM activity recorded for this phase yet)';
  }
  const ph = payload.by_phase[phase];
  if (!ph || !ph.calls) {
    return '(no LLM activity recorded for this phase yet)';
  }
  const total = payload.total || {};
  const totalShare = total.calls
    ? Math.round((ph.calls / total.calls) * 100) + '%'
    : '—';
  const lines = [
    `<div class="rr-llm-counters">` +
      `<div class="rr-llm-row"><span class="rr-llm-k">calls</span>` +
      `<span class="rr-llm-v">${_fmtNumber(ph.calls)} ` +
      `<span class="rr-llm-share">(${totalShare} of scan)</span></span></div>` +
      `<div class="rr-llm-row"><span class="rr-llm-k">tokens in</span>` +
      `<span class="rr-llm-v">${_fmtNumber(ph.tokens_in)}</span></div>` +
      `<div class="rr-llm-row"><span class="rr-llm-k">tokens out</span>` +
      `<span class="rr-llm-v">${_fmtNumber(ph.tokens_out)}</span></div>` +
    `</div>`,
  ];
  // Per-model breakdown for this phase. Group rows by total calls desc.
  const byModel = ph.by_model || {};
  const modelRows = Object.entries(byModel)
    .map(([model, stats]) => {
      const { provider, name } = _splitProviderModel(model);
      return {
        raw:        model,
        provider,
        name,
        calls:      stats.calls      || 0,
        tokens_in:  stats.tokens_in  || 0,
        tokens_out: stats.tokens_out || 0,
      };
    })
    .sort((a, b) => b.calls - a.calls);
  if (modelRows.length) {
    lines.push(
      `<div class="rr-llm-models-title">Per model</div>` +
      `<table class="rr-llm-models">` +
        `<thead><tr><th>provider</th><th>model</th><th>calls</th><th>in</th><th>out</th></tr></thead>` +
        `<tbody>` +
          modelRows.map(r =>
            `<tr>` +
            `<td title="${_esc(r.raw)}">${_esc(r.provider)}</td>` +
            `<td title="${_esc(r.raw)}">${_esc(r.name)}</td>` +
            `<td>${_fmtNumber(r.calls)}</td>` +
            `<td>${_fmtNumber(r.tokens_in)}</td>` +
            `<td>${_fmtNumber(r.tokens_out)}</td></tr>`
          ).join('') +
        `</tbody>` +
      `</table>`
    );
  }
  // Scan-wide footer so the operator can compare phase-share vs total.
  lines.push(
    `<div class="rr-llm-footer">Scan total: ` +
    `${_fmtNumber(total.calls)} calls · ` +
    `${_fmtNumber(total.tokens_in)} in / ${_fmtNumber(total.tokens_out)} out` +
    `</div>`
  );
  return lines.join('');
}

function _splitProviderModel(model) {
  // Split a LiteLLM deployment id into (provider, name):
  //   nvidia_nim/openai/gpt-oss-120b  → nvidia_nim · openai/gpt-oss-120b
  //   mistral/mistral-large-latest    → mistral    · mistral-large-latest
  //   groq/llama-3.3-70b-versatile    → groq       · llama-3.3-70b-versatile
  //   gemini/gemini-2.5-flash         → gemini     · gemini-2.5-flash
  //   rr-strong (fallback group)      → (rotator)  · rr-strong
  //   ""                              → (unknown)  · (unknown)
  if (!model) return { provider: '(unknown)', name: '(unknown)' };
  const s = String(model);
  const idx = s.indexOf('/');
  if (idx < 0) {
    // No prefix → this is the rotator group alias (`rr-strong` etc.),
    // not a real deployment. Mark it so the user knows it's a fallback.
    return { provider: '(rotator)', name: s };
  }
  const provider = s.slice(0, idx);
  const name     = s.slice(idx + 1);
  return { provider, name };
}

async function _fetchLlmCounters(phase) {
  const scanId = _getActiveScanId();
  const target = document.getElementById('rr-drawer-llm-body');
  if (!target) return;
  if (!scanId) {
    target.textContent = '(no scan in URL — open a past scan via ?scan=<id> or run a new one)';
    return;
  }
  try {
    const r = await fetch(`/api/v1/rr/scan/${scanId}/llm-counters`);
    if (!r.ok) {
      target.textContent = `Fetch failed: HTTP ${r.status}`;
      return;
    }
    const data = await r.json();
    target.innerHTML = _renderLlmCounters(phase, data);
  } catch (err) {
    target.textContent = `Fetch failed: ${err.message || err}`;
  }
}

function _closeDrawer() {
  const drawer = document.getElementById('rr-drawer');
  if (drawer) drawer.hidden = true;
}

document.getElementById('rr-drawer-close-btn')?.addEventListener('click', _closeDrawer);
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') _closeDrawer();
});


// ---------------------------------------------------------------------------
// Init — wire ensureCytoscape → StageGraph.create → window hook.
//
// Window hook (`_rrSetPipelineState`) is the contract main.js relies on.
// We define a stub immediately so calls during Cytoscape's async load
// don't blow up; once the graph is ready, we replace the stub with the
// real updater and flush the most-recent buffered (phase, message).
// ---------------------------------------------------------------------------
let _lastPhase   = null;
let _lastMessage = null;

window._rrSetPipelineState = function _rrStub(phase, message) {
  _lastPhase   = phase;
  _lastMessage = message;
};

async function initPipelineGraph() {
  const canvas = document.getElementById('rr-pipeline-canvas');
  if (!canvas) return;  // Digest page won't have this; harmless no-op.

  try { await ensureCytoscape(); }
  catch (e) {
    console.warn('[rr-pipeline] Cytoscape load failed', e);
    canvas.innerHTML =
      '<div class="rr-pipeline-empty">Cytoscape failed to load. ' +
      'The pipeline graph is unavailable until network is restored.</div>';
    return;
  }

  const topo = _readTopology(canvas);
  const graph = StageGraph.create(canvas, {
    nodes: _toStageGraphNodes(topo.nodes),
    edges: _toStageGraphEdges(topo.edges),
    onNodeClick: openNodeDrawer,
  });
  if (!graph) return;

  // Re-attach the `kind` data field on each cytoscape node — StageGraph's
  // create() filters its inputs down to {id, label, status, kpi} so our
  // raw `kind` from _toStageGraphNodes never reaches the cy elements. The
  // per-kind shape selectors below depend on `data(kind)` being present.
  topo.nodes.forEach(n => {
    const el = graph.cy.getElementById(n.key);
    if (el && el.length) el.data('kind', n.kind);
  });
  _applyKindShapes(graph.cy);

  // 2026-06-16: switched to horizontal `LR` per UX request. The pipeline
  // is a linear flow (orchestrator → discovery → triage → deep_read →
  // graph_build → synthesis → persist), so a left-to-right Sugiyama
  // layout reads more naturally than vertical at typical canvas widths.
  // Matches Planner / Synth graphs (StageGraph LR default). Larger
  // rankSep so the 6-rank flow fits the canvas comfortably.
  if (typeof cytoscape !== 'undefined' && cytoscape._dagreRegistered) {
    graph.cy.layout({
      name:    'dagre',
      rankDir: 'LR',
      nodeSep: 30,
      rankSep: 64,
      padding: 24,
      animate: false,
      fit:     true,
    }).run();
  }

  // Replace the stub with the real updater.
  window._rrSetPipelineState = function _rrApply(phase, message) {
    _lastPhase   = phase;
    _lastMessage = message;
    _applyPhase(graph, phase, message);
    // Refresh scan-wide LLM totals strip on every phase event. Cheap
    // (one Redis HGETALL × a few keys) and tracks the scan in flight.
    _refreshScanTotals();
  };

  // Flush any pre-init state that landed during the cytoscape load.
  if (_lastPhase) _applyPhase(graph, _lastPhase, _lastMessage);
  // Initial hydrate — if a scan_id is in the URL the totals strip
  // shows historical counters even before any SSE event fires.
  _refreshScanTotals();
}


// ---------------------------------------------------------------------------
// Scan-wide totals strip — fetches /llm-counters and fills the DOM under the
// graph. Called on page load (if ?scan=<id>) + every SSE phase event + on a
// short polling interval while the scan is in flight (so the numbers tick
// within a phase, not just at phase transitions).
// ---------------------------------------------------------------------------
let _totalsInFlight = false;
let _totalsPollTimer = null;

// Poll cadence — short enough to feel live, long enough to be invisible
// in the Redis logs. 2.5s is the same cadence Planner/Synth use for their
// state polls.
const _TOTALS_POLL_MS = 2500;

// Phases that mean "scan stopped — don't keep polling". Any other phase
// (including 'pending' before a scan starts) keeps the timer alive so
// the strip stays live across the full run.
const _TERMINAL_PHASES = new Set(['done', 'error', 'cancelled', 'failed']);

async function _refreshScanTotals() {
  const stripEl = document.getElementById('rr-totals');
  if (!stripEl) return;
  const scanId = _getActiveScanId();
  if (!scanId) {
    _renderScanTotals(null);
    return;
  }
  // Dedupe overlapping calls — a burst of SSE events shouldn't trigger
  // parallel fetches.
  if (_totalsInFlight) return;
  _totalsInFlight = true;
  try {
    const r = await fetch(`/api/v1/rr/scan/${scanId}/llm-counters`);
    if (!r.ok) {
      _renderScanTotals(null);
      return;
    }
    const data = await r.json();
    _renderScanTotals(data);
  } catch {
    _renderScanTotals(null);
  } finally {
    _totalsInFlight = false;
  }
}

function _startTotalsPolling() {
  if (_totalsPollTimer) return;  // already polling
  _totalsPollTimer = setInterval(() => {
    // Pause polling when the tab is hidden — no point burning Redis ops
    // for a page nobody is looking at.
    if (document.visibilityState !== 'visible') return;
    // Stop when the scan is terminal.
    if (_TERMINAL_PHASES.has(_lastPhase)) {
      _stopTotalsPolling();
      return;
    }
    _refreshScanTotals();
  }, _TOTALS_POLL_MS);
}

function _stopTotalsPolling() {
  if (!_totalsPollTimer) return;
  clearInterval(_totalsPollTimer);
  _totalsPollTimer = null;
}

// Watch tab-visibility changes — resume polling when the tab comes back
// into focus, do an immediate refresh so the user sees current state.
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') {
    _refreshScanTotals();
    if (!_TERMINAL_PHASES.has(_lastPhase)) _startTotalsPolling();
  }
});

const _PHASE_DISPLAY_ORDER = [
  'orchestrator', 'discovery', 'triage', 'deep_read',
  'graph_build', 'synthesis',
];

function _aggregateByModel(byPhase) {
  // Roll up per-phase per-model data into one scan-wide
  //   { "<deployment>": {calls, tokens_in, tokens_out} } map.
  // Same key shape as the drawer's by_model so we can reuse
  // `_splitProviderModel()` for rendering.
  const merged = {};
  for (const phase of Object.values(byPhase || {})) {
    const bm = phase.by_model || {};
    for (const [model, stats] of Object.entries(bm)) {
      const e = merged[model] || { calls: 0, tokens_in: 0, tokens_out: 0 };
      e.calls      += stats.calls      || 0;
      e.tokens_in  += stats.tokens_in  || 0;
      e.tokens_out += stats.tokens_out || 0;
      merged[model] = e;
    }
  }
  return merged;
}

function _renderScanTotals(payload) {
  const callsEl  = document.getElementById('rr-totals-calls');
  const inEl     = document.getElementById('rr-totals-in');
  const outEl    = document.getElementById('rr-totals-out');
  const chipsEl  = document.getElementById('rr-totals-phases');
  const tableEl  = document.getElementById('rr-totals-models');
  if (!callsEl || !inEl || !outEl || !chipsEl || !tableEl) return;

  // No scan in scope OR fetch failed → reset to placeholders.
  if (!payload || !payload.total) {
    callsEl.textContent = '—';
    inEl.textContent    = '—';
    outEl.textContent   = '—';
    chipsEl.innerHTML   = '';
    tableEl.innerHTML   = '';
    return;
  }

  const total   = payload.total   || {};
  const byPhase = payload.by_phase || {};

  // KPI cards.
  callsEl.textContent = _fmtNumber(total.calls      || 0);
  inEl.textContent    = _fmtNumber(total.tokens_in  || 0);
  outEl.textContent   = _fmtNumber(total.tokens_out || 0);

  // Per-phase chips — ordered left-to-right matching the pipeline flow.
  // Skip phases with zero calls so the row stays compact.
  const chips = _PHASE_DISPLAY_ORDER
    .filter(p => byPhase[p] && byPhase[p].calls)
    .map(p => {
      const ph = byPhase[p];
      const share = total.calls
        ? Math.round((ph.calls / total.calls) * 100) + '%'
        : '—';
      return (
        `<span class="rr-totals-chip" title="` +
          `${_esc(p)}: ${_fmtNumber(ph.calls)} calls · ` +
          `${_fmtNumber(ph.tokens_in)} in / ${_fmtNumber(ph.tokens_out)} out` +
        `">` +
        `<span class="rr-totals-chip-name">${_esc(p)}</span>` +
        `<span class="rr-totals-chip-count">${_fmtNumber(ph.calls)}</span>` +
        `<span class="rr-totals-chip-share">${share}</span>` +
        `</span>`
      );
    })
    .join('');
  chipsEl.innerHTML = chips;

  // Scan-wide per-(provider, model) table — same shape as the drawer's
  // per-model breakdown, but aggregated across all phases. Sorted by
  // total calls descending so the heaviest-used arms are at the top.
  const merged = _aggregateByModel(byPhase);
  const rows = Object.entries(merged)
    .map(([model, stats]) => {
      const { provider, name } = _splitProviderModel(model);
      return {
        raw:        model,
        provider,
        name,
        calls:      stats.calls      || 0,
        tokens_in:  stats.tokens_in  || 0,
        tokens_out: stats.tokens_out || 0,
      };
    })
    .sort((a, b) => b.calls - a.calls);

  if (!rows.length) {
    tableEl.innerHTML = '';
  } else {
    tableEl.innerHTML =
      `<div class="rr-totals-models-title">Per provider · model</div>` +
      `<table class="rr-totals-models-table">` +
        `<thead><tr>` +
          `<th>provider</th><th>model</th>` +
          `<th>calls</th><th>in</th><th>out</th>` +
        `</tr></thead>` +
        `<tbody>` +
          rows.map(r =>
            `<tr>` +
            `<td title="${_esc(r.raw)}">${_esc(r.provider)}</td>` +
            `<td title="${_esc(r.raw)}">${_esc(r.name)}</td>` +
            `<td>${_fmtNumber(r.calls)}</td>` +
            `<td>${_fmtNumber(r.tokens_in)}</td>` +
            `<td>${_fmtNumber(r.tokens_out)}</td>` +
            `</tr>`
          ).join('') +
        `</tbody>` +
      `</table>`;
  }

  // Manage the polling lifecycle based on current scan state. The phase
  // contextvar driving _lastPhase is set from the SSE updater; if the
  // scan is terminal, we stop polling but keep the strip filled so the
  // user can see the final numbers as long as the page is open.
  if (_TERMINAL_PHASES.has(_lastPhase)) _stopTotalsPolling();
  else                                  _startTotalsPolling();
}


// Kick off as soon as the DOM is ready. document.readyState already says
// "interactive" by the time a deferred module script runs in practice;
// the early-exit inside initPipelineGraph() covers the Digest page.
initPipelineGraph();
