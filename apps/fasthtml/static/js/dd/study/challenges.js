// study/challenges.js — recall-questions tab. Parses the synth
// `challenges.md` numbered list, renders each item as a collapsible
// active-recall row with a self-grade (Got it / Need review) persisted
// via srs. Extracted from study.js Step 1 (2026-06-05 follow-up) using
// the per-function brace-counting pattern.
import * as Si from '@dd/shared/state/ingestion.js';
import * as Ss from '@dd/shared/state/study.js';
import { escapeHtml } from '../shared/utils.js';
import * as srs from '../shared/srs.js';
import { _loadStudyArtifact } from './shared.js';

export async function _loadStudyChallenges(slug, cid) {
  if (!Ss.studyChallengesEl) return;
  Ss.studyChallengesEl.innerHTML =
    '<div class="fw-empty">Loading challenges…</div>';
  try {
    const raw = await _loadStudyArtifact(slug, cid, 'challenges.md');
    const lines = raw.split('\n');
    let title = '';
    const items = [];
    for (const line of lines) {
      const headerMatch = line.match(/^#\s+(.+)$/);
      if (headerMatch) { title = headerMatch[1].trim(); continue; }
      const numMatch = line.match(/^\s*(\d+)\.\s+(.+)$/);
      if (numMatch) {
        items.push({ num: numMatch[1], text: numMatch[2].trim() });
      }
    }
    const headerHtml = title
      ? '<h1>' + escapeHtml(title) + '</h1>'
      : '';
    // Retrieval practice: attempt → reveal guidance → self-grade. The
    // synth artifact carries QUESTIONS ONLY (no model answer), so the
    // reveal shows recall guidance + a "jump to the chapter" nudge; the
    // self-grade (Got it / Need review) persists per question. If synth
    // later emits answers, surface them in the reveal block.
    const itemsHtml = items.map((it, i) => {
      const grade = srs.getChallengeGrade(slug, cid, i) || '';
      return (
        '<div class="fw-study-challenge" data-idx="' + i + '" data-grade="' +
          grade + '">' +
          '<details class="fw-study-challenge-d">' +
            '<summary>' +
              '<span class="fw-study-challenge-num">' + it.num + '.</span>' +
              '<span class="fw-study-challenge-text">' +
                escapeHtml(it.text) + '</span>' +
            '</summary>' +
            '<div class="fw-study-challenge-hint">' +
              'Recall the answer from memory first, then check it against ' +
              'the README (same vocabulary). Grade yourself below.' +
            '</div>' +
          '</details>' +
          '<div class="fw-study-challenge-grade">' +
            '<button type="button" class="fw-cg-btn got" data-grade="got">' +
              '✓ Got it</button>' +
            '<button type="button" class="fw-cg-btn review" data-grade="review">' +
              '↻ Need review</button>' +
          '</div>' +
        '</div>'
      );
    });
    Ss.studyChallengesEl.innerHTML = headerHtml + itemsHtml.join('');
  } catch (e) {
    Ss.studyChallengesEl.innerHTML =
      '<div class="fw-empty">Failed to load challenges.md: ' +
      escapeHtml(String(e)) + '</div>';
  }
}

// Self-grade clicks (delegated). Persists per (chapter, question) so the
// got/need-review state survives reloads and tints the row.
if (Ss.studyChallengesEl) {
  Ss.studyChallengesEl.addEventListener('click', (ev) => {
    const btn = ev.target.closest('.fw-cg-btn');
    if (!btn) return;
    const row = btn.closest('.fw-study-challenge');
    if (!row || !Si.activeSlug || !Ss.studyLoadedCid) return;
    const idx = parseInt(row.dataset.idx, 10);
    const grade = btn.dataset.grade;
    const next = row.dataset.grade === grade ? '' : grade;
    row.dataset.grade = next;
    srs.setChallengeGrade(Si.activeSlug, Ss.studyLoadedCid, idx, next);
  });
}
