// state/ingestion.js — ingestion (Step 2) progress + file grid + library
// sidebar + framework slug→{name, logos} registry (populated by both the
// catalog tiles and the library sidebar; consumed by progress display and
// resume recovery).

// -------- progress display --------
export const progressBox       = document.querySelector('#fw-progress-box');
export const progressTier      = document.querySelector('#fw-progress-tier');
export const progressStatus    = document.querySelector('#fw-progress-status');
export const progressBar       = document.querySelector('#fw-progress-bar');
export const progressFill      = document.querySelector('#fw-progress-fill');
export const progressCounter   = document.querySelector('#fw-progress-counter');
export const progressUrl       = document.querySelector('#fw-progress-url');
export const progressLogos     = document.querySelector('#fw-progress-logos');
export const progressFramework = document.querySelector('#fw-progress-framework');
export const cancelBtn         = document.querySelector('#fw-cancel');
export const step2Summary      = document.querySelector('#fw-step2-summary');
export const step2Grid         = document.querySelector('#fw-step2-grid');
// Manifest mirror (also rendered for future synth view).
export const pagesSummary      = document.querySelector('#fw-pages-summary');
export const pageGrid          = document.querySelector('#fw-page-grid');
// Library sidebar
export const sidebar           = document.querySelector('#fw-sidebar');
export const sidebarList       = document.querySelector('#fw-sidebar-list');

// State
export let activeSlug   = null;   // slug currently shown in Step 3
export let activeRunId  = null;   // run currently being polled
export let pollAbort    = false;

// slug → {name, logos: [url, ...]} lookup. Built from rendered tiles
// + library sidebar. Consumed by the loading box and the resume path.
export const frameworkInfo = {};

// Setters
export function setActiveSlug(v)  { activeSlug = v; }
export function setActiveRunId(v) { activeRunId = v; }
export function setPollAbort(v)   { pollAbort = v; }
