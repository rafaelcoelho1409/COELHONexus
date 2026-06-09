// Study viewer — chapter sidebar + README reader (single-mode).
// 2026-06-08: Active Recall + FSRS Flashcards subsystems removed
// (`challenges.js`, `flashcards.js`, `flashcards_deps.js`, `../shared/srs.js`
// deleted). The Learn/Flashcards tab switch is gone; the only reader
// mode is the README + right-rail TOC pane already in body.py.

import * as Si from '@dd/shared/state/ingestion.js';
import * as Ss from '@dd/shared/state/study.js';

export function _setStudySideOpen(open) {
  if (Ss.studySideEl) Ss.studySideEl.classList.toggle('open', open);
  if (Ss.studySideBackdrop) Ss.studySideBackdrop.classList.toggle('open', open);
  if (Ss.studyTocToggle) Ss.studyTocToggle.setAttribute('aria-expanded', String(!!open));
}
export function openStudySide()  { _setStudySideOpen(true); }
export function closeStudySide() { _setStudySideOpen(false); }
export function toggleStudySide() {
  _setStudySideOpen(!(Ss.studySideEl && Ss.studySideEl.classList.contains('open')));
}
if (Ss.studyTocToggle) Ss.studyTocToggle.addEventListener('click', toggleStudySide);
if (Ss.studySideClose) Ss.studySideClose.addEventListener('click', closeStudySide);
if (Ss.studySideBackdrop) Ss.studySideBackdrop.addEventListener('click', closeStudySide);

// Focus mode — hide the left chapter rail and let the reader fill the
// freed width (the right-rail TOC stays). Toggles `.focus-mode` on
// .fw-study-grid; persisted in localStorage so it sticks across reloads.
const _FOCUS_KEY = 'dd:study:focus';
function _applyFocusMode(on) {
  const grid = document.querySelector('#fw-study-grid');
  const btn = document.querySelector('#fw-study-focus-toggle');
  if (grid) grid.classList.toggle('focus-mode', on);
  if (btn) btn.classList.toggle('active', on);
  try { localStorage.setItem(_FOCUS_KEY, on ? '1' : '0'); } catch (_) {}
}
(() => {
  const btn = document.querySelector('#fw-study-focus-toggle');
  if (!btn) return;
  let on = false;
  try { on = localStorage.getItem(_FOCUS_KEY) === '1'; } catch (_) {}
  _applyFocusMode(on);
  btn.addEventListener('click', () => {
    const grid = document.querySelector('#fw-study-grid');
    _applyFocusMode(!(grid && grid.classList.contains('focus-mode')));
  });
})();
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && Ss.studySideEl &&
      Ss.studySideEl.classList.contains('open')) {
    closeStudySide();
  }
});

export function _setStudyStagePill(status, label) {
  if (!Ss.studyPill || !Ss.studyPillText) return;
  const map = {
    idle:    'Idle',
    working: 'Loading',
    done:    'Ready',
    failed:  'Failed',
    cancelled: 'Cancelled',
  };
  Ss.studyPill.dataset.status = status;
  Ss.studyPillText.textContent = label || map[status] || status;
}

export function setStudyFramework(slug) {
  if (!Ss.studyFwName || !Ss.studyFwLogos) return;
  if (!slug) {
    Ss.studyFwName.textContent = 'Pick a framework with synthesized chapters.';
    Ss.studyFwName.classList.add('fw-planner-fw-name-empty');
    Ss.studyFwLogos.innerHTML = '';
    Ss.studyFwLogos.style.display = 'none';
    return;
  }
  const info = Si.frameworkInfo[slug] || {name: slug, logos: []};
  Ss.studyFwName.textContent = info.name || slug;
  Ss.studyFwName.classList.remove('fw-planner-fw-name-empty');
  if (info.logos && info.logos.length) {
    Ss.studyFwLogos.innerHTML = info.logos.map(u =>
      '<img class="fw-planner-fw-logo" src="' + u + '" alt="">'
    ).join('');
    Ss.studyFwLogos.style.display = '';
  } else {
    Ss.studyFwLogos.innerHTML = '';
    Ss.studyFwLogos.style.display = 'none';
  }
}

// README rendering helpers extracted to ./readme.js.
export { _loadStudyReadme } from './readme.js';

// Chapter sidebar: event delegation for chapter clicks. Picking a
// chapter closes the side window so the materials get the full width.
import { openStudyChapter } from './chapters.js';
if (Ss.studyChapterListEl) {
  Ss.studyChapterListEl.addEventListener('click', ev => {
    const btn = ev.target.closest('.fw-study-chapter');
    if (!btn) return;
    const cid = btn.dataset.chapterId;
    if (!cid) return;
    openStudyChapter(cid);
    closeStudySide();
  });
}

// Cmd-K cross-chapter search subsystem — pure side-effect module
// (overlay + index + Cmd-K/Ctrl-K shortcut + 🔍 button wiring).
import './search.js';

// Sidebar + chapter re-exports for backward compat.
export {
  _renderStudySidebar,
  _renderStudyChapterHead,
} from './sidebar.js';
export {
  _scrollReaderTop,
  openStudyChapter,
  loadStudyChapters,
  refreshStudyVisibility,
} from './chapters.js';
import {
  _renderStudySidebar,
  _renderStudyChapterHead,
} from './sidebar.js';
import {
  _scrollReaderTop,
  loadStudyChapters,
  refreshStudyVisibility,
} from './chapters.js';

// DI registration for sibling modules — runs after the dependent
// functions are defined above. Module-eval ordering guarantees this
// happens before any user-driven action.
import { registerStudyDeps } from './study_deps.js';
registerStudyDeps({
  closeStudySide,
  openStudyChapter,
  _setStudyStagePill,
  _renderStudySidebar,
  _renderStudyChapterHead,
  setStudyFramework,
  _setStudySideOpen,
});
