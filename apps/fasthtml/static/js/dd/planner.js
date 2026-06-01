// Planner module — Cytoscape graph, cards, SSE polling, start/cancel/wipe.
import * as S from './state.js';
import { StageGraph } from './stagegraph.js';
import { sleep, fmtBytes, fmtAge, escapeHtml, formatFieldValue } from './utils.js';
import {
  showConfirm, showNotice, showToast, refreshGenerateState,
  fetchPipelineState, cascadeImpactText,
} from './ui.js';
import { loadManifestForSlug, renderManifest } from './ingestion.js';
import {
  startElapsed, stopElapsed, showElapsed, isElapsedRunning,
} from './timing.js';


export function _resizePlannerCanvas() {
  if (!S.plannerGraph || !S.plannerGraph.cy) return;
  // requestAnimationFrame defers to the next paint — the CSS panel
  // transition (display:block) needs one frame to apply non-zero
  // dimensions before Cytoscape measures them. Without the rAF,
  // resize() reads stale 0x0 bounds and the graph stays hidden.
  requestAnimationFrame(() => {
    _runPlannerLayoutAndCenter('first');
    // Second-pass after a longer delay — handles the case where the
    // container's final size is only known after CSS transitions /
    // flex reflows complete. Without this the graph latches the
    // canvas's transient pre-reflow width.
    setTimeout(() => _runPlannerLayoutAndCenter('second'), 250);
  });
}

export function _runPlannerLayoutAndCenter(passLabel) {
  if (!S.plannerGraph || !S.plannerGraph.cy) return;
  try {
    const cy = S.plannerGraph.cy;
    cy.resize();
    const hasDagre = !!cytoscape._dagreRegistered;
    const layout = cy.layout(hasDagre
      ? { name: 'dagre', rankDir: 'LR', nodeSep: 36, rankSep: 56,
          padding: 32, animate: false, fit: false }
      : { name: 'breadthfirst', directed: true, padding: 32,
          spacingFactor: 1.4, animate: false, fit: false }
    );
    layout.one('layoutstop', () => {
      try {
        cy.fit(cy.elements(), 32);
        cy.center(cy.elements());
        _forceCenterHorizontal(cy, '[plannerGraph ' + passLabel + ']');
      } catch (e) {
        console.warn('[plannerGraph] center pipeline failed:', e);
      }
    });
    layout.run();
  } catch (e) {
    console.warn('[plannerGraph] resize ' + passLabel + ' failed:', e);
  }
}

// Brute-force horizontal recentering with detailed logging so we can
// SEE what Cytoscape thinks the dimensions are. The empty catch
// blocks in earlier versions silently swallowed the actual problem.
export function _forceCenterHorizontal(cy, tag) {
  tag = tag || '[graph]';
  const containerW = cy.width();
  const containerH = cy.height();
  const bb = cy.elements().renderedBoundingBox();
  const pan = cy.pan();
  const zoom = cy.zoom();
  console.log(
    tag + ' centering: containerW=' + containerW +
    ' containerH=' + containerH +
    ' zoom=' + zoom.toFixed(3) +
    ' pan=(' + pan.x.toFixed(1) + ',' + pan.y.toFixed(1) + ')' +
    ' bb={x1=' + (bb ? bb.x1.toFixed(1) : '?') +
    ' x2=' + (bb ? bb.x2.toFixed(1) : '?') +
    ' w=' + (bb ? bb.w.toFixed(1) : '?') + '}'
  );
  if (!containerW || !bb || !bb.w) {
    console.warn(tag + ' centering ABORTED — bad dims');
    return;
  }
  const graphMidX = bb.x1 + bb.w / 2;
  const containerMidX = containerW / 2;
  const dx = containerMidX - graphMidX;
  const graphMidY = bb.y1 + bb.h / 2;
  const containerMidY = containerH / 2;
  const dy = containerMidY - graphMidY;
  console.log(
    tag + ' delta: dx=' + dx.toFixed(1) + ' dy=' + dy.toFixed(1)
  );
  if (Math.abs(dx) > 0.5 || Math.abs(dy) > 0.5) {
    cy.panBy({ x: dx, y: dy });
    const newPan = cy.pan();
    console.log(
      tag + ' panned to (' + newPan.x.toFixed(1) + ',' +
      newPan.y.toFixed(1) + ')'
    );
  }
}
// Defensive: re-fit on window resize so the canvas stays responsive.
// Throttle to one rAF per resize burst.
let _resizeRafPending = false;
window.addEventListener('resize', () => {
  if (_resizeRafPending) return;
  _resizeRafPending = true;
  requestAnimationFrame(() => {
    _resizeRafPending = false;
    if (S.plannerGraph) _resizePlannerCanvas();
  });
});
// ResizeObserver — catches container size changes from sources other
// than window resize (CSS transitions, flex reflows, S.sidebar
// collapses). Critical for the left-clipping bug: the canvas's
// post-display:flex final width can land 100+ ms after the initial
// mount, and Cytoscape latches the transient pre-reflow value.
export function _attachCanvasResizeObserver(containerId, resizeFn) {
  if (typeof ResizeObserver === 'undefined') return;
  const el = document.getElementById(containerId);
  if (!el) return;
  let lastW = 0;
  let debounce = null;
  const ro = new ResizeObserver(entries => {
    for (const e of entries) {
      const w = Math.round(e.contentRect.width);
      if (w === lastW || w === 0) continue;
      lastW = w;
      console.log('[' + containerId + '] container resized to', w);
      if (debounce) clearTimeout(debounce);
      debounce = setTimeout(() => { debounce = null; resizeFn(); }, 80);
    }
  });
  ro.observe(el);
}

// ============================================================
// Day 2 — Stage pill + graph state mirror (SSE → canvas wiring)
//
// Top-of-stage pill summarizes the WHOLE pipeline at a glance
// (idle / working / done / failed). Driven by the same SSE events
// that flip per-node statuses. CSS handles the visual via
// `[data-status]` attribute selectors on `.fw-stage-pill`.
// ============================================================
export function _setPlannerStagePill(status, labelOverride) {
  const pill = document.getElementById('fw-planner-pill');
  const text = document.getElementById('fw-planner-pill-text');
  if (!pill || !text) return;
  const labels = {
    idle:     'Idle',
    working:  'Working',
    done:     'Completed',
    failed:   'Failed',
    cancelled:'Cancelled',
  };
  pill.dataset.status = status;
  text.textContent = labelOverride || labels[status] || status;
}

// Per-node KPI badge — ONE number shown as a small second-line under
// the label. Source is the per-node `*_stats` dict in state values
// (same dicts the cards use for their KPI grids). Returns '' when
// the node hasn't run yet.
export function _kpiForNode(nodeId, values) {
  if (!values) return '';
  const stats = (key) => values[key] || null;
  switch (nodeId) {
    case 'corpus_load': {
      const s = stats('corpus_stats');
      return s && s.files ? `n=${s.files}` : '';
    }
    case 'embed_corpus': {
      const s = stats('embed_stats');
      if (!s) return '';
      if (s.dim) return `dim=${s.dim}`;
      if (s.files) return `n=${s.files}`;
      return '';
    }
    case 'off_topic': {
      const s = stats('off_topic_stats');
      return s && (s.kept !== undefined)
        ? `kept=${s.kept}/${(s.kept + (s.dropped || 0))}` : '';
    }
    case 'cluster': {
      const s = stats('cluster_stats');
      return s && (s.n_clusters !== undefined)
        ? `k=${s.n_clusters}` : '';
    }
    case 'refine': {
      const s = stats('refine_stats');
      return s && (s.n_changed !== undefined)
        ? `reassigned=${s.n_changed}` : '';
    }
    case 'label': {
      const s = stats('label_stats');
      return s && (s.n_clusters !== undefined)
        ? `k=${s.n_clusters}` : '';
    }
    case 'reduce': {
      const s = stats('reduce_stats');
      return s && (s.n_chapters !== undefined)
        ? `ch=${s.n_chapters}` : '';
    }
    // ─── LLM-first nodes (DD-PLANNER-LLM-FIRST-SOTA-2026-05-27) ───
    case 'doc_distill': {
      const s = stats('doc_distill_stats');
      if (!s) return '';
      if (s.skipped) return `skip:N≤80`;
      return (s.n_distilled !== undefined)
        ? `n=${s.n_distilled}/${s.n_files || '?'}` : '';
    }
    case 'chapter_propose': {
      const s = stats('propose_stats');
      return s && (s.n_proposals !== undefined)
        ? `props=${s.n_proposals}` : '';
    }
    case 'chapter_assign': {
      const s = stats('assign_stats');
      return s && (s.n_assigned !== undefined)
        ? `assigned=${s.n_assigned}/${s.n_docs || '?'}` : '';
    }
    case 'chapter_select': {
      const s = stats('select_stats');
      return s && (s.n_chapters_out !== undefined)
        ? `ch=${s.n_chapters_out}` : '';
    }
    case 'plan_write': {
      const s = stats('plan_write_stats');
      return s && (s.n_chapters !== undefined)
        ? `ch=${s.n_chapters}` : '';
    }
  }
  return '';
}

// Mirror of renderPlannerCards for the Cytoscape canvas. Loops the
// canonical node order, derives status per node from state field
// presence (same logic as the cards path), and pushes to
// S.plannerGraph.setStatus. No-op when the canvas isn't mounted
// (?ui=cards) — keeps the call sites uniform.
export function _renderPlannerGraph(values) {
  if (!S.plannerGraph) return;
  let doneCount = 0;
  let anyRunning = false;
  let anyFailed = false;
  for (let i = 0; i < S.PLANNER_NODE_ORDER.length; i++) {
    const nodeId = S.PLANNER_NODE_ORDER[i];
    const field = S.PLANNER_SUBSTEP_FIELDS[i];
    const present = _fieldPresent(values, field);
    const isImpl = S.plannerImplemented.has(nodeId);
    let status;
    if (present) {
      status = 'done';
      doneCount++;
    } else if (!isImpl) {
      status = 'future';
    } else if (i === doneCount && S.plannerThreadId !== null) {
      status = 'running';
      anyRunning = true;
    } else {
      status = 'pending';
    }
    const kpi = present ? _kpiForNode(nodeId, values) : '';
    S.plannerGraph.setStatus(nodeId, status, kpi);
  }
  // Derive stage pill from aggregate state. Failed has priority,
  // then running, then all-done, else idle. The terminal SSE
  // handler overrides this with explicit done/failed/cancelled.
  // Progress count (N/8) is folded INTO the pill while working —
  // replaces the separate "Step N of 8" label that used to live in
  // the header actions cluster.
  const explicitStatus = (values && values.status) || null;
  const implCount = S.PLANNER_NODE_ORDER.filter(n => S.plannerImplemented.has(n)).length;
  const progress = implCount ? doneCount + '/' + implCount : null;
  if (explicitStatus === 'failed') {
    _setPlannerStagePill('failed');
    anyFailed = true;
  } else if (explicitStatus === 'cancelled') {
    _setPlannerStagePill('cancelled');
  } else if (anyRunning || S.plannerThreadId !== null) {
    _setPlannerStagePill('working',
      progress ? 'Working · ' + progress : null);
  } else if (
    doneCount > 0 && doneCount === implCount
  ) {
    _setPlannerStagePill('done');
  } else if (doneCount === 0) {
    _setPlannerStagePill('idle');
  }
  return { doneCount, anyRunning, anyFailed };
}

// Build the drawer context object for a planner node from the
// current checkpoint state. Separate from `open()` so live state
// refreshes can reuse the same logic via `_refreshOpenPlannerDrawer`.
export function _buildPlannerNodeCtx(nodeId, values) {
  const idx = S.PLANNER_NODE_ORDER.indexOf(nodeId);
  if (idx < 0) return null;
  const label = S.PLANNER_NODE_LABELS[idx] || nodeId;
  const thisField = S.PLANNER_SUBSTEP_FIELDS[idx];
  let status = 'pending';
  if (_fieldPresent(values, thisField)) status = 'done';
  else if (!S.plannerImplemented.has(nodeId)) status = 'future';
  else if (S.plannerThreadId) status = 'running';
  // KPI strip for the sticky header — same compact format as the
  // node-label KPI badge but split into key/value S.chips.
  const kpiText = _kpiForNode(nodeId, values);
  const kpis = {};
  if (kpiText) {
    const eqIdx = kpiText.indexOf('=');
    if (eqIdx > 0) kpis[kpiText.slice(0, eqIdx)] = kpiText.slice(eqIdx + 1);
  }
  // PRIMARY content — the SAME rich HTML the legacy card body
  // showed. Custom per-substep renderer if this node has produced
  // output; otherwise the drawer renders a status-aware placeholder.
  const renderer = SUBSTEP_RENDERERS[idx];
  const resultsHtml = (renderer && _fieldPresent(values, thisField))
    ? renderer(values)
    : null;
  // Raw JSON kept as collapsed debug aids (only when present).
  const inputs = idx > 0 && _fieldPresent(values, S.PLANNER_SUBSTEP_FIELDS[idx - 1])
    ? JSON.stringify({ [S.PLANNER_SUBSTEP_FIELDS[idx - 1]]: values[S.PLANNER_SUBSTEP_FIELDS[idx - 1]] }, null, 2)
    : null;
  const outputs = _fieldPresent(values, thisField)
    ? JSON.stringify({ [thisField]: values[thisField] }, null, 2)
    : null;
  return { label, status, kpis, resultsHtml, inputs, outputs };
}

// Opens the NodeDrawer for a planner node. Fetches fresh state for
// an accurate initial render; subsequent updates flow in via the
// SSE handler + _refreshOpenPlannerDrawer.
export async function _openPlannerNodeDrawer(nodeId) {
  let values = {};
  // S.plannerThreadId is set ONLY while a run is in flight — terminal
  // SSE handler nulls it on done/failed/cancelled. For a completed
  // thread we need the localStorage entry (same fallback the page-
  // refresh recovery uses) so the drawer can fetch /state and the
  // renderer can show the rich card body content.
  let tid = S.plannerThreadId;
  if (!tid && S.activeSlug) {
    try { tid = localStorage.getItem(_plannerStorageKey(S.activeSlug)); }
    catch (e) {}
  }
  if (tid) {
    try {
      const r = await fetch(S.API + '/planner/debug/graph/' + tid + '/state');
      if (r.ok) values = (await r.json()).values || {};
    } catch (e) { /* drawer opens with empty results */ }
  }
  const ctx = _buildPlannerNodeCtx(nodeId, values);
  if (ctx) NodeDrawer.open('planner', nodeId, ctx);
}

// Called from renderPlannerCards on every state refresh so the
// open drawer's results panel updates as the pipeline progresses
// (e.g. cluster card's KPI grid materializes the moment `cluster`
// commits its checkpoint, without the user having to re-click).
export function _refreshOpenPlannerDrawer(values) {
  if (NodeDrawer.openStage !== 'planner') return;
  const nodeId = NodeDrawer.openNodeId;
  if (!nodeId) return;
  const ctx = _buildPlannerNodeCtx(nodeId, values);
  if (ctx) NodeDrawer.updateContext(ctx);
}

// ============================================================
// NodeDrawer — right-side drawer showing a single graph node's
// live activity (Day 3 of UI-redesign sprint). Opens when a user
// clicks a node on the planner/synth canvas; subscribes to the SSE
// event stream for that node and streams events into a sticky-
// bottom log with rAF batching + 200-line cap.
//
// Public S.API:
//   NodeDrawer.open(stage, nodeId, ctx)  // ctx = {label, kpis, status, prompt?, inputs?, outputs?}
//   NodeDrawer.close()
//   NodeDrawer.isOpenFor(stage, nodeId)
//   NodeDrawer.appendEvent(ev)           // route an SSE event to the log + status
//   NodeDrawer.updateContext(ctx)        // refresh static sections (inputs/outputs)
// ============================================================
export const NodeDrawer = (function() {
  const elDrawer    = document.getElementById('fw-node-drawer');
  const elIcon      = document.getElementById('fw-node-drawer-icon');
  const elTitle     = document.getElementById('fw-node-drawer-title');
  const elMeta      = document.getElementById('fw-node-drawer-meta');
  const elKpis      = document.getElementById('fw-node-drawer-kpis');
  const elLog       = document.getElementById('fw-node-drawer-log');
  const elLogEmpty  = document.getElementById('fw-node-drawer-log-empty');
  const elDetails   = document.getElementById('fw-node-drawer-details');
  const elClose     = document.getElementById('fw-node-drawer-close');

  const MAX_LOG_LINES = 200;
  const STATUS_ICON = {
    future: '⏳', pending: '○', running: '◐',
    done: '●', failed: '✕', cancelled: '∅',
  };

  let _openStage = null;        // 'planner' | 'synth' | null
  let _openNodeId = null;
  let _pendingEvents = [];
  let _flushScheduled = false;
  let _userPinnedScroll = true; // true = auto-scroll to bottom; false = user scrolled up
  // "Since last viewed" tracking: maps `${stage}/${nodeId}` → epoch ms
  // of last drawer-open for that node. Events whose timestamp is
  // newer than the previous lastSeen get an `.is-new` highlight.
  // Per-session (not persisted) — chat-style affordance.
  const _lastSeenAt = new Map();
  let _prevSeenForOpen = 0;     // captured at open(); 0 = first open ever

  function _fmtTs(ts) {
    const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date();
    const h = String(d.getHours()).padStart(2, '0');
    const m = String(d.getMinutes()).padStart(2, '0');
    const s = String(d.getSeconds()).padStart(2, '0');
    return `${h}:${m}:${s}`;
  }

  function _makeLogLine(ev) {
    const div = document.createElement('div');
    div.className = 'fw-node-drawer-log-line';
    const kind = (ev && ev.kind) || 'info';
    div.dataset.kind = kind;
    // Severity coloring: errors burgundy, warnings amber, info gray.
    if (kind === 'error' || ev.error) div.classList.add('severity-error');
    else if (kind === 'warning')      div.classList.add('severity-warn');
    // "Since last viewed" highlight — events newer than the previous
    // drawer-open get a subtle left-border accent. Only after first
    // open (_prevSeenForOpen > 0); on a node's first-ever open every
    // event would be "new" which carries no signal.
    const evTsMs = (typeof ev.ts === 'number') ? ev.ts * 1000 : Date.now();
    if (_prevSeenForOpen > 0 && evTsMs > _prevSeenForOpen) {
      div.classList.add('is-new');
    }
    // Extract a tidy event payload (drop noisy fields).
    const tidy = {};
    Object.keys(ev || {}).forEach(k => {
      if (k === 'ts' || k === 'step' || k === 'kind') return;
      tidy[k] = ev[k];
    });
    const tidyStr = Object.keys(tidy).length
      ? ' ' + Object.entries(tidy)
          .map(([k, v]) => `${k}=${typeof v === 'object'
            ? JSON.stringify(v).slice(0, 60) : String(v).slice(0, 60)}`)
          .join(' ')
      : '';
    div.textContent = `▸ ${_fmtTs(ev.ts)} ${kind}${tidyStr}`;
    return div;
  }

  function _scheduleFlush() {
    if (_flushScheduled) return;
    _flushScheduled = true;
    requestAnimationFrame(() => {
      _flushScheduled = false;
      if (_pendingEvents.length === 0 || !elLog) return;
      // Hide the empty-state placeholder on first line.
      if (elLogEmpty) elLogEmpty.style.display = 'none';
      const frag = document.createDocumentFragment();
      _pendingEvents.forEach(ev => frag.appendChild(_makeLogLine(ev)));
      elLog.appendChild(frag);
      _pendingEvents = [];
      // Cap at MAX_LOG_LINES — evict oldest from top.
      while (elLog.childElementCount > MAX_LOG_LINES) {
        elLog.removeChild(elLog.firstChild);
      }
      // Sticky-bottom: only auto-scroll if the user hasn't scrolled
      // up to inspect earlier events.
      if (_userPinnedScroll) {
        elLog.scrollTop = elLog.scrollHeight;
      }
    });
  }

  function _updateStatusIcon(status) {
    if (!elIcon) return;
    elIcon.textContent = STATUS_ICON[status] || '○';
    elIcon.dataset.status = status || 'pending';
  }

  function _renderKpis(kpis) {
    if (!elKpis) return;
    if (!kpis || typeof kpis !== 'object') {
      elKpis.innerHTML = '';
      elKpis.style.display = 'none';
      return;
    }
    const entries = Object.entries(kpis).filter(([, v]) =>
      v !== undefined && v !== null && v !== '');
    if (!entries.length) {
      elKpis.innerHTML = '';
      elKpis.style.display = 'none';
      return;
    }
    elKpis.innerHTML = entries.map(([k, v]) =>
      '<span class="fw-node-drawer-kpi">' +
        '<span class="fw-node-drawer-kpi-label">' + escapeHtml(k) + '</span>' +
        '<span class="fw-node-drawer-kpi-value">' +
          escapeHtml(typeof v === 'object' ? JSON.stringify(v) : String(v)) +
        '</span>' +
      '</span>'
    ).join('');
    elKpis.style.display = '';
  }

  function _renderDetails(ctx) {
    if (!elDetails) return;
    // Primary content = the SAME rich HTML the legacy card body
    // showed (KPI grids, tables, outline cards, bandit charts).
    // Caller passes `resultsHtml` precomputed via the stage's
    // SUBSTEP_RENDERERS[idx](values). Falls back to a waiting
    // placeholder when the node hasn't produced output yet.
    const resultsBlock = ctx.resultsHtml
      ? '<div class="fw-node-drawer-results">' + ctx.resultsHtml + '</div>'
      : '<div class="fw-empty fw-node-drawer-waiting">' +
        (ctx.status === 'running'
          ? 'Running — results will appear once this node commits its checkpoint.'
          : ctx.status === 'failed'
          ? 'This node failed before producing output. See the activity log for details.'
          : ctx.status === 'future'
          ? 'Not yet implemented — substep will activate when its node code ships.'
          : 'Waiting for this node to run.') +
        '</div>';
    // Raw inputs/outputs JSON kept as a collapsed debugging aid
    // (only when present — hides when there's nothing to show).
    const debug = [];
    if (ctx.inputs) debug.push({
      id: 'inputs',  title: 'Inputs (upstream state, raw)',
      content: '<pre>' + escapeHtml(ctx.inputs) + '</pre>',
    });
    if (ctx.outputs) debug.push({
      id: 'outputs', title: 'Outputs (this node, raw)',
      content: '<pre>' + escapeHtml(ctx.outputs) + '</pre>',
    });
    const debugBlock = debug.length
      ? debug.map(s =>
          '<details class="fw-node-drawer-detail" data-section="' + s.id + '">' +
            '<summary>' + escapeHtml(s.title) + '</summary>' +
            '<div class="fw-node-drawer-detail-body">' + s.content + '</div>' +
          '</details>'
        ).join('')
      : '';
    elDetails.innerHTML = resultsBlock + debugBlock;
  }

  function _populate(stage, nodeId, ctx) {
    // Capture lastSeenAt BEFORE bumping it — so events arriving in
    // this open() session compare against the previous timestamp,
    // not the current one. First-ever open of a node has 0.
    const key = stage + '/' + nodeId;
    _prevSeenForOpen = _lastSeenAt.get(key) || 0;
    _lastSeenAt.set(key, Date.now());
    _openStage  = stage;
    _openNodeId = nodeId;
    _pendingEvents = [];
    _userPinnedScroll = true;
    if (elTitle) elTitle.textContent = ctx.label || nodeId;
    if (elMeta)  elMeta.textContent  = stage + ' · ' + nodeId;
    _updateStatusIcon(ctx.status || 'pending');
    _renderKpis(ctx.kpis);
    _renderDetails(ctx);
    // Reset log (each drawer-open starts fresh; events stream live).
    if (elLog) elLog.innerHTML = '';
    if (elLogEmpty) elLogEmpty.style.display = '';
  }

  function open(stage, nodeId, ctx) {
    if (!elDrawer) return;
    ctx = ctx || {};
    const wasVisible = elDrawer.classList.contains('visible');
    const isSameNode = (_openStage === stage && _openNodeId === nodeId);
    const elBody = document.getElementById('fw-node-drawer-body');
    // Cross-fade when switching to a different node while the drawer
    // is already open — avoids the hard content-swap flicker.
    // Same-node re-opens skip the fade (no perceptible change anyway).
    if (wasVisible && !isSameNode && elBody) {
      elBody.classList.add('fw-node-drawer-fading');
      setTimeout(() => {
        _populate(stage, nodeId, ctx);
        elBody.classList.remove('fw-node-drawer-fading');
      }, 140);
    } else {
      _populate(stage, nodeId, ctx);
    }
    elDrawer.classList.add('visible');
    // Focus close for keyboard a11y.
    if (elClose) setTimeout(() => elClose.focus(), 100);
  }

  function close() {
    if (!elDrawer) return;
    elDrawer.classList.remove('visible');
    _openStage = null;
    _openNodeId = null;
  }

  function isOpenFor(stage, nodeId) {
    return _openStage === stage && _openNodeId === nodeId;
  }

  function appendEvent(ev) {
    if (!ev || !_openNodeId) return;
    _pendingEvents.push(ev);
    _scheduleFlush();
    // Side effects on status: `done`/`failed`/`start` swap the
    // drawer's status icon to match the canvas node.
    if (ev.kind === 'start')   _updateStatusIcon('running');
    else if (ev.kind === 'done') _updateStatusIcon('done');
    else if (ev.kind === 'error') _updateStatusIcon('failed');
  }

  function updateContext(ctx) {
    if (!_openNodeId) return;
    ctx = ctx || {};
    if (ctx.status !== undefined) _updateStatusIcon(ctx.status);
    if (ctx.kpis   !== undefined) _renderKpis(ctx.kpis);
    // Re-render details only if any of the section sources changed —
    // cheap enough to do unconditionally for now.
    _renderDetails(ctx);
  }

  // Detect user scroll-away — lock auto-scroll until they return to
  // bottom. Threshold of 24px so a small wheel nudge doesn't flip it.
  if (elLog) {
    elLog.addEventListener('scroll', () => {
      const atBottom = (elLog.scrollHeight - elLog.scrollTop - elLog.clientHeight) < 24;
      _userPinnedScroll = atBottom;
    });
  }
  if (elClose) elClose.addEventListener('click', close);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && elDrawer && elDrawer.classList.contains('visible')) {
      close();
    }
  });

  // reset() — clear in-flight events + DOM log without closing the
  // drawer. Used when the study orchestrator advances to the next
  // chapter so an already-open drawer doesn't keep stale events from
  // the previous chapter's run of the same node.
  function reset() {
    _pendingEvents = [];
    if (elLog) {
      while (elLog.firstChild) elLog.removeChild(elLog.firstChild);
    }
    if (elLogEmpty) elLogEmpty.style.display = '';
    _lastSeenAt.clear();
    _prevSeenForOpen = 0;
  }

  return { open, close, reset, isOpenFor, appendEvent, updateContext,
           get openNodeId() { return _openNodeId; },
           get openStage()  { return _openStage; } };
})();

export function _initPlannerCanvas() {
  if (S.UI_MODE !== 'graph') {
    console.log('[plannerGraph] UI_MODE=cards (default) — canvas not mounted');
    return;
  }
  console.log('[plannerGraph] UI_MODE=graph — mounting Cytoscape canvas');
  const root = document.getElementById('fw-planner-graph');
  const canvasEl = document.getElementById('fw-planner-canvas');
  if (!root || !canvasEl) {
    console.warn('[plannerGraph] missing #fw-planner-graph or #fw-planner-canvas in DOM');
    return;
  }
  // Visibility is managed exclusively by _toggleStageEmpty (single
  // source of truth). Canvas init no longer touches display so it
  // can't race the toggle. Cytoscape may mount against a 0×0
  // container if the wrapper is hidden — that's fine; the toggle
  // calls _resizePlannerCanvas() the moment the wrapper becomes
  // visible.
  // Wait for Cytoscape (loaded with `defer` from CDN). Poll briefly.
  const startedAt = Date.now();
  function tryInit() {
    if (typeof cytoscape !== 'undefined') {
      const nodes = S.PLANNER_NODE_ORDER.map((id, i) => ({
        id,
        label:  S.PLANNER_NODE_LABELS[i] || id,
        status: S.plannerImplemented.has(id) ? 'pending' : 'future',
      }));
      const edges = [];
      for (let i = 0; i < S.PLANNER_NODE_ORDER.length - 1; i++) {
        edges.push({
          source: S.PLANNER_NODE_ORDER[i],
          target: S.PLANNER_NODE_ORDER[i + 1],
        });
      }
      const w = canvasEl.offsetWidth;
      const h = canvasEl.offsetHeight;
      console.log(
        `[plannerGraph] canvas container ready, dims=${w}x${h}` +
        (w === 0 || h === 0
          ? ' (WARNING: zero dim — graph will be invisible until ' +
            '_resizePlannerCanvas runs after panel becomes active)'
          : ''),
      );
      S.setPlannerGraph(StageGraph.create(canvasEl, {
        nodes, edges,
        onNodeClick: (nodeId) => _openPlannerNodeDrawer(nodeId),
      }));
      console.log(
        `[plannerGraph] Cytoscape initialized with ${nodes.length} ` +
        `nodes, ${edges.length} edges`,
      );
      // If Step 3 is already the active panel at init time, kick a
      // resize+fit immediately. Otherwise the first resize fires from
      // showStep(3) below — Cytoscape inits inside a display:none
      // ancestor with 0x0 bounds, and without resize() it stays
      // invisible even after the panel becomes active.
      if (S.plannerGraph) _resizePlannerCanvas();
      _attachCanvasResizeObserver('fw-planner-canvas', _resizePlannerCanvas);
      return;
    }
    if (Date.now() - startedAt > 5000) {
      console.warn(
        '[plannerGraph] Cytoscape failed to load within 5s — ' +
        'canvas unavailable. Reload the page to retry.',
      );
      // No cards fallback anymore (cards DOM was removed 2026-05-19).
      // Surface an in-place error so the user knows what happened
      // instead of staring at an empty pane.
      const canvasEl = document.getElementById('fw-planner-canvas');
      if (canvasEl) {
        canvasEl.innerHTML =
          '<div class="fw-empty">Cytoscape failed to load. ' +
          'Reload the page; if it persists, check the network panel ' +
          'for blocked /static/vendor/cytoscape.min.js.</div>';
      }
      return;
    }
    setTimeout(tryInit, 80);
  }
  tryInit();
}

// ============================================================
// Utility
// ============================================================

export function refreshPlannerStartState() {
  if (!S.plannerStartBtn) return;   // not on the planner page
  // Three states for the Start/Cancel button:
  //  - idle, ready    → "Start Planner" enabled
  //  - idle, blocked  → "Start Planner" disabled (no slug, ingest active,
  //                     or no ingested corpus yet)
  //  - running        → button becomes "Cancel Planner" (always enabled
  //                     during a run; same behavior pattern as Step 2's
  //                     ingestion cancel)
  const running = S.plannerThreadId !== null;
  if (running) {
    S.plannerStartBtn.removeAttribute('disabled');
    S.plannerStartBtn.classList.add('btn-outline');
    S.plannerStartBtn.classList.remove('btn-primary');
    S.plannerStartBtn.innerHTML = 'Cancel Planner';
  } else {
    // CORPUS-FIRST GATE — the planner needs an ingested corpus. Mirrors
    // the server-side read_framework_manifest 404 so the disabled button
    // and the API agree. S.ingestedSlugs is populated by loadLibrary.
    const hasCorpus = S.ingestedSlugs.has(S.activeSlug);
    const ready = S.activeSlug && S.activeRunId == null && hasCorpus;
    if (ready) {
      S.plannerStartBtn.removeAttribute('disabled');
      S.plannerStartBtn.removeAttribute('title');
    } else {
      S.plannerStartBtn.setAttribute('disabled', 'disabled');
      if (!S.activeSlug) {
        S.plannerStartBtn.setAttribute('title', 'Pick a framework first.');
      } else if (!hasCorpus) {
        S.plannerStartBtn.setAttribute('title',
          'Ingest this framework first — the planner needs its corpus.');
      } else {
        S.plannerStartBtn.removeAttribute('title');
      }
    }
    S.plannerStartBtn.classList.add('btn-primary');
    S.plannerStartBtn.classList.remove('btn-outline');
    S.plannerStartBtn.innerHTML = 'Start Planner';
  }
  // Wipe button — enabled whenever a slug is active and no run is
  // currently in flight (wiping mid-run would corrupt LangGraph state).
  if (S.plannerWipeBtn) {
    if (S.activeSlug && !running) {
      S.plannerWipeBtn.removeAttribute('disabled');
      S.plannerWipeBtn.setAttribute('title',
        "Delete this framework's planner cache " +
        '(MinIO embeddings + Postgres checkpoints + browser state)');
    } else {
      S.plannerWipeBtn.setAttribute('disabled', 'disabled');
      S.plannerWipeBtn.setAttribute('title', running
        ? 'Cannot wipe while a planner run is in flight.'
        : 'Pick a framework first.');
    }
  }
  // Framework chip — logo(s) + catalog name. Mirrors the Step 2
  // progress framework strip; same `frameworkInfo` source.
  setPlannerFramework(S.activeSlug);
  // Empty-state placeholder — show "pick a framework" when no slug
  // is active, hide the cards/canvas in that case so the user isn't
  // confused by an inert pipeline UI dangling from prior context.
  _toggleStageEmpty('planner', !S.activeSlug);
}

// Toggles the "Pick a framework from the library to view the
// {stage} pipeline" placeholder for a stage panel. Single source of
// truth for graph-wrapper visibility — canvas init MUST NOT touch
// it directly or it races this toggle. On reveal, kicks a Cytoscape
// resize so the canvas picks up freshly-visible container dimensions
// (otherwise the graph latches 0×0 from when it was hidden).
export function _toggleStageEmpty(stage, showEmpty) {
  const emptyEl  = document.getElementById('fw-' + stage + '-empty');
  const graphEl  = document.getElementById('fw-' + stage + '-graph');
  if (!emptyEl) return;
  if (showEmpty) {
    emptyEl.style.display = '';
    if (graphEl) graphEl.style.display = 'none';
  } else {
    emptyEl.style.display = 'none';
    if (graphEl) graphEl.style.display = 'flex';
    // Re-fit Cytoscape now that the wrapper has real dimensions.
    if (stage === 'planner' && S.plannerGraph) _resizePlannerCanvas();
    if (stage === 'synth'   && S.synthGraph)   _resizeSynthCanvas();
  }
}

export function setPlannerFramework(slug) {
  if (!S.plannerFwNameEl || !S.plannerFwLogosEl) return;
  if (!slug) {
    S.plannerFwNameEl.textContent = 'Pick a framework to start.';
    S.plannerFwNameEl.classList.add('fw-planner-fw-name-empty');
    S.plannerFwLogosEl.innerHTML = '';
    S.plannerFwLogosEl.style.display = 'none';
    return;
  }
  const info = S.frameworkInfo[slug] || {name: slug, logos: []};
  S.plannerFwNameEl.textContent = info.name || slug;
  S.plannerFwNameEl.classList.remove('fw-planner-fw-name-empty');
  if (info.logos && info.logos.length) {
    S.plannerFwLogosEl.innerHTML = info.logos.map(u =>
      '<img class="fw-planner-fw-logo" src="' + u + '" alt="">'
    ).join('');
    S.plannerFwLogosEl.style.display = '';
  } else {
    S.plannerFwLogosEl.innerHTML = '';
    S.plannerFwLogosEl.style.display = 'none';
  }
}

export function cardEl(idx) {
  // Cards DOM removed 2026-05-19. Always null in the new graph-only
  // UI; the cards-rendering loops short-circuit cleanly via
  // `if (!c) continue;` while still calling `_renderPlannerGraph`
  // + `_refreshOpenPlannerDrawer` at the tail.
  if (!S.plannerCardsEl) return null;
  return S.plannerCardsEl.querySelector(
    '.fw-planner-card[data-idx="' + idx + '"]');
}

export function resetPlannerCards() {
  S.PLANNER_SUBSTEP_FIELDS.forEach((_, i) => {
    const c = cardEl(i);
    if (!c) return;
    c.classList.remove('running', 'done', 'failed', 'expanded');
    const icon = c.querySelector('.fw-planner-card-icon');
    icon.textContent = '○';
    icon.dataset.status = 'pending';
    c.querySelector('.fw-planner-card-latency').textContent = '';
    c.querySelector('.fw-planner-card-body').innerHTML =
      '<div class="fw-empty">Output will appear here once the substep runs.</div>';
  });
  // Day 2: also reset the Cytoscape canvas + stage pill so a fresh
  // Start Planner click presents a clean visual baseline.
  if (S.plannerGraph) S.plannerGraph.reset();
  _setPlannerStagePill('idle');
}

export function _fieldPresent(values, field) {
  // `field in values` (even when value is null) counts as "this node
  // ran" — some nodes may legitimately write null as their output.
  return values && Object.prototype.hasOwnProperty.call(values, field);
}

// Per-substep custom body renderers. Each returns an HTML string for
// the card body. Keyed by substep idx (matches S.PLANNER_SUBSTEP_FIELDS).
// Substeps without an entry here fall back to formatFieldValue/JSON.
const SUBSTEP_RENDERERS = {
  // corpus_load — KPI-card S.grid + percentile distribution + meta footer.
  // Design follows 2026 dashboard best practices: 4 headline KPI cards
  // (one visual element max per card), then a compact percentile row,
  // then a metadata footer line. Avoids the "Christmas Tree" effect.
  0: function renderCorpusLoad(values) {
    const s = values.corpus_stats || {};
    if (!s.total_files) {
      return '<div class="fw-empty">no corpus stats reported</div>';
    }
    const rate = s.load_ms
      ? Math.round(s.total_files / s.load_ms * 1000)
      : 0;
    const ts = s.ingested_at
      ? new Date(s.ingested_at * 1000).toISOString().replace('T',' ').slice(0, 16) + ' UTC'
      : '—';

    // 4 KPI cards
    const kpi = (label, value, sub) =>
      '<div class="fw-stat-card">' +
        '<div class="fw-stat-card-label">' + escapeHtml(label) + '</div>' +
        '<div class="fw-stat-card-value">' + escapeHtml(value) + '</div>' +
        (sub ? '<div class="fw-stat-card-sub">' + escapeHtml(sub) + '</div>' : '') +
      '</div>';

    const cards =
      kpi('Files',        s.total_files.toLocaleString(), null) +
      kpi('Total bytes',  fmtBytes(s.total_bytes),        null) +
      kpi('Median page',  fmtBytes(s.median_bytes),       null) +
      kpi('Load time',    s.load_ms + ' ms',
                          rate ? rate.toLocaleString() + ' files/s' : null);

    // Compact distribution row — percentiles inline (log-scale not needed
    // when bytes span 3 orders of magnitude; raw numbers tell the story).
    const dist =
      '<div class="fw-stat-dist">' +
        '<div class="fw-stat-dist-title">Page size distribution</div>' +
        '<div class="fw-stat-dist-row">' +
          ['min', 'p10', 'median', 'p90', 'max'].map((k, i) => {
            const val = [s.min_bytes, s.p10_bytes, s.median_bytes,
                         s.p90_bytes, s.max_bytes][i];
            return '<div class="fw-stat-dist-cell">' +
                     '<div class="fw-stat-dist-key">' + k + '</div>' +
                     '<div class="fw-stat-dist-val">' + fmtBytes(val) + '</div>' +
                   '</div>';
          }).join('') +
        '</div>' +
      '</div>';

    // Footer — tier + ingested timestamp
    const foot =
      '<div class="fw-stat-foot">' +
        'Tier <strong>' + escapeHtml(s.tier_kind || '—') + '</strong>' +
        ' · ingested <strong>' + escapeHtml(ts) + '</strong>' +
      '</div>';

    return '<div class="fw-stat-grid">' + cards + '</div>' + dist + foot;
  },

  // embed_corpus — one-shot NIM pass; KPI cards show files / dim /
  // cache_hit / wall_ms / blob path. Cache-hit runs report ~10 ms
  // (just the HEAD + read); cold runs show the full embedding wall.
  1: function renderEmbedCorpus(values) {
    const s = values.embed_stats || {};
    if (!s.files) {
      return '<div class="fw-empty">no embed stats reported</div>';
    }
    const kpi = (label, value, sub) =>
      '<div class="fw-stat-card">' +
        '<div class="fw-stat-card-label">' + escapeHtml(label) + '</div>' +
        '<div class="fw-stat-card-value">' + escapeHtml(value) + '</div>' +
        (sub ? '<div class="fw-stat-card-sub">' + escapeHtml(sub) + '</div>' : '') +
      '</div>';

    const cacheLabel = s.cache_hit ? 'HIT' : 'cold';
    const cacheSub   = s.cache_hit
      ? 'reused stored vectors'
      : 'NIM embedding pass';
    const blobKB = s.blob_bytes
      ? Math.round(s.blob_bytes / 1024).toLocaleString() + ' KB blob'
      : null;

    const cards =
      kpi('Files',     s.files.toLocaleString(), null) +
      kpi('Dimensions', String(s.dim || 0),       'per-doc vector') +
      kpi('Cache',     cacheLabel,                cacheSub) +
      kpi('Wall time', (s.wall_ms || 0) + ' ms',  blobKB);

    const truncatedLine = (s.truncated_count !== undefined && s.truncated_count > 0)
      ? ' · truncated <strong>' + s.truncated_count.toLocaleString() + '</strong>'
      : '';

    const foot =
      '<div class="fw-stat-foot">' +
        'NIM <strong>nvidia/llama-nemotron-embed-1b-v2</strong>' +
        ' · hash <strong>' + escapeHtml(s.manifest_hash || '—') + '</strong>' +
        truncatedLine +
        ' · path <code style="font-family:JetBrains Mono,monospace;font-size:0.72rem">' +
          escapeHtml(s.store_path || '—') + '</code>' +
      '</div>';

    return '<div class="fw-stat-grid">' + cards + '</div>' + foot;
  },

  // off_topic — pure LLM-as-Judge (no cosine cleave). Every doc is
  // routed through the ParetoBandit-driven dd-grader cells: the bandit
  // picks the top-K best deployments by UCB score, calls each via
  // direct litellm, and submits reward signals so future calls learn
  // which deployments are reliable. KPI cards show keep/drop split +
  // bandit telemetry (deployments used + average reward). The verdict
  // sample table shows per-page judgments with the model that answered.
  2: function renderOffTopic(values) {
    const s = values.off_topic_stats || {};
    if (s.kept === undefined && s.dropped === undefined) {
      return '<div class="fw-empty">no off_topic stats reported</div>';
    }
    const kept     = s.kept    || 0;
    const dropped  = s.dropped || 0;
    const total    = s.total   || (kept + dropped);
    const dropPct  = total ? Math.round(dropped / total * 100) : 0;
    const elapsed  = s.elapsed_ms || 0;
    const judged   = s.llm_judged || 0;
    const lkeep    = s.llm_kept || 0;
    const ldrop    = s.llm_dropped || 0;
    const lerr     = s.llm_errors || 0;
    const depUsage = s.deployment_usage || [];

    const kpi = (label, value, sub) =>
      '<div class="fw-stat-card">' +
        '<div class="fw-stat-card-label">' + escapeHtml(label) + '</div>' +
        '<div class="fw-stat-card-value">' + escapeHtml(value) + '</div>' +
        (sub ? '<div class="fw-stat-card-sub">' + escapeHtml(sub) + '</div>' : '') +
      '</div>';

    const topDep = depUsage[0]
      ? (depUsage[0].deployment.split('/').pop() + ' · ' + depUsage[0].calls + ' calls')
      : '—';
    const cards =
      kpi('Kept',    kept.toLocaleString(),    'of ' + total.toLocaleString()) +
      kpi('Dropped', dropped.toLocaleString(), dropPct + '% off-topic') +
      kpi('LLM judged', judged.toLocaleString(),
          '+keep ' + lkeep + ' · -drop ' + ldrop +
          (lerr ? ' · err ' + lerr : '')) +
      kpi('Top deployment', topDep,
          depUsage.length > 1 ? '+' + (depUsage.length - 1) + ' more' : null);

    // LLM verdict table — focused on the NEW telemetry that matters
    // now (which model answered + latency), since cosine margin is
    // no longer a decision input. ALL decisions rendered into a
    // scrollable container (sticky header) so the operator can
    // inspect every per-page verdict without clicking through pages.
    // Sortable columns: click any header to sort asc; click again to
    // toggle desc. Sort state survives re-renders via module scope.
    S.set_lastOffTopicValues(values);
    const decisions = (s.judge_decisions || []).slice();
    // Apply current sort state.
    const sortCol = S._offTopicSort.col;
    const sortDir = S._offTopicSort.dir === 'desc' ? -1 : 1;
    const _key = d => {
      if (sortCol === 'verdict')    return (d.verdict || '');
      if (sortCol === 'deployment') return ((d.deployment || '').split('/').pop() || '');
      if (sortCol === 'latency')    return (d.latency_s !== undefined && d.latency_s !== null) ? d.latency_s : -1;
      if (sortCol === 'page')       return ((d.key || '').split('/').pop() || '');
      return 0;   // 'index' / null: keep original order
    };
    if (sortCol) {
      decisions.sort((a, b) => {
        const ka = _key(a); const kb = _key(b);
        if (ka < kb) return -1 * sortDir;
        if (ka > kb) return 1 * sortDir;
        return 0;
      });
    }
    let table = '';
    if (decisions.length) {
      const rows = decisions.map(d => {
        const keep = d.verdict === 'KEEP';
        const dot = keep
          ? '<span style="color:#2a8b46">●</span>'
          : '<span style="color:var(--error-text)">●</span>';
        const errBadge = d.error
          ? '<span title="' + escapeHtml(d.error) + '" style="margin-left:4px;font-size:0.7rem;color:var(--accent)">!</span>'
          : '';
        const leaf = (d.key || '').split('/').pop();
        const depShort = (d.deployment || '?').split('/').pop();
        const lat = (d.latency_s !== undefined && d.latency_s !== null)
          ? d.latency_s.toFixed(2) + 's' : '—';
        return '<tr>' +
          '<td style="padding:3px 8px 3px 0">' + dot + errBadge + '</td>' +
          '<td style="padding:3px 8px 3px 0;font-size:0.78rem;font-weight:600">' +
            escapeHtml(d.verdict || '—') + '</td>' +
          '<td style="padding:3px 8px 3px 0;font-family:JetBrains Mono,monospace;font-size:0.72rem;color:var(--text-muted)">' +
            escapeHtml(depShort) + '</td>' +
          '<td style="padding:3px 8px 3px 0;font-family:JetBrains Mono,monospace;font-size:0.72rem;color:var(--text-muted)">' +
            lat + '</td>' +
          '<td style="padding:3px 0;font-size:0.78rem;color:var(--text-muted)">' +
            escapeHtml(leaf) + '</td>' +
        '</tr>';
      }).join('');
      const headStyle =
        'position:sticky;top:0;background:var(--card);' +
        'text-align:left;padding:8px 12px;font-size:0.7rem;' +
        'color:var(--text-muted);text-transform:uppercase;' +
        'border-bottom:1px solid var(--border);z-index:2;cursor:pointer;' +
        'user-select:none';
      const _arrow = (col) => {
        if (S._offTopicSort.col !== col) return ' <span style="opacity:0.3">↕</span>';
        return S._offTopicSort.dir === 'desc'
          ? ' <span style="color:var(--text)">↓</span>'
          : ' <span style="color:var(--text)">↑</span>';
      };
      const th = (col, label) =>
        '<th data-sort-col="' + col + '" style="' + headStyle + '">' +
          escapeHtml(label) + _arrow(col) +
        '</th>';
      table =
        '<div class="fw-stat-dist" style="margin-top:14px">' +
          '<div class="fw-stat-dist-title">LLM verdict (' +
            decisions.length + ' decisions, click column headers to sort)</div>' +
          '<div style="max-height:340px;overflow-y:auto;border:1px solid var(--border);border-radius:4px;background:var(--card)">' +
            '<table data-table="off-topic-verdicts" style="width:100%;border-collapse:collapse;font-family:Raleway">' +
              '<thead><tr>' +
                th('index',      'In') +
                th('verdict',    'Verdict') +
                th('deployment', 'Deployment') +
                th('latency',    'Latency') +
                th('page',       'Page') +
              '</tr></thead>' +
              '<tbody>' + rows + '</tbody>' +
            '</table>' +
          '</div>' +
        '</div>';
    }

    // Bandit deployment breakdown — show all that answered with reward avg.
    let depRow = '';
    if (depUsage.length) {
      const drows = depUsage.slice(0, 10).map(d => {
        const r = (d.reward_avg !== undefined && d.reward_avg !== null)
          ? d.reward_avg.toFixed(3) : '—';
        return '<tr>' +
          '<td style="padding:3px 12px 3px 0;font-size:0.78rem">' +
            escapeHtml((d.deployment || '?').split('/').pop()) + '</td>' +
          '<td style="padding:3px 12px 3px 0;font-family:JetBrains Mono,monospace;font-size:0.78rem">' +
            d.calls + ' calls</td>' +
          '<td style="padding:3px 0;font-family:JetBrains Mono,monospace;font-size:0.78rem;color:var(--text-muted)">' +
            'reward avg ' + r + '</td>' +
          '</tr>';
      }).join('');
      depRow =
        '<div class="fw-stat-dist" style="margin-top:14px">' +
          '<div class="fw-stat-dist-title">Bandit deployment usage (top ' +
            Math.min(10, depUsage.length) + ')</div>' +
          '<table style="width:100%;border-collapse:collapse;font-family:Raleway">' +
            '<tbody>' + drows + '</tbody>' +
          '</table>' +
        '</div>';
    }

    const embedModel = s.embed_model || 'nvidia/llama-nemotron-embed-1b-v2';
    const router = s.judge_router || 'bandit/dd-grader';
    const foot =
      '<div class="fw-stat-foot">' +
        'embed <strong>' + escapeHtml(embedModel) + '</strong>' +
        ' · judge <strong>' + escapeHtml(router) + '</strong>' +
        ' · LLM judge ' + judged + ' calls (concurrency ' +
          (s.judge_concurrency || '?') + ')' +
        ' · coherence ' + (s.domain_coherence || 0).toFixed(3) +
        ' · ' + elapsed + ' ms total' +
      '</div>';

    return '<div class="fw-stat-grid">' + cards + '</div>' + table + depRow + foot;
  },

  // 2026-05-27 P4 — LLM-first renderers replacing the legacy
  // cluster/refine/label/reduce path. PLANNER_NODE_ORDER indices
  // 3-6 are now doc_distill/chapter_propose/chapter_assign/
  // chapter_select. plan_write moved 7→8 to match the new 9-slot
  // PLANNER_SUBSTEP_FIELDS ordering.

  // doc_distill — per-doc summary + key terms via parallel rotator.
  // Skip-pass for N ≤ 80 (pass-through to chapter_propose's raw-body
  // path). KPI cards show distill success/failure + cache + wall.
  3: function renderDocDistill(values) {
    const s = values.doc_distill_stats || {};
    if (!s.n_files && !s.skipped) {
      return '<div class="fw-empty">no doc_distill stats reported</div>';
    }
    const kpi = (label, value, sub) =>
      '<div class="fw-stat-card">' +
        '<div class="fw-stat-card-label">' + escapeHtml(label) + '</div>' +
        '<div class="fw-stat-card-value">' + escapeHtml(value) + '</div>' +
        (sub ? '<div class="fw-stat-card-sub">' + escapeHtml(sub) + '</div>' : '') +
      '</div>';

    if (s.skipped === 'pass_through_small_n') {
      const cards =
        kpi('Skipped', 'PASS-THROUGH', 'small-N optimization') +
        kpi('Files',   String(s.n_files || 0), 'no LLM call needed') +
        kpi('Reason',  'N ≤ 80',               'proposer ingests raw bodies') +
        kpi('Wall',    (s.wall_ms || 0) + ' ms', 'cheap');
      return '<div class="fw-stat-grid">' + cards + '</div>' +
        '<div class="fw-stat-foot">' +
          'doc_distill bypassed; chapter_propose reads doc bodies directly. ' +
          'Triggered only when relevant_files ≤ 80 — keeps small corpora fast.' +
        '</div>';
    }
    const n = s.n_files || 0;
    const distilled = s.n_distilled || 0;
    const failed = s.n_failed || 0;
    const successPct = n ? Math.round(distilled / n * 100) : 0;
    const cards =
      kpi('Distilled', distilled.toLocaleString(),
          successPct + '% of ' + n.toLocaleString()) +
      kpi('Failed',    String(failed),
          failed ? 'rate-limited (429) or parse-fail' : 'clean run') +
      kpi('Cache',     s.cache_hit ? 'HIT' : 'cold',
          s.cache_hit ? 'reused stored distillates' : 'fresh distillation') +
      kpi('Wall',      (s.wall_ms || 0) + ' ms',
          n && s.wall_ms ? Math.round(n / s.wall_ms * 1000) + ' docs/s' : null);
    const foot =
      '<div class="fw-stat-foot">' +
        'hash <code style="font-family:JetBrains Mono,monospace;font-size:0.72rem">' +
          escapeHtml((s.manifest_hash || '').slice(0, 12)) + '</code>' +
        ' · per-doc summary + 5 key terms · concurrency 8' +
        (failed
          ? ' · <strong style="color:var(--accent)">' + failed +
              ' docs skipped, downstream still works on ' + distilled + '</strong>'
          : '') +
      '</div>';
    return '<div class="fw-stat-grid">' + cards + '</div>' + foot;
  },

  // chapter_propose — long-context LLM proposes 6-15 candidate chapters
  // from distillates + structural seeds (markdown headings + file-tree
  // namespaces). N=3 parallel samples + USC vote picks the best.
  4: function renderChapterPropose(values) {
    const s = values.propose_stats || {};
    const titles = s.titles || [];
    if (!s.n_proposals && !titles.length) {
      return '<div class="fw-empty">no chapter_propose stats reported</div>';
    }
    const kpi = (label, value, sub) =>
      '<div class="fw-stat-card">' +
        '<div class="fw-stat-card-label">' + escapeHtml(label) + '</div>' +
        '<div class="fw-stat-card-value">' + escapeHtml(value) + '</div>' +
        (sub ? '<div class="fw-stat-card-sub">' + escapeHtml(sub) + '</div>' : '') +
      '</div>';

    const samplesValid = s.n_samples_valid !== undefined
      ? s.n_samples_valid + '/3' : '?';
    const cards =
      kpi('Proposals',   String(s.n_proposals || 0),
          'candidate chapters') +
      kpi('Samples OK',  samplesValid,
          'USC-voted winner: idx ' + (s.chosen_idx ?? '?')) +
      kpi('From docs',   (s.n_files || 0).toLocaleString(),
          'distillates + structural seeds') +
      kpi('Wall',        (s.wall_ms || 0) + ' ms',
          s.cache_hit ? 'cache HIT' : 'cold');

    const titlesList = titles.length
      ? '<div class="fw-stat-dist" style="margin-top:14px">' +
          '<div class="fw-stat-dist-title">Proposed chapters (chosen sample)</div>' +
          '<ol style="margin:8px 0 0;padding:0 0 0 20px;font-size:0.85rem;color:var(--text)">' +
            titles.map(t =>
              '<li style="padding:3px 0">' + escapeHtml(t) + '</li>',
            ).join('') +
          '</ol>' +
        '</div>'
      : '';
    const foot =
      '<div class="fw-stat-foot">' +
        'hash <code style="font-family:JetBrains Mono,monospace;font-size:0.72rem">' +
          escapeHtml((s.manifest_hash || '').slice(0, 12)) + '</code>' +
        ' · long-context LLM call via FGTS-VA · ' +
        '<strong>N=3 samples + USC vote</strong>' +
      '</div>';
    return '<div class="fw-stat-grid">' + cards + '</div>' + titlesList + foot;
  },

  // chapter_assign — per-doc LLM scores membership against each proposal
  // (confidence 0-1, multi-assignment allowed). Concurrent rotator calls;
  // chapter_select consumes the matrix downstream.
  5: function renderChapterAssign(values) {
    const s = values.assign_stats || {};
    if (!s.n_docs) {
      return '<div class="fw-empty">no chapter_assign stats reported</div>';
    }
    const kpi = (label, value, sub) =>
      '<div class="fw-stat-card">' +
        '<div class="fw-stat-card-label">' + escapeHtml(label) + '</div>' +
        '<div class="fw-stat-card-value">' + escapeHtml(value) + '</div>' +
        (sub ? '<div class="fw-stat-card-sub">' + escapeHtml(sub) + '</div>' : '') +
      '</div>';

    const assigned = s.n_assigned || 0;
    const failed = s.n_failed || 0;
    const cards =
      kpi('Assigned',   assigned.toLocaleString(),
          'of ' + (s.n_docs || 0).toLocaleString() + ' docs') +
      kpi('Proposals',  String(s.n_proposals || 0),
          'each doc scored against all') +
      kpi('Failed',     String(failed),
          failed ? 'rate-limited or parse-fail' : 'clean run') +
      kpi('Wall',       (s.wall_ms || 0) + ' ms',
          s.cache_hit ? 'cache HIT' : 'cold');

    // Coverage breakdown — per-proposal count of docs with confidence ≥0.5.
    let cov = '';
    const cc = s.coverage_count || {};
    const proposalsList = (values.propose_stats || {}).titles || [];
    const covEntries = Object.entries(cc)
      .map(([idx, n]) => ({ idx: parseInt(idx), n: parseInt(n) }))
      .sort((a, b) => b.n - a.n);
    if (covEntries.length) {
      const maxN = covEntries[0].n || 1;
      const rows = covEntries.map(e => {
        const title = proposalsList[e.idx] || ('proposal #' + e.idx);
        const pct = Math.max(2, Math.round(e.n / maxN * 100));
        return '<tr style="border-bottom:1px solid var(--border)">' +
          '<td style="padding:6px 8px;font-family:JetBrains Mono,monospace;font-size:0.72rem;color:var(--text-muted);width:40px">' +
            '[' + e.idx + ']' +
          '</td>' +
          '<td style="padding:6px 8px;font-size:0.85rem">' + escapeHtml(title) + '</td>' +
          '<td style="padding:6px 8px;width:80px;text-align:right;font-variant-numeric:tabular-nums">' +
            e.n + ' docs' +
          '</td>' +
          '<td style="padding:6px 8px;width:140px">' +
            '<div style="width:' + pct + '%;height:10px;background:var(--accent,#4a7);border-radius:2px"></div>' +
          '</td>' +
          '</tr>';
      }).join('');
      cov =
        '<div class="fw-stat-dist" style="margin-top:14px">' +
          '<div class="fw-stat-dist-title">Coverage per proposal (docs with confidence ≥0.5)</div>' +
          '<div style="max-height:300px;overflow-y:auto;border:1px solid var(--border);border-radius:4px">' +
            '<table style="width:100%;border-collapse:collapse;font-family:Raleway">' +
              '<tbody>' + rows + '</tbody>' +
            '</table>' +
          '</div>' +
        '</div>';
    }
    const foot =
      '<div class="fw-stat-foot">' +
        'hash <code style="font-family:JetBrains Mono,monospace;font-size:0.72rem">' +
          escapeHtml((s.manifest_hash || '').slice(0, 12)) + '</code>' +
        ' · per-doc rotator call · concurrency 8' +
      '</div>';
    return '<div class="fw-stat-grid">' + cards + '</div>' + cov + foot;
  },

  // chapter_select — pure-algorithm greedy coverage. Picks minimum
  // chapter set covering ≥95% of docs above confidence threshold, then
  // prunes <3-doc chapters unless structurally pinned.
  6: function renderChapterSelect(values) {
    const s = values.select_stats || {};
    if (!s.n_chapters_out && !(s.chapter_titles || []).length) {
      return '<div class="fw-empty">no chapter_select stats reported</div>';
    }
    const kpi = (label, value, sub) =>
      '<div class="fw-stat-card">' +
        '<div class="fw-stat-card-label">' + escapeHtml(label) + '</div>' +
        '<div class="fw-stat-card-value">' + escapeHtml(value) + '</div>' +
        (sub ? '<div class="fw-stat-card-sub">' + escapeHtml(sub) + '</div>' : '') +
      '</div>';

    const out = s.n_chapters_out || 0;
    const propIn = s.n_proposals_in || 0;
    const pruned = s.n_pruned || 0;
    const cov = s.coverage_fraction !== undefined
      ? Math.round(s.coverage_fraction * 100) + '%' : '?';
    const cards =
      kpi('Selected', String(out),
          'from ' + propIn + ' proposals') +
      kpi('Pruned',   String(pruned),
          pruned ? '<3 docs, unpinned' : 'all kept') +
      kpi('Coverage', cov,
          (s.n_assigned_docs || 0) + ' of ' +
          (s.n_total_docs || 0) + ' docs') +
      kpi('Wall',     (s.wall_ms || 0) + ' ms', 'pure algorithm');

    const titles = s.chapter_titles || [];
    const sizes  = s.chapter_sizes  || [];
    let list = '';
    if (titles.length) {
      const maxSize = Math.max(...sizes, 1);
      const rows = titles.map((t, i) => {
        const n = sizes[i] || 0;
        const pct = Math.max(2, Math.round(n / maxSize * 100));
        return '<tr style="border-bottom:1px solid var(--border)">' +
          '<td style="padding:6px 8px;font-family:JetBrains Mono,monospace;font-size:0.72rem;color:var(--text-muted);width:50px;text-align:right">' +
            'ch-' + (i + 1).toString().padStart(2, '0') +
          '</td>' +
          '<td style="padding:6px 8px;font-size:0.9rem;font-weight:500">' +
            escapeHtml(t) +
          '</td>' +
          '<td style="padding:6px 8px;width:80px;text-align:right;font-variant-numeric:tabular-nums">' +
            n + ' docs' +
          '</td>' +
          '<td style="padding:6px 8px;width:140px">' +
            '<div style="width:' + pct + '%;height:10px;background:var(--accent,#4a7);border-radius:2px"></div>' +
          '</td>' +
          '</tr>';
      }).join('');
      list =
        '<div class="fw-stat-dist" style="margin-top:14px">' +
          '<div class="fw-stat-dist-title">Final chapter set (' +
            titles.length + ', balanced)</div>' +
          '<div style="max-height:380px;overflow-y:auto;border:1px solid var(--border);border-radius:4px">' +
            '<table style="width:100%;border-collapse:collapse;font-family:Raleway">' +
              '<tbody>' + rows + '</tbody>' +
            '</table>' +
          '</div>' +
        '</div>';
    }
    const foot =
      '<div class="fw-stat-foot">' +
        'hash <code style="font-family:JetBrains Mono,monospace;font-size:0.72rem">' +
          escapeHtml((s.manifest_hash || '').slice(0, 12)) + '</code>' +
        ' · greedy coverage (≥95% target, &lt;3-doc prune) · no LLM' +
      '</div>';
    return '<div class="fw-stat-grid">' + cards + '</div>' + list + foot;
  },

  // ============================================================
  // LEGACY (deprecated 2026-05-27) — cluster/refine/label/reduce
  // ============================================================
  // These run only under KD_PLANNER_LLM_FIRST=false (emergency
  // fallback). Renderers retained for that case but NOT mapped to
  // any PLANNER_NODE_ORDER index in the LLM-first default UI.
  // Kept under named keys so dead-code-elimination doesn't drop them.
  // To restore for legacy debugging: change UI mapping in state.js.
  _legacy_cluster: function renderCluster(values) {
    const s = values.cluster_stats || {};
    if (!s.n_docs) {
      return '<div class="fw-empty">no cluster stats reported</div>';
    }
    const kpi = (label, value, sub) =>
      '<div class="fw-stat-card">' +
        '<div class="fw-stat-card-label">' + escapeHtml(label) + '</div>' +
        '<div class="fw-stat-card-value">' + escapeHtml(value) + '</div>' +
        (sub ? '<div class="fw-stat-card-sub">' + escapeHtml(sub) + '</div>' : '') +
      '</div>';

    const noisePct = s.n_docs ? Math.round(s.n_noise / s.n_docs * 100) : 0;
    const boundaryPct = s.n_docs ? Math.round(s.n_boundary / s.n_docs * 100) : 0;
    const cards =
      kpi('Clusters', String(s.n_clusters || 0),
          'on ' + (s.n_docs || 0).toLocaleString() + ' docs') +
      kpi('Noise',    String(s.n_noise || 0),
          noisePct + '% unassigned') +
      kpi('Boundary', String(s.n_boundary || 0),
          boundaryPct + '% (max-prob < ' + (s.boundary_floor || 0.5) + ')') +
      kpi('Wall',     (s.wall_ms || 0) + ' ms',
          'UMAP→HDBSCAN');

    // Cluster size distribution — sparkline-style row.
    let dist = '';
    const sizes = s.cluster_sizes || [];
    if (sizes.length) {
      const maxSize = Math.max(...sizes);
      const bars = sizes.map(n => {
        const pct = Math.max(4, Math.round(n / maxSize * 100));
        return '<div title="' + n + ' docs" style="display:inline-block;' +
               'width:' + pct + '%;max-width:48px;height:14px;' +
               'background:var(--accent,#4a7);margin-right:2px;border-radius:2px;' +
               'vertical-align:bottom"></div>';
      }).join('');
      dist =
        '<div class="fw-stat-dist" style="margin-top:14px">' +
          '<div class="fw-stat-dist-title">Cluster sizes (top ' +
            sizes.length + ', descending) — max ' + maxSize + ' docs</div>' +
          '<div style="padding:6px 0">' + bars + '</div>' +
        '</div>';
    }

    const fallback = s.fallback
      ? ' · <strong style="color:var(--accent)">' + escapeHtml(s.fallback) + '</strong>'
      : '';
    const foot =
      '<div class="fw-stat-foot">' +
        'UMAP <strong>n_components=' + (s.umap_dim || '?') + '</strong>' +
        ' · HDBSCAN <strong>min_cluster=' + (s.min_cluster_size || '?') + '</strong>' +
        ' · blob ' + Math.round((s.blob_bytes || 0) / 1024) + ' KB' +
        fallback +
      '</div>';

    return '<div class="fw-stat-grid">' + cards + '</div>' + dist + foot;
  },

  // LEGACY refine — LITA boundary-doc reassignment. Now under
  // KD_PLANNER_LLM_FIRST=false only.
  _legacy_refine: function renderRefine(values) {
    const s = values.refine_stats || {};
    const total = s.n_boundary || 0;
    if (!s.n_docs && !total) {
      return '<div class="fw-empty">no refine stats reported</div>';
    }
    const changed = s.n_changed || 0;
    const nulld   = s.n_null || 0;
    const errs    = s.n_errors || 0;
    const wall    = s.wall_ms || 0;
    const depUsage = s.deployment_usage || [];

    const kpi = (label, value, sub) =>
      '<div class="fw-stat-card">' +
        '<div class="fw-stat-card-label">' + escapeHtml(label) + '</div>' +
        '<div class="fw-stat-card-value">' + escapeHtml(value) + '</div>' +
        (sub ? '<div class="fw-stat-card-sub">' + escapeHtml(sub) + '</div>' : '') +
      '</div>';

    const changePct = total ? Math.round(changed / total * 100) : 0;
    const nullPct = total ? Math.round(nulld / total * 100) : 0;
    const topDep = depUsage[0]
      ? (depUsage[0].deployment.split('/').pop() + ' · ' + depUsage[0].calls + ' calls')
      : '—';
    const cards =
      kpi('Boundary docs', String(total),
          'max_prob < ' + (s.boundary_floor || 0.60)) +
      kpi('Reassigned', String(changed), changePct + '% of boundary') +
      kpi('Sent to noise', String(nulld),
          nullPct + '% null' + (errs ? ' · ' + errs + ' errors' : '')) +
      kpi('Top deployment', topDep,
          depUsage.length > 1 ? '+' + (depUsage.length - 1) + ' more' : null);

    // Bandit deployment breakdown (same pattern as off_topic).
    let depRow = '';
    if (depUsage.length) {
      const drows = depUsage.slice(0, 10).map(d =>
        '<tr>' +
          '<td style="padding:3px 12px 3px 0;font-size:0.78rem">' +
            escapeHtml((d.deployment || '?').split('/').pop()) + '</td>' +
          '<td style="padding:3px 0;font-family:JetBrains Mono,monospace;font-size:0.78rem;color:var(--text-muted)">' +
            d.calls + ' calls</td>' +
        '</tr>'
      ).join('');
      depRow =
        '<div class="fw-stat-dist" style="margin-top:14px">' +
          '<div class="fw-stat-dist-title">Bandit deployment usage (top ' +
            Math.min(10, depUsage.length) + ')</div>' +
          '<table style="width:100%;border-collapse:collapse;font-family:Raleway">' +
            '<tbody>' + drows + '</tbody>' +
          '</table>' +
        '</div>';
    }

    const fallback = s.skipped
      ? ' · <strong style="color:var(--accent)">' + escapeHtml(s.skipped) + '</strong>'
      : '';
    const cache = s.cache_hit ? ' · cache HIT' : '';
    const foot =
      '<div class="fw-stat-foot">' +
        'router <strong>bandit/dd-grader</strong>' +
        ' · top-K ' + (s.top_k || '?') +
        ' · prompt <code style="font-family:JetBrains Mono,monospace;font-size:0.72rem">' +
          escapeHtml(s.prompt_version || '?') + '</code>' +
        ' · ' + wall + ' ms' +
        cache + fallback +
      '</div>';

    return '<div class="fw-stat-grid">' + cards + '</div>' + depRow + foot;
  },

  // LEGACY label — KeyLLM-style cluster naming. Now under
  // KD_PLANNER_LLM_FIRST=false only.
  // Universal Self-Consistency + 2-round sibling-aware re-labeling.
  // KPI cards: clusters / unanimous vs USC-voted / round 2 / wall.
  // Below: full label list as a sortable table so the operator can
  // verify names match cluster contents.
  _legacy_label: function renderLabel(values) {
    const s = values.label_stats || {};
    const n = s.n_clusters || 0;
    const labelsMap = s.labels || {};
    if (!n && Object.keys(labelsMap).length === 0) {
      return '<div class="fw-empty">no label stats reported</div>';
    }
    const unanimous = s.n_unanimous || 0;
    const usc = s.n_usc_voted || 0;
    const round2 = s.n_round2 || 0;
    const errs = s.n_errors || 0;
    const wall = s.wall_ms || 0;

    const kpi = (label, value, sub) =>
      '<div class="fw-stat-card">' +
        '<div class="fw-stat-card-label">' + escapeHtml(label) + '</div>' +
        '<div class="fw-stat-card-value">' + escapeHtml(value) + '</div>' +
        (sub ? '<div class="fw-stat-card-sub">' + escapeHtml(sub) + '</div>' : '') +
      '</div>';

    const unanimousPct = n ? Math.round(unanimous / n * 100) : 0;
    const cards =
      kpi('Clusters labeled', String(n),
          'wall ' + wall + ' ms' + (s.cache_hit ? ' · cache HIT' : '')) +
      kpi('Unanimous', String(unanimous),
          unanimousPct + '% on first try') +
      kpi('USC-voted', String(usc),
          'samples disagreed → LLM picked best') +
      kpi('Round 2 re-labels', String(round2),
          'with sibling-aware context' +
            (errs ? ' · ' + errs + ' errors' : ''));

    // Label table — full list, sorted by cluster ID, scrollable.
    const entries = Object.entries(labelsMap)
      .map(([k, v]) => [parseInt(k, 10), v])
      .sort((a, b) => a[0] - b[0]);
    let table = '';
    if (entries.length) {
      const rows = entries.map(([cid, label]) => {
        const cidLabel = cid < 0 ? 'noise' : '#' + cid;
        const cidColor = cid < 0 ? 'var(--text-muted)' : 'var(--text)';
        return '<tr>' +
          '<td style="padding:4px 12px 4px 8px;font-family:JetBrains Mono,monospace;font-size:0.78rem;color:' +
            cidColor + '">' + escapeHtml(cidLabel) + '</td>' +
          '<td style="padding:4px 0;font-size:0.85rem;font-weight:600">' +
            escapeHtml(label || '?') + '</td>' +
        '</tr>';
      }).join('');
      const headStyle =
        'position:sticky;top:0;background:var(--card);' +
        'text-align:left;padding:8px 12px;font-size:0.7rem;' +
        'color:var(--text-muted);text-transform:uppercase;' +
        'border-bottom:1px solid var(--border);z-index:2';
      table =
        '<div class="fw-stat-dist" style="margin-top:14px">' +
          '<div class="fw-stat-dist-title">Cluster labels (' +
            entries.length + ' total)</div>' +
          '<div style="max-height:340px;overflow-y:auto;border:1px solid var(--border);border-radius:4px;background:var(--card)">' +
            '<table style="width:100%;border-collapse:collapse;font-family:Raleway">' +
              '<thead><tr>' +
                '<th style="' + headStyle + ';padding-left:8px">Cluster</th>' +
                '<th style="' + headStyle + '">Label</th>' +
              '</tr></thead>' +
              '<tbody>' + rows + '</tbody>' +
            '</table>' +
          '</div>' +
        '</div>';
    }

    // Bandit deployment usage
    const depUsage = s.deployment_usage || [];
    let depRow = '';
    if (depUsage.length) {
      const drows = depUsage.slice(0, 10).map(d =>
        '<tr>' +
          '<td style="padding:3px 12px 3px 0;font-size:0.78rem">' +
            escapeHtml((d.deployment || '?').split('/').pop()) + '</td>' +
          '<td style="padding:3px 0;font-family:JetBrains Mono,monospace;font-size:0.78rem;color:var(--text-muted)">' +
            d.calls + ' calls</td>' +
        '</tr>'
      ).join('');
      depRow =
        '<div class="fw-stat-dist" style="margin-top:14px">' +
          '<div class="fw-stat-dist-title">Bandit deployment usage</div>' +
          '<table style="width:100%;border-collapse:collapse;font-family:Raleway">' +
            '<tbody>' + drows + '</tbody>' +
          '</table>' +
        '</div>';
    }

    const foot =
      '<div class="fw-stat-foot">' +
        'router <strong>bandit/dd-grader</strong>' +
        ' · N=' + (s.n_samples || '?') + ' samples + USC vote' +
        ' · prompt <code style="font-family:JetBrains Mono,monospace;font-size:0.72rem">' +
          escapeHtml(s.prompt_version || '?') + '</code>' +
      '</div>';

    return '<div class="fw-stat-grid">' + cards + '</div>' + table + depRow + foot;
  },

  // LEGACY reduce — 4-12 chapter outline merged from labeled clusters.
  // KPI cards: chapters / input clusters / repairs / wall_ms.
  // Below: the full ordered outline with title + description + member
  // cluster IDs. This is the FINAL human-facing artifact of the
  // planner pipeline.
  _legacy_reduce: function renderReduce(values) {
    const s = values.reduce_stats || {};
    const outline = s.outline || {};
    const chapters = outline.chapters || [];
    if (!chapters.length) {
      return '<div class="fw-empty">no reduce stats reported</div>';
    }

    const kpi = (label, value, sub) =>
      '<div class="fw-stat-card">' +
        '<div class="fw-stat-card-label">' + escapeHtml(label) + '</div>' +
        '<div class="fw-stat-card-value">' + escapeHtml(value) + '</div>' +
        (sub ? '<div class="fw-stat-card-sub">' + escapeHtml(sub) + '</div>' : '') +
      '</div>';

    const cards =
      kpi('Chapters', String(chapters.length),
          'from ' + (s.n_clusters_in || 0) + ' clusters') +
      kpi('Samples', String(s.n_samples || '?'),
          'N=3 + USC vote + self-refine') +
      kpi('Coverage repairs', String(s.n_repairs || 0),
          s.forced_repair ? 'forced fallback applied' : 'auto-fixed') +
      kpi('Wall', (s.wall_ms || 0) + ' ms',
          s.cache_hit ? 'cache HIT' : 'cold');

    // Full ordered outline — chapter cards
    const sortedChapters = chapters.slice().sort(
      (a, b) => (a.order || 0) - (b.order || 0),
    );
    const chapterRows = sortedChapters.map(ch => {
      const memberIds = (ch.member_cluster_ids || []).slice().sort((a,b) => a-b);
      const memberCidStr = memberIds.length
        ? memberIds.map(c => '#' + c).join(' ')
        : '<em style="color:var(--text-muted)">no clusters</em>';
      return '<tr style="border-bottom:1px solid var(--border)">' +
        '<td style="padding:8px 12px 8px 0;font-family:JetBrains Mono,monospace;font-size:0.78rem;color:var(--text-muted);vertical-align:top">' +
          (ch.order || '?') + '</td>' +
        '<td style="padding:8px 12px 8px 0;vertical-align:top;width:30%">' +
          '<div style="font-weight:700;font-size:0.95rem">' +
            escapeHtml(ch.title || '?') + '</div>' +
          '<div style="font-family:JetBrains Mono,monospace;font-size:0.7rem;color:var(--text-muted);margin-top:4px">' +
            memberCidStr + '</div>' +
        '</td>' +
        '<td style="padding:8px 0;vertical-align:top;font-size:0.85rem;color:var(--text-muted)">' +
          escapeHtml(ch.description || '') +
        '</td>' +
        '</tr>';
    }).join('');
    const headStyle =
      'position:sticky;top:0;background:var(--card);' +
      'text-align:left;padding:10px 12px;font-size:0.7rem;' +
      'color:var(--text-muted);text-transform:uppercase;' +
      'border-bottom:1px solid var(--border);z-index:2';
    const table =
      '<div class="fw-stat-dist" style="margin-top:14px">' +
        '<div class="fw-stat-dist-title">Chapter outline (' +
          sortedChapters.length + ' chapters, ordered)</div>' +
        '<div style="max-height:400px;overflow-y:auto;border:1px solid var(--border);border-radius:4px;background:var(--card)">' +
          '<table style="width:100%;border-collapse:collapse;font-family:Raleway">' +
            '<thead><tr>' +
              '<th style="' + headStyle + ';padding-left:8px;width:40px">#</th>' +
              '<th style="' + headStyle + '">Title</th>' +
              '<th style="' + headStyle + '">Description</th>' +
            '</tr></thead>' +
            '<tbody>' + chapterRows + '</tbody>' +
          '</table>' +
        '</div>' +
      '</div>';

    const fallback = s.skipped
      ? ' · <strong style="color:var(--accent)">' + escapeHtml(s.skipped) + '</strong>'
      : '';
    const errorPart = s.error
      ? ' · <strong style="color:var(--error-text)">' + escapeHtml(s.error) + '</strong>'
      : '';
    const foot =
      '<div class="fw-stat-foot">' +
        'router <strong>bandit/dd-grader</strong>' +
        ' · single-call + USC + self-refine' +
        ' · prompt <code style="font-family:JetBrains Mono,monospace;font-size:0.72rem">' +
          escapeHtml(s.prompt_version || '?') + '</code>' +
        fallback + errorPart +
      '</div>';

    return '<div class="fw-stat-grid">' + cards + '</div>' + table + foot;
  },

  // plan_write — consumer-facing final plan with hydrated `sources`.
  // KPI cards: chapters / sources / unassigned / wall_ms.
  // Below: the final outline with title, description, per-chapter
  // source count + first-N source paths (so a developer can sanity-
  // check which docs ended up where). Last card of the pipeline.
  // 2026-05-27 P4 — re-keyed 7 → 8 to match the LLM-first 9-slot
  // PLANNER_SUBSTEP_FIELDS (index 7 is now order_chapters, which
  // renders via KPI-only on the graph; no rich drawer panel).
  8: function renderPlanWrite(values) {
    const s = values.plan_write_stats || {};
    const plan = s.plan || {};
    const chapters = (plan.chapters || []).slice();
    if (!chapters.length) {
      // Two cases: (a) plan_path missing entirely — node hasn't run
      // yet; (b) plan_path set but stats not yet refreshed from the
      // checkpoint commit (race window between SSE `done` and the
      // /state poll catching the latest checkpoint). Show a neutral
      // running-style message instead of the error-looking
      // placeholders previously rendered.
      if (values.plan_path) {
        return '<div class="fw-empty">plan persisted at <code style="font-family:JetBrains Mono,monospace">' +
          escapeHtml(values.plan_path) +
          '</code> — refreshing chapter details…</div>';
      }
      return '<div class="fw-empty">waiting for plan_write to commit…</div>';
    }

    const kpi = (label, value, sub) =>
      '<div class="fw-stat-card">' +
        '<div class="fw-stat-card-label">' + escapeHtml(label) + '</div>' +
        '<div class="fw-stat-card-value">' + escapeHtml(value) + '</div>' +
        (sub ? '<div class="fw-stat-card-sub">' + escapeHtml(sub) + '</div>' : '') +
      '</div>';

    const nSources = s.n_sources || (plan.stats || {}).n_sources || 0;
    const nUnassigned = s.n_unassigned || (plan.stats || {}).n_unassigned || 0;
    const nDropped = s.n_dropped || (plan.stats || {}).n_dropped || 0;
    const corpusN = (plan.provenance || {}).corpus_doc_count || 0;
    const cards =
      kpi('Chapters', String(chapters.length),
          'final ordered outline') +
      kpi('Sources',  String(nSources),
          corpusN ? 'of ' + corpusN + ' corpus docs' : 'hydrated from refine') +
      kpi('Unassigned', String(nUnassigned),
          nDropped ? nDropped + ' empty chapters dropped' : 'none dropped') +
      kpi('Wall', (s.wall_ms || 0) + ' ms',
          s.cache_hit ? 'cache HIT' : 'cold');

    const sortedChapters = chapters.slice().sort(
      (a, b) => (a.order || 0) - (b.order || 0),
    );
    const headStyle =
      'position:sticky;top:0;background:var(--card);' +
      'text-align:left;padding:10px 12px;font-size:0.7rem;' +
      'color:var(--text-muted);text-transform:uppercase;' +
      'border-bottom:1px solid var(--border);z-index:2';
    const chapterRows = sortedChapters.map(ch => {
      const srcs = (ch.sources || []).slice();
      const previewSrcs = srcs.slice(0, 4).map(p => {
        const tail = p.split('/').slice(-2).join('/');
        return '<div style="font-family:JetBrains Mono,monospace;font-size:0.7rem;color:var(--text-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%">' +
          escapeHtml(tail) + '</div>';
      }).join('');
      const moreSrcs = srcs.length > 4
        ? '<div style="font-family:JetBrains Mono,monospace;font-size:0.7rem;color:var(--text-muted);font-style:italic">… ' +
            (srcs.length - 4) + ' more</div>'
        : '';
      return '<tr style="border-bottom:1px solid var(--border)">' +
        '<td style="padding:8px 12px 8px 0;font-family:JetBrains Mono,monospace;font-size:0.78rem;color:var(--text-muted);vertical-align:top">' +
          (ch.order || '?') + '</td>' +
        '<td style="padding:8px 12px 8px 0;vertical-align:top;width:32%">' +
          '<div style="font-weight:700;font-size:0.95rem">' +
            escapeHtml(ch.title || '?') + '</div>' +
          '<div style="font-family:JetBrains Mono,monospace;font-size:0.7rem;color:var(--text-muted);margin-top:4px">' +
            escapeHtml(ch.id || '') + ' · ' + (ch.n_sources || srcs.length) + ' sources' +
          '</div>' +
        '</td>' +
        '<td style="padding:8px 12px 8px 0;vertical-align:top;font-size:0.85rem;color:var(--text-muted)">' +
          escapeHtml(ch.description || '') +
        '</td>' +
        '<td style="padding:8px 0;vertical-align:top">' +
          previewSrcs + moreSrcs +
        '</td>' +
        '</tr>';
    }).join('');
    const table =
      '<div class="fw-stat-dist" style="margin-top:14px">' +
        '<div class="fw-stat-dist-title">Final plan (' +
          sortedChapters.length + ' chapters, hydrated sources)</div>' +
        '<div style="max-height:460px;overflow-y:auto;border:1px solid var(--border);border-radius:4px;background:var(--card)">' +
          '<table style="width:100%;border-collapse:collapse;font-family:Raleway">' +
            '<thead><tr>' +
              '<th style="' + headStyle + ';padding-left:8px;width:40px">#</th>' +
              '<th style="' + headStyle + '">Chapter</th>' +
              '<th style="' + headStyle + '">Description</th>' +
              '<th style="' + headStyle + ';width:34%">Sources (sample)</th>' +
            '</tr></thead>' +
            '<tbody>' + chapterRows + '</tbody>' +
          '</table>' +
        '</div>' +
      '</div>';

    const prov = plan.provenance || {};
    const provLine =
      '<div class="fw-stat-foot">' +
        'wrote <code style="font-family:JetBrains Mono,monospace;font-size:0.72rem">' +
          escapeHtml(s.store_path || values.plan_path || '') + '</code>' +
        ' · hash <code style="font-family:JetBrains Mono,monospace;font-size:0.72rem">' +
          escapeHtml((s.manifest_hash || plan.manifest_hash || '').slice(0, 12)) + '</code>' +
        ' · upstream prompts ' +
        escapeHtml(JSON.stringify(prov.prompt_versions || {})) +
      '</div>';

    return '<div class="fw-stat-grid">' + cards + '</div>' + table + provLine;
  },
};

export function renderPlannerCards(values) {
  // values = the latest checkpoint's accumulated state
  let doneCount = 0;
  for (let i = 0; i < S.PLANNER_SUBSTEP_FIELDS.length; i++) {
    const field = S.PLANNER_SUBSTEP_FIELDS[i];
    const c = cardEl(i);
    const present = _fieldPresent(values, field);
    if (!c) {
      // Cards DOM removed 2026-05-19. Still count done so the
      // tail-end `_renderPlannerGraph` has accurate progress.
      if (present) doneCount++;
      continue;
    }
    const icon = c.querySelector('.fw-planner-card-icon');
    const body = c.querySelector('.fw-planner-card-body');
    // Substep name = the PLANNER_SUBSTEPS index → graph node name.
    // Lookup the implementation flag for visual treatment.
    const cardData = c.dataset.substep || '';
    const isImplemented = S.plannerImplemented.has(cardData);
    if (present) {
      c.classList.add('done');
      c.classList.remove('running', 'failed', 'future');
      icon.textContent = '●'; icon.dataset.status = 'done';
      const renderer = SUBSTEP_RENDERERS[i];
      if (renderer) {
        body.innerHTML = renderer(values);
      } else {
        const v = values[field];
        body.innerHTML = '<pre>' + escapeHtml(formatFieldValue(v)) + '</pre>';
      }
      doneCount++;
    } else if (!isImplemented) {
      // Substep stub — not wired into the runtime graph. Render as
      // "future" so the user sees it's a planned step, not a failure.
      c.classList.add('future');
      c.classList.remove('running', 'done', 'failed');
      icon.textContent = '⏳'; icon.dataset.status = 'future';
      body.innerHTML =
        '<div class="fw-empty">Substep not yet implemented — will be ' +
        'wired into the graph as its real logic lands.</div>';
    } else if (i === doneCount && S.plannerThreadId !== null) {
      // First not-done IMPLEMENTED card while polling = currently running
      c.classList.add('running');
      c.classList.remove('done', 'failed', 'future');
      icon.textContent = '◐'; icon.dataset.status = 'running';
    } else {
      c.classList.remove('running', 'done', 'failed', 'future');
      icon.textContent = '○'; icon.dataset.status = 'pending';
    }
  }
  // Day 2: mirror the same state into the Cytoscape canvas. No-op
  // when ?ui=cards (S.plannerGraph is null). Drives node colors,
  // KPI badges, and the top-of-stage status pill (which now also
  // carries the N/8 progress count while working).
  _renderPlannerGraph(values);
  // Drawer live-refresh: if the user has the drawer open for a
  // planner node, re-hydrate its Results panel with the latest
  // SUBSTEP_RENDERERS output. Lets the drawer evolve in lockstep
  // with the card body without forcing the user to re-click.
  _refreshOpenPlannerDrawer(values);
}

export function markPlannerFailed(message) {
  // Find the first card still running (or first pending) and flag it.
  let failedNodeId = null;
  for (let i = 0; i < S.PLANNER_SUBSTEP_FIELDS.length; i++) {
    const c = cardEl(i);
    if (!c) continue;
    if (c.classList.contains('running') ||
        (!c.classList.contains('done') && !c.classList.contains('failed'))) {
      c.classList.remove('running');
      c.classList.add('failed', 'expanded');
      const icon = c.querySelector('.fw-planner-card-icon');
      icon.textContent = '✕';
      icon.dataset.status = 'failed';
      c.querySelector('.fw-planner-card-body').innerHTML =
        '<div class="fw-planner-error">' + escapeHtml(message) + '</div>';
      failedNodeId = S.PLANNER_NODE_ORDER[i];
      break;
    }
  }
  // Day 2: mirror to canvas + flip stage pill to failed.
  if (S.plannerGraph && failedNodeId) {
    S.plannerGraph.setStatus(failedNodeId, 'failed');
  }
  _setPlannerStagePill('failed');
}

// formatFieldValue + escapeHtml are imported from utils.js (shared).

export async function pollPlanner(threadId) {
  S.setPlannerPollAbort(false);
  while (!S.plannerPollAbort && S.plannerThreadId === threadId) {
    try {
      // thread_id has slashes (docs-distiller/{slug}/{uuid}). Don't
      // encode — the FastAPI `:path` converter accepts slashes; the
      // smoke test in /history confirmed unencoded paths round-trip.
      const r = await fetch(
        S.API + '/planner/debug/graph/' + threadId + '/state');
      if (r.status === 404) { await sleep(700); continue; }
      if (!r.ok) { await sleep(1500); continue; }
      const data = await r.json();
      const values = data.values || {};
      renderPlannerCards(values);
      if (values.status === 'done') {
        S.setPlannerThreadId(null);
        refreshPlannerStartState();
        return;
      }
      if (values.status === 'failed') {
        markPlannerFailed(values.error || 'Planner failed.');
        S.setPlannerThreadId(null);
        refreshPlannerStartState();
        return;
      }
    } catch (e) { /* transient — retry */ }
    await sleep(1000);
  }
}

export function _genPlannerThreadId(slug) {
  // Client-side UUID v4 — uses crypto.randomUUID where available,
  // falls back to a sufficient-quality polyfill for older browsers.
  const uuid = (typeof crypto !== 'undefined' && crypto.randomUUID)
    ? crypto.randomUUID()
    : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
        const r = Math.random() * 16 | 0;
        return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
      });
  return 'docs-distiller/' + slug + '/' + uuid;
}

// Live progress text per substep card (populated by SSE events).
// Keyed by step name (matches the node names emitted server-side).
export function _liveProgressEl(stepName, idx) {
  const c = cardEl(idx);
  if (!c) return null;
  const body = c.querySelector('.fw-planner-card-body');
  if (!body) return null;
  let el = body.querySelector('.fw-planner-card-live');
  if (!el) {
    el = document.createElement('div');
    el.className = 'fw-planner-card-live';
    el.style.cssText =
      'font-family:JetBrains Mono,monospace;font-size:0.78rem;' +
      'color:var(--text-muted);padding:8px 12px;border-top:1px dashed var(--border);' +
      'margin-top:8px';
    body.appendChild(el);
  }
  return el;
}

export function _stepIdx(stepName) {
  return S.PLANNER_SUBSTEP_FIELDS.findIndex((_, i) =>
    cardEl(i)?.dataset.substep === stepName);
}

export function _markCardRunning(stepName) {
  const idx = _stepIdx(stepName);
  if (idx < 0) return;
  // Graph-only UI (cards DOM removed 2026-05-19): flip the Cytoscape
  // node to 'running' FIRST, unconditionally. This is the sole live
  // "Working" indicator now — the burgundy border + active-edge
  // animation kick in immediately on the SSE `start` event (without
  // waiting for the next /state refresh). Must run BEFORE the legacy
  // card guard below, which early-returns when no card element exists
  // (always, post-2026-05-19) and previously suppressed this update.
  if (S.plannerGraph) {
    // Don't downgrade an already-finished node. SSE snapshot replay on
    // page refresh re-delivers old `start` events for done steps; without
    // this guard they'd flip a completed node back to 'running' (the
    // graph-only equivalent of the old card `.done` guard).
    let cur = null;
    try { cur = S.plannerGraph.cy.getElementById(stepName).data('status'); }
    catch (_) {}
    if (cur !== 'done' && cur !== 'failed') {
      S.plannerGraph.setStatus(stepName, 'running');
      // Pill carries the in-flight step's ordinal so the user sees a
      // crisp "Working · 3/8" without waiting for the next state poll.
      const stepIdx = S.PLANNER_NODE_ORDER.indexOf(stepName);
      const implCount = S.PLANNER_NODE_ORDER.filter(n => S.plannerImplemented.has(n)).length;
      const progress = (stepIdx >= 0 && implCount)
        ? (stepIdx + '/' + implCount) : null;
      _setPlannerStagePill('working',
        progress ? 'Working · ' + progress : null);
    }
  }
  // Legacy card path — no-op in the graph-only UI (cardEl is null), but
  // kept so a future cards-mode reintroduction still works.
  const c = cardEl(idx);
  if (!c) return;
  // Don't downgrade an already-completed card. Without this guard, SSE
  // snapshot replay during page-refresh recovery would re-process the
  // original `start` event for an already-done step and flip its
  // spinner back to running, hiding the KPI grid behind a stale
  // "filtering N files…" live-progress line.
  if (c.classList.contains('done')) return;
  c.classList.add('running');
  c.classList.remove('failed', 'future');
  const icon = c.querySelector('.fw-planner-card-icon');
  if (icon) { icon.textContent = '◐'; icon.dataset.status = 'running'; }
  const body = c.querySelector('.fw-planner-card-body');
  if (body && body.querySelector('.fw-empty')) {
    body.innerHTML = '';
  }
}

export function _renderLiveProgress(stepName, ev) {
  const idx = _stepIdx(stepName);
  if (idx < 0) return;
  const c = cardEl(idx);
  // Same reason as _markCardRunning: skip live-text rewrites for
  // cards already marked done by the LangGraph state snapshot.
  if (c && c.classList.contains('done')) return;
  const el = _liveProgressEl(stepName, idx);
  if (!el) return;
  let text = '';
  if (stepName === 'corpus_load') {
    if (ev.kind === 'start')      text = '· reading manifest…';
    else if (ev.kind === 'done')  text = '✓ ' + (ev.files||0).toLocaleString() + ' files, ' + ((ev.total_bytes||0)/1024|0) + ' KB';
  } else if (stepName === 'embed_corpus') {
    if (ev.kind === 'start')             text = '· starting NIM embed (' + (ev.files||0) + ' files)…';
    else if (ev.kind === 'chunks_prepared') text = '· ' + (ev.chunks_total||0).toLocaleString() + ' chunks prepared (' + (ev.docs_chunked||0) + '/' + (ev.docs_total||0) + ' docs split)';
    else if (ev.kind === 'batch')        text = '· embedding chunk ' + (ev.chunks_done||0).toLocaleString() + ' / ' + (ev.chunks_total||0).toLocaleString();
    else if (ev.kind === 'done')         text = '✓ ' + (ev.files||0).toLocaleString() + ' vectors @ ' + (ev.dim||'?') + '-D (' + (ev.cache_hit ? 'cache hit' : ((ev.wall_ms||0) + ' ms cold') ) + ')';
  } else if (stepName === 'off_topic') {
    if (ev.kind === 'start')              text = '· filtering ' + (ev.files||0).toLocaleString() + ' files…';
    else if (ev.kind === 'anchors_embedded') text = '· anchors embedded (pos + neg) · LLM-as-Judge routing via ParetoBandit/dd-grader';
    else if (ev.kind === 'llm_progress')  text = '· LLM judged ' + (ev.judged||0).toLocaleString() + ' / ' + (ev.total||0).toLocaleString() + ' (keep ' + (ev.llm_keep||0) + ', drop ' + (ev.llm_drop||0) + (ev.llm_err ? ', err ' + ev.llm_err : '') + ')';
    else if (ev.kind === 'done')          text = '✓ kept ' + (ev.kept||0).toLocaleString() + '/' + (ev.total||0).toLocaleString() + ' (' + (ev.wall_ms||0) + ' ms)';
  } else if (stepName === 'cluster') {
    if (ev.kind === 'start')              text = '· clustering ' + (ev.n_docs||0).toLocaleString() + ' docs…';
    else if (ev.kind === 'umap_start')    text = '· UMAP ' + (ev.in_dim||'?') + '-D → ' + (ev.out_dim||'?') + '-D (cosine metric, ' + (ev.n_docs||0) + ' docs)';
    else if (ev.kind === 'hdbscan_start') text = '· HDBSCAN density clustering on ' + (ev.reduced_dim||'?') + '-D embeddings';
    else if (ev.kind === 'done')          text = '✓ ' + (ev.n_clusters||0) + ' clusters · ' + (ev.n_noise||0) + ' noise · ' + (ev.n_boundary||0) + ' boundary (' + (ev.wall_ms||0) + ' ms)';
  } else if (stepName === 'refine') {
    if (ev.kind === 'start')              text = '· reading cluster state…';
    else if (ev.kind === 'context_prepared') text = '· prepared c-TF-IDF context for ' + (ev.n_clusters||0) + ' clusters; LLM-judging ' + (ev.n_boundary||0) + ' boundary docs…';
    else if (ev.kind === 'llm_progress')  text = '· LLM judged ' + (ev.judged||0).toLocaleString() + ' / ' + (ev.total||0).toLocaleString() + ' (reassigned ' + (ev.changed||0) + ', null ' + (ev.null||0) + (ev.err ? ', err ' + ev.err : '') + ')';
    else if (ev.kind === 'done')          text = '✓ ' + (ev.n_changed||0) + ' reassigned · ' + (ev.n_null||0) + ' sent to noise (' + (ev.wall_ms||0) + ' ms)';
  } else if (stepName === 'label') {
    if (ev.kind === 'start')                 text = '· preparing label context…';
    else if (ev.kind === 'context_prepared') text = '· c-TF-IDF + rep-doc context ready for ' + (ev.n_clusters||0) + ' clusters; round 1 USC labeling…';
    else if (ev.kind === 'llm_progress')     text = '· ' + (ev.round || 'round1') + ': labeled ' + (ev.judged||0) + ' / ' + (ev.total||0) + ' (unanimous ' + (ev.unanimous||0) + ', USC ' + (ev.usc||0) + (ev.err ? ', err ' + ev.err : '') + ')';
    else if (ev.kind === 'round2_start')     text = '· round 2: re-labeling ' + (ev.n_round2||0) + ' USC-split clusters with sibling context…';
    else if (ev.kind === 'done')             text = '✓ ' + (ev.n_clusters||0) + ' clusters named' + (ev.n_round2 ? ' (' + ev.n_round2 + ' via round 2)' : '') + ' · ' + (ev.wall_ms||0) + ' ms';
  } else if (stepName === 'reduce') {
    if (ev.kind === 'start')                 text = '· reading cluster + refine + label artifacts…';
    else if (ev.kind === 'context_prepared') text = '· prepared context for ' + (ev.n_clusters_in||0) + ' input clusters; generating N=3 outline samples…';
    else if (ev.kind === 'samples_generated') text = '· ' + (ev.n_samples||0) + ' samples generated; USC voting…';
    else if (ev.kind === 'usc_voted')        text = '· USC vote done; running self-refine pass (feedback → refine)…';
    else if (ev.kind === 'refined')          text = '· self-refine done; validating coverage…';
    else if (ev.kind === 'repair_attempt')   text = '· repair attempt ' + (ev.attempt||0) + ': missing ' + (ev.missing||0) + ', dup ' + (ev.duplicate||0) + ', unknown ' + (ev.unknown||0);
    else if (ev.kind === 'done')             text = '✓ ' + (ev.n_chapters||0) + ' chapters' + (ev.n_repairs ? ' (' + ev.n_repairs + ' repair' + (ev.n_repairs > 1 ? 's' : '') + ')' : '') + (ev.forced_repair ? ' [forced]' : '') + ' · ' + (ev.wall_ms||0) + ' ms';
  } else if (stepName === 'doc_distill') {
    if (ev.kind === 'start')           text = '· distilling ' + (ev.n_files||0) + ' docs… (skip≤' + (ev.pass_through_threshold||80) + ')';
    else if (ev.kind === 'done' && ev.skipped) text = '✓ skipped (small N pass-through, ' + (ev.wall_ms||0) + ' ms)';
    else if (ev.kind === 'done')       text = '✓ ' + (ev.n_distilled||0) + ' distilled' + (ev.n_failed ? ' · ' + ev.n_failed + ' failed' : '') + ' (' + (ev.cache_hit ? 'cache hit' : ((ev.wall_ms||0) + ' ms')) + ')';
  } else if (stepName === 'chapter_propose') {
    if (ev.kind === 'start')           text = '· loading distillates + extracting structural seeds…';
    else if (ev.kind === 'sampling')   text = '· firing N=' + (ev.n_samples||3) + ' proposals (' + (ev.n_heading_seeds||0) + ' heading + ' + (ev.n_namespace_seeds||0) + ' namespace seeds)…';
    else if (ev.kind === 'done')       text = '✓ ' + (ev.n_proposals||0) + ' chapters proposed' + (ev.titles ? ': ' + (ev.titles||[]).slice(0,3).join(', ') + (ev.titles.length>3 ? '…' : '') : '') + ' (' + (ev.cache_hit ? 'cache hit' : ((ev.wall_ms||0) + ' ms')) + ')';
  } else if (stepName === 'chapter_assign') {
    if (ev.kind === 'start')           text = '· scoring ' + (ev.n_docs||0) + ' docs against ' + (ev.n_proposals||0) + ' chapter proposals…';
    else if (ev.kind === 'done')       text = '✓ ' + (ev.n_assigned||0) + ' assigned' + (ev.n_failed ? ' · ' + ev.n_failed + ' failed' : '') + ' (' + (ev.cache_hit ? 'cache hit' : ((ev.wall_ms||0) + ' ms')) + ')';
  } else if (stepName === 'chapter_select') {
    if (ev.kind === 'start')           text = '· greedy coverage over ' + (ev.n_proposals||0) + ' proposals · ' + (ev.n_docs||0) + ' docs' + (ev.n_pinned ? ' · ' + ev.n_pinned + ' pinned' : '') + '…';
    else if (ev.kind === 'done')       text = '✓ ' + (ev.n_chapters||0) + ' chapters selected' + (ev.n_pruned ? ' · ' + ev.n_pruned + ' pruned' : '') + ' · ' + Math.round((ev.coverage||0)*100) + '% coverage (' + (ev.wall_ms||0) + ' ms)';
  } else if (stepName === 'plan_write') {
    if (ev.kind === 'start')           text = '· hashing inputs… (manifest ' + ((ev.manifest_hash||'').slice(0,8)) + ')';
    else if (ev.kind === 'loaded')     text = '· loaded ' + (ev.n_chapters_in||0) + ' chapters · ' + (ev.n_clusters||0) + ' clusters · ' + (ev.n_docs||0) + ' docs';
    else if (ev.kind === 'sanitized')  text = '· sanitized · ' + (ev.n_chapters||0) + ' chapters · ' + (ev.n_sources||0) + ' sources' + (ev.n_dropped ? ' · ' + ev.n_dropped + ' empty dropped' : '') + (ev.n_unassigned ? ' · ' + ev.n_unassigned + ' unassigned' : '');
    else if (ev.kind === 'done')       text = '✓ ' + (ev.n_chapters||0) + ' chapters · ' + (ev.n_sources||0) + ' sources persisted (' + ((ev.cache_hit) ? 'cache hit' : ((ev.wall_ms||0) + ' ms')) + ')';
  }
  if (text) el.textContent = text;
}

// Race-tolerant state fetch. The LangGraph checkpoint commit lands a
// tick AFTER the node's `done` event fires on the SSE channel, so a
// naive fetch right after `done` may see stale state. When the caller
// knows which field is expected to have just appeared, we retry with
// backoff until it's present (or we exhaust attempts).
export async function _refreshCardsFromState(threadId, expectedField) {
  const maxAttempts = expectedField ? 6 : 1;
  for (let i = 0; i < maxAttempts; i++) {
    try {
      const r = await fetch(S.API + '/planner/debug/graph/' + threadId + '/state');
      if (r.ok) {
        const data = await r.json();
        const values = data.values || {};
        if (!expectedField || _fieldPresent(values, expectedField)) {
          renderPlannerCards(values);
          return;
        }
      }
    } catch (e) { /* transient */ }
    await sleep(250 + 150 * i);   // ~250ms / 400 / 550 / 700 / 850 / 1000
  }
}

// Mapping: SSE step name → the state field that becomes present once
// that node's checkpoint is committed. Used by the retry-fetch above
// so we wait for the previous node's commit before re-rendering.

// Wall-clock ms at which the current planner run started — set on an
// explicit Start (Date.now()) or recovered from the live-run registry's
// `started_ts` on refresh, so the navbar timer continues from the real run
// start (not from 0) when reconnecting. 0 = no known run.
let _plannerRunStartMs = 0;
export function _setPlannerRunStartMs(ms) { _plannerRunStartMs = ms || 0; }

export async function pollPlannerState(threadId) {
  // 2026-canonical pattern: Server-Sent Events instead of HTTP polling.
  // Backend pub/sub channel (Redis) is bridged by the FastAPI
  // /planner/{thread_id}/events endpoint which streams text/event-stream.
  // Each event carries {step, kind, ts, ...}; we route to the matching
  // substep card and render either a live progress sub-line or
  // (on "done") fetch the full state and let renderPlannerCards
  // redraw the card with KPI grids.
  //
  // Name kept for back-compat with existing callers (startPlanner).
  const url = S.API + '/planner/' + threadId + '/events';
  let es;
  try {
    es = new EventSource(url);
  } catch (e) {
    markPlannerFailed('EventSource open failed: ' + String(e));
    S.setPlannerThreadId(null);
    refreshPlannerStartState();
    return;
  }
  es.onmessage = async (msg) => {
    if (S.plannerThreadId !== threadId) {
      try { es.close(); } catch (_) {}
      return;
    }
    let ev;
    try { ev = JSON.parse(msg.data); } catch (_) { return; }
    // Only "fresh" events (within the last ~20 seconds) count for
    // orphan-detect. Without this, the Redis snapshot replay of an
    // old run's events (e.g. a previous cluster start that errored)
    // would suppress the auto-/resume needed to actually run the
    // step now.
    if (ev.ts && (Date.now() / 1000 - ev.ts) < 20) {
      S.set_liveEventReceived(true);
      // Live navbar wall-clock — idempotent, starts ticking on the FIRST
      // fresh event (so a dead run's stale snapshot replay never starts it).
      // Seed from the run's real start so a refresh reconnect continues the
      // timer instead of restarting at 0.
      const base = _plannerRunStartMs
        ? Math.max(0, Date.now() - _plannerRunStartMs)
        : 0;
      startElapsed('planner', base);
    }

    // Planner-level terminal event: end the stream + reset UI.
    if (ev.step === 'planner' && ev.kind === 'terminal') {
      // Freeze the navbar total at the authoritative wall-clock (carried in
      // the terminal event — a separate post-terminal event would be missed
      // because the stream closes here).
      stopElapsed('planner', Number(ev.total_wall_ms || 0) || undefined);
      _plannerRunStartMs = 0;   // run ended — don't seed a future ticker

      // Pull the final state once so the cards reflect the very last
      // checkpoint. status field is set by aupdate_state right before
      // the terminal SSE event is emitted, so retry-by-status is the
      // race-safe expected field.
      await _refreshCardsFromState(threadId, 'status');
      const status = ev.status || 'done';
      if (status === 'failed') {
        markPlannerFailed(ev.error || 'Planner failed.');
      } else if (status === 'cancelled') {
        showToast('Planner cancelled. Checkpoints up to the cancel point are preserved.');
        _setPlannerStagePill('cancelled');
      } else {
        // Day 2: explicit done → flip pill so the at-a-glance
        // indicator transitions out of 'working' even before the
        // user navigates away. _renderPlannerGraph's aggregate
        // logic also sets this, but the explicit signal is
        // race-safer (covers the all-impl-done detection edge).
        _setPlannerStagePill('done');
      }
      try { es.close(); } catch (_) {}
      S.setPlannerThreadId(null);
      // Intentionally NOT calling _forgetActivePlanner here — the
      // localStorage entry stays so a page refresh can still recover
      // the completed cards via the same thread_id. The entry only
      // clears on explicit Wipe Planner or on the next Start Planner
      // on this slug (which overwrites it).
      refreshPlannerStartState();
      return;
    }

    // Per-step lifecycle.
    if (ev.step) {
      if (ev.kind === 'start') {
        _markCardRunning(ev.step);
        // Previous step's checkpoint is necessarily committed by the
        // time the NEXT step starts (graph is sequential), so refresh
        // state to paint the previous card's full KPI grid. Skip for
        // the first step (no previous).
        const stepIdx = S.PLANNER_NODE_ORDER.indexOf(ev.step);
        if (stepIdx > 0) {
          const prevStep = S.PLANNER_NODE_ORDER[stepIdx - 1];
          const prevField = S.STEP_TO_FIELD[prevStep];
          await _refreshCardsFromState(threadId, prevField);
          // _markCardRunning was called BEFORE the state refresh; if
          // renderPlannerCards happens to have flipped this card back
          // to pending (because its field isn't in state yet), re-mark
          // it running here so the spinner stays correct.
          _markCardRunning(ev.step);
        }
      }
      _renderLiveProgress(ev.step, ev);
      // Day 3: route the same event into NodeDrawer if it's open for
      // this node. The drawer's rAF batching + sticky-bottom log
      // turns the SSE stream into a live activity tail.
      if (NodeDrawer.isOpenFor('planner', ev.step)) {
        NodeDrawer.appendEvent(ev);
      }
    }
  };
  es.onerror = (_e) => {
    // Browser auto-reconnects EventSource on transient errors; we
    // only intervene if the run was already torn down server-side.
    if (S.plannerThreadId !== threadId) {
      try { es.close(); } catch (_) {}
    }
  };
}

export function _plannerStorageKey(slug) {
  return 'dd:planner:active:' + slug;
}

// Full planner wipe for `slug` — DELETE backend (MinIO embeddings +
// Postgres LangGraph checkpoints) + clear localStorage + reset cards
// if currently viewing that slug. Exposed on `window.ddWipePlanner`
// so an operator can run `ddWipePlanner('pydantic')` from the
// browser console without leaving the page.
export async function wipePlanner(slug) {
  if (!slug) return {error: 'no slug'};
  let result = {};
  try {
    const r = await fetch(S.API + '/planner/' + slug + '/wipe',
      {method: 'DELETE'});
    result = r.ok ? (await r.json()) : {http_status: r.status};
  } catch (e) {
    result = {error: String(e)};
  }
  _forgetActivePlanner(slug);
  if (S.activeSlug === slug) {
    S.setPlannerThreadId(null);
    resetPlannerCards();
    refreshPlannerStartState();
  }
  console.log('[ddWipePlanner]', slug, result);
  return result;
}
window.ddWipePlanner = wipePlanner;

// Separate key tracking the LAST slug the user kicked off a planner
// run for. recoverActivePlanner uses this to disambiguate when multiple
// slugs have localStorage entries — without it, the JS scan order is
// undefined and we might auto-activate the wrong framework on reload.

export function _rememberActivePlanner(slug, tid) {
  try {
    localStorage.setItem(_plannerStorageKey(slug), tid);
    localStorage.setItem(S._LAST_PLANNER_SLUG_KEY, slug);
  } catch (e) { /* private mode etc — silently ignore */ }
}

export function _forgetActivePlanner(slug) {
  try { localStorage.removeItem(_plannerStorageKey(slug)); }
  catch (e) { /* ignore */ }
}

// Page-refresh recovery: when the user reloads while a planner is
// mid-run, reconnect to the SSE stream + replay snapshot events so the
// UI catches up to the live state, mirroring the loading-box recovery
// on the Ingestion step. After a pod restart the in-flight bg task is
// dead but the LangGraph checkpoints persist — if no SSE events arrive
// within S._ORPHAN_DETECT_MS, we POST /resume which makes LangGraph
// continue from the last committed checkpoint (completed nodes skipped).
// Returns true if a run was resumed.

// Returns true if every CURRENTLY-IMPLEMENTED planner node has its
// output field present in `values`. Lets us treat a stuck `status:
// "running"` (e.g. pod-restart killed the bg task before
// aupdate_state(status='done') ran) as effectively-terminal so we
// don't burn orphan-detect timers + /resume calls on a run that
// actually finished.
export function _allImplementedComplete(values) {
  if (!values) return false;
  if (!S.plannerImplemented || !S.plannerImplemented.size) return false;
  for (let i = 0; i < S.PLANNER_NODE_ORDER.length; i++) {
    const step = S.PLANNER_NODE_ORDER[i];
    if (!S.plannerImplemented.has(step)) continue;
    const field = S.PLANNER_SUBSTEP_FIELDS[i];
    if (!_fieldPresent(values, field)) return false;
  }
  return true;
}

export async function _tryResumeActivePlanner(slug) {
  // Tear down any prior session FIRST so a switch from framework A
  // (which had cached planner state) to framework B doesn't leave
  // A's KPI grids on B's cards. S.plannerThreadId !== new tid implies
  // the previous SSE loop should self-exit on its next message
  // (see the guard inside pollPlannerState). We also reset the
  // visual state so a slug with no localStorage entry shows pending
  // cards instead of inheriting the previous slug's render.
  S.setPlannerThreadId(null);
  resetPlannerCards();
  refreshPlannerStartState();

  // LIVE-RUN RECONNECT (2026-05-29). A server-side registry
  // (`dd:planner:current:{slug}`, set by start_planner, cleared on
  // terminal/cancel/wipe) is the authoritative "is a run live NOW?" signal
  // — a checkpoint with status="running" is NOT (a crashed/pod-restarted
  // run leaves it stuck forever). If the registry says active, reconnect to
  // the live SSE: the snapshot replay repaints the current running step and
  // fresh events resume the live progress + timer. This is READ-ONLY — it
  // does NOT POST /resume (only an explicit Start does), so a stale entry
  // can never restart compute; worst case it shows the last step until the
  // 1h registry TTL lapses (timer won't tick without fresh events).
  try {
    const ar = await fetch(S.API + '/planner/' + slug + '/active');
    if (ar.ok) {
      const a = await ar.json();
      if (a && a.active && a.thread_id) {
        _plannerRunStartMs = a.started_ts
          ? a.started_ts * 1000
          : Date.now();
        S.setPlannerThreadId(a.thread_id);
        try { localStorage.setItem(_plannerStorageKey(slug), a.thread_id); }
        catch (_) {}
        _setPlannerStagePill('working');
        refreshPlannerStartState();      // Start → Cancel
        pollPlannerState(a.thread_id);    // snapshot replay + live SSE
        return true;
      }
    }
  } catch (_) { /* fall through to view-only recovery */ }

  // Persisted planner total → navbar (finished/cached runs survive refresh).
  // Skip if a live run is currently ticking so we don't clobber it.
  if (!isElapsedRunning('planner')) {
    fetch(S.API + '/planner/' + slug + '/timing')
      .then(r => (r.ok ? r.json() : null))
      .then(d => {
        if (d && !isElapsedRunning('planner')) {
          showElapsed('planner', Number(d.total_wall_ms || 0));
        }
      })
      .catch(() => {});
  }

  let tid = null;
  try { tid = localStorage.getItem(_plannerStorageKey(slug)); }
  catch (e) { return false; }
  if (!tid) return false;
  try {
    const r = await fetch(S.API + '/planner/debug/graph/' + tid + '/state');
    if (!r.ok) {
      _forgetActivePlanner(slug);
      return false;
    }
    const data = await r.json();
    const values = data.values || {};
    const status = values.status;
    // Terminal means "no more work to do":
    //   - failed/cancelled: explicit user/system halt, regardless of
    //     how many nodes ran
    //   - done AND all currently-wired nodes have committed: full
    //     completion under the current IMPLEMENTED set
    // CRITICAL: status="done" ALONE isn't enough. If new nodes were
    // added to IMPLEMENTED after the run finished, the thread shows
    // status="done" but missing the new node's field. Treating that as
    // terminal would skip the auto-/resume that needs to run the new
    // node — exactly the cluster-not-syncing bug.
    const allImplDone = _allImplementedComplete(values);
    const effectivelyDone = (
      status === 'failed' || status === 'cancelled' ||
      (status === 'done' && allImplDone) ||
      allImplDone
    );
    if (effectivelyDone) {
      // Terminal (or all-impl-done) — paint final state, don't subscribe.
      // KEEP localStorage entry so subsequent page refreshes can still
      // recover the cached cards. Entry only clears on explicit
      // Wipe Planner OR when a new run on this slug overwrites it.
      renderPlannerCards(values);
      return false;
    }
    // Non-terminal checkpoint ("running" / incomplete). This runs on
    // EVERY navigation to a framework's Planner page, so it is strictly
    // VIEW-ONLY. A checkpoint with status="running" is NOT proof of a
    // live task: a crashed/interrupted/pod-restarted run leaves the
    // status stuck at "running" forever (see planner.py /resume
    // docstring), and there is no backend liveness signal to tell a
    // live run from a dead one.
    //
    // So we paint the partial progress as a STATIC snapshot and DO NOT:
    //   - set S.plannerThreadId — which would flip the pill to
    //     "Working · N/9" (via _renderPlannerGraph) and the button to
    //     "Cancel", falsely implying a live run;
    //   - start pollPlannerState (live polling);
    //   - auto-POST /resume — which previously kicked off REAL compute
    //     just by visiting the page. THAT is the bug this fixes: a
    //     stale "running" checkpoint (e.g. FastMCP) showed "Working 5/9"
    //     and silently restarted the planner with no Start click.
    //
    // Live progress only ever runs in the session that explicitly
    // clicked Start Planner (startPlanner → smart-resume → pollPlannerState).
    // To continue an incomplete plan, the user clicks Start Planner: it
    // finds this thread via /planner/recent and POSTs /resume. Resuming
    // is therefore always an explicit, intentional action.
    renderPlannerCards(values);     // static view of the partial progress
    _setPlannerStagePill('idle');   // accurate — nothing is running now
    refreshPlannerStartState();     // Start Planner stays enabled (resume)
    return false;
  } catch (e) {
    _forgetActivePlanner(slug);
    return false;
  }
}

export async function startPlanner() {
  if (!S.activeSlug || S.plannerThreadId) return;
  resetPlannerCards();

  // Smart resume: if a thread already exists for this slug, reuse its
  // thread_id and POST /resume instead of /planner/{slug}. LangGraph's
  // ainvoke(None, config) on the expanded graph automatically skips
  // already-checkpointed nodes and runs only the new downstream ones.
  // Net: adding a 4th planner node + clicking Start Planner on a slug
  // that has S.steps 1-3 cached → only step 4 actually executes.
  let tid = null;
  let isResume = false;
  try {
    const r = await fetch(S.API + '/planner/recent');
    if (r.ok) {
      const data = await r.json();
      const found = ((data && data.recent) || [])
        .find(item => item.slug === S.activeSlug);
      if (found && found.thread_id) {
        tid = found.thread_id;
        isResume = true;
      }
    }
  } catch (e) { /* fall through to fresh thread */ }

  if (!tid) tid = _genPlannerThreadId(S.activeSlug);
  S.setPlannerThreadId(tid);
  _rememberActivePlanner(S.activeSlug, tid);   // page-refresh recovery
  // Mark the run start for the navbar timer (a refresh reconnect recovers
  // this from the registry's started_ts instead). A fresh start begins ~now.
  _plannerRunStartMs = Date.now();
  refreshPlannerStartState();   // button flips to "Cancel Planner"
  // Kick off polling in parallel with the main POST so the user sees
  // cards advance progressively.
  pollPlannerState(tid);
  try {
    // Mode is fixed to "llm" (the unified LITA-pattern planner) —
    // the dropdown was removed; the server still defaults `mode=llm`
    // if omitted, so we don't even need to pass it.
    const url = isResume
      ? S.API + '/planner/' + tid + '/resume'
      : S.API + '/planner/' + S.activeSlug +
        '?mode=llm&thread_id=' + encodeURIComponent(tid);
    const r = await fetch(url, {method: 'POST'});
    if (!r.ok) {
      const txt = await r.text();
      markPlannerFailed('HTTP ' + r.status + ': ' + txt.slice(0, 400));
      S.setPlannerThreadId(null);
      refreshPlannerStartState();
      return;
    }
    // POST now returns immediately with status="running" — the
    // background graph task runs server-side and the polling loop
    // (pollPlannerState above) owns terminal-state detection +
    // resetting S.plannerThreadId / the button. Nothing to do here.
    await r.json();   // drain the body
  } catch (e) {
    markPlannerFailed('Request failed: ' + String(e));
    S.setPlannerThreadId(null);
    refreshPlannerStartState();
  }
}

export async function cancelPlanner() {
  if (!S.plannerThreadId) return;
  const tid = S.plannerThreadId;
  // Spinner + "Cancelling…" — mirrors the Step 2 ingestion cancel UX.
  S.plannerStartBtn.setAttribute('disabled', 'disabled');
  S.plannerStartBtn.innerHTML =
    '<div class="fw-spinner" style="display:inline-block;' +
    'vertical-align:middle;margin-right:8px"></div>Cancelling…';
  try {
    // Fire-and-forget — the cancel watcher on the server detects the
    // Redis flag within ~1s, raises CancelledError inside graph.ainvoke,
    // and the in-flight POST /planner/{slug} returns with
    // status='cancelled'. THAT response triggers the UI cleanup
    // (refreshPlannerStartState in startPlanner's finally).
    await fetch(S.API + '/planner/' + tid + '/cancel', {method: 'POST'});
  } catch (e) {
    // If the cancel POST itself fails, restore the button so the user
    // can retry. The startPlanner POST is still in flight either way.
    S.plannerStartBtn.removeAttribute('disabled');
    S.plannerStartBtn.innerHTML = 'Cancel Planner';
    showToast('Cancel request failed: ' + String(e));
  }
}

S.plannerStartBtn?.addEventListener('click', () => {
  // Dual-purpose: Start when idle, Cancel when a thread_id is set.
  if (S.plannerThreadId) {
    cancelPlanner();
  } else {
    startPlanner();
  }
});

// Wipe-planner button — destructive, gated by a confirm dialog. Hits
// the backend DELETE /planner/{slug}/wipe (MinIO embeddings + Postgres
// checkpoints) then clears localStorage + resets cards.
if (S.plannerWipeBtn) {
  S.plannerWipeBtn.addEventListener('click', async () => {
    if (!S.activeSlug || S.plannerThreadId) return;
    // Probe downstream state so the confirm dialog reports the real
    // cascade (Synth + Study get nuked when planner is wiped — they
    // depend on the planner's chapter map).
    const state = await fetchPipelineState(S.activeSlug);
    const cascade = cascadeImpactText(state, 'planner');
    const ok = await showConfirm(
      'Wipe planner cache for ' + S.activeSlug + '?',
      'Deletes MinIO embedding blobs (forces a cold re-embed next ' +
      'run), Postgres LangGraph checkpoints (all threads for this ' +
      'slug), and the browser-cached thread_id.' + cascade +
      ' Cannot be undone.',
      'Wipe',
    );
    if (!ok) return;
    S.plannerWipeBtn.setAttribute('disabled', 'disabled');
    const orig = S.plannerWipeBtn.textContent;
    S.plannerWipeBtn.textContent = 'Wiping…';
    try {
      const result = await wipePlanner(S.activeSlug);
      // Cascade downstream — Synth's chapter outputs and the Study
      // renders MUST go too because they were produced from THIS
      // planner's plan-latest.json. Skip the cascade call when there's
      // nothing to delete (avoids one round-trip + a meaningless toast).
      let synthDeleted = 0;
      if (state && (state.synth || state.study)) {
        try {
          const { wipeSynth } = await import('./synth.js');
          const sr = await wipeSynth(S.activeSlug);
          synthDeleted = (sr && sr.minio_objects_deleted) || 0;
        } catch (e) {
          console.warn('[wipePlanner] cascade wipeSynth failed:', e);
          showToast('Planner wiped but Synth cascade failed: ' + String(e));
        }
      }
      const minio = (result && result.minio_blobs_deleted) || 0;
      const pg = result && result.postgres_rows_deleted;
      const pgTotal = pg
        ? Object.values(pg).reduce(
            (a, b) => a + (typeof b === 'number' ? b : 0), 0)
        : 0;
      const tail = synthDeleted
        ? ' Cascaded: ' + synthDeleted + ' Synth/Study object(s) deleted.'
        : '';
      showToast('Planner cache wiped for ' + S.activeSlug +
        ' (' + minio + ' MinIO blobs, ' + pgTotal + ' Postgres rows).' +
        tail);
    } catch (e) {
      showToast('Wipe failed: ' + String(e));
    } finally {
      S.plannerWipeBtn.textContent = orig;
      refreshPlannerStartState();
    }
  });
}

// Card-head click → toggle expanded body (legacy cards-mode handler).
// Cards DOM was removed 2026-05-19 — `S.plannerCardsEl` is null in the
// graph-only UI, so the handler is registered conditionally. The
// off_topic verdict-table sort branch lived inside this handler too;
// it now activates only when the planner drawer renders that table
// (handled by SUBSTEP_RENDERERS[2] inside the drawer details panel,
// which has its own delegate).
if (S.plannerCardsEl) S.plannerCardsEl.addEventListener('click', ev => {
  // Sort header click — take precedence over card-head expansion.
  const sortTh = ev.target.closest('th[data-sort-col]');
  if (sortTh) {
    ev.stopPropagation();
    const col = sortTh.dataset.sortCol;
    if (S._offTopicSort.col === col) {
      // Toggle direction; third click clears the sort.
      if (S._offTopicSort.dir === 'asc') S._offTopicSort.dir = 'desc';
      else { S._offTopicSort.col = null; S._offTopicSort.dir = 'asc'; }
    } else {
      S._offTopicSort.col = col;
      S._offTopicSort.dir = 'asc';
    }
    // Re-render the off_topic card body from cached values (no refetch).
    const c = cardEl(2);   // off_topic substep idx
    if (c && S._lastOffTopicValues) {
      const body = c.querySelector('.fw-planner-card-body');
      const renderer = SUBSTEP_RENDERERS[2];
      if (body && renderer) {
        body.innerHTML = renderer(S._lastOffTopicValues);
      }
    }
    return;
  }
  const head = ev.target.closest('.fw-planner-card-head');
  if (!head) return;
  head.parentElement.classList.toggle('expanded');
});

// NOTE: synth-cards click-to-expand handler is registered LATER in
// the IIFE (after `synthCardsEl` is declared at ~line 3504). Placing
// it here previously hit a Temporal Dead Zone error (const is not
// hoisted) which crashed the IIFE on load — silently breaking
// loadLibrary() and every other init step.

// ============================================================
// POST /runs — Generate / Refresh
// ============================================================
