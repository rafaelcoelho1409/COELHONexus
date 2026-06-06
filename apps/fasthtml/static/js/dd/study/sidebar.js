// study/sidebar.js — chapter sidebar rendering + chapter-head bar +
// tab switching. Extracted from study.js Step 2 (2026-06-05 follow-
// up) using per-function grep + brace-counting (Step 1's line-range
// approach broke a function mid-body today; this method is safer).
// closeStudySide is needed from study.js — wired via DI.
import * as Si from '@dd/shared/state/ingestion.js';
import * as Ss from '@dd/shared/state/study.js';
import { escapeHtml } from '../shared/utils.js';
import * as srs from '../shared/srs.js';
import { fmtMs } from '../shared/timing.js';
import { getStudyTotalWallMs } from './shared.js';
import { deps as studyDeps } from './study_deps.js';

export function _renderStudySidebar() {
  if (!Ss.studyChapterListEl) return;
  if (!Ss.studyChapters.length) {
    Ss.studyChapterListEl.innerHTML =
      '<div class="fw-empty" style="font-size:0.8rem;padding:8px 4px">' +
      'No chapters in this framework\'s plan. Run Planner first.' +
      '</div>';
    return;
  }
  const slug = Si.activeSlug;
  // Header progress counts SYNTHESIZED chapters (those with synth output
  // on the server), NOT chapters the user has personally opened. `totalDue`
  // is gated on `rendered` too: a wiped chapter's lingering local SRS deck
  // (keyed slug::cid, never reconciled with the server, survives wipes +
  // hard refresh) must not resurface as phantom due-card counts.
  const synthesized = Ss.studyChapters.filter(ch => ch.rendered).length;
  // Nothing synthesized yet — fresh framework OR just after Wipe Synth.
  // Don't list the planner's chapter names as if a study exists (that's
  // what made a wiped framework still "show the old synth"); show a clean
  // empty note instead. The list returns as soon as ≥1 chapter renders.
  if (synthesized === 0) {
    Ss.studyChapterListEl.innerHTML =
      '<div class="fw-empty" style="font-size:0.8rem;padding:8px 4px">' +
      'No chapters synthesized yet. Run Synth to generate this study.' +
      '</div>';
    return;
  }
  let totalDue = 0;
  Ss.studyChapters.forEach(ch => {
    if (ch.rendered) totalDue += srs.deckDueCount(slug, ch.id);
  });
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
      (totalDue ? '<button type="button" class="fw-study-review-due" ' +
        'title="Review all due flashcards across chapters">▶ Review ' +
        totalDue + ' due</button>' : '') +
    '</div>';
  const rows = Ss.studyChapters.map(ch => {
    const status = !ch.rendered
      ? 'not-rendered'
      : (ch.audit_passed ? 'rendered' : 'audit-failed');
    const icon = !ch.rendered
      ? '○'
      : (ch.audit_passed ? '●' : '✕');
    // Studied/due markers only for RENDERED chapters — see header note.
    const studiedFlag = ch.rendered && srs.isChapterStudied(slug, ch.id);
    const cls = [
      'fw-study-chapter',
      ch.id === Ss.studyActiveChapter ? 'active' : '',
      studiedFlag ? 'studied' : '',
    ].filter(Boolean).join(' ');
    const title = ch.title || ch.id;
    const due = ch.rendered ? srs.deckDueCount(slug, ch.id) : 0;
    const dueBadge = due
      ? '<span class="fw-study-chapter-due" title="' + due +
        ' flashcards due">' + due + '</span>'
      : '';
    // Per-chapter synth wall-clock (from study-timing-latest.json via the
    // chapters API). Only shown once measured (>0).
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
        dueBadge +
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

export function _switchStudyTab(tab) {
  Ss.setStudyActiveTab(tab);
  Ss.studyTabBtns.forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tab);
  });
  document.querySelectorAll('.fw-study-pane').forEach(pane => {
    pane.classList.toggle('active', pane.dataset.tab === tab);
  });
}

