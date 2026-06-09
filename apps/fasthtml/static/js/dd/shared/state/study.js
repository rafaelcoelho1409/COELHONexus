// state/study.js — Study reader DOM + per-framework reader state.
// (Orchestrator state lives in state/synth.js; this file is the
// READER UI's own state.)

// -------- DOM --------
export const studyPillText      = document.querySelector('#fw-study-pill-text');
export const studyPill          = document.querySelector('#fw-study-pill');
export const studyFwName        = document.querySelector('#fw-study-fw-name');
export const studyFwLogos       = document.querySelector('#fw-study-fw-logos');
export const studyEmptyEl       = document.querySelector('#fw-study-empty');
export const studyGridEl        = document.querySelector('#fw-study-grid');
export const studyChapterListEl = document.querySelector('#fw-study-chapter-list');
export const studyChapterHeadEl = document.querySelector('#fw-study-chapter-head');
export const studyReadmeEl      = document.querySelector('#fw-study-readme');
export const studySideEl        = document.querySelector('#fw-study-side');
export const studySideBackdrop  = document.querySelector('#fw-study-side-backdrop');
export const studySideClose     = document.querySelector('#fw-study-side-close');
export const studyTocToggle     = document.querySelector('#fw-study-toc-toggle');

// -------- per-framework reader state --------
export let studyChapters       = [];
export let studyActiveChapter  = null;
export let studyLoadedSlug     = null;
export let studyLoadedCid      = null;

// -------- setters --------
export function setStudyChapters(v)       { studyChapters = v; }
export function setStudyActiveChapter(v)  { studyActiveChapter = v; }
export function setStudyLoadedSlug(v)     { studyLoadedSlug = v; }
export function setStudyLoadedCid(v)      { studyLoadedCid = v; }
