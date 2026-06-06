// planner/canvas.js — Cytoscape canvas resize / layout helpers + the
// window resize handler. Extracted from planner.js Step 3 (2026-06-05).
// Fully self-contained: zero cross-refs back to planner.js, only
// Sp.* state. _initPlannerCanvas stays in planner.js because it
// references _openPlannerNodeDrawer (the click handler), which lives
// alongside the SUBSTEP_RENDERERS-consuming drawer code there.
import * as Sp from '@dd/shared/state/planner.js';

export function _resizePlannerCanvas() {
  if (!Sp.plannerGraph || !Sp.plannerGraph.cy) return;
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
  if (!Sp.plannerGraph || !Sp.plannerGraph.cy) return;
  try {
    const cy = Sp.plannerGraph.cy;
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
    if (Sp.plannerGraph) _resizePlannerCanvas();
  });
});
// ResizeObserver — catches container size changes from sources other
// than window resize (CSS transitions, flex reflows, sidebar
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
