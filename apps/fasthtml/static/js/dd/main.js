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
  // Discover any in-flight ingestion (started from THIS tab earlier OR
  // from another tab / framework). Sets S.activeRunId + S.activeSlug so
  // refreshGenerateState below can block the Start Ingestion button AND
  // swap the bottom bar from "Selected: X" to "Ingesting: <active>".
  // Previously this only fired on the Ingestion stage — leaving the
  // Catalog blind to cross-tab / cross-slug runs. pollRun (kicked off
  // inside recoverActiveRuns) is null-safe re: the missing progress box
  // here, so the background poll continues and the Catalog learns when
  // the run finishes.
  try { await recoverActiveRuns(); }
  catch (e) { console.warn('[init] catalog ingestion-recover failed:', e); }
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
  // After bootstrap (`S.setActiveSlug(slug)` at top of main.js) AND
  // `recoverActiveRuns` (which may overwrite `S.activeSlug` with the
  // in-flight framework), four cases on the Ingestion page:
  //
  //   A. No URL slug AND no in-flight run     → nothing to render.
  //   B. URL slug AND no in-flight run        → load URL slug's manifest.
  //   C. URL slug == in-flight slug           → show "in progress" placeholder.
  //   D. URL slug != in-flight slug           → user opened a DONE framework
  //      while ANOTHER framework's ingestion runs in the background. Load
  //      this framework's manifest WITHOUT clobbering `S.activeSlug` (so
  //      the bottom-bar "Ingesting" indicator + global running-dot keep
  //      pointing at the background run). Hide the progress box — it
  //      belongs to the OTHER run, not the one being viewed.
  if (slug && S.activeRunId === null) {
    // CASE B
    try {
      const { loadManifestForSlug } = await import('./ingestion.js');
      loadManifestForSlug(slug).catch(() => {});
    } catch (_) {}
  } else if (slug && S.activeRunId !== null && S.activeSlug !== slug) {
    // CASE D — open the done framework's file list without disturbing
    // the in-flight run's bookkeeping.
    if (S.progressBox) S.progressBox.style.display = 'none';
    try {
      const { loadManifestForSlug } = await import('./ingestion.js');
      loadManifestForSlug(slug, { preserveActiveSlug: true })
        .catch(() => {});
    } catch (_) {}
    // Sync the header dropdown trigger label to the framework being
    // VIEWED (recoverActiveRuns labelled it with the in-flight slug —
    // override here so the dropdown matches the visible content).
    try {
      const { updatePickerTrigger } = await import('./picker.js');
      updatePickerTrigger(slug).catch(() => {});
    } catch (_) {}
  } else if (S.activeRunId !== null && S.step2Grid) {
    // CASE C — viewing the in-flight slug. Manifest doesn't exist yet
    // (fetching would 404); show the placeholder. pollRun's done-handler
    // populates the grid with the real files once the run completes.
    S.step2Grid.innerHTML =
      '<div class="fw-empty">Ingestion in progress — materials will ' +
      'appear here when it completes.</div>';
  }
  // CASE A: nothing to render.
}

async function initPlanner() {
  _toggleStageEmpty('planner', !slug);
  try { await loadPlannerInfo(); }
  catch (e) { console.warn('[init] planner-info failed:', e); }
  try { _initPlannerCanvas(); }
  catch (e) { console.warn('[init] planner-canvas failed:', e); }
  // CROSS-STAGE GATE — refresh the global blocker so the Start Planner
  // button is correctly disabled when a synth is running on any slug.
  // Fire-and-forget; refreshPlannerStartState reads the cached result.
  try {
    const { refreshCrossStageBlocker } = await import('./ui.js');
    await refreshCrossStageBlocker();
  } catch (e) { console.warn('[init] planner cross-stage-gate failed:', e); }
  if (slug) {
    try {
      const { _tryResumeActivePlanner, setPlannerFramework,
              refreshPlannerStartState } = await import('./planner.js');
      setPlannerFramework(slug);
      await _tryResumeActivePlanner(slug);
      refreshPlannerStartState();
    } catch (e) { console.warn('[init] planner-resume failed:', e); }
  } else {
    // No slug → just hydrate localStorage from /planner/recent so the
    // user's next library click can resume cleanly. Still refresh the
    // Start state so the button picks up the cross-stage blocker.
    try { await recoverActivePlanner(); }
    catch (e) { console.warn('[init] planner-recover failed:', e); }
    try {
      const { refreshPlannerStartState } = await import('./planner.js');
      refreshPlannerStartState();
    } catch (_) {}
  }
}

async function initSynth() {
  _toggleStageEmpty('synth', !slug);
  try { await loadSynthInfo(); }
  catch (e) { console.warn('[init] synth-info failed:', e); }
  try { _initSynthCanvas(); }
  catch (e) { console.warn('[init] synth-canvas failed:', e); }
  // CROSS-STAGE GATE — same mirror as initPlanner. Refresh BEFORE the
  // start-state refresher fires so the Start Synth button sees the
  // current planner-active blocker on first render.
  try {
    const { refreshCrossStageBlocker } = await import('./ui.js');
    await refreshCrossStageBlocker();
  } catch (e) { console.warn('[init] synth cross-stage-gate failed:', e); }
  if (slug) {
    try {
      const { _tryResumeActiveSynth, refreshSynthStartState } =
        await import('./synth.js');
      await _tryResumeActiveSynth(slug);
      refreshSynthStartState();
    } catch (e) { console.warn('[init] synth-resume failed:', e); }
    // Gate Start Synth on planner-plan existence (mirrors the server's
    // _load_plan 404). Independent of resume so it always runs.
    try { await _refreshSynthPlanGate(slug); }
    catch (e) { console.warn('[init] synth-plan-gate failed:', e); }
  } else {
    try {
      const { refreshSynthStartState } = await import('./synth.js');
      refreshSynthStartState();
    } catch (_) {}
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
