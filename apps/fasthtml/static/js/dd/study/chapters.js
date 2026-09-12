// study/chapters.js — open / load / scroll-to-top / visibility-refresh
// for chapter content. 2026-06-08: stripped of all Active Recall +
// Flashcards loading paths. README is the sole artifact loaded per
// chapter; the right-rail TOC builds from its headings.
import * as Sa from '@dd/shared/state/api.js';
import * as Si from '@dd/shared/state/ingestion.js';
import * as Ss from '@dd/shared/state/study.js';
import { escapeHtml } from '../shared/utils.js';
import { showElapsed } from '../shared/timing.js';
import { setStudyTotalWallMs } from './shared.js';
import { _loadStudyReadme, _buildReadmeToc } from './readme.js';
import { resetStudyProgressBar } from './progress.js';
import { deps as studyDeps } from './study_deps.js';

export function _scrollReaderTop() {
  const page = document.querySelector('.page');
  if (page) page.scrollTo({ top: 0, behavior: 'instant' });
}

// Prev/next footer nav — order follows the plan's chapter order (not just
// rendered ones); clicking a not-yet-synthesized neighbor still works,
// openStudyChapter() already shows the "run Synth first" placeholder for it.
function _renderChapterNav(cid) {
  if (!Ss.studyChapterNavEl) return;
  const list = Ss.studyChapters;
  const idx = list.findIndex(c => c.id === cid);
  if (idx < 0) { Ss.studyChapterNavEl.innerHTML = ''; return; }
  const prev = idx > 0 ? list[idx - 1] : null;
  const next = idx < list.length - 1 ? list[idx + 1] : null;
  const side = (ch, dir) => {
    if (!ch) return '<span class="fw-study-nav-btn fw-study-nav-spacer"></span>';
    const arrow = dir === 'prev' ? '←' : '→';
    const label = dir === 'prev' ? 'Previous' : 'Next';
    return (
      '<button type="button" class="fw-study-nav-btn fw-study-nav-' + dir + '" ' +
      'data-chapter-id="' + escapeHtml(ch.id) + '">' +
        (dir === 'prev' ? '<span class="fw-study-nav-arrow">' + arrow + '</span>' : '') +
        '<span class="fw-study-nav-text">' +
          '<span class="fw-study-nav-label">' + label + '</span>' +
          '<span class="fw-study-nav-title">' + escapeHtml(ch.title || ch.id) + '</span>' +
        '</span>' +
        (dir === 'next' ? '<span class="fw-study-nav-arrow">' + arrow + '</span>' : '') +
      '</button>'
    );
  };
  Ss.studyChapterNavEl.innerHTML = side(prev, 'prev') + side(next, 'next');
}
if (Ss.studyChapterNavEl) {
  Ss.studyChapterNavEl.addEventListener('click', ev => {
    const btn = ev.target.closest('.fw-study-nav-btn');
    if (!btn) return;
    const cid = btn.dataset.chapterId;
    if (cid) openStudyChapter(cid);
  });
}

export async function openStudyChapter(cid) {
  if (!Si.activeSlug || !cid) return;
  const ch = Ss.studyChapters.find(c => c.id === cid);
  if (!ch) return;
  if (!ch.rendered) {
    studyDeps._renderStudyChapterHead?.(ch);
    Ss.studyReadmeEl.innerHTML =
      '<div class="fw-empty">This chapter has not been synthesized yet. ' +
      'Run Synth on this chapter first.</div>';
    _renderChapterNav(cid);
    _scrollReaderTop();
    return;
  }
  Ss.setStudyActiveChapter(cid);
  Ss.setStudyLoadedCid(cid);
  studyDeps._renderStudySidebar?.();   // re-render to update active highlight
  studyDeps._renderStudyChapterHead?.(ch);
  _renderChapterNav(cid);
  studyDeps._setStudyStagePill?.('working', 'Loading…');
  await _loadStudyReadme(Si.activeSlug, cid);
  _buildReadmeToc();
  studyDeps._renderStudySidebar?.();
  studyDeps._setStudyStagePill?.('done', 'Reading · ' + (ch.title || cid));
  _scrollReaderTop();   // new chapter always starts at the top
  resetStudyProgressBar();
}

export async function loadStudyChapters(slug) {
  if (!Ss.studyChapterListEl) return;
  Ss.setStudyChapters([]);
  Ss.setStudyActiveChapter(null);
  Ss.setStudyLoadedCid(null);
  if (Ss.studyChapterNavEl) Ss.studyChapterNavEl.innerHTML = '';
  studyDeps._setStudyStagePill?.('working', 'Loading chapters…');
  Ss.studyChapterListEl.innerHTML =
    '<div class="fw-empty" style="font-size:0.8rem;padding:8px 4px">' +
    'Loading chapters…</div>';
  try {
    const r = await fetch(Sa.API + '/synth/' + slug + '/study/chapters');
    if (!r.ok) {
      Ss.studyChapterListEl.innerHTML =
        '<div class="fw-empty" style="font-size:0.8rem;padding:8px 4px">' +
        'No chapter to pick.</div>';
      studyDeps._setStudyStagePill?.('failed', 'Failed');
      return;
    }
    const data = await r.json();
    Ss.setStudyChapters((data.chapters || []).sort(
      (a, b) => (a.order || 0) - (b.order || 0)
    ));
    setStudyTotalWallMs(data.study_total_wall_ms || 0);
    // Mirror the persisted Synth total onto the navbar row-3 indicator.
    showElapsed('synth', data.study_total_wall_ms || 0);
    Ss.setStudyLoadedSlug(slug);
    studyDeps._renderStudySidebar?.();
    // Deep-link support (2026-06-08): if `?chapter=cid` is in the URL
    // AND that chapter is rendered, open it instead of the auto-first.
    // Used by the Pipeline page's per-chapter "Open in Study" button.
    let target = null;
    try {
      const wantCid = new URLSearchParams(window.location.search).get('chapter');
      if (wantCid) {
        const match = Ss.studyChapters.find(c => c.id === wantCid && c.rendered);
        if (match) target = match;
      }
    } catch (_) {}
    // Fall back to the first rendered chapter so the user always sees
    // content instead of an empty pane.
    const firstReady = target || Ss.studyChapters.find(c => c.rendered);
    if (firstReady) {
      await openStudyChapter(firstReady.id);
    } else {
      studyDeps._setStudyStagePill?.('idle',
        'No rendered chapters yet — run Synth first.');
      Ss.studyReadmeEl.innerHTML =
        '<div class="fw-empty">No chapters have been synthesized for ' +
        'this framework yet. Run Synth to generate content.</div>';
    }
  } catch (e) {
    Ss.studyChapterListEl.innerHTML =
      '<div class="fw-empty" style="font-size:0.8rem;padding:8px 4px">' +
      'Network error loading chapters.</div>';
    studyDeps._setStudyStagePill?.('failed', 'Failed');
  }
}

export function refreshStudyVisibility() {
  if (!Ss.studyEmptyEl || !Ss.studyGridEl) return;
  if (!Si.activeSlug) {
    Ss.studyEmptyEl.style.display = '';
    Ss.studyGridEl.style.display = 'none';
  } else {
    Ss.studyEmptyEl.style.display = 'none';
    Ss.studyGridEl.style.display = '';
  }
}
