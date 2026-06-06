// Study viewer — chapter sidebar, tabs, flashcards, artifact loading.
import * as Si from '@dd/shared/state/ingestion.js';
import * as Ss from '@dd/shared/state/study.js';

// _studyTotalWallMs ms-state moved to ./shared.js (Step 1 follow-up,
// 2026-06-05): chapters.js writes via setStudyTotalWallMs and
// sidebar.js reads via getStudyTotalWallMs. Used to live as a
// module-local `let` here but chapters.js + sidebar.js referenced it
// without an import — latent ReferenceError that surfaced during this
// pass.

// Flashcard session state (_fcSession / _fcPos / _fcRevealed / _fcCards /
// _fcGlobal) moved into flashcards.js (Step 1 follow-up, 2026-06-05) —
// flashcards.js is the sole consumer; the prior declarations here were
// dead and broke flashcards.js at runtime (ReferenceError).

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

// Per-framework state — lives in state/study.js, accessed via per-domain
// Ss namespace imports (Phase H complete, 2026-06-05).

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




// README rendering helpers (_slugifyHeading, _buildReadmeToc,
// _entryBasename, _postProcessReadme, _loadStudyReadme) extracted to
// ./readme.js (Step 8, 2026-06-05). _loadStudyArtifact moved to
// ./shared.js. Re-exported here so consumers using
// `import { _loadStudyReadme } from './study/study.js'` keep working.
export {
  _loadStudyReadme,
} from './readme.js';
// 2026-06-06 — re-export-without-local-import bug. `_loadStudyReadme`
// is referenced at line ~191 inside `registerFlashcardsDeps({...})`
// (bare identifier passed to the DI registry). Without this local
// import, study.js throws ReferenceError at module init → study page
// JS aborts → sidebar empty, chapter open broken.
import { _loadStudyReadme } from './readme.js';
// _loadStudyChallenges (challenges-tab renderer + self-grade click
// delegation) extracted to ./challenges.js (Step 1, 2026-06-05
// follow-up). The source-file click handler that lives in readme.js
// (it calls readme.js-private _openSourceFile) was moved with it —
// it was a latent ReferenceError waiting to fire when invoked from
// here. Re-exported below to preserve `import { ... }
// from './study/study.js'` callers.
export { _loadStudyChallenges } from './challenges.js';
import { _loadStudyChallenges } from './challenges.js';

// Build the due-card queue for the current chapter. `reviewAll` ignores
// the FSRS schedule and queues every card (used by the "review all" CTA
// when nothing is due yet).
// Flashcards subsystem (_buildFlashcardSession, startGlobalReview,
// _renderFlashcard, _gradeFlashcard, _mdInline, _loadStudyFlashcards)
// extracted to ./flashcards.js (Step 5, 2026-06-05 follow-up) using the
// DI registration pattern. Cross-refs wired via registerFlashcardsDeps
// after function definitions land in study.js. Re-exports keep main.js
// + sibling callers resolving without churn.
export {
  startGlobalReview,
  _renderFlashcard,
  _mdInline,
  _loadStudyFlashcards,
} from './flashcards.js';
import { registerFlashcardsDeps } from './flashcards_deps.js';
// `startGlobalReview` used at line ~153 (event handler), same re-export
// bug. Aliased import below would also work but a plain local import
// reads cleaner here.
import { startGlobalReview } from './flashcards.js';



// Tab buttons: simple click delegation
Ss.studyTabBtns.forEach(btn => {
  btn.addEventListener('click', () => {
    _switchStudyTab(btn.dataset.tab || 'learn');
  });
});

// Chapter sidebar: event delegation for chapter clicks. Picking a
// chapter closes the side window so the materials get the full width.
if (Ss.studyChapterListEl) {
  Ss.studyChapterListEl.addEventListener('click', ev => {
    // Cross-chapter "Review due" — walks every chapter's due cards.
    if (ev.target.closest('.fw-study-review-due')) {
      closeStudySide();
      startGlobalReview();
      return;
    }
    const btn = ev.target.closest('.fw-study-chapter');
    if (!btn) return;
    const cid = btn.dataset.chapterId;
    if (!cid) return;
    openStudyChapter(cid);
    closeStudySide();
  });
}

// Visibility toggle — show empty-state when no slug active. Also
// exposed as a function so other code paths (slug click, step nav)
// can re-trigger after Si.activeSlug changes.
// (The JS viewport-fit hack was removed 2026-05-28 — the app-shell grid
// in base.css makes `.page` the scroll region, so the reader fits the
// viewport via CSS with no measuring.)

// Study-page load is driven by main.js initStudy (per-stage route) —
// the wizard-era stepFn.showStep(5) hook was removed 2026-05-28.

// Cmd-K cross-chapter search subsystem (overlay + index + Cmd-K/Ctrl-K
// shortcut + 🔍 button wiring) extracted to ./search.js (Step 2,
// 2026-06-05 follow-up). Pure side-effect module — no consumers
// outside the study tree, so it's just imported for its install-time
// listeners (no re-export needed).
import './search.js';


// DI registration for flashcards.js (Step 5, 2026-06-05 follow-up). Must
// run AFTER the 10 dependent functions are defined above. Module-eval
// semantics guarantee this happens before any user-driven action.
registerFlashcardsDeps({
  _setStudyStagePill,
  _renderStudySidebar,
  _renderStudyChapterHead,
  _switchStudyTab,
  _loadStudyReadme,
  _loadStudyChallenges,
  openStudyChapter,
  loadStudyChapters,
  refreshStudyVisibility,
  _scrollReaderTop,
});

// Re-exports for backward compat (Step 2 follow-up extractions).
export {
  _renderStudySidebar,
  _renderStudyChapterHead,
  _switchStudyTab,
} from './sidebar.js';
export {
  _scrollReaderTop,
  openStudyChapter,
  loadStudyChapters,
  refreshStudyVisibility,
} from './chapters.js';
// Local-scope imports — same re-export-without-import bug pattern.
// These are referenced as bare identifiers at:
//   line ~142: _switchStudyTab(...)          — tab-button click handler
//   line ~160: openStudyChapter(...)         — chapter-button click handler
//   lines ~188-196: inside registerFlashcardsDeps({...}) — module init
// Without these imports, study.js throws ReferenceError at module init,
// killing the whole study page (sidebar empty, chapters not loadable).
import {
  _renderStudySidebar,
  _renderStudyChapterHead,
  _switchStudyTab,
} from './sidebar.js';
import {
  _scrollReaderTop,
  openStudyChapter,
  loadStudyChapters,
  refreshStudyVisibility,
} from './chapters.js';

// DI registration for sidebar.js + chapters.js — must run AFTER the
// 12 dependent functions are defined above. The aliased-as-shortname
// imports that used to live here (_rss / _rsch / _sst / _osc / _sgr)
// were removed 2026-06-06 — those same symbols are now imported with
// their canonical names at the local-scope import block above, so the
// aliases were redundant (and obscured the call sites).
import { registerStudyDeps } from './study_deps.js';
registerStudyDeps({
  closeStudySide,
  openStudyChapter,
  startGlobalReview,
  _setStudyStagePill,
  _renderStudySidebar,
  _renderStudyChapterHead,
  _switchStudyTab,
  setStudyFramework,
  _loadStudyChallenges,
  _setStudySideOpen,
});
