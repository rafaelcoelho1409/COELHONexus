// Study viewer — chapter S.sidebar, tabs, flashcards, artifact loading.
import * as S from './state.js';
import { escapeHtml } from './utils.js';
import * as srs from './srs.js';
import { openDrawer } from './ui.js';
import { fmtMs, showElapsed } from './timing.js';

// Persisted study total wall-clock (ms) from GET /synth/{slug}/study/chapters
// (`study_total_wall_ms`). Shown in the sidebar header + navbar total.
let _studyTotalWallMs = 0;

// ---- flashcard review session (module-local) ----
// _fcSession is the queue of due cards as {cid, idx} tuples; _fcPos points
// at the current card; _fcRevealed tracks whether the answer is showing.
// Tuples (not bare indices) so the SAME reviewer drives both per-chapter
// review and the cross-chapter "Review due" session. _fcCards maps a
// chapter id → its parsed flashcards array. _fcGlobal flags cross-chapter
// mode (changes the header copy).
let _fcSession = [];
let _fcPos = 0;
let _fcRevealed = false;
let _fcCards = new Map();
let _fcGlobal = false;

export function _setStudySideOpen(open) {
  if (S.studySideEl) S.studySideEl.classList.toggle('open', open);
  if (S.studySideBackdrop) S.studySideBackdrop.classList.toggle('open', open);
  if (S.studyTocToggle) S.studyTocToggle.setAttribute('aria-expanded', String(!!open));
}
export function openStudySide()  { _setStudySideOpen(true); }
export function closeStudySide() { _setStudySideOpen(false); }
export function toggleStudySide() {
  _setStudySideOpen(!(S.studySideEl && S.studySideEl.classList.contains('open')));
}
if (S.studyTocToggle) S.studyTocToggle.addEventListener('click', toggleStudySide);
if (S.studySideClose) S.studySideClose.addEventListener('click', closeStudySide);
if (S.studySideBackdrop) S.studySideBackdrop.addEventListener('click', closeStudySide);

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
  if (e.key === 'Escape' && S.studySideEl &&
      S.studySideEl.classList.contains('open')) {
    closeStudySide();
  }
});

// Per-framework state — lives in state.js, accessed via S.xxx / S.setXxx()

export function _setStudyStagePill(status, label) {
  if (!S.studyPill || !S.studyPillText) return;
  const map = {
    idle:    'Idle',
    working: 'Loading',
    done:    'Ready',
    failed:  'Failed',
    cancelled: 'Cancelled',
  };
  S.studyPill.dataset.status = status;
  S.studyPillText.textContent = label || map[status] || status;
}

export function setStudyFramework(slug) {
  if (!S.studyFwName || !S.studyFwLogos) return;
  if (!slug) {
    S.studyFwName.textContent = 'Pick a framework with synthesized chapters.';
    S.studyFwName.classList.add('fw-planner-fw-name-empty');
    S.studyFwLogos.innerHTML = '';
    S.studyFwLogos.style.display = 'none';
    return;
  }
  const info = S.frameworkInfo[slug] || {name: slug, logos: []};
  S.studyFwName.textContent = info.name || slug;
  S.studyFwName.classList.remove('fw-planner-fw-name-empty');
  if (info.logos && info.logos.length) {
    S.studyFwLogos.innerHTML = info.logos.map(u =>
      '<img class="fw-planner-fw-logo" src="' + u + '" alt="">'
    ).join('');
    S.studyFwLogos.style.display = '';
  } else {
    S.studyFwLogos.innerHTML = '';
    S.studyFwLogos.style.display = 'none';
  }
}

export function _renderStudySidebar() {
  if (!S.studyChapterListEl) return;
  if (!S.studyChapters.length) {
    S.studyChapterListEl.innerHTML =
      '<div class="fw-empty" style="font-size:0.8rem;padding:8px 4px">' +
      'No chapters in this framework\'s plan. Run Planner first.' +
      '</div>';
    return;
  }
  const slug = S.activeSlug;
  // Header progress counts SYNTHESIZED chapters (those with synth output
  // on the server), NOT chapters the user has personally opened. `totalDue`
  // is gated on `rendered` too: a wiped chapter's lingering local SRS deck
  // (keyed slug::cid, never reconciled with the server, survives wipes +
  // hard refresh) must not resurface as phantom due-card counts.
  const synthesized = S.studyChapters.filter(ch => ch.rendered).length;
  // Nothing synthesized yet — fresh framework OR just after Wipe Synth.
  // Don't list the planner's chapter names as if a study exists (that's
  // what made a wiped framework still "show the old synth"); show a clean
  // empty note instead. The list returns as soon as ≥1 chapter renders.
  if (synthesized === 0) {
    S.studyChapterListEl.innerHTML =
      '<div class="fw-empty" style="font-size:0.8rem;padding:8px 4px">' +
      'No chapters synthesized yet. Run Synth to generate this study.' +
      '</div>';
    return;
  }
  let totalDue = 0;
  S.studyChapters.forEach(ch => {
    if (ch.rendered) totalDue += srs.deckDueCount(slug, ch.id);
  });
  const totalTimeHtml = _studyTotalWallMs > 0
    ? '<span class="fw-study-total-time" title="Total Synth wall-clock ' +
      '(cumulative chapter time + book harmonize)">⏱ ' +
      fmtMs(_studyTotalWallMs) + '</span>'
    : '';
  const progressHtml =
    '<div class="fw-study-progress">' +
      '<span>' + synthesized + ' / ' + S.studyChapters.length + ' synthesized</span>' +
      totalTimeHtml +
      (totalDue ? '<button type="button" class="fw-study-review-due" ' +
        'title="Review all due flashcards across chapters">▶ Review ' +
        totalDue + ' due</button>' : '') +
    '</div>';
  const rows = S.studyChapters.map(ch => {
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
      ch.id === S.studyActiveChapter ? 'active' : '',
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
  S.studyChapterListEl.innerHTML = progressHtml + rows;
}

export function _renderStudyChapterHead(ch) {
  if (!S.studyChapterHeadEl) return;
  if (!ch) {
    S.studyChapterHeadEl.classList.remove('visible');
    S.studyChapterHeadEl.innerHTML = '';
    return;
  }
  const auditBadge = ch.rendered
    ? (ch.audit_passed
        ? '<span class="badge pass">Audit ✓</span>'
        : '<span class="badge fail">Audit ✗</span>')
    : '<span class="badge">Not rendered</span>';
  S.studyChapterHeadEl.innerHTML =
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
  S.studyChapterHeadEl.classList.add('visible');
}

export function _switchStudyTab(tab) {
  S.setStudyActiveTab(tab);
  S.studyTabBtns.forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tab);
  });
  document.querySelectorAll('.fw-study-pane').forEach(pane => {
    pane.classList.toggle('active', pane.dataset.tab === tab);
  });
}

export async function _loadStudyArtifact(slug, cid, name) {
  const url = S.API + '/synth/' + slug + '/study/' + cid + '/artifact/' + name;
  const r = await fetch(url);
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return await r.text();
}

let _scrollSpyObserver = null;

function _slugifyHeading(text, i) {
  const base = (text || '').toLowerCase()
    .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40);
  return 'sec-' + i + (base ? '-' + base : '');
}

// Build the right-rail table of contents from the rendered headings and
// wire IntersectionObserver scroll-spy to highlight the section in view.
// Hidden when a chapter has fewer than 2 headings (nothing to navigate).
function _buildReadmeToc() {
  const toc = document.querySelector('#fw-study-toc');
  if (!toc || !S.studyReadmeEl) return;
  const heads = Array.from(S.studyReadmeEl.querySelectorAll('h2, h3'));
  if (_scrollSpyObserver) { _scrollSpyObserver.disconnect(); _scrollSpyObserver = null; }
  if (heads.length < 2) { toc.innerHTML = ''; toc.style.display = 'none'; return; }
  toc.style.display = '';
  heads.forEach((h, i) => { if (!h.id) h.id = _slugifyHeading(h.textContent, i); });
  // Welded recall block lives in the same scroll — give the TOC a jump
  // link to it when it has questions (it uses an <h1>, so it isn't in the
  // h2/h3 scan above).
  const recallEl = document.querySelector('#fw-study-challenges');
  const hasRecall = recallEl && recallEl.querySelector('.fw-study-challenge');
  const recallLink = hasRecall
    ? '<a href="#fw-study-challenges" class="fw-study-toc-link recall" ' +
      'data-target="fw-study-challenges">↻ Recall questions</a>'
    : '';
  toc.innerHTML =
    '<div class="fw-study-toc-title">On this page</div>' +
    heads.map(h =>
      '<a href="#' + h.id + '" class="fw-study-toc-link ' +
      h.tagName.toLowerCase() + '" data-target="' + h.id + '">' +
      escapeHtml(h.textContent || '') + '</a>'
    ).join('') + recallLink;
  // App-shell: the scroll container is `.page` (the 1fr grid row), so the
  // observer root must be `.page` (else scroll-spy never fires). rootMargin
  // shrinks the active band to the top slice of that scroll region.
  const scrollRoot = S.studyReadmeEl.closest('.page') || null;
  _scrollSpyObserver = new IntersectionObserver((entries) => {
    entries.forEach(en => {
      if (!en.isIntersecting) return;
      toc.querySelectorAll('.fw-study-toc-link.active')
        .forEach(a => a.classList.remove('active'));
      const lk = toc.querySelector(
        '.fw-study-toc-link[data-target="' + en.target.id + '"]');
      if (lk) lk.classList.add('active');
    });
  }, { root: scrollRoot, rootMargin: '0px 0px -75% 0px', threshold: 0 });
  heads.forEach(h => _scrollSpyObserver.observe(h));
}

// Rich content rendering (code / terminal / mermaid / math / callouts) lives
// in ./content_renderer.js — shared with the per-page drawer (ui.js) so the
// Study chapter view and the Ingestion drawer go through the same pipeline.
import {
  renderMarkdownInto as _renderMarkdownInto,
} from './content_renderer.js';

// ---- "Sources for this section" → open the ingested page in the drawer ----
// The right-side file drawer (ui.js) is index-based over the ingestion
// manifest (S.currentManifestEntries). We lazily fetch that manifest — the
// SAME data the Ingestion page uses — and index it by page-file basename so
// a citation basename (synth's `source_basename` = basename of the page's
// MinIO key) resolves to a drawer entry. Cached per active slug.
let _srcIndex = null;
let _srcIndexSlug = null;
function _entryBasename(e) {
  if (e.key) return e.key.replace(/\/+$/, '').split('/').pop();
  // Manifests written before `key` was stored: reconstruct page_key's
  // basename `<idx:04d>-<slug>.md` (see storage/constants.py:page_key).
  return String(e.idx).padStart(4, '0') + '-' + (e.slug || 'page') + '.md';
}
async function _ensureSourceIndex() {
  if (_srcIndex && _srcIndexSlug === S.activeSlug) return _srcIndex;
  const map = new Map();
  try {
    const r = await fetch(S.API + '/ingestion/' + S.activeSlug + '/manifest');
    if (r.ok) {
      const entries = (await r.json()).entries || [];
      S.setCurrentManifestEntries(entries);   // drawer prev/next walk these
      entries.forEach((e, i) => {
        const bn = _entryBasename(e).toLowerCase();
        if (bn) map.set(bn, i);
      });
    }
  } catch (_) { /* leave the map empty → clicks no-op */ }
  _srcIndex = map;
  _srcIndexSlug = S.activeSlug;
  return _srcIndex;
}
async function _openSourceFile(basename) {
  if (!basename || !S.activeSlug) return;
  const map = await _ensureSourceIndex();
  const k = basename.trim().toLowerCase();
  let idx = map.get(k);
  if (idx == null && k.endsWith('.md')) idx = map.get(k.slice(0, -3));
  if (idx == null && !k.endsWith('.md')) idx = map.get(k + '.md');
  if (idx == null) return;   // unknown source — silently ignore
  openDrawer(idx);
}

// Post-process the rendered README DOM:
//   1) Drop the inline "## Contents" list + its trailing `---` divider —
//      the sticky right-rail TOC (scroll-spy) makes it redundant.
//   2) Fold every "Sources for this section:" citation block into a
//      collapsed <details> so the prose stays scannable; sources on demand.
//      Each source's .md basename becomes a clickable link that opens the
//      ingested page in the same drawer the Ingestion page uses.
function _postProcessReadme() {
  const root = S.studyReadmeEl;
  if (!root) return;
  root.querySelectorAll('h2').forEach((h) => {
    if ((h.textContent || '').trim().toLowerCase() !== 'contents') return;
    let n = h.nextElementSibling;
    h.remove();
    while (n && (n.tagName === 'UL' || n.tagName === 'OL')) {
      const next = n.nextElementSibling;
      n.remove();
      n = next;
    }
    if (n && n.tagName === 'HR') n.remove();   // the `---` under Contents
  });
  Array.from(root.querySelectorAll('p')).forEach((p) => {
    const label = (p.textContent || '').trim().toLowerCase().replace(/:$/, '');
    if (label !== 'sources for this section') return;
    const list = p.nextElementSibling;
    const n = (list && (list.tagName === 'UL' || list.tagName === 'OL'))
      ? list.children.length : 0;
    const det = document.createElement('details');
    det.className = 'fw-study-sources';
    const sum = document.createElement('summary');
    sum.textContent = 'Sources for this section' + (n ? ' (' + n + ')' : '');
    det.appendChild(sum);
    p.replaceWith(det);
    if (n) {
      det.appendChild(list);
      // First <code> in each line is the source file basename — make it
      // an openable link into the drawer.
      det.querySelectorAll('li').forEach((li) => {
        const code = li.querySelector('code');
        if (!code) return;
        code.classList.add('fw-source-file');
        code.dataset.basename = (code.textContent || '').trim();
        code.setAttribute('role', 'button');
        code.setAttribute('tabindex', '0');
        code.title = 'Open this source file';
      });
    }
  });
}

export async function _loadStudyReadme(slug, cid) {
  if (!S.studyReadmeEl) return;
  S.studyReadmeEl.innerHTML =
    '<div class="fw-empty">Loading chapter…</div>';
  try {
    const raw = await _loadStudyArtifact(slug, cid, 'README.md');
    await _renderMarkdownInto(S.studyReadmeEl, raw, {
      // Post-process hook: fold "Sources for this section" lists into
      // collapsed <details> and drop the inline ## Contents block (the
      // sticky right-rail TOC makes it redundant). Runs BEFORE block
      // renderers so the TOC scroll-spy sees the final heading set.
      postProcess: () => { _postProcessReadme(); _buildReadmeToc(); },
    });
  } catch (e) {
    S.studyReadmeEl.innerHTML =
      '<div class="fw-empty">Failed to load README.md: ' +
      escapeHtml(String(e)) + '</div>';
    const toc = document.querySelector('#fw-study-toc');
    if (toc) { toc.innerHTML = ''; toc.style.display = 'none'; }
  }
}

export async function _loadStudyChallenges(slug, cid) {
  if (!S.studyChallengesEl) return;
  S.studyChallengesEl.innerHTML =
    '<div class="fw-empty">Loading challenges…</div>';
  try {
    const raw = await _loadStudyArtifact(slug, cid, 'challenges.md');
    // Parse the numbered list manually so we can render each item
    // as a collapsible <details> for active-recall UX.
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
    S.studyChallengesEl.innerHTML = headerHtml + itemsHtml.join('');
  } catch (e) {
    S.studyChallengesEl.innerHTML =
      '<div class="fw-empty">Failed to load challenges.md: ' +
      escapeHtml(String(e)) + '</div>';
  }
}

// Self-grade clicks (delegated). Persists per (chapter, question) so the
// got/need-review state survives reloads and tints the row.
if (S.studyChallengesEl) {
  S.studyChallengesEl.addEventListener('click', (ev) => {
    const btn = ev.target.closest('.fw-cg-btn');
    if (!btn) return;
    const row = btn.closest('.fw-study-challenge');
    if (!row || !S.activeSlug || !S.studyLoadedCid) return;
    const idx = parseInt(row.dataset.idx, 10);
    const grade = btn.dataset.grade;
    // Toggle off if re-clicking the active grade.
    const next = row.dataset.grade === grade ? '' : grade;
    row.dataset.grade = next;
    srs.setChallengeGrade(S.activeSlug, S.studyLoadedCid, idx, next);
  });
}

// Source-file links inside "Sources for this section" boxes — open the raw
// ingested page in the same right-side drawer the Ingestion page uses.
if (S.studyReadmeEl) {
  const onSourceActivate = (ev) => {
    const code = ev.target.closest('.fw-source-file');
    if (!code) return;
    if (ev.type === 'keydown' && ev.key !== 'Enter' && ev.key !== ' ') return;
    ev.preventDefault();
    _openSourceFile(code.dataset.basename);
  };
  S.studyReadmeEl.addEventListener('click', onSourceActivate);
  S.studyReadmeEl.addEventListener('keydown', onSourceActivate);
}

// Build the due-card queue for the current chapter. `reviewAll` ignores
// the FSRS schedule and queues every card (used by the "review all" CTA
// when nothing is due yet).
function _buildFlashcardSession(reviewAll) {
  const cid = S.studyLoadedCid;
  const deck = srs.loadDeck(S.activeSlug, cid);
  _fcGlobal = false;
  _fcCards = new Map([[cid, S.studyCards]]);
  _fcSession = [];
  for (let i = 0; i < S.studyCards.length; i++) {
    if (reviewAll || srs.isDue(deck[i])) _fcSession.push({ cid, idx: i });
  }
  _fcPos = 0;
  _fcRevealed = false;
}

// Cross-chapter "Review due" — walk every rendered chapter's DUE cards in
// one session. Fetches flashcards.json only for chapters that actually
// have due cards (deckDueCount > 0), so it's cheap when little is due.
export async function startGlobalReview() {
  const slug = S.activeSlug;
  if (!slug) return;
  _fcCards = new Map();
  const queue = [];
  for (const ch of S.studyChapters) {
    if (!ch.rendered || !srs.deckDueCount(slug, ch.id)) continue;
    try {
      const raw = await _loadStudyArtifact(slug, ch.id, 'flashcards.json');
      const cards = JSON.parse(raw) || [];
      _fcCards.set(ch.id, cards);
      const deck = srs.loadDeck(slug, ch.id);
      for (let i = 0; i < cards.length; i++) {
        if (srs.isDue(deck[i])) queue.push({ cid: ch.id, idx: i });
      }
    } catch (_) { /* skip a chapter that fails to load */ }
  }
  _fcGlobal = true;
  _fcSession = queue;
  _fcPos = 0;
  _fcRevealed = false;
  _switchStudyTab('flashcards');
  _renderFlashcard();
}

function _chapterTitle(cid) {
  const ch = S.studyChapters.find(c => c.id === cid);
  return (ch && ch.title) || cid;
}

const _GRADES = [
  { r: srs.Rating.Again, label: 'Again', key: '1', cls: 'again' },
  { r: srs.Rating.Hard,  label: 'Hard',  key: '2', cls: 'hard' },
  { r: srs.Rating.Good,  label: 'Good',  key: '3', cls: 'good' },
  { r: srs.Rating.Easy,  label: 'Easy',  key: '4', cls: 'easy' },
];

export function _renderFlashcard() {
  if (!S.studyFlashcardsEl) return;
  const total = S.studyCards.length;
  // Per-chapter mode with no cards → nothing to show. (Global mode draws
  // from _fcCards across chapters, so the current chapter's count is
  // irrelevant — fall through to the queue-driven render.)
  if (!_fcGlobal && !total) {
    S.studyFlashcardsEl.innerHTML =
      '<div class="fw-empty">No flashcards for this chapter.</div>';
    return;
  }
  // Caught-up: nothing due. Per-chapter mode offers to review the whole
  // deck anyway; global mode just confirms the queue is clear.
  if (!_fcSession.length) {
    S.studyFlashcardsEl.innerHTML =
      '<div class="fw-fc-done">' +
        '<div class="fw-fc-done-check">✓</div>' +
        '<div>' + (_fcGlobal
          ? 'All caught up — no cards due across chapters right now.'
          : 'All caught up — no cards due in this chapter right now.') +
        '</div>' +
        (_fcGlobal ? '' :
          '<button type="button" id="fw-fc-reviewall" class="btn-outline">' +
            'Review all ' + total + ' anyway</button>') +
      '</div>';
    const all = document.querySelector('#fw-fc-reviewall');
    if (all) all.addEventListener('click', () => {
      _buildFlashcardSession(true); _renderFlashcard();
    });
    return;
  }
  const entry = _fcSession[_fcPos];
  const cards = _fcCards.get(entry.cid) || [];
  const card = cards[entry.idx] || { q: '', a: '' };
  const deck = srs.loadDeck(S.activeSlug, entry.cid);
  const cardState = deck[entry.idx] || srs.newCard();

  const gradesHtml = _GRADES.map(g =>
    '<button type="button" class="fw-fc-grade ' + g.cls + '" data-rating="' +
    g.r + '">' +
      '<span class="fw-fc-grade-label">' + g.label + '</span>' +
      '<span class="fw-fc-grade-ivl">' +
        escapeHtml(srs.intervalPreview(cardState, g.r)) + '</span>' +
    '</button>'
  ).join('');

  // Right side of the head: per-chapter shows the deck size; global mode
  // shows which chapter the current card belongs to.
  const headRight = _fcGlobal
    ? '<span class="fw-fc-ctx">' + escapeHtml(_chapterTitle(entry.cid)) + '</span>'
    : '<span class="fw-fc-total">' + total + ' cards</span>';

  S.studyFlashcardsEl.innerHTML =
    '<div class="fw-fc-head">' +
      '<span class="fw-fc-due">' + _fcSession.length +
        (_fcGlobal ? ' due · all chapters' : ' due') + '</span>' +
      headRight +
    '</div>' +
    '<div class="fw-fc-card">' +
      '<div class="fw-fc-face">' +
        '<span class="fw-fc-facelabel">Question</span>' +
        '<div class="fw-fc-body">' + _mdInline(card.q) + '</div>' +
      '</div>' +
      (_fcRevealed
        ? '<div class="fw-fc-face fw-fc-answer">' +
            '<span class="fw-fc-facelabel">Answer</span>' +
            '<div class="fw-fc-body">' + _mdInline(card.a) + '</div>' +
          '</div>'
        : '') +
    '</div>' +
    (_fcRevealed
      ? '<div class="fw-fc-grades">' + gradesHtml + '</div>' +
        '<div class="fw-fc-hint">Rate your recall · keys 1–4</div>'
      : '<div class="fw-fc-reveal-wrap">' +
          '<button type="button" id="fw-fc-show" class="btn-primary">' +
            'Show answer</button>' +
          '<div class="fw-fc-hint">Try to recall, then reveal · Space</div>' +
        '</div>');

  const show = document.querySelector('#fw-fc-show');
  if (show) show.addEventListener('click', () => {
    _fcRevealed = true; _renderFlashcard();
  });
  S.studyFlashcardsEl.querySelectorAll('.fw-fc-grade').forEach(b => {
    b.addEventListener('click', () =>
      _gradeFlashcard(parseInt(b.dataset.rating, 10)));
  });
}

function _gradeFlashcard(rating) {
  if (!_fcSession.length) return;
  const entry = _fcSession[_fcPos];
  const deck = srs.loadDeck(S.activeSlug, entry.cid);
  const updated = srs.schedule(deck[entry.idx] || srs.newCard(), rating);
  srs.saveCard(S.activeSlug, entry.cid, entry.idx, updated);
  // Drop the card from the queue; an "Again" re-queues at the end so it
  // gets re-tested later this session.
  _fcSession.splice(_fcPos, 1);
  if (rating === srs.Rating.Again) _fcSession.push(entry);
  if (_fcPos >= _fcSession.length) _fcPos = 0;
  _fcRevealed = false;
  _renderFlashcard();
  _renderStudySidebar();   // refresh per-chapter due badges + total
}

// Tiny inline-markdown helper for flashcard faces — just handles
// `code` spans + **bold** + line breaks. marked.parse() would wrap
// everything in <p> which fights the flex-center layout.
export function _mdInline(text) {
  let s = escapeHtml(text || '');
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/\n/g, '<br>');
  return s;
}

export async function _loadStudyFlashcards(slug, cid) {
  if (!S.studyFlashcardsEl) return;
  S.studyFlashcardsEl.innerHTML =
    '<div class="fw-empty">Loading flashcards…</div>';
  try {
    const raw = await _loadStudyArtifact(slug, cid, 'flashcards.json');
    S.setStudyCards(JSON.parse(raw) || []);
    _buildFlashcardSession(false);   // FSRS due-card queue for this chapter
    _renderFlashcard();
  } catch (e) {
    S.studyFlashcardsEl.innerHTML =
      '<div class="fw-empty">Failed to load flashcards.json: ' +
      escapeHtml(String(e)) + '</div>';
  }
}

// Keyboard shortcuts for the flashcard reviewer — Space reveals the
// answer; 1–4 grade it. Active only while the Flashcards tab is showing
// and the user isn't typing in a field.
document.addEventListener('keydown', (e) => {
  if (S.studyActiveTab !== 'flashcards') return;
  if (!S.studyFlashcardsEl || !S.studyFlashcardsEl.closest('.fw-study-pane.active')) return;
  const tag = (document.activeElement?.tagName || '').toLowerCase();
  if (tag === 'input' || tag === 'textarea') return;
  if (!_fcSession.length) return;
  if (e.key === ' ' || e.key === 'Spacebar') {
    e.preventDefault();
    if (!_fcRevealed) { _fcRevealed = true; _renderFlashcard(); }
  } else if (_fcRevealed && ['1', '2', '3', '4'].includes(e.key)) {
    e.preventDefault();
    _gradeFlashcard(parseInt(e.key, 10));
  }
});

// Always show a freshly-opened chapter from the top — reset the `.page`
// scroll region (the app-shell scroll container) no matter where the user
// had scrolled. `instant` so there's no distracting glide on chapter switch.
function _scrollReaderTop() {
  const page = document.querySelector('.page');
  if (page) page.scrollTo({ top: 0, behavior: 'instant' });
}

export async function openStudyChapter(cid) {
  if (!S.activeSlug || !cid) return;
  const ch = S.studyChapters.find(c => c.id === cid);
  if (!ch) return;
  if (!ch.rendered) {
    _renderStudyChapterHead(ch);
    S.studyReadmeEl.innerHTML =
      '<div class="fw-empty">This chapter has not been synthesized yet. ' +
      'Run Synth on this chapter first.</div>';
    S.studyChallengesEl.innerHTML =
      '<div class="fw-empty">No challenges available — chapter not synthesized.</div>';
    S.studyFlashcardsEl.innerHTML =
      '<div class="fw-empty">No flashcards available — chapter not synthesized.</div>';
    _scrollReaderTop();
    return;
  }
  S.setStudyActiveChapter(cid);
  S.setStudyLoadedCid(cid);
  _renderStudySidebar();   // re-render to update active highlight
  _renderStudyChapterHead(ch);
  _setStudyStagePill('working', 'Loading…');
  // Fire all three loads in parallel
  await Promise.all([
    _loadStudyReadme(S.activeSlug, cid),
    _loadStudyChallenges(S.activeSlug, cid),
    _loadStudyFlashcards(S.activeSlug, cid),
  ]);
  // Both prose + recall are now in the DOM — rebuild the TOC so it
  // includes the "↻ Recall questions" jump link.
  _buildReadmeToc();
  // Opening + loading a rendered chapter counts as "studied" — mark it
  // and re-render the sidebar so the ✓ + progress update immediately.
  srs.markChapterStudied(S.activeSlug, cid, true);
  _renderStudySidebar();
  _setStudyStagePill('done', 'Reading · ' + (ch.title || cid));
  _scrollReaderTop();   // new chapter always starts at the top
}

export async function loadStudyChapters(slug) {
  if (!S.studyChapterListEl) return;
  S.setStudyChapters([]);
  S.setStudyActiveChapter(null);
  S.setStudyLoadedCid(null);
  _setStudyStagePill('working', 'Loading chapters…');
  S.studyChapterListEl.innerHTML =
    '<div class="fw-empty" style="font-size:0.8rem;padding:8px 4px">' +
    'Loading chapters…</div>';
  try {
    const r = await fetch(S.API + '/synth/' + slug + '/study/chapters');
    if (!r.ok) {
      S.studyChapterListEl.innerHTML =
        '<div class="fw-empty" style="font-size:0.8rem;padding:8px 4px">' +
        'Failed to load chapters (HTTP ' + r.status + ').</div>';
      _setStudyStagePill('failed', 'Failed');
      return;
    }
    const data = await r.json();
    S.setStudyChapters((data.chapters || []).sort(
      (a, b) => (a.order || 0) - (b.order || 0)
    ));
    _studyTotalWallMs = Number(data.study_total_wall_ms || 0);
    // Mirror the persisted Synth total onto the navbar row-3 indicator.
    showElapsed('synth', _studyTotalWallMs);
    S.setStudyLoadedSlug(slug);
    _renderStudySidebar();
    // Auto-open the first rendered chapter (if any) so the user
    // immediately sees content instead of an empty pane.
    const firstReady = S.studyChapters.find(c => c.rendered);
    if (firstReady) {
      await openStudyChapter(firstReady.id);
    } else {
      _setStudyStagePill('idle',
        'No rendered chapters yet — run Synth first.');
      S.studyReadmeEl.innerHTML =
        '<div class="fw-empty">No chapters have been synthesized for ' +
        'this framework yet. Run Synth to generate content.</div>';
    }
  } catch (e) {
    S.studyChapterListEl.innerHTML =
      '<div class="fw-empty" style="font-size:0.8rem;padding:8px 4px">' +
      'Network error loading chapters.</div>';
    _setStudyStagePill('failed', 'Failed');
  }
}

// Tab buttons: simple click delegation
S.studyTabBtns.forEach(btn => {
  btn.addEventListener('click', () => {
    _switchStudyTab(btn.dataset.tab || 'learn');
  });
});

// Chapter S.sidebar: event delegation for chapter clicks. Picking a
// chapter closes the side window so the materials get the full width.
if (S.studyChapterListEl) {
  S.studyChapterListEl.addEventListener('click', ev => {
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
// can re-trigger after S.activeSlug changes.
// (The JS viewport-fit hack was removed 2026-05-28 — the app-shell grid
// in base.css makes `.page` the scroll region, so the reader fits the
// viewport via CSS with no measuring.)
export function refreshStudyVisibility() {
  if (!S.studyEmptyEl || !S.studyGridEl) return;
  if (!S.activeSlug) {
    S.studyEmptyEl.style.display = '';
    S.studyGridEl.style.display = 'none';
    return;
  }
  S.studyEmptyEl.style.display = 'none';
  S.studyGridEl.style.display = '';
}

// Study-page load is driven by main.js initStudy (per-stage route) —
// the wizard-era stepFn.showStep(5) hook was removed 2026-05-28.

// ============================================================
// Cmd-K cross-chapter search (2026-05-28 Wave 2).
// Local-first + vanilla: lazily fetch every rendered chapter's README
// on first open, build a tiny in-memory index (title + headings + body),
// and match queries against it. No framework, no CDN, no backend index.
// A result deep-links to its chapter and scrolls to the matched heading.
// ============================================================
let _searchIndex = null;
let _searchBuilt = false;
let _searchSel = 0;
let _searchHits = [];

async function _buildSearchIndex() {
  if (_searchBuilt) return;
  _searchBuilt = true;
  _searchIndex = [];
  const slug = S.activeSlug;
  for (const ch of S.studyChapters) {
    if (!ch.rendered) continue;
    try {
      const raw = await _loadStudyArtifact(slug, ch.id, 'README.md');
      const headings = [];
      raw.split('\n').forEach(line => {
        const m = line.match(/^(#{2,3})\s+(.+)$/);
        if (m) {
          const text = m[2].trim();
          // Match the id _buildReadmeToc assigns (running h2/h3 index).
          headings.push({ text, id: _slugifyHeading(text, headings.length) });
        }
      });
      _searchIndex.push({
        cid: ch.id, title: ch.title || ch.id, headings,
        text: raw.toLowerCase(),
      });
    } catch (_) { /* skip unreadable chapter */ }
  }
}

function _runSearch(q) {
  q = (q || '').trim().toLowerCase();
  if (!q || !_searchIndex) return [];
  const terms = q.split(/\s+/).filter(Boolean);
  const out = [];
  for (const ch of _searchIndex) {
    const titleHit = terms.every(t => ch.title.toLowerCase().includes(t));
    const headingHits = ch.headings.filter(h =>
      terms.some(t => h.text.toLowerCase().includes(t)));
    const bodyHit = terms.every(t => ch.text.includes(t));
    if (titleHit) out.push({ cid: ch.cid, title: ch.title, sub: ch.title, score: 3 });
    for (const h of headingHits) {
      out.push({ cid: ch.cid, title: ch.title, sub: h.text, hid: h.id, score: 2 });
    }
    if (!titleHit && !headingHits.length && bodyHit) {
      out.push({ cid: ch.cid, title: ch.title, sub: '… match in text', score: 1 });
    }
  }
  out.sort((a, b) => b.score - a.score);
  return out.slice(0, 30);
}

let _searchOverlay = null;
function _ensureSearchOverlay() {
  if (_searchOverlay) return _searchOverlay;
  const ov = document.createElement('div');
  ov.id = 'fw-study-search';
  ov.className = 'fw-study-search-overlay';
  ov.innerHTML =
    '<div class="fw-study-search-box">' +
      '<input class="fw-study-search-input" type="search" ' +
        'placeholder="Search all chapters…  (Esc to close)" ' +
        'autocomplete="off" spellcheck="false">' +
      '<div class="fw-study-search-results"></div>' +
    '</div>';
  document.body.appendChild(ov);
  const input = ov.querySelector('.fw-study-search-input');
  const results = ov.querySelector('.fw-study-search-results');

  const render = () => {
    if (!_searchHits.length) {
      results.innerHTML = input.value.trim()
        ? '<div class="fw-study-search-empty">No matches.</div>'
        : '<div class="fw-study-search-empty">Type to search every chapter.</div>';
      return;
    }
    results.innerHTML = _searchHits.map((h, i) =>
      '<div class="fw-study-search-row' + (i === _searchSel ? ' sel' : '') +
      '" data-i="' + i + '">' +
        '<span class="fw-study-search-ch">' + escapeHtml(h.title) + '</span>' +
        '<span class="fw-study-search-sub">' + escapeHtml(h.sub) + '</span>' +
      '</div>'
    ).join('');
  };
  const choose = async (i) => {
    const hit = _searchHits[i];
    if (!hit) return;
    closeSearch();
    _switchStudyTab('learn');   // results live in the reading pane
    await openStudyChapter(hit.cid);
    if (hit.hid) {
      const el = document.getElementById(hit.hid);
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  };
  input.addEventListener('input', () => {
    _searchHits = _runSearch(input.value);
    _searchSel = 0;
    render();
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); _searchSel = Math.min(_searchSel + 1, _searchHits.length - 1); render(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); _searchSel = Math.max(_searchSel - 1, 0); render(); }
    else if (e.key === 'Enter') { e.preventDefault(); choose(_searchSel); }
    else if (e.key === 'Escape') { e.preventDefault(); closeSearch(); }
  });
  results.addEventListener('click', (e) => {
    const row = e.target.closest('.fw-study-search-row');
    if (row) choose(parseInt(row.dataset.i, 10));
  });
  ov.addEventListener('click', (e) => { if (e.target === ov) closeSearch(); });
  _searchOverlay = ov;
  return ov;
}

async function openSearch() {
  if (!S.activeSlug || !S.studyChapters.length) return;
  const ov = _ensureSearchOverlay();
  ov.classList.add('open');
  const input = ov.querySelector('.fw-study-search-input');
  input.value = '';
  _searchHits = []; _searchSel = 0;
  ov.querySelector('.fw-study-search-results').innerHTML =
    '<div class="fw-study-search-empty">Indexing chapters…</div>';
  input.focus();
  await _buildSearchIndex();
  ov.querySelector('.fw-study-search-results').innerHTML =
    '<div class="fw-study-search-empty">Type to search every chapter.</div>';
}
function closeSearch() {
  if (_searchOverlay) _searchOverlay.classList.remove('open');
}

// ⌘K / Ctrl-K opens search (study page only); the 🔍 button does too.
document.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
    if (!S.studyGridEl) return;   // not on the study page
    e.preventDefault();
    openSearch();
  }
});
(() => {
  const btn = document.querySelector('#fw-study-search-btn');
  if (btn) btn.addEventListener('click', openSearch);
})();
