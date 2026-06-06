// study/flashcards.js — FSRS flashcard session + per-card rendering +
// grading + cross-chapter "Review due" sweep. Extracted from study.js
// Step 5 (2026-06-05 follow-up) using the DI registration pattern (see
// flashcards_deps.js for the contract).
//
// Why DI: 10 cross-refs to functions defined later in study.js — direct
// import would cycle. flashcards.js reads from `deps`; study.js mutates
// it via registerFlashcardsDeps(...) once the dependent functions land.
import * as Si from '@dd/shared/state/ingestion.js';
import * as Ss from '@dd/shared/state/study.js';
import { escapeHtml } from '../shared/utils.js';
import * as srs from '../shared/srs.js';
import { _loadStudyArtifact } from './shared.js';
import { deps } from './flashcards_deps.js';

// Flashcard session state (module-local). _fcSession is the queue of due
// cards as {cid, idx} tuples; _fcPos points at the current card;
// _fcRevealed tracks whether the answer is showing. Tuples (not bare
// indices) so the SAME reviewer drives both per-chapter review and the
// cross-chapter "Review due" session. _fcCards maps a chapter id → its
// parsed flashcards array. _fcGlobal flags cross-chapter mode (changes
// the header copy). Lives here after Step 1 follow-up (2026-06-05) —
// these previously sat in study.js without an import, breaking this
// module at runtime.
let _fcSession = [];
let _fcPos = 0;
let _fcRevealed = false;
let _fcCards = new Map();
let _fcGlobal = false;

function _buildFlashcardSession(reviewAll) {
  const cid = Ss.studyLoadedCid;
  const deck = srs.loadDeck(Si.activeSlug, cid);
  _fcGlobal = false;
  _fcCards = new Map([[cid, Ss.studyCards]]);
  _fcSession = [];
  for (let i = 0; i < Ss.studyCards.length; i++) {
    if (reviewAll || srs.isDue(deck[i])) _fcSession.push({ cid, idx: i });
  }
  _fcPos = 0;
  _fcRevealed = false;
}

// Cross-chapter "Review due" — walk every rendered chapter's DUE cards in
// one session. Fetches flashcards.json only for chapters that actually
// have due cards (deckDueCount > 0), so it's cheap when little is due.
export async function startGlobalReview() {
  const slug = Si.activeSlug;
  if (!slug) return;
  _fcCards = new Map();
  const queue = [];
  for (const ch of Ss.studyChapters) {
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
  deps._switchStudyTab?.('flashcards');
  _renderFlashcard();
}

function _chapterTitle(cid) {
  const ch = Ss.studyChapters.find(c => c.id === cid);
  return (ch && ch.title) || cid;
}

const _GRADES = [
  { r: srs.Rating.Again, label: 'Again', key: '1', cls: 'again' },
  { r: srs.Rating.Hard,  label: 'Hard',  key: '2', cls: 'hard' },
  { r: srs.Rating.Good,  label: 'Good',  key: '3', cls: 'good' },
  { r: srs.Rating.Easy,  label: 'Easy',  key: '4', cls: 'easy' },
];

export function _renderFlashcard() {
  if (!Ss.studyFlashcardsEl) return;
  const total = Ss.studyCards.length;
  // Per-chapter mode with no cards → nothing to show. (Global mode draws
  // from _fcCards across chapters, so the current chapter's count is
  // irrelevant — fall through to the queue-driven render.)
  if (!_fcGlobal && !total) {
    Ss.studyFlashcardsEl.innerHTML =
      '<div class="fw-empty">No flashcards for this chapter.</div>';
    return;
  }
  // Caught-up: nothing due. Per-chapter mode offers to review the whole
  // deck anyway; global mode just confirms the queue is clear.
  if (!_fcSession.length) {
    Ss.studyFlashcardsEl.innerHTML =
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
  const deck = srs.loadDeck(Si.activeSlug, entry.cid);
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

  Ss.studyFlashcardsEl.innerHTML =
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
  Ss.studyFlashcardsEl.querySelectorAll('.fw-fc-grade').forEach(b => {
    b.addEventListener('click', () =>
      _gradeFlashcard(parseInt(b.dataset.rating, 10)));
  });
}

function _gradeFlashcard(rating) {
  if (!_fcSession.length) return;
  const entry = _fcSession[_fcPos];
  const deck = srs.loadDeck(Si.activeSlug, entry.cid);
  const updated = srs.schedule(deck[entry.idx] || srs.newCard(), rating);
  srs.saveCard(Si.activeSlug, entry.cid, entry.idx, updated);
  // Drop the card from the queue; an "Again" re-queues at the end so it
  // gets re-tested later this session.
  _fcSession.splice(_fcPos, 1);
  if (rating === srs.Rating.Again) _fcSession.push(entry);
  if (_fcPos >= _fcSession.length) _fcPos = 0;
  _fcRevealed = false;
  _renderFlashcard();
  deps._renderStudySidebar?.();   // refresh per-chapter due badges + total
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
  if (!Ss.studyFlashcardsEl) return;
  Ss.studyFlashcardsEl.innerHTML =
    '<div class="fw-empty">Loading flashcards…</div>';
  try {
    const raw = await _loadStudyArtifact(slug, cid, 'flashcards.json');
    Ss.setStudyCards(JSON.parse(raw) || []);
    _buildFlashcardSession(false);   // FSRS due-card queue for this chapter
    _renderFlashcard();
  } catch (e) {
    Ss.studyFlashcardsEl.innerHTML =
      '<div class="fw-empty">Failed to load flashcards.json: ' +
      escapeHtml(String(e)) + '</div>';
  }
}

// Keyboard shortcuts for the flashcard reviewer — Space reveals the
// answer; 1–4 grade it. Active only while the Flashcards tab is showing
// and the user isn't typing in a field.
document.addEventListener('keydown', (e) => {
  if (Ss.studyActiveTab !== 'flashcards') return;
  if (!Ss.studyFlashcardsEl || !Ss.studyFlashcardsEl.closest('.fw-study-pane.active')) return;
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
