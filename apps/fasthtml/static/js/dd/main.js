// Boot entry point — stage-aware.
//
// The Docs Distiller is split into per-stage routes
// (/docs-distiller, /docs-distiller/ingestion, /planner, /synth,
// /study). Each route renders ONLY its stage's panel + shared
// chrome and stamps `data-dd-stage="<stage>"` on the .fw-picker
// wrapper. This bootstrap reads that attribute and runs ONLY the
// init sequence the current stage needs.
//
// Importing the feature modules triggers their top-level handler
// registration. Every module's top-level handlers are now null-
// safe (`element?.addEventListener(...)`) so loading them on a
// stage where their DOM is absent is a silent no-op.

import * as S from './state.js';
import { refreshGenerateState } from './ui.js';
import { currentStage, currentSlug, currentRunId } from './nav.js';

// Side-effect imports — modules wire their own DOM handlers at
// module-init. Null-safe; safe on every stage.
import './picker.js';
import './ingestion.js';
// Header framework dropdown — registers open/close + search filter.
// Self-no-ops if the picker isn't on the current page (it ships on
// every DD stage page today, but the guard is cheap insurance).
import './fw_picker.js';
import { _initPlannerCanvas, _toggleStageEmpty, NodeDrawer } from './planner.js';
import {
  loadLibrary, recoverActiveRuns, recoverActivePlanner, loadPlannerInfo,
} from './library.js';
import {
  _initSynthCanvas, loadSynthInfo, recoverActiveSynth, _setNodeDrawerRef,
  _refreshSynthPlanGate,
} from './synth.js';
import './study.js';
import { indexTilesForFramework, markIngestedTiles } from './picker.js';
import { pollRun } from './ingestion.js';

// Synth's live-update hot path needs synchronous access to the planner-
// owned NodeDrawer; inject the reference once at boot.
_setNodeDrawerRef(NodeDrawer);

const stage  = currentStage();
const slug   = currentSlug();
const runId  = currentRunId();

// Seed in-memory state from the URL so the rest of the modules can
// keep using S.activeSlug / S.activeRunId as their single source of
// truth (no need to thread URL params through every call site).
if (slug)  S.setActiveSlug(slug);
if (runId) S.setActiveRunId(runId);

// ============================================================ //
// Stage-specific init dispatch                                 //
// ============================================================ //
async function initCatalog() {
  // Tile index drives the framework-info lookup for the progress
  // box logo strip. The tiles only exist on Catalog so this runs
  // here, not on other stages.
  try { indexTilesForFramework(); } catch (_) {}
  if (S.countEl && S.total > 0) {
    S.countEl.textContent = S.total + ' of ' + S.total;
  }
  // loadLibrary() (already awaited in the boot sequence below) populated
  // S.ingestedSlugs — green-badge the tiles that are already downloaded.
  try { markIngestedTiles(); } catch (_) {}
  refreshGenerateState();
}

async function initIngestion() {
  // Explicit ?run= → resume polling directly (no /runs/active round
  // trip). This is the path the catalog page redirects through after
  // POST /runs returns `queued`. pollRun sets S.activeRunId
  // synchronously and reveals the progress box.
  if (runId) {
    pollRun(runId);
  } else {
    try { await recoverActiveRuns(); }
    catch (e) { console.warn('[init] ingestion-recover failed:', e); }
  }
  if (slug && S.activeRunId === null) {
    // No ingestion in flight → load the finalized manifest into the
    // file grid (the user is viewing an already-ingested framework).
    try {
      const { loadManifestForSlug } = await import('./ingestion.js');
      loadManifestForSlug(slug).catch(() => {});
    } catch (_) {}
  } else if (S.activeRunId !== null && S.step2Grid) {
    // Ingestion IS in flight — the manifest doesn't exist yet (fetching
    // it would 404). Show an in-progress note; pollRun's done-handler
    // populates the grid with the real files once the run completes.
    S.step2Grid.innerHTML =
      '<div class="fw-empty">Ingestion in progress — materials will ' +
      'appear here when it completes.</div>';
  }
}

async function initPlanner() {
  _toggleStageEmpty('planner', !slug);
  try { await loadPlannerInfo(); }
  catch (e) { console.warn('[init] planner-info failed:', e); }
  try { _initPlannerCanvas(); }
  catch (e) { console.warn('[init] planner-canvas failed:', e); }
  if (slug) {
    try {
      const { _tryResumeActivePlanner, setPlannerFramework } = await import('./planner.js');
      setPlannerFramework(slug);
      await _tryResumeActivePlanner(slug);
    } catch (e) { console.warn('[init] planner-resume failed:', e); }
  } else {
    // No slug → just hydrate localStorage from /planner/recent so the
    // user's next library click can resume cleanly.
    try { await recoverActivePlanner(); }
    catch (e) { console.warn('[init] planner-recover failed:', e); }
  }
}

async function initSynth() {
  _toggleStageEmpty('synth', !slug);
  try { await loadSynthInfo(); }
  catch (e) { console.warn('[init] synth-info failed:', e); }
  try { _initSynthCanvas(); }
  catch (e) { console.warn('[init] synth-canvas failed:', e); }
  if (slug) {
    try {
      const { _tryResumeActiveSynth } = await import('./synth.js');
      await _tryResumeActiveSynth(slug);
    } catch (e) { console.warn('[init] synth-resume failed:', e); }
    // Gate Start Synth on planner-plan existence (mirrors the server's
    // _load_plan 404). Independent of resume so it always runs.
    try { await _refreshSynthPlanGate(slug); }
    catch (e) { console.warn('[init] synth-plan-gate failed:', e); }
  }
}

async function initStudy() {
  if (!slug) return;
  try {
    const study = await import('./study.js');
    study.setStudyFramework?.(slug);
    study.refreshStudyVisibility?.();
    study.loadStudyChapters?.(slug);
  } catch (e) { console.warn('[init] study-load failed:', e); }
}

const STAGE_INITS = {
  catalog:   initCatalog,
  ingestion: initIngestion,
  planner:   initPlanner,
  synth:     initSynth,
  study:     initStudy,
};

// Sequence: library first (sidebar is shared), then the stage's own
// init. Each step is independently try/caught so a failure in one
// doesn't prevent the next from running.
(async () => {
  try { await loadLibrary(); }
  catch (e) { console.warn('[init] library failed:', e); }

  const init = STAGE_INITS[stage];
  if (init) {
    try { await init(); }
    catch (e) { console.warn('[init] stage ' + stage + ' failed:', e); }
  }
})();
