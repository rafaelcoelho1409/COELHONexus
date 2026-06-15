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
    .selector("node[status = 'failed']")
    .style({
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
    bodyEl.innerHTML = sections.join('');
  }
  drawer.hidden = false;
  if (d.live_fs_path) _fetchLiveState(d.live_fs_path);
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

  // StageGraph defaults to rankDir='LR' (Planner/Synth read left-to-right).
  // RR's pipeline is conceptually a top-down flow (discovery → triage →
  // deep_read → … → persist), so re-run the layout in vertical orientation
  // without touching the shared helper. Falls back gracefully when Dagre
  // isn't registered (StageGraph then used the breadthfirst layout, which
  // is already top-down — nothing to do).
  if (typeof cytoscape !== 'undefined' && cytoscape._dagreRegistered) {
    graph.cy.layout({
      name:    'dagre',
      rankDir: 'TB',
      nodeSep: 28,
      rankSep: 44,
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
  };

  // Flush any pre-init state that landed during the cytoscape load.
  if (_lastPhase) _applyPhase(graph, _lastPhase, _lastMessage);
}

// Kick off as soon as the DOM is ready. document.readyState already says
// "interactive" by the time a deferred module script runs in practice;
// the early-exit inside initPipelineGraph() covers the Digest page.
initPipelineGraph();
