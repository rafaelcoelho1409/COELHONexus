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
import { _initPlannerCanvas, _toggleStageEmpty, NodeDrawer } from './planner.js';
import {
  loadLibrary, recoverActiveRuns, recoverActivePlanner, loadPlannerInfo,
} from './library.js';
import {
  _initSynthCanvas, loadSynthInfo, recoverActiveSynth, _setNodeDrawerRef,
} from './synth.js';
import './study.js';
import { indexTilesForFramework } from './picker.js';
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
  refreshGenerateState();
}

async function initIngestion() {
  // Explicit ?run= → resume polling directly (no /runs/active round
  // trip). This is the path the catalog page redirects through after
  // POST /runs returns `queued`.
  if (runId) {
    pollRun(runId);
  } else {
    try { await recoverActiveRuns(); }
    catch (e) { console.warn('[init] ingestion-recover failed:', e); }
  }
  // Render the manifest file grid for the URL's slug whenever the
  // user lands directly on /docs-distiller/ingestion?slug=...
  if (slug) {
    try {
      const { loadManifestForSlug } = await import('./ingestion.js');
      loadManifestForSlug(slug).catch(() => {});
    } catch (_) {}
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
