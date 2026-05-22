// Boot entry point.
//
// Importing the feature modules below triggers their top-level
// event-listener registration (picker, ingestion, planner, synth,
// study each self-wire their own DOM handlers at module-init). This
// file's job is only the INIT sequence the original IIFE ran at the
// bottom: initial render passes + library/recovery bootstrap.
import * as S from './state.js';
import { renderStepper, refreshGenerateState } from './ui.js';
// Side-effect imports — these modules register their event listeners
// at top level when imported.
import './picker.js';
import './ingestion.js';
import { _initPlannerCanvas, _toggleStageEmpty } from './planner.js';
import {
  loadLibrary, recoverActiveRuns, recoverActivePlanner, loadPlannerInfo,
} from './library.js';
import { _initSynthCanvas, loadSynthInfo, recoverActiveSynth } from './synth.js';
import './study.js';

// ============================================================
// Init — mirrors the original IIFE bottom-of-file sequence.
// ============================================================
if (S.countEl) S.countEl.textContent = S.total + ' of ' + S.total;
renderStepper();
refreshGenerateState();   // initial pass — disabled until a tile is picked
// Initial empty-state — "pick a framework" placeholder on Planner + Synth.
_toggleStageEmpty('planner', true);
_toggleStageEmpty('synth',   true);

// Sequence init steps WITHOUT chaining — if one fails the next still runs.
(async () => {
  try { await loadLibrary(); }
  catch (e) { console.warn('[init] library failed:', e); }
  try { await recoverActiveRuns(); }
  catch (e) { console.warn('[init] ingestion-recover failed:', e); }
  try { await loadPlannerInfo(); }
  catch (e) { console.warn('[init] planner-info failed:', e); }
  try { _initPlannerCanvas(); }
  catch (e) { console.warn('[init] planner-canvas failed:', e); }
  try { await recoverActivePlanner(); }
  catch (e) { console.warn('[init] planner-recover failed:', e); }
  try { await loadSynthInfo(); }
  catch (e) { console.warn('[init] synth-info failed:', e); }
  try { _initSynthCanvas(); }
  catch (e) { console.warn('[init] synth-canvas failed:', e); }
  try { await recoverActiveSynth(); }
  catch (e) { console.warn('[init] synth-recover failed:', e); }
})();
