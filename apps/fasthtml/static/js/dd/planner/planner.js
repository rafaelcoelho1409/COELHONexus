// Planner module — Cytoscape graph, cards, SSE polling, start/cancel/wipe.
//
// 2026-06-05 (Phase D): NodeDrawer IIFE extracted to ./drawer.js as the
// cleanest first sibling — self-contained, no shared module-private
// state, ~270 LOC removed. Future siblings (canvas.js, renderers.js,
// polling.js, lifecycle.js) will follow the same pattern of pulling out
// independently-testable units. Until then, this file remains the
// monolith with NodeDrawer imported.
import * as Sa from '@dd/shared/state/api.js';
import * as Sc from '@dd/shared/state/catalog.js';
import * as Si from '@dd/shared/state/ingestion.js';
import * as Sp from '@dd/shared/state/planner.js';
import * as Sy from '@dd/shared/state/synth.js';
import { StageGraph } from '../shared/stagegraph.js';
import { sleep, fmtBytes, fmtAge, escapeHtml, formatFieldValue } from '../shared/utils.js';
import {
  showConfirm, showNotice, showToast, refreshGenerateState,
  fetchPipelineState, cascadeImpactText,
  refreshCrossStageBlocker, crossStageBlockerFor,
} from '../shared/ui.js';
import { loadManifestForSlug, renderManifest } from '../ingestion/ingestion.js';
import {
  startElapsed, stopElapsed, showElapsed, isElapsedRunning,
} from '../shared/timing.js';
// NodeDrawer extracted to a sibling file (Phase D). Re-exported below
// so main.js's `import { NodeDrawer } from './planner/planner.js'`
// keeps resolving without churn.
import { NodeDrawer } from './drawer.js';
export { NodeDrawer };
// Phase 3 (2026-06-05): $activePipeline reflects "is a planner run live
// right now". Writes here in start/cancel/terminal; main.js subscribes
// and surfaces the value on body.dataset.activePipeline so topbar.js /
// CSS can react without polling.
import { $activePipeline } from '@nx/stores/pipeline.js';

// Graph/drawer cluster extracted to ./graph.js (Step 4, 2026-06-05
// follow-up). _fieldPresent + _plannerStorageKey moved to ./shared.js.
// Re-imported + re-exported for backward compat.
export { _fieldPresent, _plannerStorageKey } from './shared.js';
import { _fieldPresent, _plannerStorageKey } from './shared.js';
export {
  _setPlannerStagePill,
  _kpiForNode,
  _renderPlannerGraph,
  _buildPlannerNodeCtx,
  _openPlannerNodeDrawer,
  _refreshOpenPlannerDrawer,
} from './graph.js';
import {
  _setPlannerStagePill,
  _renderPlannerGraph,
  _refreshOpenPlannerDrawer,
  // _openPlannerNodeDrawer is used inside `_initPlannerCanvas` (line ~149)
  // as the StageGraph onNodeClick callback. The `export { ... } from ... `
  // block above is RE-EXPORT only — does not bind the symbol locally.
  // Without this import, clicking any planner node would throw
  // `ReferenceError: _openPlannerNodeDrawer is not defined`.
  _openPlannerNodeDrawer,
} from './graph.js';


// Canvas helpers (_resizePlannerCanvas, _runPlannerLayoutAndCenter,
// _forceCenterHorizontal, _attachCanvasResizeObserver) + the window
// resize handler extracted to ./canvas.js (Step 3, 2026-06-05).
// Re-exported here so main.js's `import { ... } from
// './planner/planner.js'` consumers resolve without churn.
export {
  _resizePlannerCanvas,
  _runPlannerLayoutAndCenter,
  _forceCenterHorizontal,
  _attachCanvasResizeObserver,
} from './canvas.js';
import {
  _resizePlannerCanvas,
  _attachCanvasResizeObserver,
} from './canvas.js';
// ============================================================

// Per-node KPI badge — ONE number shown as a small second-line under
// the label. Source is the per-node `*_stats` dict in state values
// (same dicts the cards use for their KPI grids). Returns '' when
// the node hasn't run yet.

// Mirror of renderPlannerCards for the Cytoscape canvas. Loops the
// canonical node order, derives status per node from state field
// presence (same logic as the cards path), and pushes to
// Sp.plannerGraph.setStatus. No-op when the canvas isn't mounted
// (?ui=cards) — keeps the call sites uniform.

// Build the drawer context object for a planner node from the
// current checkpoint state. Separate from `open()` so live state
// refreshes can reuse the same logic via `_refreshOpenPlannerDrawer`.

// Opens the NodeDrawer for a planner node. Fetches fresh state for
// an accurate initial render; subsequent updates flow in via the
// SSE handler + _refreshOpenPlannerDrawer.

// Called from renderPlannerCards on every state refresh so the
// open drawer's results panel updates as the pipeline progresses
// (e.g. cluster card's KPI grid materializes the moment `cluster`
// commits its checkpoint, without the user having to re-click).

export async function _initPlannerCanvas() {
  if (Sp.UI_MODE !== 'graph') {
    console.log('[plannerGraph] UI_MODE=cards (default) — canvas not mounted');
    return;
  }
  console.log('[plannerGraph] UI_MODE=graph — lazy-loading Cytoscape stack');
  const root = document.getElementById('fw-planner-graph');
  const canvasEl = document.getElementById('fw-planner-canvas');
  if (!root || !canvasEl) {
    console.warn('[plannerGraph] missing #fw-planner-graph or #fw-planner-canvas in DOM');
    return;
  }
  // Phase 2 (2026-06-05): the 3 vendor scripts are no longer in HEAD.
  // ensureCytoscape() injects them on first call, caches the promise,
  // and resolves once `window.cytoscape` is defined. Other stages that
  // never call this never download the 460 KB. The poll loop is now
  // dead code — kept only as a 5s timeout fallback for the surfacing
  // error UI (the actual load is single-shot via the promise).
  const { ensureCytoscape } = await import('../shared/cytoscape_loader.js');
  try {
    await ensureCytoscape();
  } catch (e) {
    console.warn('[plannerGraph] Cytoscape load failed:', e);
    canvasEl.innerHTML =
      '<div class="fw-empty">Cytoscape failed to load. ' +
      'Reload the page; if it persists, check the network panel ' +
      'for blocked CDN scripts.</div>';
    return;
  }
  const nodes = Sp.PLANNER_NODE_ORDER.map((id, i) => ({
    id,
    label:  Sp.PLANNER_NODE_LABELS[i] || id,
    status: Sp.plannerImplemented.has(id) ? 'pending' : 'future',
  }));
  const edges = [];
  for (let i = 0; i < Sp.PLANNER_NODE_ORDER.length - 1; i++) {
    edges.push({
      source: Sp.PLANNER_NODE_ORDER[i],
      target: Sp.PLANNER_NODE_ORDER[i + 1],
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
  Sp.setPlannerGraph(StageGraph.create(canvasEl, {
    nodes, edges,
    onNodeClick: (nodeId) => _openPlannerNodeDrawer(nodeId),
  }));
  console.log(
    `[plannerGraph] Cytoscape initialized with ${nodes.length} ` +
    `nodes, ${edges.length} edges`,
  );
  if (Sp.plannerGraph) _resizePlannerCanvas();
  _attachCanvasResizeObserver('fw-planner-canvas', _resizePlannerCanvas);
}

// ============================================================
// Utility
// ============================================================

// `refreshPlannerStartState` moved to lifecycle.js (2026-06-06) — all
// of its deps (Sc / Si / Sp / crossStageBlockerFor / setPlannerFramework
// / _toggleStageEmpty) were already there. Keeping it here while
// lifecycle.js + polling.js needed to call it required a circular
// import that browsers handle differently than Node. Now both consumers
// import directly from lifecycle.js; planner.js re-exports the symbol
// below (line ~340) so existing `planner.refreshPlannerStartState`
// call sites (main.js initPlanner) still resolve.

// Toggles the "Pick a framework from the library to view the
// {stage} pipeline" placeholder for a stage panel. Single source of
// truth for graph-wrapper visibility — canvas init MUST NOT touch
// it directly or it races this toggle. On reveal, kicks a Cytoscape
// resize so the canvas picks up freshly-visible container dimensions
// (otherwise the graph latches 0×0 from when it was hidden).




// SUBSTEP_RENDERERS extracted to ./renderers.js (Step 6, 2026-06-05).
// Re-exported here so the existing `import { SUBSTEP_RENDERERS } from
// './planner/planner.js'` consumers keep working without churn.
import { SUBSTEP_RENDERERS } from './renderers.js';
export { SUBSTEP_RENDERERS };



// formatFieldValue + escapeHtml are imported from utils.js (shared).



// Live progress text per substep card (populated by SSE events).
// Keyed by step name (matches the node names emitted server-side).




// Race-tolerant state fetch. The LangGraph checkpoint commit lands a
// tick AFTER the node's `done` event fires on the SSE channel, so a
// naive fetch right after `done` may see stale state. When the caller
// knows which field is expected to have just appeared, we retry with
// backoff until it's present (or we exhaust attempts).

// Mapping: SSE step name → the state field that becomes present once
// that node's checkpoint is committed. Used by the retry-fetch above
// so we wait for the previous node's commit before re-rendering.

// Wall-clock ms at which the current planner run started — set on an
// explicit Start (Date.now()) or recovered from the live-run registry's
// `started_ts` on refresh, so the navbar timer continues from the real run
// start (not from 0) when reconnecting. 0 = no known run.


// Full planner wipe for `slug` — DELETE backend (MinIO embeddings +
// Postgres LangGraph checkpoints) + clear localStorage + reset cards
// if currently viewing that slug. Exposed on `window.ddWipePlanner`
// so an operator can run `ddWipePlanner('pydantic')` from the
// browser console without leaving the page.
window.ddWipePlanner = wipePlanner;

// Separate key tracking the LAST slug the user kicked off a planner
// run for. recoverActivePlanner uses this to disambiguate when multiple
// slugs have localStorage entries — without it, the JS scan order is
// undefined and we might auto-activate the wrong framework on reload.



// Page-refresh recovery: when the user reloads while a planner is
// mid-run, reconnect to the SSE stream + replay snapshot events so the
// UI catches up to the live state, mirroring the loading-box recovery
// on the Ingestion step. After a pod restart the in-flight bg task is
// dead but the LangGraph checkpoints persist — if no SSE events arrive
// within Sp._ORPHAN_DETECT_MS, we POST /resume which makes LangGraph
// continue from the last committed checkpoint (completed nodes skipped).
// Returns true if a run was resumed.

// Returns true if every CURRENTLY-IMPLEMENTED planner node has its
// output field present in `values`. Lets us treat a stuck `status:
// "running"` (e.g. pod-restart killed the bg task before
// aupdate_state(status='done') ran) as effectively-terminal so we
// don't burn orphan-detect timers + /resume calls on a run that
// actually finished.




// Click handling for #fw-planner-start moved to the server-rendered
// inline <script> in `features/dd/planner/body.py` (2026-06-06). The
// module-side document.addEventListener pattern attached fine on
// desktop but produced a silent no-op on mobile (Brave on Galaxy
// Tab S9 — confirmed via on-screen alert probes: click event fired,
// reached the listener, but neither startPlanner's `showToast` nor
// its `fetch` produced any observable UI change). Rather than chase
// a mobile-specific event-bubble / showToast / setActiveSlug timing
// issue across the module graph, click handling now sits in a single
// inline script that runs at HTML-parse time, has no module
// dependencies, and posts directly to FastAPI. The module continues
// to handle EVERYTHING ELSE — graph render, SSE polling, node
// drawer, Cancel button mid-run, refreshPlannerStartState's gate
// updates — only the initial Start tap is routed through the inline
// path now. After the page reloads on a successful start, the
// module's `_tryResumeActivePlanner` reconnects to the live SSE
// stream from the localStorage `dd:planner:active:{slug}` key the
// inline path set, so graph + chapter cards still update in real
// time as the run progresses.

// Wipe-planner button — destructive, gated by a confirm dialog. Hits
// the backend DELETE /planner/{slug}/wipe (MinIO embeddings + Postgres
// checkpoints) then clears localStorage + resets cards.
if (Sp.plannerWipeBtn) {
  Sp.plannerWipeBtn.addEventListener('click', async () => {
    if (!Si.activeSlug || Sp.plannerThreadId) return;
    // Probe downstream state so the confirm dialog reports the real
    // cascade (Synth + Study get nuked when planner is wiped — they
    // depend on the planner's chapter map).
    const state = await fetchPipelineState(Si.activeSlug);
    const cascade = cascadeImpactText(state, 'planner');
    const ok = await showConfirm(
      'Wipe planner cache for ' + Si.activeSlug + '?',
      'Deletes MinIO embedding blobs (forces a cold re-embed next ' +
      'run), Postgres LangGraph checkpoints (all threads for this ' +
      'slug), and the browser-cached thread_id.' + cascade +
      ' Cannot be undone.',
      'Wipe',
    );
    if (!ok) return;
    Sp.plannerWipeBtn.setAttribute('disabled', 'disabled');
    const orig = Sp.plannerWipeBtn.textContent;
    Sp.plannerWipeBtn.textContent = 'Wiping…';
    try {
      const result = await wipePlanner(Si.activeSlug);
      // Cascade downstream — Synth's chapter outputs and the Study
      // renders MUST go too because they were produced from THIS
      // planner's plan-latest.json. Skip the cascade call when there's
      // nothing to delete (avoids one round-trip + a meaningless toast).
      let synthDeleted = 0;
      if (state && (state.synth || state.study)) {
        try {
          const { wipeSynth } = await import('@dd/synth/synth.js');
          const sr = await wipeSynth(Si.activeSlug);
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
      showToast('Planner cache wiped for ' + Si.activeSlug +
        ' (' + minio + ' MinIO blobs, ' + pgTotal + ' Postgres rows).' +
        tail);
    } catch (e) {
      showToast('Wipe failed: ' + String(e));
    } finally {
      Sp.plannerWipeBtn.textContent = orig;
      refreshPlannerStartState();
    }
  });
}

// Card-head click handler removed 2026-06-05 — cards DOM was removed
// 2026-05-19, so `Sp.plannerCardsEl` is permanently null, and the
// `if (Sp.plannerCardsEl) { ... }` guard never registered. The off_topic
// verdict-table sort branch that lived inside this handler now activates
// only when the planner drawer renders that table (handled by
// SUBSTEP_RENDERERS[2] inside the drawer details panel, with its own
// event delegate).

// ============================================================
// POST /runs — Generate / Refresh
// ============================================================

// Polling subsystem extracted to ./polling.js + ./polling_deps.js
// (Step 5, 2026-06-05 follow-up).
export {
  pollPlanner,
  pollPlannerState,
  _refreshCardsFromState,
  _renderLiveProgress,
  _markCardRunning,
  _liveProgressEl,
  _stepIdx,
  _genPlannerThreadId,
  _setPlannerRunStartMs,
} from './polling.js';
import { _setPlannerRunStartMs, pollPlannerState } from './polling.js';
import { registerPollingDeps } from './polling_deps.js';
registerPollingDeps({
  renderPlannerCards,
  markPlannerFailed,
  cardEl,
  refreshPlannerStartState,
});

// Lifecycle extracted to ./lifecycle.js (Step 6, 2026-06-05 follow-up).
// refreshPlannerStartState moved there 2026-06-06 — added below so
// `planner.refreshPlannerStartState` from main.js still resolves.
export {
  _toggleStageEmpty,
  setPlannerFramework,
  cardEl,
  resetPlannerCards,
  renderPlannerCards,
  refreshPlannerStartState,
  markPlannerFailed,
  wipePlanner,
  _rememberActivePlanner,
  _forgetActivePlanner,
  _allImplementedComplete,
  _tryResumeActivePlanner,
  startPlanner,
  cancelPlanner,
} from './lifecycle.js';
// Local-scope re-imports for symbols planner.js's MODULE BODY
// references. `export { foo } from './lifecycle.js'` only re-exports;
// it does NOT bind the symbol in this module's lexical scope. After the
// 2026-06-05 split, line 221 (`window.ddWipePlanner = wipePlanner`),
// line 252 (`cancelPlanner()`), line 254 (`startPlanner()`), and line
// 282 (`await wipePlanner(...)`) all referenced symbols that only
// existed in the re-export block above — module load threw
// `ReferenceError: wipePlanner is not defined` at line 221, aborting
// planner.js's entire module evaluation, which is why _initPlannerCanvas
// never ran (graph empty) and refreshPlannerStartState never fired
// (Start button stuck disabled).
import {
  renderPlannerCards,
  markPlannerFailed,
  cardEl,
  refreshPlannerStartState,
  wipePlanner,
  startPlanner,
  cancelPlanner,
} from './lifecycle.js';
