// synth/canvas.js — Cytoscape canvas resize + layout + init + drawer
// integration + co-refine chip + SSE event buffer.
//
// Extracted from synth.js Step 3 (2026-06-05 follow-up) using per-
// function grep + brace-counting (the safe pattern after Step 1's
// line-range bug). _nodeDrawerRef module-state lives here too —
// _setNodeDrawerRef mutates it; _openSynthNodeDrawer reads it.
// main.js calls _setNodeDrawerRef via the synth.js re-export, so
// the injection path is unchanged.
import * as Sa from '@dd/shared/state/api.js';
import * as Si from '@dd/shared/state/ingestion.js';
import * as Sp from '@dd/shared/state/planner.js';
import * as Sy from '@dd/shared/state/synth.js';
import { StageGraph } from '../shared/stagegraph.js';
import { _buildSynthNodeCtx } from './graph.js';
// _synthStorageKey defined in lifecycle.js — there's a CYCLE risk
// (lifecycle.js → canvas.js for _resizeSynthCanvas, then canvas.js →
// lifecycle.js for this) but ESM resolves these cleanly when only the
// CALLER reference is hoisted (not at module-init expression position).
// _openSynthNodeDrawer below uses it inside a function body, so it
// resolves at call time — safe.
import { _synthStorageKey } from './lifecycle.js';

export function _bufferSynthEvent(ev) {
  if (!ev || !ev.step) return;
  let list = Sy._synthEventBuffer.get(ev.step);
  if (!list) { list = []; Sy._synthEventBuffer.set(ev.step, list); }
  list.push(ev);
  if (list.length > Sy._SYNTH_EVENT_BUFFER_PER_STEP) {
    list.splice(0, list.length - Sy._SYNTH_EVENT_BUFFER_PER_STEP);
  }
}

export function _resetSynthEventBuffer() {
  Sy._synthEventBuffer.clear();
}

export async function _openSynthNodeDrawer(nodeId) {
  let values = {};
  // Same fallback as planner: localStorage thread id covers the
  // post-terminal case when Sy.synthThreadId has been nulled.
  let tid = Sy.synthThreadId;
  if (!tid && Si.activeSlug) {
    try { tid = localStorage.getItem(_synthStorageKey(Si.activeSlug)); }
    catch (e) {}
  }
  if (tid) {
    try {
      const r = await fetch(Sa.API + '/synth/debug/graph/' + tid + '/state');
      if (r.ok) values = (await r.json()).values || {};
    } catch (e) { /* drawer opens with empty results */ }
  }
  const ctx = _buildSynthNodeCtx(nodeId, values);
  // NodeDrawer is from the planner module — use dynamic import to
  // avoid circular dependency at module parse time.
  const { NodeDrawer } = await import('@dd/planner/planner.js');
  if (ctx) NodeDrawer.open('synth', nodeId, ctx);
  // Replay buffered events for this node so a late-open drawer sees
  // the full event history, not just future events.
  const buffered = Sy._synthEventBuffer.get(nodeId) || [];
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

let _nodeDrawerRef = null;
export function _setNodeDrawerRef(nd) { _nodeDrawerRef = nd; }
// Public getter so other modules (synth.js → chstrip_deps DI) can pass
// a stable reference to the latest `_nodeDrawerRef` without trying to
// close over a module-local variable that lives in this file.
// chstrip.js's `(deps._getNodeDrawerRef?.())` calls this.
export function _getNodeDrawerRef() { return _nodeDrawerRef; }

export function _resizeSynthCanvas() {
  if (!Sy.synthGraph || !Sy.synthGraph.cy) return;
  requestAnimationFrame(() => {
    _runSynthLayoutAndCenter('first');
    setTimeout(() => _runSynthLayoutAndCenter('second'), 250);
  });
}

export function _runSynthLayoutAndCenter(passLabel) {
  if (!Sy.synthGraph || !Sy.synthGraph.cy) return;
  try {
    const cy = Sy.synthGraph.cy;
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
        import('@dd/planner/planner.js').then(m => {
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

export async function _initSynthCanvas() {
  if (Sp.UI_MODE !== 'graph') return;
  const root = document.getElementById('fw-synth-graph');
  const canvasEl = document.getElementById('fw-synth-canvas');
  if (!root || !canvasEl) return;
  // Phase 2 (2026-06-05): same lazy-load pattern as the planner — the
  // Cytoscape stack downloads only when this function runs (synth page
  // mount). The dynamic import of planner.js below is preserved because
  // _attachCanvasResizeObserver lives there.
  const { ensureCytoscape } = await import('../shared/cytoscape_loader.js');
  try {
    await ensureCytoscape();
  } catch (e) {
    console.warn('[synthGraph] Cytoscape load failed:', e);
    canvasEl.innerHTML =
      '<div class="fw-empty">Cytoscape failed to load. ' +
      'Reload the page; if it persists, check the network panel ' +
      'for blocked CDN scripts.</div>';
    return;
  }
  const nodes = Sy.SYNTH_NODE_ORDER.map((id, i) => ({
    id,
    label:  Sy.SYNTH_NODE_LABELS[i] || id,
    status: Sy.synthImplemented.has(id) ? 'pending' : 'future',
  }));
  const edges = [];
  for (let i = 0; i < Sy.SYNTH_NODE_ORDER.length - 1; i++) {
    edges.push({ source: Sy.SYNTH_NODE_ORDER[i],
                 target: Sy.SYNTH_NODE_ORDER[i + 1] });
  }
  // ── CoRefine loopback edge (Pattern 1 from May 2026 UX research) ──
  // The synth graph is CYCLIC: when checklist scores < 0.80, mgsr routes
  // RETHINK and the graph re-enters sawc_write. Surface that structurally
  // with a backward-arc edge tagged `kind='loopback'` — the stylesheet
  // renders it as an amber arc above the row, dashed when dormant,
  // solid+pulsing when firing. See domains/dd/synth/graph.py:_route_after_mgsr.
  if (Sy.SYNTH_NODE_ORDER.includes('sawc_write') &&
      Sy.SYNTH_NODE_ORDER.includes('mgsr_replan')) {
    edges.push({
      source: 'mgsr_replan',
      target: 'sawc_write',
      kind:   'loopback',
    });
  }
  console.log(
    `[synthGraph] canvas container ready, dims=${canvasEl.offsetWidth}x${canvasEl.offsetHeight}`
  );
  // _attachCanvasResizeObserver lives in planner.js (sibling within dd/);
  // dynamic-imported to keep the synth→planner edge async and avoid any
  // static-cycle hazards as the monoliths split further.
  const m = await import('../planner/planner.js');
  Sy.setSynthGraph(StageGraph.create(canvasEl, {
    nodes, edges,
    onNodeClick: (nodeId) => _openSynthNodeDrawer(nodeId),
  }));
  console.log(
    `[synthGraph] Cytoscape initialized with ${nodes.length} nodes, ${edges.length} edges`
  );
  if (Sy.synthGraph) _resizeSynthCanvas();
  m._attachCanvasResizeObserver('fw-synth-canvas', _resizeSynthCanvas);
}

