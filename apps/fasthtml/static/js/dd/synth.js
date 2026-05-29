// ============================================================
// synth.js — Step 4: Synth pipeline (Cytoscape canvas, SSE,
//            study orchestrator, chapter strip, persistence)
// ============================================================

import * as S from './state.js';
import { StageGraph } from './stagegraph.js';
import { sleep, escapeHtml, formatFieldValue } from './utils.js';
import {
  showToast, showConfirm, refreshGenerateState,
} from './ui.js';

// ============================================================
// Day 5 — Synth canvas parity. Mirrors planner's helpers so each
// shipped synth node lights up the same way Planner does today.
// The canvas appears under ?ui=graph; cards remain the default view.
// ============================================================

export function _setSynthStagePill(status, labelOverride) {
  const pill = document.getElementById('fw-synth-pill');
  const text = document.getElementById('fw-synth-pill-text');
  if (!pill || !text) return;
  const labels = {
    idle: 'Idle', working: 'Working', done: 'Completed',
    failed: 'Failed', cancelled: 'Cancelled',
  };
  pill.dataset.status = status;
  text.textContent = labelOverride || labels[status] || status;
}

// KPI extraction per synth node. Currently every field is empty
// because no synth nodes ship state yet — populated as each lands.
// Format mirrors _kpiForNode (planner side): returns 'k=v' string or
// empty. When synth nodes start emitting real `*_stats`, fill these.
export function _kpiForSynthNode(nodeId, values) {
  if (!values) return '';
  const stats = (key) => values[key] || null;
  switch (nodeId) {
    case 'outline_sdp':        {
      const s = stats('outline_stats');
      if (!s) return '';
      const parts = [];
      if (s.n_sections   !== undefined) parts.push(`sec=${s.n_sections}`);
      if (s.max_stage    !== undefined) parts.push(`depth=${s.max_stage}`);
      if (s.n_violations !== undefined) parts.push(`viol=${s.n_violations}`);
      return parts.join(' · ');
    }
    case 'digest_construct':   {
      const s = stats('digest_stats');
      if (!s) return '';
      const parts = [];
      if (s.n_sources !== undefined) parts.push(`src=${s.n_sources}`);
      if (s.n_sections !== undefined &&
          s.n_sections_covered !== undefined) {
        parts.push(`cov=${s.n_sections_covered}/${s.n_sections}`);
      }
      if (s.n_orphan_code_refs !== undefined) {
        parts.push(`orph=${s.n_orphan_code_refs}`);
      }
      if (s.n_empty_sections) parts.push(`empty=${s.n_empty_sections}`);
      return parts.join(' · ');
    }
    case 'sawc_write':         {
      const s = stats('sawc_stats');
      if (!s) return '';
      const parts = [];
      if (s.n_sections !== undefined && s.n_completed !== undefined) {
        parts.push(`sec=${s.n_completed}/${s.n_sections}`);
      }
      if (s.n_fallback) parts.push(`fb=${s.n_fallback}`);
      if (s.n_repairs) parts.push(`rep=${s.n_repairs}`);
      if (s.n_picker_fallbacks) {
        parts.push(`pfb=${s.n_picker_fallbacks}`);
      }
      // Ship #7 (2026-05-24): show refine_iter when sawc has looped
      // back from mgsr_replan (>1 means the CoRefine loop fired).
      const iter = values.refine_iter;
      if (iter !== undefined && iter > 1) {
        parts.push(`iter=${iter}`);
      }
      return parts.join(' · ');
    }
    case 'checklist_eval':     {
      const s = stats('checklist_stats');
      if (!s) return '';
      const parts = [];
      if (s.n_total !== undefined && s.n_passed !== undefined) {
        parts.push(`pass=${s.n_passed}/${s.n_total}`);
      }
      if (s.pass_rate !== undefined) {
        parts.push(`rate=${(s.pass_rate * 100).toFixed(0)}%`);
      }
      if (s.chapter_passed === true)  parts.push('✓');
      if (s.chapter_passed === false) parts.push('✗');
      if (s.n_failed_feedback) parts.push(`fb=${s.n_failed_feedback}`);
      return parts.join(' · ');
    }
    case 'mgsr_replan':        {
      const s = stats('mgsr_stats');
      if (!s) return '';
      const parts = [];
      if (s.halt !== undefined) {
        parts.push(s.halt ? '✓halt' : '↻loop');
      }
      if (s.halt_reason) parts.push(s.halt_reason);
      if (s.n_actions !== undefined) parts.push(`act=${s.n_actions}`);
      if (s.confidence !== undefined) {
        parts.push(`conf=${(s.confidence * 100).toFixed(0)}%`);
      }
      return parts.join(' · ');
    }
    case 'render_audit_write': {
      const s = stats('chapter_stats');
      if (!s) return '';
      const parts = [];
      if (s.audit_passed === true)  parts.push('audit=✓');
      if (s.audit_passed === false) parts.push('audit=✗');
      if (s.n_artifacts !== undefined) parts.push(`arts=${s.n_artifacts}`);
      if (s.n_code_refs !== undefined && s.n_resolved !== undefined &&
          s.n_code_refs > 0) {
        parts.push(`refs=${s.n_resolved}/${s.n_code_refs}`);
      }
      if (s.n_missing) parts.push(`miss=${s.n_missing}`);
      if (s.n_byte_drift) parts.push(`drift=${s.n_byte_drift}`);
      if (s.rendered_chars) {
        parts.push(`${(s.rendered_chars / 1000).toFixed(1)}k`);
      }
      return parts.join(' · ');
    }
  }
  return '';
}

export function _renderSynthGraph(values, nextNodes) {
  if (!S.synthGraph) return;
  // BUGFIX 2026-05-24: previously this routine inferred "currently
  // running" by finding the FIRST not-done node (`i === doneCount`).
  // That assumes monotonic progression, which CoRefine loopbacks break:
  // after `mgsr_replan → RETHINK → sawc_write` re-enters, sawc's output
  // field from iter 1 is still in the checkpoint values, so the loop
  // misclassifies sawc as 'done' and lights up the next un-output node
  // (typically render_audit_write) as 'running' even though Python is
  // actually re-executing sawc_write.
  //
  // Fix: when the caller passes `nextNodes` (= snap.next from LangGraph
  // state), use it as the authoritative "currently running" set. Any
  // node in nextNodes that the synth thread is actively running gets
  // status='running', overriding the field-presence heuristic.
  // The heuristic stays as a fallback for the pre-first-checkpoint
  // window (when nextNodes is empty/unknown).
  const nextSet = (Array.isArray(nextNodes) && nextNodes.length > 0)
    ? new Set(nextNodes) : null;
  const useAuthoritative = nextSet !== null && S.synthThreadId !== null;
  // 2026-05-25: per-node iter badge + global CoRefine chip.
  // refine_iter is a SynthState field bumped by sawc_write each pass;
  // it survives across the loopback because LangGraph checkpoints the
  // value. Default 0 → no badge displayed (first pass / no run yet).
  const refineIter = Number(values && values.refine_iter || 0);
  const maxIter    = 5;   // matches graph.py:_MAX_REFINE_ITER
  // A loopback is "actively firing" when sawc_write is in nextSet AND
  // there's already a sawc output (i.e. we've completed at least iter 1
  // and are re-entering). Same predicate the SAWC running-state uses.
  const isLooping = (
    refineIter >= 1 &&
    useAuthoritative &&
    nextSet.has('sawc_write')
  );
  let doneCount = 0;
  let anyRunning = false;
  for (let i = 0; i < S.SYNTH_NODE_ORDER.length; i++) {
    const nodeId = S.SYNTH_NODE_ORDER[i];
    const field = S.SYNTH_SUBSTEP_FIELDS[i];
    const present = _synthFieldPresent(values, field);
    const isImpl = S.synthImplemented.has(nodeId);
    let status;
    if (useAuthoritative && nextSet.has(nodeId)) {
      // Authoritative running signal — overrides field presence.
      status = 'running'; anyRunning = true;
    } else if (present) { status = 'done'; doneCount++; }
    else if (!isImpl)   { status = 'future'; }
    else if (i === doneCount && S.synthThreadId !== null) {
      // Pre-checkpoint fallback for the very first superstep.
      status = 'running'; anyRunning = true;
    } else              { status = 'pending'; }
    // Per-node iter badge on sawc_write only (the loop target). KPI text
    // gets concatenated with the existing KPI line when present.
    let kpiText = present ? _kpiForSynthNode(nodeId, values) : '';
    if (nodeId === 'sawc_write' && refineIter >= 1) {
      const badge = `iter ${refineIter}/${maxIter}`;
      kpiText = kpiText ? `${badge} · ${kpiText}` : badge;
    }
    S.synthGraph.setStatus(nodeId, status, kpiText);
  }
  // Drive the loopback edge state (amber arc — dashed dormant / solid
  // animated when firing). Cheap no-op when the graph has no loopback
  // edges (e.g. planner reuses StageGraph but has no cycle).
  if (typeof S.synthGraph.setLoopActive === 'function') {
    S.synthGraph.setLoopActive(isLooping);
  }
  _updateCoRefineChip(isLooping, refineIter, maxIter);
  const explicitStatus = (values && values.status) || null;
  const implCount = S.SYNTH_NODE_ORDER.filter(n => S.synthImplemented.has(n)).length;
  const progress = implCount ? doneCount + '/' + implCount : null;
  if (explicitStatus === 'failed')        _setSynthStagePill('failed');
  else if (explicitStatus === 'cancelled') _setSynthStagePill('cancelled');
  else if (anyRunning || S.synthThreadId !== null) {
    _setSynthStagePill('working',
      progress ? 'Working · ' + progress : null);
  } else if (doneCount > 0 && doneCount === implCount) {
    _setSynthStagePill('done');
  } else if (doneCount === 0) {
    _setSynthStagePill('idle');
  }
}

export function _buildSynthNodeCtx(nodeId, values) {
  const idx = S.SYNTH_NODE_ORDER.indexOf(nodeId);
  if (idx < 0) return null;
  const label = S.SYNTH_NODE_LABELS[idx] || nodeId;
  const thisField = S.SYNTH_SUBSTEP_FIELDS[idx];
  let status = 'pending';
  if (_synthFieldPresent(values, thisField)) status = 'done';
  else if (!S.synthImplemented.has(nodeId)) status = 'future';
  else if (S.synthThreadId) status = 'running';
  const kpiText = _kpiForSynthNode(nodeId, values);
  const kpis = {};
  if (kpiText) {
    // KPI text format is `k1=v1 · k2=v2 · k3=v3` (space-dot-space
    // separator). Older code only grabbed the first `k=v` because it
    // split on the FIRST `=` for the whole string, dropping multi-key
    // KPIs. Split on the separator first, then on `=` per pair.
    kpiText.split(' · ').forEach(pair => {
      const eqIdx = pair.indexOf('=');
      if (eqIdx > 0) {
        kpis[pair.slice(0, eqIdx).trim()] = pair.slice(eqIdx + 1).trim();
      }
    });
  }
  // Synth's SUBSTEP_RENDERERS is empty until nodes ship; same
  // pattern as planner — when a renderer lands, drawer gets the
  // rich KPI/table/outline view automatically.
  const renderer = S.SYNTH_SUBSTEP_RENDERERS[idx];
  const resultsHtml = (renderer && _synthFieldPresent(values, thisField))
    ? renderer(values)
    : null;
  const inputs = idx > 0 && _synthFieldPresent(values, S.SYNTH_SUBSTEP_FIELDS[idx - 1])
    ? JSON.stringify({ [S.SYNTH_SUBSTEP_FIELDS[idx - 1]]: values[S.SYNTH_SUBSTEP_FIELDS[idx - 1]] }, null, 2)
    : null;
  const outputs = _synthFieldPresent(values, thisField)
    ? JSON.stringify({ [thisField]: values[thisField] }, null, 2)
    : null;
  return { label, status, kpis, resultsHtml, inputs, outputs };
}

// In-memory event buffer keyed by step name. The SSE handler in
// pollSynthState pushes every event here AS IT ARRIVES, regardless of
// whether the drawer is currently open. When the user opens the
// drawer for `outline_sdp` mid-run (or after the run finishes), we
// replay the buffered events into the drawer log so they see the
// full activity history — not just events that fire AFTER the drawer
// open. Without this, the long silent windows between SDP events
// (~28s while 3 LLM samples S.generate concurrently) made the drawer
// look empty even though the run was making progress.
// Capped per-step to avoid unbounded growth on very long runs.

export function _bufferSynthEvent(ev) {
  if (!ev || !ev.step) return;
  let list = S._synthEventBuffer.get(ev.step);
  if (!list) { list = []; S._synthEventBuffer.set(ev.step, list); }
  list.push(ev);
  if (list.length > S._SYNTH_EVENT_BUFFER_PER_STEP) {
    list.splice(0, list.length - S._SYNTH_EVENT_BUFFER_PER_STEP);
  }
}

export function _resetSynthEventBuffer() {
  S._synthEventBuffer.clear();
}

export async function _openSynthNodeDrawer(nodeId) {
  let values = {};
  // Same fallback as planner: localStorage thread id covers the
  // post-terminal case when S.synthThreadId has been nulled.
  let tid = S.synthThreadId;
  if (!tid && S.activeSlug) {
    try { tid = localStorage.getItem(_synthStorageKey(S.activeSlug)); }
    catch (e) {}
  }
  if (tid) {
    try {
      const r = await fetch(S.API + '/synth/debug/graph/' + tid + '/state');
      if (r.ok) values = (await r.json()).values || {};
    } catch (e) { /* drawer opens with empty results */ }
  }
  const ctx = _buildSynthNodeCtx(nodeId, values);
  // NodeDrawer is from the planner module — use dynamic import to
  // avoid circular dependency at module parse time.
  const { NodeDrawer } = await import('./planner.js');
  if (ctx) NodeDrawer.open('synth', nodeId, ctx);
  // Replay buffered events for this node so a late-open drawer sees
  // the full event history, not just future events.
  const buffered = S._synthEventBuffer.get(nodeId) || [];
  if (buffered.length) {
    for (const ev of buffered) NodeDrawer.appendEvent(ev);
  }
}

export function _refreshOpenSynthDrawer(values) {
  // NodeDrawer lives in planner.js — access synchronously via the
  // module-level reference that main.js wires at boot. If it hasn't
  // been wired yet (race during init), silently skip.
  const nd = _nodeDrawerRef;
  if (!nd || nd.openStage !== 'synth') return;
  const nodeId = nd.openNodeId;
  if (!nodeId) return;
  const ctx = _buildSynthNodeCtx(nodeId, values);
  if (ctx) nd.updateContext(ctx);
}

// Reference to NodeDrawer — set by main.js after planner.js loads.
// Avoids synchronous circular import for the hot path.
let _nodeDrawerRef = null;
export function _setNodeDrawerRef(nd) { _nodeDrawerRef = nd; }

export function _resizeSynthCanvas() {
  if (!S.synthGraph || !S.synthGraph.cy) return;
  requestAnimationFrame(() => {
    _runSynthLayoutAndCenter('first');
    setTimeout(() => _runSynthLayoutAndCenter('second'), 250);
  });
}

export function _runSynthLayoutAndCenter(passLabel) {
  if (!S.synthGraph || !S.synthGraph.cy) return;
  try {
    const cy = S.synthGraph.cy;
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
        // _forceCenterHorizontal lives in planner.js — use dynamic import.
        import('./planner.js').then(m => {
          m._forceCenterHorizontal(cy, '[synthGraph ' + passLabel + ']');
        });
      } catch (e) {
        console.warn('[synthGraph] center pipeline failed:', e);
      }
    });
    layout.run();
  } catch (e) {
    console.warn('[synthGraph] resize ' + passLabel + ' failed:', e);
  }
}

// ──────────────────────────────────────────────────────────────────
// CoRefine chip — top-of-canvas iteration indicator (May 2026 SOTA
// pattern; mirrors Temporal Web UI's run-level retry indicator).
// Dormant: hidden. Active: amber pill showing "CoRefine · iter N/5".
// ──────────────────────────────────────────────────────────────────
const _COREFINE_CHIP_ID = 'fw-synth-corefine-chip';

function _ensureCoRefineChip() {
  let chip = document.getElementById(_COREFINE_CHIP_ID);
  if (chip) return chip;
  const root = document.getElementById('fw-synth-graph');
  if (!root) return null;
  chip = document.createElement('div');
  chip.id = _COREFINE_CHIP_ID;
  chip.className = 'fw-corefine-chip';
  // Inline styles — avoids touching app CSS for this small affordance.
  // Hidden by default; _updateCoRefineChip flips display + text.
  chip.style.cssText = [
    'position: absolute',
    'top: 8px',
    'left: 50%',
    'transform: translateX(-50%)',
    'padding: 4px 12px',
    'border-radius: 999px',
    'background: #fef3c7',                // amber-100
    'color: #92400e',                     // amber-800
    'border: 1px solid #d97706',          // amber-600
    'font: 600 12px/1.0 Raleway, Helvetica Neue, Arial, sans-serif',
    'letter-spacing: 0.02em',
    'box-shadow: 0 1px 3px rgba(0,0,0,0.08)',
    'pointer-events: none',               // never blocks canvas hits
    'z-index: 5',
    'display: none',
  ].join('; ');
  // Ensure parent is positioned so absolute children anchor correctly.
  if (root && getComputedStyle(root).position === 'static') {
    root.style.position = 'relative';
  }
  root.appendChild(chip);
  return chip;
}

export function _updateCoRefineChip(isLooping, refineIter, maxIter) {
  const chip = _ensureCoRefineChip();
  if (!chip) return;
  if (isLooping && refineIter >= 1) {
    chip.textContent = `CoRefine · iter ${refineIter}/${maxIter}`;
    chip.style.display = 'block';
  } else {
    chip.style.display = 'none';
  }
}

export function _initSynthCanvas() {
  if (S.UI_MODE !== 'graph') return;
  const root = document.getElementById('fw-synth-graph');
  const canvasEl = document.getElementById('fw-synth-canvas');
  if (!root || !canvasEl) return;
  // Visibility managed by _toggleStageEmpty (single source of truth)
  // — mirror of the planner-side fix. Canvas init no longer races
  // the toggle by setting display directly.
  const startedAt = Date.now();
  function tryInit() {
    if (typeof cytoscape !== 'undefined') {
      const nodes = S.SYNTH_NODE_ORDER.map((id, i) => ({
        id,
        label:  S.SYNTH_NODE_LABELS[i] || id,
        status: S.synthImplemented.has(id) ? 'pending' : 'future',
      }));
      const edges = [];
      for (let i = 0; i < S.SYNTH_NODE_ORDER.length - 1; i++) {
        edges.push({ source: S.SYNTH_NODE_ORDER[i],
                     target: S.SYNTH_NODE_ORDER[i + 1] });
      }
      // ── CoRefine loopback edge (Pattern 1 from May 2026 UX research) ──
      // The synth graph is CYCLIC: when checklist scores < 0.80, mgsr
      // routes RETHINK and the graph re-enters sawc_write. Surface that
      // structurally with a backward-arc edge tagged `kind='loopback'` —
      // the stylesheet renders it as an amber arc above the row, dashed
      // when dormant, solid+pulsing when actively firing. Source path:
      // apps/fastapi/domains/dd/synth/graph.py:_route_after_mgsr.
      if (S.SYNTH_NODE_ORDER.includes('sawc_write') &&
          S.SYNTH_NODE_ORDER.includes('mgsr_replan')) {
        edges.push({
          source: 'mgsr_replan',
          target: 'sawc_write',
          kind:   'loopback',
        });
      }
      console.log(
        `[synthGraph] canvas container ready, dims=${canvasEl.offsetWidth}x${canvasEl.offsetHeight}`
      );
      // StageGraph is shared (stagegraph.js); _attachCanvasResizeObserver
      // is a planner helper pulled in via dynamic import to avoid a static
      // synth→planner cycle.
      import('./planner.js').then(m => {
        S.setSynthGraph(StageGraph.create(canvasEl, {
          nodes, edges,
          onNodeClick: (nodeId) => _openSynthNodeDrawer(nodeId),
        }));
        console.log(
          `[synthGraph] Cytoscape initialized with ${nodes.length} nodes, ${edges.length} edges`
        );
        if (S.synthGraph) _resizeSynthCanvas();
        m._attachCanvasResizeObserver('fw-synth-canvas', _resizeSynthCanvas);
      });
      return;
    }
    if (Date.now() - startedAt > 5000) {
      console.warn(
        '[synthGraph] Cytoscape failed to load within 5s — ' +
        'canvas unavailable. Reload the page to retry.',
      );
      // No cards fallback anymore (removed 2026-05-19). Same in-place
      // error shape as the planner-side handler above.
      const synthCanvasEl = document.getElementById('fw-synth-canvas');
      if (synthCanvasEl) {
        synthCanvasEl.innerHTML =
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

// Window resize handler — rAF-throttled (mirrors planner equivalent).
window.addEventListener('resize', () => {
  if (S._synthResizeRafPending) return;
  S.set_synthResizeRafPending(true);
  requestAnimationFrame(() => {
    S.set_synthResizeRafPending(false);
    if (S.synthGraph) _resizeSynthCanvas();
  });
});

export function synthCardEl(idx) {
  if (!S.synthCardsEl) return null;
  return S.synthCardsEl.querySelector(
    '.fw-planner-card[data-idx="' + idx + '"]');
}

export function _synthStepIdx(stepName) {
  return S.SYNTH_SUBSTEP_FIELDS.findIndex((_, i) =>
    synthCardEl(i)?.dataset.substep === stepName);
}

export function _synthFieldPresent(values, field) {
  return values && Object.prototype.hasOwnProperty.call(values, field);
}

export function _synthAllImplementedComplete(values) {
  if (!S.synthImplemented || !S.synthImplemented.size) return false;
  for (let i = 0; i < S.SYNTH_NODE_ORDER.length; i++) {
    const step = S.SYNTH_NODE_ORDER[i];
    if (!S.synthImplemented.has(step)) continue;
    const field = S.SYNTH_SUBSTEP_FIELDS[i];
    if (!_synthFieldPresent(values, field)) return false;
  }
  return true;
}

export function _synthLiveProgressEl(stepName, idx) {
  const c = synthCardEl(idx);
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

export function _markSynthCardRunning(stepName) {
  const idx = _synthStepIdx(stepName);
  if (idx < 0) return;
  // Graph-only UI (cards DOM removed): flip the Cytoscape node to
  // 'running' FIRST, unconditionally — it's the sole live "Working"
  // indicator now. Must run BEFORE the legacy card guard below, which
  // early-returns when no card element exists (always) and previously
  // suppressed this update. Mirrors planner._markCardRunning.
  if (S.synthGraph) {
    // Don't downgrade an already-finished node (SSE snapshot replay on
    // refresh re-delivers old `start` events for done steps).
    let cur = null;
    try { cur = S.synthGraph.cy.getElementById(stepName).data('status'); }
    catch (_) {}
    if (cur !== 'done' && cur !== 'failed') {
      S.synthGraph.setStatus(stepName, 'running');
      const stepIdx = S.SYNTH_NODE_ORDER.indexOf(stepName);
      const implCount = S.SYNTH_NODE_ORDER.filter(n => S.synthImplemented.has(n)).length;
      const progress = (stepIdx >= 0 && implCount)
        ? (stepIdx + '/' + implCount) : null;
      _setSynthStagePill('working',
        progress ? 'Working · ' + progress : null);
    }
  }
  // Legacy card path — no-op in the graph-only UI (synthCardEl is null).
  const c = synthCardEl(idx);
  if (!c) return;
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

// Per-step live-progress text. Every step starts with a generic
// "running…" line; specific event kinds get richer messages as nodes
// ship + define their SSE event surface. Mirrors planner's
// _renderLiveProgress pattern.
export function _renderSynthLiveProgress(stepName, ev) {
  const idx = _synthStepIdx(stepName);
  if (idx < 0) return;
  const c = synthCardEl(idx);
  if (c && c.classList.contains('done')) return;
  const el = _synthLiveProgressEl(stepName, idx);
  if (!el) return;
  let text = '';
  // Generic lifecycle fallbacks — every node SHOULD emit start/done at
  // minimum.
  if (ev.kind === 'start')      text = '· starting ' + stepName + '…';
  else if (ev.kind === 'done')  text = '✓ done (' + (ev.wall_ms || 0) + ' ms)';
  else if (ev.kind === 'error') text = '✕ ' + (ev.error || 'failed');
  // outline_sdp — SurveyGen-I SDP per-event progress
  if (stepName === 'outline_sdp') {
    if (ev.kind === 'start') {
      text = '· loading sources for ' + (ev.chapter_title || ev.chapter_id || 'chapter') +
             ' (' + (ev.n_sources || 0) + ' sources)';
    } else if (ev.kind === 'sources_loaded') {
      text = '· sources loaded: ' + (ev.n_bodies || 0) + '/' + (ev.n_sources || 0) +
             ' bodies, ' + ((ev.bytes || 0) / 1000).toFixed(1) + 'k chars, ' +
             (ev.n_vault_hashes || 0) + ' code refs' +
             (ev.truncated ? ' (truncated)' : '');
    } else if (ev.kind === 'sample_done') {
      // Per-sample event (one per concurrent LLM draft). `sample_idx`
      // is 0-based; show 1-based for the user.
      const idx = (ev.sample_idx ?? 0) + 1;
      const tot = ev.n_total || 0;
      const dep = ev.deployment ? ' [' + ev.deployment + ']' : '';
      if (ev.ok) {
        text = '· sample ' + idx + '/' + tot + ' done (' +
               (ev.n_sections || '?') + ' sections, ' +
               (ev.wall_ms || 0) + ' ms)' + dep;
      } else {
        text = '· sample ' + idx + '/' + tot + ' FAILED: ' +
               (ev.error || 'unknown');
      }
    } else if (ev.kind === 'samples_drafted') {
      text = '· drafted ' + (ev.n_samples || 0) + '/' +
             (ev.n_requested || 0) + ' candidate outlines';
    } else if (ev.kind === 'samples_validated') {
      text = '· validated ' + (ev.n_candidates || 0) + ' candidate(s)' +
             (ev.n_pydantic_fail ? ', ' + ev.n_pydantic_fail + ' pydantic-rejected' : '');
    } else if (ev.kind === 'usc_voted') {
      text = '· USC picked candidate #' + (ev.chosen_index || 0) +
             ' (' + (ev.n_initial_violations || 0) + ' initial violations)';
    } else if (ev.kind === 'repair_attempt') {
      text = '· repair attempt ' + (ev.attempt || 0) +
             ' (' + (ev.n_violations || 0) + ' violations)';
    } else if (ev.kind === 'done') {
      text = '✓ done — ' + (ev.n_sections || 0) + ' sections, ' +
             'depth=' + (ev.max_stage || 0) + ', ' +
             'repairs=' + (ev.n_repairs || 0) + ', ' +
             'violations=' + (ev.n_violations || 0) +
             ' (' + (ev.wall_ms || 0) + ' ms)';
    }
  }
  // digest_construct — per-source LLM-assigned routing (LLMxMapReduce-V3
  // pattern). N parallel source digests with one `source_done` event per
  // completion, plus lifecycle events.
  if (stepName === 'digest_construct') {
    if (ev.kind === 'start') {
      text = '· starting digests for ' + (ev.chapter_title || ev.chapter_id || 'chapter') +
             ' (' + (ev.n_sources || 0) + ' sources × ' +
             (ev.n_sections || 0) + ' sections)';
    } else if (ev.kind === 'outline_loaded') {
      text = '· outline loaded: ' + (ev.n_sources || 0) + ' source(s), ' +
             (ev.n_total_vault_hashes || 0) + ' code refs, ' +
             (((ev.total_bytes || 0) / 1000).toFixed(1)) + 'k chars';
    } else if (ev.kind === 'source_done') {
      const idx = (ev.sample_idx ?? 0) + 1;
      const tot = ev.n_total || 0;
      const dep = ev.deployment ? ' [' + ev.deployment + ']' : '';
      const src = (ev.source_key || '').split('/').pop();
      if (ev.ok) {
        text = '· source ' + idx + '/' + tot + ' done · ' + src + ' · ' +
               (ev.n_contributions || 0) + ' contribs, ' +
               (ev.wall_ms || 0) + ' ms' + dep;
      } else {
        text = '· source ' + idx + '/' + tot + ' FAILED · ' + src +
               ': ' + (ev.error || 'unknown');
      }
    } else if (ev.kind === 'digests_aggregated') {
      text = '· aggregated ' + (ev.n_digests_ok || 0) + '/' +
             (ev.n_total || 0) + ' digests' +
             (ev.n_pydantic_fail
                ? ', ' + ev.n_pydantic_fail + ' pydantic-rejected'
                : '');
    } else if (ev.kind === 'done') {
      text = '✓ done — ' + (ev.n_sources || 0) + ' sources, ' +
             'cov=' + (ev.n_sections_covered || 0) + '/' +
             (ev.n_sections || 0) + ', ' +
             'empty=' + (ev.n_empty_sections || 0) + ', ' +
             'orph=' + (ev.n_orphan_code_refs || 0) +
             ' (' + (ev.wall_ms || 0) + ' ms)';
    }
  }
  // sawc_write — Structure-Aware Writing Controller (SurveyGen-I §3.2
  // + MAMM-Refine). Stage-parallel; N=3 best-of-N per section; per-
  // section critic-pick. Emits 6 event kinds so the live progress
  // stream has steady cadence across the stage loop.
  if (stepName === 'sawc_write') {
    if (ev.kind === 'start') {
      text = '· starting writes for ' + (ev.chapter_title || ev.chapter_id || 'chapter') +
             ' (' + (ev.n_sections || 0) + ' sections × 3 drafts = ' +
             (ev.n_total_drafts || 0) + ' draft calls + critic picks across ' +
             (ev.n_stages || 0) + ' stages)';
    } else if (ev.kind === 'stage_start') {
      const sids = (ev.section_ids || []).join(', ');
      text = '· stage ' + (ev.stage_idx ?? '?') + ' starting (' +
             (ev.n_sections_in_stage || 0) + ' sections in parallel: ' +
             sids + ')';
    } else if (ev.kind === 'section_draft_done') {
      const di = (ev.draft_idx ?? 0) + 1;
      const tot = ev.n_total || 3;
      const sid = ev.section_id || '?';
      const dep = ev.deployment ? ' [' + ev.deployment + ']' : '';
      if (ev.ok) {
        text = '· ' + sid + ' draft ' + di + '/' + tot + ' done · ' +
               (ev.n_paragraphs || 0) + ' paras, ' +
               (ev.n_citations || 0) + ' cites, ' +
               (ev.wall_ms || 0) + ' ms' +
               (ev.n_violations ? ', ' + ev.n_violations + ' viol' : '') + dep;
      } else {
        text = '· ' + sid + ' draft ' + di + '/' + tot + ' FAILED: ' +
               (ev.error || 'unknown');
      }
    } else if (ev.kind === 'section_picked') {
      const sid = ev.section_id || '?';
      const fb = ev.fallback ? ' [fallback=' + ev.fallback + ']' : '';
      const dep = ev.deployment_critic ? ' [' + ev.deployment_critic + ']' : '';
      if (ev.chosen_idx === -1) {
        text = '· ' + sid + ' all 3 drafts failed → placeholder';
      } else {
        text = '· ' + sid + ' picked draft ' + ev.chosen_idx +
               ' (score=' + (ev.structural_score || 0).toFixed(2) +
               (ev.n_violations ? ', ' + ev.n_violations + ' viol' : '') +
               ')' + fb + dep;
      }
    } else if (ev.kind === 'section_done') {
      const sid = ev.section_id || '?';
      const fb = ev.fallback ? ' [' + ev.fallback + ']' : '';
      text = '· ' + sid + ' written — ' + (ev.n_paragraphs || 0) + ' paras, ' +
             (ev.n_code_refs || 0) + ' refs, ' +
             (ev.n_citations || 0) + ' cites, ' +
             ((ev.total_chars || 0) / 1000).toFixed(1) + 'k chars, ' +
             (ev.wall_ms || 0) + ' ms' + fb;
    } else if (ev.kind === 'stage_done') {
      text = '✓ stage ' + (ev.stage_idx ?? '?') + ' complete: ' +
             (ev.n_completed || 0) + ' sections written, ' +
             (ev.n_failed || 0) + ' failed (' +
             (ev.wall_ms || 0) + ' ms)';
    } else if (ev.kind === 'done') {
      text = '✓ done — ' + (ev.n_completed || 0) + '/' +
             (ev.n_sections || 0) + ' sections, ' +
             (ev.n_fallback || 0) + ' fallbacks, ' +
             (ev.n_repairs || 0) + ' repairs, ' +
             (ev.total_drafts_fired || 0) + ' drafts fired' +
             ' (' + (ev.wall_ms || 0) + ' ms)';
    }
  }
  // checklist_eval — 12 binary criteria (7 deterministic pre-gates +
  // 5 LLM-judge). Fast node (1 LLM call S.total). Emits 4 event kinds.
  if (stepName === 'checklist_eval') {
    if (ev.kind === 'start') {
      text = '· starting checklist for ' + (ev.chapter_title || ev.chapter_id || 'chapter') +
             ' (' + (ev.n_total_criteria || 0) + ' criteria, threshold ' +
             ((ev.pass_threshold || 0.8) * 100).toFixed(0) + '%)';
    } else if (ev.kind === 'pregates_done') {
      const failed = ev.names_failed || [];
      text = '· pre-gates: ' + (ev.n_passed || 0) + '/' +
             (ev.n_pregate || 0) + ' passed' +
             (failed.length
                ? ' · failed: ' + failed.slice(0, 3).join(', ') +
                  (failed.length > 3 ? ` (+${failed.length - 3})` : '')
                : '');
    } else if (ev.kind === 'judge_request') {
      text = '· LLM judge: dispatching (' +
             ((ev.chapter_chars || 0) / 1000).toFixed(1) + 'k chars chapter' +
             (ev.truncated ? ', truncated' : '') + ')…';
    } else if (ev.kind === 'judge_done') {
      const failed = ev.names_failed || [];
      const dep = ev.deployment ? ' [' + ev.deployment + ']' : '';
      const rep = ev.repaired ? ' (repaired)' : '';
      text = '· LLM judge done: ' + (ev.n_passed || 0) + '/' +
             (ev.n_llm || 0) + ' passed' + rep +
             (failed.length
                ? ' · failed: ' + failed.slice(0, 3).join(', ') +
                  (failed.length > 3 ? ` (+${failed.length - 3})` : '')
                : '') +
             ' (' + (ev.wall_ms || 0) + ' ms)' + dep;
    } else if (ev.kind === 'done') {
      const passMark = ev.chapter_passed ? '✓ PASSED' : '✗ FAILED';
      text = '✓ done — ' + passMark + ' — ' +
             (ev.n_passed || 0) + '/' + (ev.n_total || 0) +
             ' criteria (' + ((ev.pass_rate || 0) * 100).toFixed(0) + '%), ' +
             (ev.n_failed_feedback || 0) + ' feedback notes' +
             ' (' + (ev.wall_ms || 0) + ' ms)';
    }
  }
  // render_audit_write — Final node. Zero LLM calls. Renders 3
  // artifacts (README.md, challenges.md, flashcards.json) via Jinja2
  // + runs SHA-256 round-trip audit on code refs. 5 event kinds.
  if (stepName === 'render_audit_write') {
    if (ev.kind === 'start') {
      text = '· starting render for ' + (ev.chapter_title || ev.chapter_id || 'chapter') +
             ' (' + (ev.n_sections || 0) + ' sections, ' +
             (ev.n_challenges || 0) + ' challenges, ' +
             (ev.n_flashcards || 0) + ' flashcards · mgsr ' +
             (ev.mgsr_halt_reason || '?') + ')';
    } else if (ev.kind === 'inputs_loaded') {
      text = '· vaults loaded: ' + (ev.n_vault_files_loaded || 0) + '/' +
             (ev.n_sources || 0) + ' source vaults' +
             (ev.n_vault_files_skipped
               ? ', ' + ev.n_vault_files_skipped + ' skipped'
               : '') +
             ' · ' + (ev.n_vault_entries || 0) + ' total vault entries';
    } else if (ev.kind === 'rendered') {
      const auditMark = ev.audit_passed ? '✓' : '✗';
      text = '· rendered chapter (' +
             ((ev.chapter_chars || 0) / 1000).toFixed(1) + 'k chars, ' +
             (ev.n_sections_rendered || 0) + ' sections) · ' +
             'audit=' + auditMark + ' refs=' +
             (ev.n_code_refs_resolved || 0) + '/' +
             ((ev.n_code_refs_resolved || 0) +
              (ev.n_code_refs_missing || 0)) +
             (ev.n_code_refs_missing
               ? ' · miss=' + ev.n_code_refs_missing : '') +
             (ev.n_code_refs_drift
               ? ' · drift=' + ev.n_code_refs_drift : '') +
             (ev.sentinels_in_output
               ? ' · sent=' + ev.sentinels_in_output : '');
    } else if (ev.kind === 'artifacts_written') {
      const names = (ev.artifact_names || []).join(', ');
      text = '· wrote ' + (ev.n_artifacts || 0) + ' artifacts (' +
             ((ev.total_bytes || 0) / 1000).toFixed(1) + 'k bytes total) — ' +
             names;
    } else if (ev.kind === 'done') {
      const mark = ev.audit_passed ? '✓ AUDIT PASSED' : '✗ AUDIT FAILED';
      text = '✓ done — ' + mark + ' · ' +
             (ev.n_artifacts || 0) + ' artifacts, ' +
             ((ev.rendered_chars || 0) / 1000).toFixed(1) + 'k chars rendered' +
             (ev.n_missing ? ' · ' + ev.n_missing + ' missing refs' : '') +
             (ev.n_byte_drift ? ' · ' + ev.n_byte_drift + ' drift' : '') +
             (ev.sentinels_in_output
               ? ' · ' + ev.sentinels_in_output + ' unresolved sentinels'
               : '') +
             ' (' + (ev.wall_ms || 0) + ' ms)';
    }
  }
  // mgsr_replan — Memory-Guided Structure Replanner (SurveyGen-I +
  // CoRefine). Fast path = trivial_pass (no LLM call) when chapter
  // already passed checklist. Slow path = 1 LLM call emitting typed
  // replan actions on the outline DAG. 5 event kinds.
  if (stepName === 'mgsr_replan') {
    if (ev.kind === 'start') {
      const fmtRate = ((ev.pass_rate || 0) * 100).toFixed(0);
      text = '· starting replan for ' + (ev.chapter_title || ev.chapter_id || 'chapter') +
             ' (pass=' + fmtRate + '%, ' +
             (ev.n_failed_criteria || 0) + ' failed criteria)';
    } else if (ev.kind === 'trivial_pass') {
      text = '· chapter already passed (' +
             ((ev.pass_rate || 0) * 100).toFixed(0) +
             '%) — halting trivially, no LLM call';
    } else if (ev.kind === 'llm_request') {
      text = '· LLM replan: dispatching (' +
             (ev.n_failed_criteria || 0) + ' failed criteria)…';
    } else if (ev.kind === 'llm_done') {
      const dep = ev.deployment ? ' [' + ev.deployment + ']' : '';
      const rep = ev.repaired ? ' (repaired)' : '';
      const halt = ev.halt ? 'halt' : 'continue';
      if (ev.error) {
        text = '· LLM replan FAILED — fallback halt (' +
               (ev.wall_ms || 0) + ' ms)';
      } else {
        text = '· LLM replan done: ' + halt + ', ' +
               (ev.n_actions || 0) + ' actions, conf=' +
               ((ev.confidence || 0) * 100).toFixed(0) + '%' +
               rep + ' (' + (ev.wall_ms || 0) + ' ms)' + dep;
      }
    } else if (ev.kind === 'done') {
      const mark = ev.halt ? '✓ HALT' : '↻ LOOP';
      text = '✓ done — ' + mark + ' (' + (ev.halt_reason || '?') + '), ' +
             (ev.n_actions || 0) + ' actions, conf=' +
             ((ev.confidence || 0) * 100).toFixed(0) + '%' +
             ' (' + (ev.wall_ms || 0) + ' ms)';
    }
  }
  if (text) el.textContent = text;
}

export function renderSynthCards(values, nextNodes) {
  // Cards DOM was removed 2026-05-19 — S.synthCardsEl is null. The
  // per-card loop below now early-skips at `if (!c) continue;` but
  // `_renderSynthGraph` + `_refreshOpenSynthDrawer` at the tail MUST
  // still fire (they own the graph-canvas + drawer state). Previous
  // `if (!synthCardsEl) return;` short-circuit silently broke them.
  //
  // CoRefine fix (see _renderSynthGraph): `nextNodes` comes from
  // snap.next on /state polls. When set, treat any node in it as
  // the running one regardless of field presence; falls through to
  // field-presence + first-not-done heuristic otherwise.
  const nextSet = (Array.isArray(nextNodes) && nextNodes.length > 0)
    ? new Set(nextNodes) : null;
  const useAuthoritative = nextSet !== null && S.synthThreadId !== null;
  let doneCount = 0;
  for (let i = 0; i < S.SYNTH_SUBSTEP_FIELDS.length; i++) {
    const field = S.SYNTH_SUBSTEP_FIELDS[i];
    const nodeId = S.SYNTH_NODE_ORDER[i];
    const c = synthCardEl(i);
    if (!c) {
      // Without cards we can't count done state from the DOM, so
      // derive it from values directly to keep the "first not-done
      // → running" canvas logic intact.
      if (_synthFieldPresent(values, field)) doneCount++;
      continue;
    }
    const icon = c.querySelector('.fw-planner-card-icon');
    const body = c.querySelector('.fw-planner-card-body');
    const present = _synthFieldPresent(values, field);
    const cardData = c.dataset.substep || '';
    const isImplemented = S.synthImplemented.has(cardData);
    if (useAuthoritative && nextSet.has(nodeId)) {
      // Authoritative running signal — beats field-presence so CoRefine
      // loopbacks display the currently-re-executing node correctly.
      c.classList.add('running');
      c.classList.remove('done', 'failed', 'future');
      icon.textContent = '◐'; icon.dataset.status = 'running';
    } else if (present) {
      c.classList.add('done');
      c.classList.remove('running', 'failed', 'future');
      icon.textContent = '●'; icon.dataset.status = 'done';
      const renderer = S.SYNTH_SUBSTEP_RENDERERS[i];
      if (renderer) {
        body.innerHTML = renderer(values);
      } else {
        const v = values[field];
        body.innerHTML = '<pre>' + escapeHtml(formatFieldValue(v)) + '</pre>';
      }
      doneCount++;
    } else if (!isImplemented) {
      // Stub — render as future (⏳).
      c.classList.add('future');
      c.classList.remove('running', 'done', 'failed');
      icon.textContent = '⏳'; icon.dataset.status = 'future';
      body.innerHTML =
        '<div class="fw-empty">Substep not yet implemented — will be ' +
        'wired into the graph as its real logic lands.</div>';
    } else if (i === doneCount && S.synthThreadId !== null) {
      // First not-done IMPLEMENTED card while polling = currently running.
      c.classList.add('running');
      c.classList.remove('done', 'failed', 'future');
      icon.textContent = '◐'; icon.dataset.status = 'running';
    } else {
      c.classList.remove('running', 'done', 'failed', 'future');
      icon.textContent = '○'; icon.dataset.status = 'pending';
    }
  }
  // Mirror state into the Cytoscape canvas (no-op when ?ui=cards).
  // Drives node colors + KPI badges + the top-of-stage status pill.
  _renderSynthGraph(values, nextNodes);
  // Live-refresh drawer if open for a synth node (same pattern as
  // planner — _refreshOpenSynthDrawer is a no-op when not open).
  _refreshOpenSynthDrawer(values);
}

export function markSynthFailed(message) {
  let failedNodeId = null;
  for (let i = 0; i < S.SYNTH_SUBSTEP_FIELDS.length; i++) {
    const c = synthCardEl(i);
    if (!c) continue;
    if (c.classList.contains('running') ||
        (!c.classList.contains('done') && !c.classList.contains('failed') &&
         !c.classList.contains('future'))) {
      c.classList.remove('running');
      c.classList.add('failed', 'expanded');
      const icon = c.querySelector('.fw-planner-card-icon');
      icon.textContent = '✕';
      icon.dataset.status = 'failed';
      c.querySelector('.fw-planner-card-body').innerHTML =
        '<div class="fw-planner-error">' + escapeHtml(message) + '</div>';
      failedNodeId = S.SYNTH_NODE_ORDER[i];
      break;
    }
  }
  if (S.synthGraph && failedNodeId) S.synthGraph.setStatus(failedNodeId, 'failed');
  _setSynthStagePill('failed');
}

export function resetSynthCards() {
  S.SYNTH_SUBSTEP_FIELDS.forEach((_, i) => {
    const c = synthCardEl(i);
    if (!c) return;
    c.classList.remove('running', 'done', 'failed', 'expanded');
    const substep = c.dataset.substep || '';
    // Stubs go back to future (⏳); implemented nodes go to pending (○).
    const isImpl = S.synthImplemented.has(substep);
    c.classList.toggle('future', !isImpl);
    const icon = c.querySelector('.fw-planner-card-icon');
    icon.textContent = isImpl ? '○' : '⏳';
    icon.dataset.status = isImpl ? 'pending' : 'future';
    c.querySelector('.fw-planner-card-latency').textContent = '';
    c.querySelector('.fw-planner-card-body').innerHTML = isImpl
      ? '<div class="fw-empty">Output will appear here once the substep runs.</div>'
      : '<div class="fw-empty">Substep not yet implemented — will be ' +
        'wired into the graph as its real logic lands.</div>';
  });
  // Day 5: also reset the Cytoscape canvas + stage pill on Start.
  if (S.synthGraph) S.synthGraph.reset();
  _setSynthStagePill('idle');
}

export function refreshSynthStartState() {
  if (!S.synthStartBtn) return;
  // Three states for the Start/Cancel button (mirrors planner):
  //  - running        → "Cancel Synth"
  //  - idle, ready    → "Start Synth" enabled
  //  - idle, blocked  → "Start Synth" disabled
  // Until the first synth node ships, "ready" requires the server's
  // /synth/info implemented list to be non-empty — otherwise clicking
  // Start would just hit the 503 stub. Show the button but disabled
  // with a clarifying tooltip so the user sees the path is wired but
  // not yet active.
  const running = S.synthThreadId !== null || S.studyThreadId !== null;
  if (running) {
    S.synthStartBtn.removeAttribute('disabled');
    S.synthStartBtn.classList.add('btn-outline');
    S.synthStartBtn.classList.remove('btn-primary');
    S.synthStartBtn.innerHTML = 'Cancel Synth';
  } else {
    const hasNodes = S.synthImplemented && S.synthImplemented.size > 0;
    // Synth REQUIRES a planner plan — block Start until one exists for
    // this framework (mirrors the server-side _load_plan 404 guard, so
    // the disabled button and the API agree). See main.js initSynth /
    // _hydrateChStripFromChapters which set S.synthHasPlan.
    const ready = S.activeSlug && S.activeRunId === null
                  && hasNodes && S.synthHasPlan;
    if (ready) {
      S.synthStartBtn.removeAttribute('disabled');
      S.synthStartBtn.removeAttribute('title');
    } else {
      S.synthStartBtn.setAttribute('disabled', 'disabled');
      if (!hasNodes) {
        S.synthStartBtn.setAttribute(
          'title',
          'Synth pipeline not yet implemented — substeps light up as nodes ship.',
        );
      } else if (!S.activeSlug) {
        S.synthStartBtn.setAttribute('title', 'Pick a framework first.');
      } else if (!S.synthHasPlan) {
        S.synthStartBtn.setAttribute(
          'title',
          'Run the Planner first — Synth needs a chapter plan for this framework.',
        );
      }
    }
    S.synthStartBtn.classList.add('btn-primary');
    S.synthStartBtn.classList.remove('btn-outline');
    S.synthStartBtn.innerHTML = 'Start Synth';
  }
  if (S.synthWipeBtn) {
    if (S.activeSlug && !running && S.synthImplemented.size > 0) {
      S.synthWipeBtn.removeAttribute('disabled');
      S.synthWipeBtn.setAttribute('title',
        "Delete this framework's synth cache " +
        '(MinIO chapter artifacts + Postgres checkpoints + browser state)');
    } else {
      S.synthWipeBtn.setAttribute('disabled', 'disabled');
      S.synthWipeBtn.setAttribute('title', running
        ? 'Cannot wipe while a synth run is in flight.'
        : (S.synthImplemented.size === 0
            ? 'Synth pipeline not yet implemented.'
            : 'Pick a framework first.'));
    }
  }
  // Framework chip + stage-pill aggregate state.
  setSynthFramework(S.activeSlug);
  if (!running) {
    // When idle, pill reflects "have any synth output for this slug?"
    // — but since no nodes are implemented yet, default to 'idle'.
    // _renderSynthGraph overrides this on the next state refresh.
    _setSynthStagePill('idle');
  }
  // Empty-state placeholder — hide the cards/canvas when no slug
  // is active so the panel doesn't show an inert pipeline UI.
  // _toggleStageEmpty lives in planner.js — dynamic import.
  import('./planner.js').then(m => m._toggleStageEmpty('synth', !S.activeSlug));
}

export function setSynthFramework(slug) {
  if (!S.synthFwNameEl || !S.synthFwLogosEl) return;
  if (!slug) {
    S.synthFwNameEl.textContent = 'Pick a framework to start.';
    S.synthFwNameEl.classList.add('fw-planner-fw-name-empty');
    S.synthFwLogosEl.innerHTML = '';
    S.synthFwLogosEl.style.display = 'none';
    return;
  }
  const info = S.frameworkInfo[slug] || {name: slug, logos: []};
  S.synthFwNameEl.textContent = info.name || slug;
  S.synthFwNameEl.classList.remove('fw-planner-fw-name-empty');
  if (info.logos && info.logos.length) {
    S.synthFwLogosEl.innerHTML = info.logos.map(u =>
      '<img class="fw-planner-fw-logo" src="' + u + '" alt="">'
    ).join('');
    S.synthFwLogosEl.style.display = '';
  } else {
    S.synthFwLogosEl.innerHTML = '';
    S.synthFwLogosEl.style.display = 'none';
  }
}

// Race-tolerant state fetch (mirrors planner's _refreshCardsFromState).
export async function _refreshSynthCardsFromState(threadId, expectedField) {
  const maxAttempts = expectedField ? 6 : 1;
  for (let i = 0; i < maxAttempts; i++) {
    try {
      const r = await fetch(S.API + '/synth/debug/graph/' + threadId + '/state');
      if (r.ok) {
        const data = await r.json();
        const values = data.values || {};
        // data.next is LangGraph's snap.next — the authoritative set of
        // nodes currently scheduled / executing. Passing it through lets
        // _renderSynthGraph distinguish CoRefine-loop re-entries from
        // truly completed steps (see comment there).
        const nextNodes = Array.isArray(data.next) ? data.next : null;
        if (!expectedField || _synthFieldPresent(values, expectedField)) {
          renderSynthCards(values, nextNodes);
          return;
        }
      }
    } catch (e) { /* transient */ }
    await sleep(250 + 150 * i);
  }
}

// ──────────────────────────────────────────────────────────────────
// Chapter progress strip — visible only during STUDY-mode runs.
// ──────────────────────────────────────────────────────────────────

export function _showChStrip(visible) {
  if (!S.chstripEl) return;
  S.chstripEl.classList.toggle('visible', !!visible);
  // Showing/hiding the 30% chapter panel reflows the graph column
  // (100% ↔ ~70%). Cytoscape latches its container size, so re-fit on
  // the next frame (after layout settles) or the DAG renders at the
  // stale width. No-op until the canvas is mounted.
  if (S.synthGraph) {
    requestAnimationFrame(() => { try { _resizeSynthCanvas(); } catch (_) {} });
  }
}
// Derive a readable label from a chapter id when no real title is
// available yet (live SSE path only carries ids). Strips the "ch-NN-"
// prefix and turns separators into spaces:
//   ch-01-introduction-to-pydantic-basics → "Introduction to pydantic basics"
// Used only as a fallback — _applyChStripTitles upgrades to the exact
// backend title (e.g. "Introduction to Pydantic Basics") right after.
function _humanizeChapterId(id) {
  const s = String(id || '')
    .replace(/^ch[-_]?\d+[-_]?/i, '')
    .replace(/[-_]+/g, ' ')
    .trim();
  if (!s) return String(id || '');
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// Render the Chapters checklist. `items` may be an array of id STRINGS
// (live SSE / POST paths, ids only) OR {id, title} OBJECTS (durable
// hydrate path, exact titles). Vertical task-list layout: status glyph
// + ordinal + chapter title, one row per chapter (DD-CHAPTERS-SOTA
// 2026-05-28 — the agent/pipeline task-list pattern).
export function _renderChStrip(items) {
  if (!S.chstripCellsEl) return;
  const norm = (items || []).map(it =>
    (typeof it === 'string')
      ? { id: it, title: null }
      : { id: it.id, title: it.title || null }
  );
  const ids = norm.map(c => c.id);
  S.setStudyChapterIds(ids.slice());
  S.setStudyChapterStatus(new Map(ids.map(id => [id, 'pending'])));
  S.setStudyCurrentChapterId(null);
  S.chstripCellsEl.innerHTML = norm.map((c, i) => {
    const title = c.title || _humanizeChapterId(c.id);
    // title="" → full chapter name on hover when the row ellipsis-
    // truncates it (single-line rows; SOTA truncate+tooltip pattern).
    return (
      '<div class="fw-chstrip-cell" data-status="pending" ' +
      'data-chapter-id="' + c.id.replace(/"/g, '&quot;') + '" ' +
      'title="' + escapeHtml(title) + '">' +
      '  <span class="icon"></span>' +
      '  <span class="num">' + (i + 1) + '</span>' +
      '  <span class="label">' + escapeHtml(title) + '</span>' +
      '</div>'
    );
  }).join('');
  _updateChStripCounter();
}

// Upgrade the checklist labels from id-derived fallbacks to the exact
// backend titles. Called right after a live _renderChStrip(ids) so the
// rows show real chapter names within one fetch. Silent on failure —
// the humanized fallback stays.
export async function _applyChStripTitles(slug) {
  if (!slug || !S.chstripCellsEl) return;
  try {
    const r = await fetch(S.API + '/synth/' + slug + '/study/chapters');
    if (!r.ok) return;
    const data = await r.json();
    (data.chapters || []).forEach(c => {
      if (!c || !c.id || !c.title) return;
      const cell = S.chstripCellsEl.querySelector(
        '.fw-chstrip-cell[data-chapter-id="' + c.id.replace(/"/g, '\\"') + '"]'
      );
      if (!cell) return;
      const lbl = cell.querySelector('.label');
      if (lbl) lbl.textContent = c.title;
      cell.title = c.title;   // keep the hover tooltip in sync
    });
  } catch (_) { /* keep humanized fallback */ }
}
export function _markChStripCell(chapterId, status) {
  if (!S.chstripCellsEl) return;
  S.studyChapterStatus.set(chapterId, status);
  const cell = S.chstripCellsEl.querySelector(
    '.fw-chstrip-cell[data-chapter-id="' + chapterId.replace(/"/g, '\\"') + '"]'
  );
  if (cell) cell.dataset.status = status;
  _updateChStripCounter();
}
export function _updateChStripCounter() {
  if (!S.chstripCounterEl) return;
  let done = 0, failed = 0, total = S.studyChapterIds.length;
  for (const s of S.studyChapterStatus.values()) {
    if (s === 'done') done++;
    else if (s === 'failed' || s === 'cancelled') failed++;
  }
  const txt = failed
    ? (done + ' done, ' + failed + ' failed / ' + total)
    : (done + ' / ' + total);
  S.chstripCounterEl.textContent = txt;
}
export function _resetStudyState() {
  S.setStudyThreadId(null);
  S.setStudyChapterIds([]);
  S.setStudyChapterStatus(new Map());
  S.setStudyCurrentChapterId(null);
  S.setStudyCurrentChapterThreadId(null);
  S.setStudyChapterThreads(new Map());
  S.setStudyPinnedChapterId(null);
  if (S.chstripCellsEl) S.chstripCellsEl.innerHTML = '';
  if (S.chstripCounterEl) S.chstripCounterEl.textContent = '';
  _showChStrip(false);
}

// Plan-existence gate for the Start Synth button. Synth REQUIRES a
// planner plan; GET /synth/{slug}/study/chapters returns 404 when none
// exists (it calls _load_plan server-side), so `r.ok` ⇔ a plan is
// written. This mirrors the server's _load_plan guard so the disabled
// button and the API agree (no bypass via a stray click). Fail-safe:
// any error → treated as "no plan" → button stays blocked.
export async function _refreshSynthPlanGate(slug) {
  let hasPlan = false;
  try {
    if (slug) {
      const r = await fetch(S.API + '/synth/' + slug + '/study/chapters');
      if (r.ok) {
        const data = await r.json();
        hasPlan = (((data && data.chapters) || []).length > 0);
      }
    }
  } catch (_) { /* network hiccup → no plan */ }
  S.setSynthHasPlan(hasPlan);
  refreshSynthStartState();
}

// Durable strip reconstruction — rebuilds the chapter progress strip from
// MinIO-backed render status (GET /synth/{slug}/study/chapters) instead of
// the ephemeral SSE snapshot. THIS is what makes the strip survive a page
// refresh after a study run finishes.
export async function _hydrateChStripFromChapters(slug) {
  if (!slug || !S.chstripCellsEl) return false;
  try {
    const r = await fetch(S.API + '/synth/' + slug + '/study/chapters');
    if (!r.ok) return false;
    const data = await r.json();
    const chapters = (data.chapters || []).slice()
      .sort((a, b) => (a.order || 0) - (b.order || 0));
    if (chapters.length < 2) { _showChStrip(false); return false; }
    // Durable path — chapters carry exact titles; pass them through.
    _renderChStrip(chapters.map(c => ({ id: c.id, title: c.title })));
    chapters.forEach(c => {
      if (!c) return;
      if (c.rendered) _markChStripCell(c.id, 'done');
      // Persist the durable thread_id (from render-latest.json) so a
      // post-refresh click can re-open the chapter's graph canvas.
      if (c.thread_id) {
        S.studyChapterThreads.set(c.id, c.thread_id);
        const cell = S.chstripCellsEl.querySelector(
          '.fw-chstrip-cell[data-chapter-id="' + c.id.replace(/"/g, '\\"') + '"]'
        );
        if (cell) cell.dataset.chapterThreadId = c.thread_id;
      }
    });
    _showChStrip(true);
    return true;
  } catch (e) {
    return false;
  }
}

// Visual: highlight the strip cell whose chapter the canvas is currently
// showing. Mutually exclusive — clears any prior selection.
export function _highlightStripCell(chapterId) {
  if (!S.chstripCellsEl) return;
  S.chstripCellsEl.querySelectorAll('.fw-chstrip-cell.selected')
    .forEach(c => c.classList.remove('selected'));
  if (!chapterId) return;
  const cell = S.chstripCellsEl.querySelector(
    '.fw-chstrip-cell[data-chapter-id="' + chapterId.replace(/"/g, '\\"') + '"]'
  );
  if (cell) cell.classList.add('selected');
}

// Strip-cell click handler — wires the "switch canvas to this chapter"
// behavior.
export function _onStripCellClick(cellEl) {
  if (!cellEl) return;
  const cid = cellEl.dataset.chapterId;
  if (!cid) return;
  const status = cellEl.dataset.status || 'pending';
  const chTid = cellEl.dataset.chapterThreadId
              || S.studyChapterThreads.get(cid)
              || null;

  // Unpin if user clicks the currently-running cell while pinned to it.
  if (cid === S.studyCurrentChapterId && S.studyPinnedChapterId === cid) {
    S.setStudyPinnedChapterId(null);
    _highlightStripCell(cid);   // stays highlighted as the running one
    return;
  }
  // Already showing this chapter's canvas — just pin/highlight, don't
  // reopen SSE (which would duplicate live event streams).
  if (chTid && S.synthThreadId === chTid) {
    S.setStudyPinnedChapterId(cid);
    _highlightStripCell(cid);
    return;
  }
  S.setStudyPinnedChapterId(cid);
  _highlightStripCell(cid);

  // No thread for this chapter.
  if (!chTid) {
    S.setSynthThreadId(null);
    resetSynthCards();
    _resetSynthEventBuffer();
    if (_nodeDrawerRef && _nodeDrawerRef.reset) {
      _nodeDrawerRef.reset();
    }
    try { renderSynthCards({}); } catch (_) {}
    if (status === 'done') {
      showToast('This chapter was rendered before graph-history tracking ' +
                'was added. Re-run Synth to inspect its node graph.');
    }
    return;
  }

  // Switch the canvas to the clicked chapter's thread.
  S.setSynthThreadId(chTid);
  resetSynthCards();
  _resetSynthEventBuffer();
  if (_nodeDrawerRef && _nodeDrawerRef.reset) {
    _nodeDrawerRef.reset();
  }
  // Initial paint from checkpoint state.
  (async () => {
    try {
      const r = await fetch(S.API + '/synth/debug/graph/' + chTid + '/state');
      if (r.ok) {
        const data = await r.json();
        renderSynthCards(
          data.values || {},
          Array.isArray(data.next) ? data.next : null,
        );
      }
    } catch (_) {}
    S.set_synthLiveEventReceived(false);
    pollSynthState(chTid);
  })();
}

if (S.chstripCellsEl) {
  S.chstripCellsEl.addEventListener('click', ev => {
    const cell = ev.target.closest('.fw-chstrip-cell');
    if (cell) _onStripCellClick(cell);
  });
}

// SSE consumer for the STUDY-LEVEL channel — receives orchestrator
// events (study_start, chapter_running, chapter_done, study_done).
export async function pollStudyState(sid) {
  const url = S.API + '/synth/' + sid + '/events';
  let es;
  try {
    es = new EventSource(url);
  } catch (e) {
    markSynthFailed('Study EventSource open failed: ' + String(e));
    _resetStudyState();
    refreshSynthStartState();
    return;
  }
  // Helper: open per-chapter SSE for the currently-active chapter if
  // we haven't already. Debounced (120 ms).
  let _studyAttachTimer = null;
  const _maybeAttachCurrentChapterSSE = () => {
    // If the user pinned to a specific chapter (clicked its strip cell),
    // do NOT yank the canvas back to the orchestrator's current chapter.
    if (S.studyPinnedChapterId &&
        S.studyPinnedChapterId !== S.studyCurrentChapterId) return;
    const chTid = S.studyCurrentChapterThreadId;
    if (!chTid) return;
    if (S.synthThreadId === chTid) return;
    resetSynthCards();
    _resetSynthEventBuffer();
    if (_nodeDrawerRef && _nodeDrawerRef.reset) {
      _nodeDrawerRef.reset();
    }
    S.setSynthThreadId(chTid);
    S.set_synthLiveEventReceived(false);
    pollSynthState(chTid);
    _highlightStripCell(S.studyCurrentChapterId);
  };
  const _scheduleAttachCurrent = () => {
    if (_studyAttachTimer) clearTimeout(_studyAttachTimer);
    _studyAttachTimer = setTimeout(() => {
      _studyAttachTimer = null;
      _maybeAttachCurrentChapterSSE();
    }, 120);
  };

  es.onmessage = (msg) => {
    if (S.studyThreadId !== sid) {
      try { es.close(); } catch (_) {}
      return;
    }
    let ev;
    try { ev = JSON.parse(msg.data); } catch (_) { return; }

    if (ev.step === 'study' && ev.kind === 'study_start') {
      const ids = ev.chapter_ids || [];
      _renderChStrip(ids);
      _applyChStripTitles(S.activeSlug);   // upgrade ids → real titles
      _showChStrip(true);
      _setSynthStagePill('working', 'Study running (0 / ' + ids.length + ')');
      return;
    }
    if (ev.step === 'study' && ev.kind === 'chapter_running') {
      const cid = ev.chapter_id;
      const chTid = ev.chapter_thread_id;
      S.setStudyCurrentChapterId(cid);
      S.setStudyCurrentChapterThreadId(chTid || null);
      if (cid && chTid) {
        S.studyChapterThreads.set(cid, chTid);
        // Stash on the cell dataset too.
        const cell = S.chstripCellsEl && S.chstripCellsEl.querySelector(
          '.fw-chstrip-cell[data-chapter-id="' + cid.replace(/"/g, '\\"') + '"]'
        );
        if (cell) cell.dataset.chapterThreadId = chTid;
      }
      _markChStripCell(cid, 'running');
      _setSynthStagePill('working',
        'Chapter ' + (ev.position || '?') + ' / ' +
        (ev.n_total || S.studyChapterIds.length) + ' — ' + cid);
      _scheduleAttachCurrent();
      return;
    }
    if (ev.step === 'study' && ev.kind === 'chapter_done') {
      const cid = ev.chapter_id;
      const status = ev.status || 'done';
      _markChStripCell(cid, status);
      if (status === 'failed') {
        showToast('Chapter ' + cid + ' failed: ' +
          (ev.error || 'unknown error') + ' — continuing.');
      }
      if (cid === S.studyCurrentChapterId) {
        S.setStudyCurrentChapterId(null);
        S.setStudyCurrentChapterThreadId(null);
      }
      S.setSynthThreadId(null);
      // NOTE: Step-5 auto-refresh moved to `chapter_ready` so the Study
      // page reloads its chapter list THE INSTANT each chapter becomes
      // readable, not after the whole book finishes.
      return;
    }
    // Bundle 6 (2026-05-25) — Streaming chapter delivery.
    // `chapter_ready` fires the moment a chapter's render_audit_write
    // completes successfully — the chapter is now readable in MinIO. We:
    //   - lock the cell to the `done` visual (idempotent with chapter_done)
    //   - surface a toast (only for the user actively waiting on Study)
    //   - reload the Step-5 Study list so the new chapter appears
    //     immediately, instead of after ~2h when the orchestrator emits
    //     its final `done` event.
    if (ev.step === 'study' && ev.kind === 'chapter_ready') {
      const cid = ev.chapter_id;
      _markChStripCell(cid, 'done');
      try {
        const studyPanel = document.querySelector('#fw-step-5-panel');
        if (studyPanel && studyPanel.classList.contains('active') &&
            S.activeSlug) {
          import('./study.js').then(m => m.loadStudyChapters(S.activeSlug)).catch(() => {});
        }
      } catch (_) {}
      // Friendly notification so the user knows they can start reading.
      try {
        const pos = ev.position || '?';
        const total = ev.n_total || S.studyChapterIds.length;
        showToast('Chapter ' + cid + ' ready to read (' + pos + ' / ' + total + ')');
      } catch (_) {}
      return;
    }
    // Ship #7 (2026-05-24): book_harmonize study-level events
    if (ev.step === 'study' && ev.kind === 'book_harmonize_start') {
      _setSynthStagePill('working', 'Harmonizing chapters…');
      showToast(`Cross-chapter harmonization started (${ev.n_chapters || '?'} chapters)`);
      return;
    }
    if (ev.step === 'study' && ev.kind === 'book_harmonize_skipped') {
      console.log('[book-harmonize] skipped:', ev.reason);
      return;
    }
    if (ev.step === 'study' && ev.kind === 'book_harmonize_done') {
      const patched = ev.n_chapters_patched || 0;
      const overwritten = ev.n_chapters_overwritten || 0;
      const issues = ev.n_chapters_with_issues || 0;
      const cache = ev.cache_hit ? ' (cache)' : '';
      if (overwritten > 0) {
        showToast(
          `Harmonized ${overwritten}/${issues} chapter(s) for cross-chapter consistency${cache}`
        );
      } else if (issues === 0) {
        showToast(`Cross-chapter coherence verified, no patches needed${cache}`);
      }
      return;
    }
    if (ev.step === 'study' && ev.kind === 'study_done') {
      if (_studyAttachTimer) {
        clearTimeout(_studyAttachTimer);
        _studyAttachTimer = null;
      }
      S.setStudyCurrentChapterId(null);
      S.setStudyCurrentChapterThreadId(null);
      const ok = ev.n_completed || 0;
      const tot = ev.n_total || S.studyChapterIds.length;
      const fail = ev.n_failed || 0;
      const final = ev.final_status || 'done';
      if (final === 'cancelled') {
        showToast('Study cancelled: ' + ok + '/' + tot + ' chapters done.');
        _setSynthStagePill('cancelled');
      } else if (fail > 0) {
        showToast('Study finished with ' + fail + ' failed chapter(s); ' +
          ok + '/' + tot + ' succeeded.');
        _setSynthStagePill('done', 'Done (' + ok + '/' + tot + ')');
      } else {
        showToast('All ' + tot + ' chapters synthesized. ' +
          'Open Step 5 to study.');
        _setSynthStagePill('done', 'Done (' + ok + '/' + tot + ')');
      }
      // Study finished (or cancelled) — forget the resume key + thread.
      // Without this the key lingers and a page reload re-opens this
      // finished study's SSE, replaying its cached Redis snapshot
      // (chapter_ready + study_done) and re-marking every chapter "Done"
      // — a phantom "cached study" that survives hard refresh even after
      // the artifacts/checkpoints are wiped. The durable strip state is
      // rebuilt from MinIO render status on reload (_hydrateChStripFrom-
      // Chapters), not from this ephemeral replay, so dropping it is safe.
      if (S.activeSlug) { try { _forgetActiveStudy(S.activeSlug); } catch (_) {} }
      S.setStudyThreadId(null);
      return;
    }
    if (ev.step === 'synth' && ev.kind === 'terminal') {
      try { es.close(); } catch (_) {}
      if (S.activeSlug) _forgetActiveStudy(S.activeSlug);
      S.setStudyThreadId(null);
      refreshSynthStartState();
      return;
    }
  };
  es.onerror = () => {
    if (S.studyThreadId !== sid) {
      try { es.close(); } catch (_) {}
    }
  };
}

// SSE consumer — symmetric with pollPlannerState.
export async function pollSynthState(threadId) {
  const url = S.API + '/synth/' + threadId + '/events';
  let es;
  try {
    es = new EventSource(url);
  } catch (e) {
    markSynthFailed('EventSource open failed: ' + String(e));
    S.setSynthThreadId(null);
    refreshSynthStartState();
    return;
  }
  es.onmessage = async (msg) => {
    if (S.synthThreadId !== threadId) {
      try { es.close(); } catch (_) {}
      return;
    }
    let ev;
    try { ev = JSON.parse(msg.data); } catch (_) { return; }
    if (ev.ts && (Date.now() / 1000 - ev.ts) < 20) {
      S.set_synthLiveEventReceived(true);
    }
    if (ev.step === 'synth' && ev.kind === 'terminal') {
      await _refreshSynthCardsFromState(threadId, 'status');
      const status = ev.status || 'done';
      if (status === 'failed') {
        if (!S.studyThreadId) markSynthFailed(ev.error || 'Synth failed.');
      } else if (status === 'cancelled') {
        if (!S.studyThreadId) {
          showToast('Synth cancelled. Checkpoints up to the cancel point are preserved.');
          _setSynthStagePill('cancelled');
        }
      } else if (status === 'not_implemented') {
        // Router stub — no run happened.
      } else {
        if (!S.studyThreadId) _setSynthStagePill('done');
      }
      try { es.close(); } catch (_) {}
      if (!S.studyThreadId) {
        S.setSynthThreadId(null);
        refreshSynthStartState();
      }
      return;
    }
    if (ev.step) {
      _bufferSynthEvent(ev);
      if (ev.kind === 'start') {
        _markSynthCardRunning(ev.step);
        const stepIdx = S.SYNTH_NODE_ORDER.indexOf(ev.step);
        if (stepIdx > 0) {
          const prevStep = S.SYNTH_NODE_ORDER[stepIdx - 1];
          const prevField = S.SYNTH_STEP_TO_FIELD[prevStep];
          await _refreshSynthCardsFromState(threadId, prevField);
          _markSynthCardRunning(ev.step);
        }
      }
      if (ev.kind === 'done') {
        const field = S.SYNTH_STEP_TO_FIELD[ev.step];
        await _refreshSynthCardsFromState(threadId, field);
      }
      _renderSynthLiveProgress(ev.step, ev);
      // Day 5: route to NodeDrawer if open for this synth node.
      if (_nodeDrawerRef && _nodeDrawerRef.isOpenFor('synth', ev.step)) {
        _nodeDrawerRef.appendEvent(ev);
      }
    }
  };
  es.onerror = () => {
    if (S.synthThreadId !== threadId) {
      try { es.close(); } catch (_) {}
    }
  };
}

// Per-slug isolation — same key shape as planner, separate namespace.
export function _synthStorageKey(slug) { return 'dd:synth:active:' + slug; }
export function _rememberActiveSynth(slug, tid) {
  try {
    localStorage.setItem(_synthStorageKey(slug), tid);
    localStorage.setItem(S._LAST_SYNTH_SLUG_KEY, slug);
  } catch (e) {}
}
export function _forgetActiveSynth(slug) {
  try { localStorage.removeItem(_synthStorageKey(slug)); } catch (e) {}
}

// STUDY-mode persistence — separate namespace.
export function _studyStorageKey(slug) { return 'dd:study:active:' + slug; }
export function _rememberActiveStudy(slug, sid) {
  try {
    localStorage.setItem(_studyStorageKey(slug), sid);
    localStorage.setItem(S._LAST_SYNTH_SLUG_KEY, slug);
  } catch (e) {}
}
export function _forgetActiveStudy(slug) {
  try { localStorage.removeItem(_studyStorageKey(slug)); } catch (e) {}
}
export function _getActiveStudy(slug) {
  try { return localStorage.getItem(_studyStorageKey(slug)); }
  catch (e) { return null; }
}
export function _genSynthThreadId(slug) {
  // Canonical synth thread_id format — MUST match server-side
  // _make_thread_id in routers/v1/docs_distiller/synth.py.
  const uuid = (typeof crypto !== 'undefined' && crypto.randomUUID)
    ? crypto.randomUUID()
    : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
        const r = Math.random() * 16 | 0;
        return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
      });
  return 'docs-distiller/synth/' + slug + '/' + uuid;
}

// Page-refresh recovery for synth.
export async function _tryResumeActiveSynth(slug) {
  S.setSynthThreadId(null);
  S.setSynthHasPlan(false);   // re-gated below by _refreshSynthPlanGate
  resetSynthCards();
  refreshSynthStartState();

  // STUDY mode recovery.
  const sid = _getActiveStudy(slug);
  if (sid) {
    _resetStudyState();
    S.setStudyThreadId(sid);
    _showChStrip(true);
    _setSynthStagePill('working', 'Resuming study…');
    refreshSynthStartState();
    pollStudyState(sid);
    setTimeout(() => {
      if (S.studyThreadId === sid && S.studyChapterIds.length === 0) {
        console.log('[study-recover] no replay events in 5s; forgetting',
                    sid);
        _forgetActiveStudy(slug);
        S.setStudyThreadId(null);
        _resetStudyState();
        refreshSynthStartState();
      }
    }, 5000);
    return true;
  }

  // No in-flight study → rebuild strip from durable MinIO render status.
  _resetStudyState();
  _hydrateChStripFromChapters(slug).catch(() => {});

  let tid = null;
  try { tid = localStorage.getItem(_synthStorageKey(slug)); }
  catch (e) { return false; }
  if (!tid) return false;
  try {
    const r = await fetch(S.API + '/synth/debug/graph/' + tid + '/state');
    if (!r.ok) {
      _forgetActiveSynth(slug);
      return false;
    }
    const data = await r.json();
    const values = data.values || {};
    const nextNodes = Array.isArray(data.next) ? data.next : null;
    const status = values.status;
    const allImplDone = _synthAllImplementedComplete(values);
    const effectivelyDone = (
      status === 'failed' || status === 'cancelled' ||
      (status === 'done' && allImplDone) ||
      allImplDone
    );
    if (effectivelyDone) {
      renderSynthCards(values, nextNodes);
      return false;
    }
    // VIEW-ONLY (mirrors _tryResumeActivePlanner). A "running" synth
    // checkpoint is NOT proof of a live task — a crashed/interrupted run
    // leaves status stuck at "running" with no live process. So we paint
    // the partial progress as a STATIC snapshot and never set
    // synthThreadId (which would show "Working" + "Cancel"), never start
    // pollSynthState, and never auto-POST /resume (which would silently
    // restart synth compute just by navigating to the page). Continuing
    // is always an explicit Start Synth click (smart-resume).
    renderSynthCards(values, nextNodes);   // static view of partial progress
    _setSynthStagePill('idle');            // accurate — nothing running now
    refreshSynthStartState();              // Start Synth stays enabled
    return false;
  } catch (e) {
    _forgetActiveSynth(slug);
    return false;
  }
}

// Page-load auto-recovery — mirrors recoverActivePlanner.
export async function recoverActiveSynth() {
  if (!S.activeSlug) {
    let lastSlug = null;
    try { lastSlug = localStorage.getItem(S._LAST_SYNTH_SLUG_KEY); }
    catch (e) {}
    const keys = [];
    try {
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && k.startsWith('dd:synth:active:')) keys.push(k);
      }
    } catch (e) { return; }
    if (!keys.length) {
      try {
        const r = await fetch(S.API + '/synth/recent');
        if (r.ok) {
          const data = await r.json();
          const recent = (data && data.recent) || [];
          for (const item of recent) {
            try {
              localStorage.setItem(
                _synthStorageKey(item.slug), item.thread_id,
              );
            } catch (e) {}
          }
          if (recent.length) {
            try {
              localStorage.setItem(S._LAST_SYNTH_SLUG_KEY, recent[0].slug);
            } catch (e) {}
          }
        }
      } catch (e) {}
      return;
    }
  } else {
    await _tryResumeActiveSynth(S.activeSlug).catch(() => {});
  }
}

export async function startSynth() {
  if (!S.activeSlug || S.synthThreadId || S.studyThreadId) return;
  if (!S.synthImplemented || !S.synthImplemented.size) {
    showToast('Synth pipeline not yet implemented. UI is ready; ' +
              'substeps light up as nodes ship.');
    return;
  }
  resetSynthCards();
  _resetSynthEventBuffer();
  _resetStudyState();

  // STUDY MODE — Start Synth always fans out across ALL chapters.
  try {
    const budget = (S.synthBudgetSel && S.synthBudgetSel.value) || '5';
    const url = S.API + '/synth/' + S.activeSlug +
      '?mode=quality' +
      '&budget=' + encodeURIComponent(budget);
    const r = await fetch(url, {method: 'POST'});
    if (!r.ok) {
      const txt = await r.text();
      markSynthFailed('HTTP ' + r.status + ': ' + txt.slice(0, 400));
      return;
    }
    const data = await r.json();
    const sid = data.study_thread_id;
    const chapterIds = data.chapter_ids || [];
    if (!sid) {
      markSynthFailed('Server did not return a study_thread_id.');
      return;
    }
    S.setStudyThreadId(sid);
    _rememberActiveStudy(S.activeSlug, sid);
    _renderChStrip(chapterIds);
    _applyChStripTitles(S.activeSlug);   // upgrade ids → real titles
    _showChStrip(true);
    _setSynthStagePill('working',
      'Study running (0 / ' + chapterIds.length + ')');
    refreshSynthStartState();
    pollStudyState(sid);
  } catch (e) {
    markSynthFailed('Request failed: ' + String(e));
  }
}

// Safety-net timeout (ms) — if no SSE `terminal` arrives within this
// window the button auto-resets so the user is never stuck waiting.
// Cancel watchers poll every ~1s; a 15s ceiling gives the slowest LLM
// call enough time to land + the watcher to detect + emit terminal.
const CANCEL_TIMEOUT_MS = 15000;

// Cancel semantics (2026-05-24):
//   • The in-flight Synth node aborts; nodes that already wrote a final
//     `*-latest.json` to MinIO stay intact. LangGraph commits checkpoints
//     only AFTER a node completes, so a cancelled mid-flight node never
//     pollutes prior state.
//   • Wipe Synth is the explicit "delete EVERYTHING" path — it's a
//     separate button gated on the run being stopped first.
//   • Use Resume after cancel to re-attempt from the last completed
//     checkpoint (the in-flight node restarts cleanly).
export async function cancelSynth() {
  const tid = S.studyThreadId || S.synthThreadId;
  if (!tid) return;
  S.synthStartBtn.setAttribute('disabled', 'disabled');
  S.synthStartBtn.innerHTML =
    '<div class="fw-spinner" style="display:inline-block;' +
    'vertical-align:middle;margin-right:8px"></div>Cancelling…';

  // Safety-net timer. If the SSE terminal event doesn't fire within
  // CANCEL_TIMEOUT_MS (e.g., the chapter watcher missed the flag, the
  // pod restarted mid-cancel, or the SSE connection closed before the
  // terminal event arrived), we forcibly reset the UI so the user isn't
  // stuck. CRITICAL: this MUST clear synthThreadId AND studyThreadId
  // — otherwise refreshSynthStartState keeps `running=true` and the
  // Wipe button stays disabled even after the button looks like
  // "Start Synth". This was the cause of the "Wipe button does
  // nothing" bug. Backend cancel flag stays set (TTL=1h) so the
  // worker still drains on its own watcher tick — the state we're
  // clearing is purely browser-side.
  const safetyTimer = setTimeout(() => {
    if (S.synthStartBtn && S.synthStartBtn.innerHTML.includes('Cancelling')) {
      // Clear all thread refs so `running` flips to false everywhere.
      S.setSynthThreadId(null);
      S.setStudyThreadId(null);
      // Forget per-slug persistence too, so a page reload doesn't try
      // to re-attach to the cancelled study.
      if (S.activeSlug) {
        try { _forgetActiveStudy(S.activeSlug); } catch (_) {}
        try { _forgetActiveSynth(S.activeSlug); } catch (_) {}
      }
      // Now flip the visuals — refreshSynthStartState will see no
      // running threads → enable Wipe + reset the Start button cleanly.
      refreshSynthStartState();
      showToast(
        'Cancel sent. Cleanup is still finishing in the background. '
        + 'Previously-completed nodes are preserved — click Wipe Synth '
        + 'to delete the whole cache, or Resume to continue from the '
        + 'last checkpoint.'
      );
    }
  }, CANCEL_TIMEOUT_MS);

  try {
    const r = await fetch(S.API + '/synth/' + tid + '/cancel', {method: 'POST'});
    if (r.ok) {
      const data = await r.json().catch(() => ({}));
      const n = (data.propagated_to || []).length;
      if (n > 0) {
        console.log('[cancelSynth] cancel propagated to '
          + n + ' chapter thread(s); the in-flight node will abort. '
          + 'Previously-completed node outputs are preserved.');
      }
      // Don't reset the button here — wait for the SSE `terminal` event
      // which signals the watcher actually fired and the task cancelled.
      // The safetyTimer above is the fallback if that never happens.
    } else {
      clearTimeout(safetyTimer);
      S.synthStartBtn.removeAttribute('disabled');
      S.synthStartBtn.innerHTML = 'Cancel Synth';
      showToast('Cancel request failed: HTTP ' + r.status);
    }
  } catch (e) {
    clearTimeout(safetyTimer);
    S.synthStartBtn.removeAttribute('disabled');
    S.synthStartBtn.innerHTML = 'Cancel Synth';
    showToast('Cancel request failed: ' + String(e));
  }
}

export async function wipeSynth(slug) {
  if (!slug) return {error: 'no slug'};
  let result = {};
  try {
    const r = await fetch(S.API + '/synth/' + slug + '/wipe',
      {method: 'DELETE'});
    result = r.ok ? (await r.json()) : {http_status: r.status};
  } catch (e) { result = {error: String(e)}; }
  _forgetActiveSynth(slug);
  _forgetActiveStudy(slug);  // study-orchestrator resume key — else a wiped
                             // slug re-opens the finished study's SSE on
                             // reload and replays its snapshot (see study_done)
  // Wipe the framework's LOCAL study state too (FSRS decks + studied
  // flags + challenge grades in dd:srs:v1 / dd:study:progress:v1). These
  // aren't gated by the server, so without this the Study sidebar keeps
  // showing phantom "N studied" / due-card badges for a wiped framework.
  try { (await import('./srs.js')).forgetFramework(slug); } catch (_) {}
  if (S.activeSlug === slug) {
    S.setSynthThreadId(null);
    _resetStudyState();      // clears study-level state + HIDES the chapter strip
    resetSynthCards();
    refreshSynthStartState();
    // Re-populate the chapter strip from the planner's plan-latest.json.
    // Wipe Synth deletes the SYNTH cache (rendered chapter outputs +
    // LangGraph checkpoints) — it does NOT touch the planner. The chapter
    // list shown in the "red box" comes from the planner via
    // GET /synth/{slug}/study/chapters; after wiping synth, those
    // chapters are still listed (none rendered yet). Without this re-
    // hydrate the strip stays hidden and the user can't pick chapters to
    // re-synthesize without a page reload.
    _hydrateChStripFromChapters(slug).catch((e) => {
      console.warn('[ddWipeSynth] chapter strip rehydrate failed:', e);
    });
  }
  console.log('[ddWipeSynth]', slug, result);
  return result;
}
window.ddWipeSynth = wipeSynth;

export async function loadSynthInfo() {
  try {
    const r = await fetch(S.API + '/synth/info');
    if (!r.ok) return;
    const data = await r.json();
    S.setSynthImplemented(new Set(data.implemented || []));
    renderSynthCards({});
    refreshSynthStartState();
  } catch (e) { /* silent — defaults to all "future" */ }
}

// Synth-cards click-to-expand — mirrors the planner handler.
if (S.synthCardsEl) {
  S.synthCardsEl.addEventListener('click', ev => {
    const head = ev.target.closest('.fw-planner-card-head');
    if (!head) return;
    head.parentElement.classList.toggle('expanded');
  });
}

// Synth start/cancel button.
if (S.synthStartBtn) {
  S.synthStartBtn.addEventListener('click', () => {
    if (S.synthThreadId) cancelSynth();
    else startSynth();
  });
}
// ────────────────────────────────────────────────────────────────────
// Force-reset escape hatch (2026-05-24)
//
// If a Synth run gets into a stuck state (terminal SSE event never
// arrived, browser was offline during cancel, pod restart raced with
// cancel propagation, etc.), the Wipe button stays disabled because
// `refreshSynthStartState()` sees `running=true`. This helper gives
// the user (or me, debugging) a one-line way out: clear all in-memory
// + localStorage refs to the supposedly-running threads, refresh the
// UI, and the Wipe button becomes available again.
//
// Available globally as `window.ddForceResetSynthUI()` for console use.
window.ddForceResetSynthUI = function () {
  const beforeSynth = S.synthThreadId;
  const beforeStudy = S.studyThreadId;
  S.setSynthThreadId(null);
  S.setStudyThreadId(null);
  if (S.activeSlug) {
    try { _forgetActiveStudy(S.activeSlug); } catch (_) {}
    try { _forgetActiveSynth(S.activeSlug); } catch (_) {}
  }
  refreshSynthStartState();
  console.log(
    '[ddForceResetSynthUI] cleared synthThreadId=' + beforeSynth
    + ', studyThreadId=' + beforeStudy
    + ', activeSlug=' + S.activeSlug + ' — Wipe button is now enabled.'
  );
  showToast('Synth UI state cleared. Wipe is now available.');
  return {synthThreadId: beforeSynth, studyThreadId: beforeStudy};
};

// Synth wipe button.
//
// UX contract (2026-05-24):
//   • Wipe is BLOCKED whenever a Synth run is in flight (study-level or
//     single-chapter). The button is also marked disabled via
//     `refreshSynthStartState()`, but we re-check here as defense-in-depth
//     so the wipe can never accidentally fire during a run (e.g., if the
//     disabled attribute gets toggled by DevTools or a race between state
//     updates).
//   • If the user attempts to wipe while the UI THINKS a run is in flight,
//     we show an explicit toast directing them to Cancel Synth first OR
//     run `ddForceResetSynthUI()` in console if the state is stuck.
if (S.synthWipeBtn) {
  S.synthWipeBtn.addEventListener('click', async () => {
    console.log('[wipeSynth-click] activeSlug=' + S.activeSlug
      + ' synthThreadId=' + S.synthThreadId
      + ' studyThreadId=' + S.studyThreadId
      + ' disabled=' + (S.synthWipeBtn.getAttribute('disabled') === 'disabled'));

    if (!S.activeSlug) {
      showToast('Pick a framework first before wiping.');
      return;
    }
    // Defense-in-depth: check BOTH thread IDs. Previously this only
    // checked synthThreadId so a study-level run could slip through.
    const running = S.synthThreadId !== null || S.studyThreadId !== null;
    if (running) {
      showToast(
        'A Synth run is in progress (synth=' + S.synthThreadId
        + ', study=' + S.studyThreadId + '). Click Cancel Synth first. '
        + 'If the state is stuck, run `ddForceResetSynthUI()` in console.'
      );
      return;
    }
    const ok = await showConfirm(
      'Wipe synth cache for ' + S.activeSlug + '?',
      ('Deletes MinIO chapter artifacts + Postgres checkpoints + ' +
       'browser state for ' + S.activeSlug +
       '. Planner cache is untouched. This cannot be undone.'),
      'Wipe',
    );
    if (!ok) return;
    const result = await wipeSynth(S.activeSlug);
    if (result && result.error) {
      showToast('Wipe failed: ' + result.error);
    } else if (result && result.http_status) {
      showToast('Wipe failed: HTTP ' + result.http_status);
    } else {
      showToast('Synth cache wiped for ' + S.activeSlug + '.');
    }
  });
}
