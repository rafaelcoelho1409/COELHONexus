// state/planner.js — Planner DOM + run state + node-order constants.
//
// Substep order MUST match `NODE_ORDER` in
// apps/fastapi/domains/dd/planner/graph.py AND the field each node writes
// (`state.<field>`). LLM-first path canonical since 2026-05-27 (see
// DD-PLANNER-LLM-FIRST-SOTA-2026-05-27.md); legacy 4-node middle
// (cluster/refine/label/reduce) removed 2026-06-02.

// -------- DOM --------
export const plannerStartBtn   = document.querySelector('#fw-planner-start');
export const plannerWipeBtn    = document.querySelector('#fw-planner-wipe');
export const plannerCardsEl    = document.querySelector('#fw-planner-cards');
export const plannerFwLogosEl  = document.querySelector('#fw-planner-fw-logos');
export const plannerFwNameEl   = document.querySelector('#fw-planner-fw-name');

// -------- run state --------
export let plannerThreadId    = null;
// Used by _tryResumeActivePlanner's orphan-detection timeout: cleared on
// any SSE event so we can distinguish a stuck "running" state from a live
// one.
export let _liveEventReceived = false;
// off_topic verdict-table sort state (column + direction). Survives re-
// renders so SSE refreshes preserve the operator's current sort.
export let _offTopicSort      = {col: null, dir: 'asc'};
// Latest off_topic state values cached at render time so a sort-header
// click can re-render the card without refetching /state.
export let _lastOffTopicValues = null;
export let plannerPollAbort   = false;
// Populated from GET /planner/info — names of substeps actually wired
// into the runtime graph. Stubs aren't included; their cards render as
// "future" so the user doesn't expect them to advance.
export let plannerImplemented = new Set();
// Module-scoped planner graph instance — populated by initPlannerCanvas
// once Cytoscape has loaded.
export let plannerGraph       = null;
// Defensive: re-fit on window resize so the canvas stays responsive.
// Throttle to one rAF per resize burst.
export let _resizeRafPending  = false;

// -------- constants --------
export const PLANNER_SUBSTEP_FIELDS = [
  'raw_files',                  // corpus_load
  'embeddings_ref',             // embed_corpus
  'relevant_files',             // off_topic
  'doc_distill_ref',            // doc_distill (LLM-first)
  'chapter_proposals_ref',      // chapter_propose
  'chapter_doc_assignments_ref',// chapter_assign
  'chapter_plan_ref',           // chapter_select (same field as legacy reduce)
  'chapter_order_ref',          // order_chapters (Bundle 8, 2026-05-25)
  'plan_path',                  // plan_write
];
export const PLANNER_NODE_ORDER = [
  'corpus_load', 'embed_corpus', 'off_topic',
  'doc_distill', 'chapter_propose', 'chapter_assign', 'chapter_select',
  'order_chapters', 'plan_write',
];
export const PLANNER_NODE_LABELS = [
  'Corpus load', 'Embed corpus', 'Off-topic filter',
  'Doc distill', 'Chapter propose', 'Chapter assign', 'Chapter select',
  'Order chapters', 'Plan write',
];

// Always 'graph' since cards DOM no longer exists. Kept as a named
// constant so the legacy `if (UI_MODE === 'graph')` guards stay readable
// as "the canvas path" without renaming N call sites.
export const UI_MODE = 'graph';

// Mapping: SSE step name → the state field for the planner graph.
export const STEP_TO_FIELD = {
  corpus_load:      'raw_files',
  embed_corpus:     'embeddings_ref',
  off_topic:        'relevant_files',
  doc_distill:      'doc_distill_ref',
  chapter_propose:  'chapter_proposals_ref',
  chapter_assign:   'chapter_doc_assignments_ref',
  chapter_select:   'chapter_plan_ref',
  order_chapters:   'chapter_order_ref',
  plan_write:       'plan_path',
};

export const _ORPHAN_DETECT_MS       = 6000;
export const _LAST_PLANNER_SLUG_KEY  = 'dd:planner:last_slug';

// -------- setters --------
export function setPlannerThreadId(v)     { plannerThreadId = v; }
export function set_liveEventReceived(v)  { _liveEventReceived = v; }
export function set_offTopicSort(v)       { _offTopicSort = v; }
export function set_lastOffTopicValues(v) { _lastOffTopicValues = v; }
export function setPlannerPollAbort(v)    { plannerPollAbort = v; }
export function setPlannerImplemented(v)  { plannerImplemented = v; }
export function setPlannerGraph(v)        { plannerGraph = v; }
export function set_resizeRafPending(v)   { _resizeRafPending = v; }
