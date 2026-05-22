// ============================================================
// state.js — DOM element references + mutable state variables
// ============================================================

export const API = '/api/v1/docs-distiller';

// -------- picker controls (Step 1) --------
export const search = document.querySelector('#fw-search');
export const chips = document.querySelectorAll('.fw-chip');
export const tiles = document.querySelectorAll('.fw-tile');
export const grid = document.querySelector('#fw-grid');
export const countEl = document.querySelector('#fw-count');
export const total = tiles.length;
// -------- sticky bar --------
export const generate = document.querySelector('#fw-generate');
export const selectedName = document.querySelector('#fw-selected-name');
export const stickyBar = document.querySelector('#fw-sticky-bar');
// -------- stepper --------
export const steps = document.querySelectorAll('.fw-step');
export const connectors = document.querySelectorAll('.fw-step-connector');
export const panels = document.querySelectorAll('.fw-step-panel');
// -------- step 2 progress + file list --------
export const progressBox = document.querySelector('#fw-progress-box');
export const progressTier = document.querySelector('#fw-progress-tier');
export const progressStatus = document.querySelector('#fw-progress-status');
export const progressBar = document.querySelector('#fw-progress-bar');
export const progressFill = document.querySelector('#fw-progress-fill');
export const progressCounter = document.querySelector('#fw-progress-counter');
export const progressUrl = document.querySelector('#fw-progress-url');
export const progressLogos = document.querySelector('#fw-progress-logos');
export const progressFramework = document.querySelector('#fw-progress-framework');
export const cancelBtn = document.querySelector('#fw-cancel');
export const step2Summary = document.querySelector('#fw-step2-summary');
export const step2Grid = document.querySelector('#fw-step2-grid');
// -------- step 3 manifest (mirror — also rendered for the future synth view) --------
export const pagesSummary = document.querySelector('#fw-pages-summary');
export const pageGrid = document.querySelector('#fw-page-grid');
// -------- sidebar (library) --------
export const sidebar = document.querySelector('#fw-sidebar');
export const sidebarList = document.querySelector('#fw-sidebar-list');
// -------- notice + toast --------
export const noticeEl = document.querySelector('#fw-cache-notice');
export const noticeText = document.querySelector('#fw-cache-notice-text');
export const toastEl = document.querySelector('#fw-denied-toast');
export const toastText = document.querySelector('#fw-denied-toast-text');
export const toastClose = document.querySelector('#fw-denied-toast-close');
// -------- confirm modal --------
export const modalEl = document.querySelector('#fw-modal');
export const modalTitleEl = document.querySelector('#fw-modal-title');
export const modalMessageEl = document.querySelector('#fw-modal-message');
export const modalConfirmBtn = document.querySelector('#fw-modal-confirm');
export const modalCancelBtn = document.querySelector('#fw-modal-cancel');
// -------- file-content drawer --------
export const drawerEl = document.querySelector('#fw-drawer');
export const drawerName = document.querySelector('#fw-drawer-name');
export const drawerMeta = document.querySelector('#fw-drawer-meta');
export const drawerBody = document.querySelector('#fw-drawer-body');
export const drawerPrev = document.querySelector('#fw-drawer-prev');
export const drawerNext = document.querySelector('#fw-drawer-next');
export const drawerClose = document.querySelector('#fw-drawer-close');
// -------- planner (Step 3) --------
export const plannerStartBtn   = document.querySelector('#fw-planner-start');
export const plannerWipeBtn    = document.querySelector('#fw-planner-wipe');
export const plannerCardsEl    = document.querySelector('#fw-planner-cards');
// plannerProgressLbl removed 2026-05-18 — the "Step N of 8" counter
// moved into the status pill (`WORKING · N/8`) for less header noise.
export const plannerFwLogosEl  = document.querySelector('#fw-planner-fw-logos');
export const plannerFwNameEl   = document.querySelector('#fw-planner-fw-name');

// State
export let activeChip = 'All';
export let query = '';
export let selected = null;            // slug picked in Step 1
export let activeSlug = null;          // slug currently shown in Step 3
export let activeRunId = null;         // run currently being polled
export let pollAbort = false;
export let currentStep = 1;
export let farthestStep = 1;
// -------- planner --------
export let plannerThreadId = null;
// Used by _tryResumeActivePlanner's orphan-detection timeout: cleared
// when an SSE event arrives so we can distinguish a stuck "running"
// state (no live task) from an actively-running one.
export let _liveEventReceived = false;
// off_topic verdict-table sort state (column + direction). Survives
// re-renders so SSE refreshes preserve the operator's current sort.
export let _offTopicSort = {col: null, dir: 'asc'};
// Latest off_topic state values cached at render time so a sort-header
// click can re-render the card without refetching /state.
export let _lastOffTopicValues = null;
export let plannerPollAbort = false;
// Substep order MUST match `NODE_ORDER` in
// services/docs_distiller/planner/graph.py AND the field each node
// writes (`state.<field>`).
export const PLANNER_SUBSTEP_FIELDS = [
  'raw_files',                // corpus_load
  'embeddings_ref',           // embed_corpus
  'relevant_files',           // off_topic
  'cluster_assignments_ref',  // cluster
  'refine_assignments_ref',   // refine
  'cluster_labels_ref',       // label
  'chapter_plan_ref',         // reduce
  'plan_path',                // plan_write
];
// Parallel to PLANNER_SUBSTEP_FIELDS — the node name (matches the
// server-side step name in SSE events). Used by the SSE handler to
// map step → previous step → expected checkpoint field.
export const PLANNER_NODE_ORDER = [
  'corpus_load', 'embed_corpus', 'off_topic',
  'cluster', 'refine', 'label',
  'reduce', 'plan_write',
];
// Short labels for the graph canvas (same text as the card titles —
// hardcoded here to keep StageGraph independent of DOM-card scraping).
export const PLANNER_NODE_LABELS = [
  'Corpus load', 'Embed corpus', 'Off-topic filter',
  'Cluster', 'Refine (LITA)', 'Label',
  'Reduce (outline)', 'Plan write',
];
// Populated from GET /planner/info — names of substeps actually wired
// into the runtime graph. Stubs aren't included; their cards render
// as "future" so the user doesn't expect them to advance.
export let plannerImplemented = new Set();

// Always 'graph' since cards DOM no longer exists. Kept as a named
// constant so the legacy `if (UI_MODE === 'graph')` guards stay
// readable as "the canvas path" without renaming N call sites.
export const UI_MODE = 'graph';

// Module-scoped planner graph instance — populated by initPlannerCanvas
// once Cytoscape has loaded. null when ?ui=cards (the default).
export let plannerGraph = null;

// Defensive: re-fit on window resize so the canvas stays responsive.
// Throttle to one rAF per resize burst.
export let _resizeRafPending = false;

// ---- file-content drawer ----
export let currentManifestEntries = [];
export let drawerIdx = -1;

// ============================================================
// slug → {name, logo} lookup. Built from the rendered tiles (catalog)
// and augmented from the library sidebar (which has logos too). Used
// by the loading box to label the active ingestion + by recovery.
// ============================================================
export const frameworkInfo = {};   // slug → {name, logos: [url, ...]}

// ---- modal state ----
export let _modalResolver = null;

// -------- synth (Step 4) --------
export const synthStartBtn    = document.querySelector('#fw-synth-start');
export const synthWipeBtn     = document.querySelector('#fw-synth-wipe');
export const synthCardsEl     = document.querySelector('#fw-synth-cards');
export const synthBudgetSel   = document.querySelector('#fw-synth-budget');
export const synthFwLogosEl   = document.querySelector('#fw-synth-fw-logos');
export const synthFwNameEl    = document.querySelector('#fw-synth-fw-name');

export const SYNTH_SUBSTEP_FIELDS = [
  'outline_path',          // outline_sdp
  'digest_path',           // digest_construct
  'sawc_path',             // sawc_write
  'checklist_path',        // checklist_eval
  'mgsr_path',             // mgsr_replan
  'chapter_path',          // render_audit_write
];
export const SYNTH_NODE_ORDER = [
  'outline_sdp', 'digest_construct',
  'sawc_write', 'checklist_eval',
  'mgsr_replan', 'render_audit_write',
];
export const SYNTH_NODE_LABELS = [
  'Outline (SDP)', 'Digest',
  'SAWC write', 'Checklist eval',
  'MGSR replan', 'Render + audit',
];
export const SYNTH_STEP_TO_FIELD = {
  outline_sdp:        'outline_path',
  digest_construct:   'digest_path',
  sawc_write:         'sawc_path',
  checklist_eval:     'checklist_path',
  mgsr_replan:        'mgsr_path',
  render_audit_write: 'chapter_path',
};

// Populated from GET /synth/info. Cards whose substep isn't in this
// set stay "⏳ future" — same pattern as plannerImplemented.
export let synthImplemented = new Set();
export let synthThreadId = null;
export let _synthLiveEventReceived = false;
export let synthPollAbort = false;

// Study-mode state — when Start Synth is clicked without picking a
// specific chapter, the backend spawns the orchestrator and returns a
// study_thread_id.
export let studyThreadId = null;
export let studyChapterIds = [];
export let studyChapterStatus = new Map();
export let studyCurrentChapterId = null;
export let studyCurrentChapterThreadId = null;
export let studyChapterThreads = new Map();
export let studyPinnedChapterId = null;

// Per-substep custom body renderers, keyed by idx (matches
// SYNTH_SUBSTEP_FIELDS). Empty until nodes ship.
export const SYNTH_SUBSTEP_RENDERERS = {};

// Day 5 — Synth canvas parity.
export let synthGraph = null;

// Window resize handler — rAF-throttled (mirrors planner equivalent).
export let _synthResizeRafPending = false;

// Chapter strip
export const chstripEl       = document.querySelector('#fw-chstrip');
export const chstripCellsEl  = document.querySelector('#fw-chstrip-cells');
export const chstripCounterEl = document.querySelector('#fw-chstrip-counter');

// In-memory event buffer keyed by step name.
export const _synthEventBuffer = new Map();
export const _SYNTH_EVENT_BUFFER_PER_STEP = 200;

// Mapping: SSE step name → the state field
export const STEP_TO_FIELD = {
  corpus_load:  'raw_files',
  embed_corpus: 'embeddings_ref',
  off_topic:    'relevant_files',
  cluster:      'cluster_assignments_ref',
  refine:       'refine_assignments_ref',
  label:        'cluster_labels_ref',
  reduce:       'chapter_plan_ref',
  plan_write:   'plan_path',
};

// Orphan detection timeout ms
export const _ORPHAN_DETECT_MS = 6000;

// Separate key tracking the LAST slug the user kicked off a planner
// run for.
export const _LAST_PLANNER_SLUG_KEY = 'dd:planner:last_slug';

// Per-slug isolation — same key shape as planner, separate namespace.
export const _LAST_SYNTH_SLUG_KEY = 'dd:synth:last_slug';

// -------- study (Step 5) --------
export const studyPillText      = document.querySelector('#fw-study-pill-text');
export const studyPill          = document.querySelector('#fw-study-pill');
export const studyFwName        = document.querySelector('#fw-study-fw-name');
export const studyFwLogos       = document.querySelector('#fw-study-fw-logos');
export const studyEmptyEl       = document.querySelector('#fw-study-empty');
export const studyGridEl        = document.querySelector('#fw-study-grid');
export const studyChapterListEl = document.querySelector('#fw-study-chapter-list');
export const studyChapterHeadEl = document.querySelector('#fw-study-chapter-head');
export const studyReadmeEl      = document.querySelector('#fw-study-readme');
export const studyChallengesEl  = document.querySelector('#fw-study-challenges');
export const studyFlashcardsEl  = document.querySelector('#fw-study-flashcards');
export const studyTabBtns       = document.querySelectorAll('.fw-study-tab');
export const studySideEl        = document.querySelector('#fw-study-side');
export const studySideBackdrop  = document.querySelector('#fw-study-side-backdrop');
export const studySideClose     = document.querySelector('#fw-study-side-close');
export const studyTocToggle     = document.querySelector('#fw-study-toc-toggle');

// Per-framework study state
export let studyChapters    = [];
export let studyActiveChapter = null;
export let studyActiveTab   = 'readme';
export let studyCards       = [];
export let studyCardIdx     = 0;
export let studyLoadedSlug  = null;
export let studyLoadedCid   = null;

// ============================================================
// State setter helpers — ES modules export bindings by reference,
// but re-assignment of `let` from outside must go through a setter
// function. These are the canonical "write" paths for shared state.
// ============================================================
export function setActiveChip(v)       { activeChip = v; }
export function setQuery(v)            { query = v; }
export function setSelected(v)         { selected = v; }
export function setActiveSlug(v)       { activeSlug = v; }
export function setActiveRunId(v)      { activeRunId = v; }
export function setPollAbort(v)        { pollAbort = v; }
export function setCurrentStep(v)      { currentStep = v; }
export function setFarthestStep(v)     { farthestStep = v; }
export function setPlannerThreadId(v)  { plannerThreadId = v; }
export function set_liveEventReceived(v) { _liveEventReceived = v; }
export function set_offTopicSort(v)    { _offTopicSort = v; }
export function set_lastOffTopicValues(v) { _lastOffTopicValues = v; }
export function setPlannerPollAbort(v) { plannerPollAbort = v; }
export function setPlannerImplemented(v) { plannerImplemented = v; }
export function setPlannerGraph(v)     { plannerGraph = v; }
export function set_resizeRafPending(v){ _resizeRafPending = v; }
export function setCurrentManifestEntries(v) { currentManifestEntries = v; }
export function setDrawerIdx(v)        { drawerIdx = v; }
export function set_modalResolver(v)   { _modalResolver = v; }
export function setSynthImplemented(v) { synthImplemented = v; }
export function setSynthThreadId(v)    { synthThreadId = v; }
export function set_synthLiveEventReceived(v) { _synthLiveEventReceived = v; }
export function setSynthPollAbort(v)   { synthPollAbort = v; }
export function setStudyThreadId(v)    { studyThreadId = v; }
export function setStudyChapterIds(v)  { studyChapterIds = v; }
export function setStudyChapterStatus(v) { studyChapterStatus = v; }
export function setStudyCurrentChapterId(v) { studyCurrentChapterId = v; }
export function setStudyCurrentChapterThreadId(v) { studyCurrentChapterThreadId = v; }
export function setStudyChapterThreads(v) { studyChapterThreads = v; }
export function setStudyPinnedChapterId(v) { studyPinnedChapterId = v; }
export function setSynthGraph(v)       { synthGraph = v; }
export function set_synthResizeRafPending(v) { _synthResizeRafPending = v; }
export function setStudyChapters(v)    { studyChapters = v; }
export function setStudyActiveChapter(v) { studyActiveChapter = v; }
export function setStudyActiveTab(v)   { studyActiveTab = v; }
export function setStudyCards(v)       { studyCards = v; }
export function setStudyCardIdx(v)     { studyCardIdx = v; }
export function setStudyLoadedSlug(v)  { studyLoadedSlug = v; }
export function setStudyLoadedCid(v)   { studyLoadedCid = v; }
