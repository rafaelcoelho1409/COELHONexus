// study/chapters.js — open / load / scroll-to-top / visibility-refresh
// for chapter content. Extracted from study.js Step 2 (2026-06-05
// follow-up). DI deps for cross-refs back to study.js.
import * as Sa from '@dd/shared/state/api.js';
import * as Si from '@dd/shared/state/ingestion.js';
import * as Ss from '@dd/shared/state/study.js';
import { fmtMs, showElapsed } from '../shared/timing.js';
import * as srs from '../shared/srs.js';
import {
  _loadStudyArtifact,
  setStudyTotalWallMs,
} from './shared.js';
import { _loadStudyReadme, _buildReadmeToc } from './readme.js';
import { _loadStudyFlashcards } from './flashcards.js';
import { deps as studyDeps } from './study_deps.js';

export function _scrollReaderTop() {
  const page = document.querySelector('.page');
  if (page) page.scrollTo({ top: 0, behavior: 'instant' });
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
    Ss.studyChallengesEl.innerHTML =
      '<div class="fw-empty">No challenges available — chapter not synthesized.</div>';
    Ss.studyFlashcardsEl.innerHTML =
      '<div class="fw-empty">No flashcards available — chapter not synthesized.</div>';
    _scrollReaderTop();
    return;
  }
  Ss.setStudyActiveChapter(cid);
  Ss.setStudyLoadedCid(cid);
  studyDeps._renderStudySidebar?.();   // re-render to update active highlight
  studyDeps._renderStudyChapterHead?.(ch);
  studyDeps._setStudyStagePill?.('working', 'Loading…');
  // Fire all three loads in parallel
  await Promise.all([
    _loadStudyReadme(Si.activeSlug, cid),
    studyDeps._loadStudyChallenges?.(Si.activeSlug, cid),
    _loadStudyFlashcards(Si.activeSlug, cid),
  ]);
  // Both prose + recall are now in the DOM — rebuild the TOC so it
  // includes the "↻ Recall questions" jump link.
  _buildReadmeToc();
  // Opening + loading a rendered chapter counts as "studied" — mark it
  // and re-render the sidebar so the ✓ + progress update immediately.
  srs.markChapterStudied(Si.activeSlug, cid, true);
  studyDeps._renderStudySidebar?.();
  studyDeps._setStudyStagePill?.('done', 'Reading · ' + (ch.title || cid));
  _scrollReaderTop();   // new chapter always starts at the top
}

export async function loadStudyChapters(slug) {
  if (!Ss.studyChapterListEl) return;
  Ss.setStudyChapters([]);
  Ss.setStudyActiveChapter(null);
  Ss.setStudyLoadedCid(null);
  studyDeps._setStudyStagePill?.('working', 'Loading chapters…');
  Ss.studyChapterListEl.innerHTML =
    '<div class="fw-empty" style="font-size:0.8rem;padding:8px 4px">' +
    'Loading chapters…</div>';
  try {
    const r = await fetch(Sa.API + '/synth/' + slug + '/study/chapters');
    if (!r.ok) {
      Ss.studyChapterListEl.innerHTML =
        '<div class="fw-empty" style="font-size:0.8rem;padding:8px 4px">' +
        'Failed to load chapters (HTTP ' + r.status + ').</div>';
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
    // Auto-open the first rendered chapter (if any) so the user
    // immediately sees content instead of an empty pane.
    const firstReady = Ss.studyChapters.find(c => c.rendered);
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
    return;
  }
  Ss.studyEmptyEl.style.display = 'none';
  Ss.studyGridEl.style.display = '';
}

