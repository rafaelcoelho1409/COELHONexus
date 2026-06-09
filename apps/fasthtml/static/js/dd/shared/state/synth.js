// state/synth.js — Synth + Study-orchestrator DOM + run state + node-order
// constants. Study-mode state (studyThreadId, studyChapterIds, ...) lives
// here because the orchestrator is part of the synth pipeline; the reader's
// own UI state is in state/study.js.

// -------- DOM --------
export const synthStartBtn    = document.querySelector('#fw-synth-start');
export const synthWipeBtn     = document.querySelector('#fw-synth-wipe');
export const synthCardsEl     = document.querySelector('#fw-synth-cards');
export const synthBudgetSel   = document.querySelector('#fw-synth-budget');
export const synthFwLogosEl   = document.querySelector('#fw-synth-fw-logos');
export const synthFwNameEl    = document.querySelector('#fw-synth-fw-name');
export const chstripEl        = document.querySelector('#fw-chstrip');
export const chstripCellsEl   = document.querySelector('#fw-chstrip-cells');
export const chstripCounterEl = document.querySelector('#fw-chstrip-counter');

// -------- run state --------
// Populated from GET /synth/info. Cards whose substep isn't in this set
// stay "⏳ future" — same pattern as plannerImplemented.
export let synthImplemented = new Set();
// Whether the active framework has a planner plan (planner-latest.json).
// Synth requires it — gates the Start Synth button. Set from
// GET /synth/{slug}/study/chapters (404 ⇒ no plan). Default false so the
// button stays disabled until a plan is confirmed.
export let synthHasPlan     = false;
export let synthThreadId    = null;
export let _synthLiveEventReceived = false;
export let synthPollAbort   = false;

// Study-mode state — when Start Synth is clicked without picking a
// specific chapter, the backend spawns the orchestrator and returns a
// study_thread_id.
export let studyThreadId            = null;
export let studyChapterIds          = [];
export let studyChapterStatus       = new Map();
export let studyCurrentChapterId    = null;
export let studyCurrentChapterThreadId = null;
export let studyChapterThreads      = new Map();
export let studyPinnedChapterId     = null;

// Day 5 — Synth canvas parity.
export let synthGraph = null;

// Window resize handler — rAF-throttled (mirrors planner equivalent).
export let _synthResizeRafPending = false;

// -------- constants --------
export const SYNTH_SUBSTEP_FIELDS = [
  'outline_path',          // outline_sdp
  'digest_path',           // digest_construct
  'sawc_path',             // sawc_write
  'derive_stats',          // sawc_derive (Ship #95)
  'checklist_path',        // checklist_eval
  'mgsr_path',             // mgsr_replan
  'chapter_path',          // render_audit_write
];
export const SYNTH_NODE_ORDER = [
  'outline_sdp', 'digest_construct',
  'sawc_write', 'sawc_derive',
  'checklist_eval', 'mgsr_replan',
  'render_audit_write',
];
export const SYNTH_NODE_LABELS = [
  'Outline (SDP)', 'Digest',
  'SAWC write', 'SAWC derive',
  'Checklist eval', 'MGSR replan',
  'Render + audit',
];
export const SYNTH_STEP_TO_FIELD = {
  outline_sdp:        'outline_path',
  digest_construct:   'digest_path',
  sawc_write:         'sawc_path',
  sawc_derive:        'derive_stats',
  checklist_eval:     'checklist_path',
  mgsr_replan:        'mgsr_path',
  render_audit_write: 'chapter_path',
};

// Per-substep custom body renderers, keyed by idx (matches
// SYNTH_SUBSTEP_FIELDS). Populated 2026-06-08 — rich KPI cards +
// per-node tables/lists/decisions/coverage rows for all 7 synth
// nodes. Same shape as planner.SUBSTEP_RENDERERS so the NodeDrawer's
// Overview tab gets the rich content for both stages automatically.
// Side-imported here (not a barrel re-export) so consumers that read
// `Sy.SYNTH_SUBSTEP_RENDERERS` keep working unchanged.
import { SYNTH_RENDERERS } from '@dd/synth/renderers.js';
export const SYNTH_SUBSTEP_RENDERERS = SYNTH_RENDERERS;

// In-memory event buffer keyed by step name.
export const _synthEventBuffer            = new Map();
export const _SYNTH_EVENT_BUFFER_PER_STEP = 200;

// Per-slug isolation — same key shape as planner, separate namespace.
export const _LAST_SYNTH_SLUG_KEY = 'dd:synth:last_slug';

// -------- setters --------
export function setSynthImplemented(v)        { synthImplemented = v; }
export function setSynthHasPlan(v)            { synthHasPlan = v; }
export function setSynthThreadId(v)           { synthThreadId = v; }
export function set_synthLiveEventReceived(v) { _synthLiveEventReceived = v; }
export function setSynthPollAbort(v)          { synthPollAbort = v; }
export function setSynthGraph(v)              { synthGraph = v; }
export function set_synthResizeRafPending(v)  { _synthResizeRafPending = v; }

export function setStudyThreadId(v)                { studyThreadId = v; }
export function setStudyChapterIds(v)              { studyChapterIds = v; }
export function setStudyChapterStatus(v)           { studyChapterStatus = v; }
export function setStudyCurrentChapterId(v)        { studyCurrentChapterId = v; }
export function setStudyCurrentChapterThreadId(v)  { studyCurrentChapterThreadId = v; }
export function setStudyChapterThreads(v)          { studyChapterThreads = v; }
export function setStudyPinnedChapterId(v)         { studyPinnedChapterId = v; }
