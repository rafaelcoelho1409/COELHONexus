// study/sidebar.js — chapter sidebar rendering + chapter-head bar.
// 2026-06-08: stripped SRS due-card badges + Active Recall "studied"
// markers + Learn/Flashcards tab switch (subsystems removed).
import * as Ss from '@dd/shared/state/study.js';
import { escapeHtml } from '../shared/utils.js';
import { fmtMs } from '../shared/timing.js';
import { getStudyTotalWallMs } from './shared.js';

export function _renderStudySidebar() {
  if (!Ss.studyChapterListEl) return;
  if (!Ss.studyChapters.length) {
    Ss.studyChapterListEl.innerHTML =
      '<div class="fw-empty" style="font-size:0.8rem;padding:8px 4px">' +
      'No chapters in this framework\'s plan. Run Planner first.' +
      '</div>';
    return;
  }
  const synthesized = Ss.studyChapters.filter(ch => ch.rendered).length;
  if (synthesized === 0) {
    Ss.studyChapterListEl.innerHTML =
      '<div class="fw-empty" style="font-size:0.8rem;padding:8px 4px">' +
      'No chapters synthesized yet. Run Synth to generate this study.' +
      '</div>';
    return;
  }
  const totalWall = getStudyTotalWallMs();
  const totalTimeHtml = totalWall > 0
    ? '<span class="fw-study-total-time" title="Total Synth wall-clock ' +
      '(cumulative chapter time + book harmonize)">⏱ ' +
      fmtMs(totalWall) + '</span>'
    : '';
  const progressHtml =
    '<div class="fw-study-progress">' +
      '<span>' + synthesized + ' / ' + Ss.studyChapters.length + ' synthesized</span>' +
      totalTimeHtml +
    '</div>';
  const rows = Ss.studyChapters.map(ch => {
    const status = !ch.rendered
      ? 'not-rendered'
      : (ch.audit_passed ? 'rendered' : 'audit-failed');
    const icon = !ch.rendered
      ? '○'
      : (ch.audit_passed ? '●' : '✕');
    const cls = [
      'fw-study-chapter',
      ch.id === Ss.studyActiveChapter ? 'active' : '',
    ].filter(Boolean).join(' ');
    const title = ch.title || ch.id;
    const tms = Number(ch.wall_ms || 0);
    const timeBadge = tms > 0
      ? '<span class="fw-study-chapter-time" title="Synth time for this ' +
        'chapter">' + fmtMs(tms) + '</span>'
      : '';
    return (
      '<button type="button" class="' + cls + '" ' +
      'data-chapter-id="' + escapeHtml(ch.id) + '" ' +
      'data-rendered="' + ch.rendered + '">' +
        '<span class="fw-study-chapter-icon" data-status="' + status + '">' +
          icon + '</span>' +
        '<span class="fw-study-chapter-title">' +
          escapeHtml(title) + '</span>' +
        timeBadge +
      '</button>'
    );
  }).join('');
  Ss.studyChapterListEl.innerHTML = progressHtml + rows;
}

export function _renderStudyChapterHead(ch) {
  if (!Ss.studyChapterHeadEl) return;
  if (!ch) {
    Ss.studyChapterHeadEl.classList.remove('visible');
    Ss.studyChapterHeadEl.innerHTML = '';
    return;
  }
  const auditBadge = ch.rendered
    ? (ch.audit_passed
        ? '<span class="badge pass">Audit ✓</span>'
        : '<span class="badge fail">Audit ✗</span>')
    : '<span class="badge">Not rendered</span>';
  Ss.studyChapterHeadEl.innerHTML =
    '<div class="fw-study-chapter-head-title">' +
      escapeHtml(ch.title || ch.id) + '</div>' +
    '<div class="fw-study-chapter-head-meta">' +
      auditBadge +
      '<span>' + (ch.n_sections || 0) + ' sections</span>' +
      '<span>' + (ch.n_sources || 0) + ' sources</span>' +
      ((ch.rendered_chars || 0)
        ? '<span>' + ((ch.rendered_chars / 1000).toFixed(1)) + 'k chars</span>'
        : '') +
    '</div>';
  Ss.studyChapterHeadEl.classList.add('visible');
}
