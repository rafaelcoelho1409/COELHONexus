// ============================================================
// synth.js — Step 4: Synth pipeline (Cytoscape canvas, SSE,
//            study orchestrator, chapter strip, persistence)
// ============================================================

import * as Sa from '@dd/shared/state/api.js';
import * as Sc from '@dd/shared/state/catalog.js';
import * as Si from '@dd/shared/state/ingestion.js';
import * as Sp from '@dd/shared/state/planner.js';
import * as Sy from '@dd/shared/state/synth.js';
import { StageGraph } from '../shared/stagegraph.js';
// Phase 3 (2026-06-05): $activePipeline reflects "is a synth run live"
// — mirror of the planner-side wiring. Writes here in start/cancel/
// terminal; subscribers (main.js → body.dataset.activePipeline) react
// without polling.
import { $activePipeline } from '@nx/stores/pipeline.js';
import { sleep, escapeHtml, formatFieldValue } from '../shared/utils.js';
import {
  showToast, showNotice, showConfirm, refreshGenerateState,
  fetchPipelineState, cascadeImpactText,
  refreshCrossStageBlocker, crossStageBlockerFor,
} from '../shared/ui.js';
import { fmtMs, startElapsed, stopElapsed, showElapsed } from '../shared/timing.js';

// Wall-clock ms at which the current study run started — set on an explicit
// Start (Date.now()) or recovered from the live-run registry's `started_ts`
// on refresh, so the navbar Synth timer CONTINUES from the real run start
// _setSynthStagePill + _kpiForSynthNode + _renderSynthGraph +
// _buildSynthNodeCtx extracted to ./graph.js (Step 7, 2026-06-05).
// _synthFieldPresent moved to ./shared.js (DI break — see graph.js
// docstring). Re-exported here so consumers using
// `import { ... } from './synth/synth.js'` work without churn.
import { _synthFieldPresent } from './shared.js';
export {
  _setSynthStagePill,
  _kpiForSynthNode,
  _renderSynthGraph,
  _buildSynthNodeCtx,
} from './graph.js';
export { _synthFieldPresent };
// drawer for `outline_sdp` mid-run (or after the run finishes), we
// replay the buffered events into the drawer log so they see the
// full activity history — not just events that fire AFTER the drawer
// open. Without this, the long silent windows between SDP events
// (~28s while 3 LLM samples generate concurrently) made the drawer
// look empty even though the run was making progress.
// Capped per-step to avoid unbounded growth on very long runs.





// Reference to NodeDrawer — set by main.js after planner.js loads.
// Avoids synchronous circular import for the hot path.



// ──────────────────────────────────────────────────────────────────
// CoRefine chip — top-of-canvas iteration indicator (May 2026 SOTA
// pattern; mirrors Temporal Web UI's run-level retry indicator).
// Dormant: hidden. Active: amber pill showing "CoRefine · iter N/5".
// ──────────────────────────────────────────────────────────────────
const _COREFINE_CHIP_ID = 'fw-synth-corefine-chip';




// Window resize handler — rAF-throttled (mirrors planner equivalent).
window.addEventListener('resize', () => {
  if (Sy._synthResizeRafPending) return;
  Sy.set_synthResizeRafPending(true);
  requestAnimationFrame(() => {
    Sy.set_synthResizeRafPending(false);
    if (Sy.synthGraph) _resizeSynthCanvas();
  });
});






// Per-step live-progress text. Every step starts with a generic
// "running…" line; specific event kinds get richer messages as nodes
// ship + define their SSE event surface. Mirrors planner's
// _renderLiveProgress pattern.






// Race-tolerant state fetch (mirrors planner's _refreshCardsFromState).

// ──────────────────────────────────────────────────────────────────
// Chapter progress strip — visible only during STUDY-mode runs.
// ──────────────────────────────────────────────────────────────────

// Chstrip block (_showChStrip, _renderChStrip, _markChStripCell,
// _onStripCellClick, etc.) extracted to ./chstrip.js (Step 4,
// 2026-06-05 follow-up) using the DI registration pattern.
// Cross-refs (pollSynthState, refreshSynthStartState, etc.) are wired
// here via registerChstripDeps. Re-exported so main.js + sibling
// callers using `import { _renderChStrip } from './synth/synth.js'`
// keep resolving without churn.
export {
  _showChStrip,
  _renderChStrip,
  _applyChStripTitles,
  _markChStripCell,
  _markChStripCellTime,
  _updateChStripCounter,
  _resetStudyState,
  _refreshSynthPlanGate,
  _hydrateChStripFromChapters,
  _highlightStripCell,
  _onStripCellClick,
} from './chstrip.js';
import { registerChstripDeps } from './chstrip_deps.js';
registerChstripDeps({
  _resizeSynthCanvas,
  refreshSynthStartState,
  resetSynthCards,
  _resetSynthEventBuffer,
  renderSynthCards,
  pollSynthState,
  _getNodeDrawerRef: () => _nodeDrawerRef,
});

// SSE consumer — symmetric with pollPlannerState.

// Per-slug isolation — same key shape as planner, separate namespace.

// STUDY-mode persistence — separate namespace.

// Page-refresh recovery for synth.

// Page-load auto-recovery — mirrors recoverActivePlanner.


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

window.ddWipeSynth = wipeSynth;


// Synth-cards click-to-expand removed 2026-06-05 — `Sy.synthCardsEl` is
// permanently null since the cards DOM was removed 2026-05-19, so the
// `if (Sy.synthCardsEl) { ... }` guard never registered the handler.

// Synth Start / Resume / Stop button. Running (study OR single-chapter) →
// Stop (cancel); otherwise Start/Resume (startSynth resumes via the backend
// skip-rendered orchestrator, so completed chapters are kept).
if (Sy.synthStartBtn) {
  Sy.synthStartBtn.addEventListener('click', () => {
    const running = Sy.synthThreadId !== null || Sy.studyThreadId !== null;
    if (running) cancelSynth();
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
  const beforeSynth = Sy.synthThreadId;
  const beforeStudy = Sy.studyThreadId;
  Sy.setSynthThreadId(null);
  Sy.setStudyThreadId(null);
  if (Si.activeSlug) {
    try { _forgetActiveStudy(Si.activeSlug); } catch (_) {}
    try { _forgetActiveSynth(Si.activeSlug); } catch (_) {}
  }
  refreshSynthStartState();
  console.log(
    '[ddForceResetSynthUI] cleared synthThreadId=' + beforeSynth
    + ', studyThreadId=' + beforeStudy
    + ', activeSlug=' + Si.activeSlug + ' — Wipe button is now enabled.'
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
if (Sy.synthWipeBtn) {
  Sy.synthWipeBtn.addEventListener('click', async () => {
    console.log('[wipeSynth-click] activeSlug=' + Si.activeSlug
      + ' synthThreadId=' + Sy.synthThreadId
      + ' studyThreadId=' + Sy.studyThreadId
      + ' disabled=' + (Sy.synthWipeBtn.getAttribute('disabled') === 'disabled'));

    if (!Si.activeSlug) {
      showToast('Pick a framework first before wiping.');
      return;
    }
    // Defense-in-depth: check BOTH thread IDs. Previously this only
    // checked synthThreadId so a study-level run could slip through.
    const running = Sy.synthThreadId !== null || Sy.studyThreadId !== null;
    if (running) {
      showToast(
        'A Synth run is in progress (synth=' + Sy.synthThreadId
        + ', study=' + Sy.studyThreadId + '). Click Stop Synth first. '
        + 'If the state is stuck, run `ddForceResetSynthUI()` in console.'
      );
      return;
    }
    // Probe pipeline state so the confirm dialog tells the user
    // whether they're erasing rendered Study chapters along with the
    // Synth thread state. Study lives UNDER synth/{slug}/ in MinIO, so
    // wiping Synth always wipes Study — no separate cascade call.
    const state = await fetchPipelineState(Si.activeSlug);
    const cascade = cascadeImpactText(state, 'synth');
    const ok = await showConfirm(
      'Wipe synth cache for ' + Si.activeSlug + '?',
      ('Deletes MinIO chapter artifacts + Postgres checkpoints + ' +
       'browser state for ' + Si.activeSlug +
       '. Planner cache is untouched.' + cascade +
       ' This cannot be undone.'),
      'Wipe',
    );
    if (!ok) return;
    const result = await wipeSynth(Si.activeSlug);
    if (result && result.error) {
      showToast('Wipe failed: ' + result.error);
    } else if (result && result.http_status) {
      showToast('Wipe failed: HTTP ' + result.http_status);
    } else {
      showToast('Synth cache wiped for ' + Si.activeSlug + '.');
    }
  });
}

// Re-exports for backward compat (Step 3 follow-up extractions).
export {
  _bufferSynthEvent,
  _resetSynthEventBuffer,
  _openSynthNodeDrawer,
  _refreshOpenSynthDrawer,
  _setNodeDrawerRef,
  _resizeSynthCanvas,
  _runSynthLayoutAndCenter,
  _updateCoRefineChip,
  _initSynthCanvas,
} from './canvas.js';
import {
  _resizeSynthCanvas,
  _resetSynthEventBuffer,
} from './canvas.js';

// _synthRunStartMs run-start timestamp moved to ./shared.js (2026-06-05
// follow-up). lifecycle.js was writing it directly with no import (a
// latent ReferenceError) and polling.js had it wrapped behind a DI
// getter/setter pair. shared.js now hosts setSynthRunStartMs +
// getSynthRunStartMs — all consumers go through one store, no cycles.

// Polling extracted to ./polling.js + ./polling_deps.js (Step 7).
export {
  synthCardEl,
  _synthStepIdx,
  _synthAllImplementedComplete,
  _synthLiveProgressEl,
  _markSynthCardRunning,
  _renderSynthLiveProgress,
  _refreshSynthCardsFromState,
  pollStudyState,
  pollSynthState,
  _genSynthThreadId,
} from './polling.js';
import { pollSynthState, pollStudyState } from './polling.js';
import { registerSynthPollingDeps } from './polling_deps.js';
import {
  _markChStripCell,
  _markChStripCellTime,
  _highlightStripCell,
} from './chstrip.js';
// Local-scope imports for symbols synth.js's MODULE BODY references.
// `export { foo } from './lifecycle.js'` is RE-EXPORT only; it does
// NOT introduce `foo` into this module's lexical scope. Without
// `wipeSynth` here, line 157 (`window.ddWipeSynth = wipeSynth`) throws
// ReferenceError at module init, aborting synth.js entirely — same
// failure mode as planner.js had with `wipePlanner`. Added 2026-06-06.
import {
  renderSynthCards,
  markSynthFailed,
  refreshSynthStartState,
  resetSynthCards,
  wipeSynth,
  // 2026-06-06 — same re-export-without-local-import bug. These were
  // only re-exported below (line ~340-344). Click handler at line 168
  // referenced `startSynth`/`cancelSynth` as bare identifiers — would
  // throw ReferenceError on the user's first Start/Stop click. wipeSynth
  // click handler body (~line 192) referenced `_forgetActiveStudy` /
  // `_forgetActiveSynth` — same.
  startSynth,
  cancelSynth,
  _forgetActiveStudy,
  _forgetActiveSynth,
} from './lifecycle.js';

// DI registration for polling.js — must run AFTER the 9 dependent
// functions are defined / imported above (lifecycle, chstrip,
// synthRunStartMs getter/setter pair). Module-eval semantics
// guarantee this happens before any SSE event fires.
registerSynthPollingDeps({
  renderSynthCards,
  markSynthFailed,
  refreshSynthStartState,
  resetSynthCards,
  _markChStripCell,
  _markChStripCellTime,
  _highlightStripCell,
});

// Lifecycle extracted to ./lifecycle.js (Step 8).
export {
  renderSynthCards, markSynthFailed, resetSynthCards,
  refreshSynthStartState, setSynthFramework,
  _synthStorageKey, _rememberActiveSynth, _forgetActiveSynth,
  _studyStorageKey, _rememberActiveStudy, _forgetActiveStudy,
  _getActiveStudy,
  _tryResumeActiveSynth, recoverActiveSynth,
  startSynth, cancelSynth, wipeSynth, loadSynthInfo,
} from './lifecycle.js';
