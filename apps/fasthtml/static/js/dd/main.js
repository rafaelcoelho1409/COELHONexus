// Boot entry point — stage-aware.
//
// The Docs Distiller is split into per-stage routes
// (/docs-distiller, /docs-distiller/ingestion, /planner, /synth,
// /study). Each route renders ONLY its stage's panel + shared
// chrome and stamps `data-dd-stage="<stage>"` on the .fw-picker
// wrapper. This bootstrap reads that attribute and runs ONLY the
// init sequence the current stage needs.
//
// Phase 8 (2026-06-05): per-stage code splitting. The static imports
// at the top of main.js now cover ONLY what every stage needs (shared
// chrome + state + library). The heavy stage modules — planner.js,
// synth.js, study.js — are dynamic-imported inside their respective
// `init*` functions, so a user on the catalog page never downloads
// 2000+ LOC of synth code. Importmap aliases keep the dynamic paths
// short and survive folder renames.

// Top-level — only the chrome every stage needs.
import * as Sc from '@dd/shared/state/catalog.js';
import * as Si from '@dd/shared/state/ingestion.js';
import { refreshGenerateState } from './shared/ui.js';
import { currentStage, currentSlug, currentRunId } from './shared/nav.js';

// $activePipeline reflects "is a planner/synth run live right now". Set by
// each stage's lifecycle handlers (start/cancel/terminal); subscribed here
// so CSS / topbar.js can react via body.dataset.activePipeline without
// any of them having to poll. Mirrors the pattern $activeStudy and
// $sseStreams will follow as their wiring lands (Phase G+).
import { $activePipeline } from '@nx/stores/pipeline.js';

$activePipeline.subscribe((val) => {
  if (val && val.stage) {
    document.body.dataset.activePipeline = val.stage;
    document.body.dataset.activePipelineSlug = val.slug || '';
  } else {
    delete document.body.dataset.activePipeline;
    delete document.body.dataset.activePipelineSlug;
  }
});

// Shared chrome modules — picker/ingestion/framework_picker are lightweight
// and install null-safe DOM handlers at module-init; loading them on every
// stage is cheap (they no-op cleanly when their DOM is absent). Library
// + fetch_catalog drive the sidebar that lives on every stage page.
import './catalog/picker.js';
import './ingestion/ingestion.js';
import './shared/framework_picker.js';
import {
  loadLibrary, recoverActiveRuns, recoverActivePlanner, loadPlannerInfo,
} from './shared/library.js';
import { indexTilesForFramework, markIngestedTiles } from './catalog/picker.js';
import { pollRun } from './ingestion/ingestion.js';

const stage  = currentStage();
const slug   = currentSlug();
const runId  = currentRunId();

// Seed in-memory state from the URL so the rest of the modules can
// keep using Si.activeSlug / Si.activeRunId as their single source of
// truth (no need to thread URL params through every call site).
if (slug)  Si.setActiveSlug(slug);
if (runId) Si.setActiveRunId(runId);

// ============================================================ //
// Stage-specific init dispatch — each loads its own deps.      //
// ============================================================ //
async function initCatalog() {
  try { indexTilesForFramework(); } catch (_) {}
  if (Sc.countEl && Sc.total > 0) {
    Sc.countEl.textContent = Sc.total + ' of ' + Sc.total;
  }
  try { markIngestedTiles(); } catch (_) {}
  // Discover any in-flight ingestion so the bottom-bar reflects it.
  try { await recoverActiveRuns(); }
  catch (e) { console.warn('[init] catalog ingestion-recover failed:', e); }
  refreshGenerateState();
}

async function initIngestion() {
  if (runId) {
    pollRun(runId);
  } else {
    try { await recoverActiveRuns(); }
    catch (e) { console.warn('[init] ingestion-recover failed:', e); }
  }
  // Cases A/B/C/D — see the manifest-loading docstring in the prior
  // version of this file. All dynamic imports here use the post-Phase-A
  // paths (those were broken in the prior file — Phase A's rewrite
  // script handled static imports but missed `await import(...)`).
  if (slug && Si.activeRunId === null) {
    // CASE B
    try {
      const { loadManifestForSlug } = await import('@dd/ingestion/ingestion.js');
      loadManifestForSlug(slug).catch(() => {});
    } catch (_) {}
  } else if (slug && Si.activeRunId !== null && Si.activeSlug !== slug) {
    // CASE D — open the done framework's file list without disturbing
    // the in-flight run's bookkeeping.
    if (Si.progressBox) Si.progressBox.style.display = 'none';
    try {
      const { loadManifestForSlug } =
        await import('@dd/ingestion/ingestion.js');
      loadManifestForSlug(slug, { preserveActiveSlug: true })
        .catch(() => {});
    } catch (_) {}
    try {
      const { updatePickerTrigger } = await import('@dd/catalog/picker.js');
      updatePickerTrigger(slug).catch(() => {});
    } catch (_) {}
  } else if (Si.activeRunId !== null && Si.step2Grid) {
    // CASE C
    Si.step2Grid.innerHTML =
      '<div class="fw-empty">Ingestion in progress — materials will ' +
      'appear here when it completes.</div>';
  }
}

async function initPlanner() {
  // Load the planner module on-demand. The Cytoscape stack is further
  // lazy-loaded inside _initPlannerCanvas (see Phase 2).
  const planner = await import('@dd/planner/planner.js');
  planner._toggleStageEmpty('planner', !slug);
  try { await loadPlannerInfo(); }
  catch (e) { console.warn('[init] planner-info failed:', e); }
  try { await planner._initPlannerCanvas(); }
  catch (e) { console.warn('[init] planner-canvas failed:', e); }
  // Cross-stage gate — refresh so Start Planner sees synth-running.
  try {
    const { refreshCrossStageBlocker } = await import('@dd/shared/ui.js');
    await refreshCrossStageBlocker();
  } catch (e) { console.warn('[init] planner cross-stage-gate failed:', e); }
  if (slug) {
    try {
      planner.setPlannerFramework(slug);
      await planner._tryResumeActivePlanner(slug);
      planner.refreshPlannerStartState();
    } catch (e) { console.warn('[init] planner-resume failed:', e); }
  } else {
    try { await recoverActivePlanner(); }
    catch (e) { console.warn('[init] planner-recover failed:', e); }
    try { planner.refreshPlannerStartState(); }
    catch (_) {}
  }
}

async function initSynth() {
  // Synth needs the planner's NodeDrawer (live-update hot path uses it
  // synchronously). Load both in parallel so the inject below happens
  // as soon as both promises resolve.
  const [planner, synth] = await Promise.all([
    import('@dd/planner/planner.js'),
    import('@dd/synth/synth.js'),
  ]);
  synth._setNodeDrawerRef(planner.NodeDrawer);
  planner._toggleStageEmpty('synth', !slug);
  try { await synth.loadSynthInfo(); }
  catch (e) { console.warn('[init] synth-info failed:', e); }
  try { await synth._initSynthCanvas(); }
  catch (e) { console.warn('[init] synth-canvas failed:', e); }
  try {
    const { refreshCrossStageBlocker } = await import('@dd/shared/ui.js');
    await refreshCrossStageBlocker();
  } catch (e) { console.warn('[init] synth cross-stage-gate failed:', e); }
  if (slug) {
    try {
      await synth._tryResumeActiveSynth(slug);
      synth.refreshSynthStartState();
    } catch (e) { console.warn('[init] synth-resume failed:', e); }
    try { await synth._refreshSynthPlanGate(slug); }
    catch (e) { console.warn('[init] synth-plan-gate failed:', e); }
  } else {
    try { synth.refreshSynthStartState(); }
    catch (_) {}
  }
}

async function initStudy() {
  if (!slug) return;
  try {
    const study = await import('@dd/study/study.js');
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
