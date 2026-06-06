// ============================================================
// srs.js — FSRS-4.5 spaced-repetition scheduler + local study state.
//
// FSRS-4.5 is the 2026 SOTA scheduling algorithm (open-spaced-
// repetition; the engine behind Anki/RemNote). It models each card's
// memory as (stability S, difficulty D) and schedules the next review
// to land at ~90% recall probability. Self-contained: no deps, no
// build step, works offline — fits the vanilla-JS / local-first BFF.
//
// All state lives in localStorage (single local learner, no backend):
//   dd:srs:v1            → per-(slug,chapter,cardIdx) FSRS card state
//   dd:study:progress:v1 → chapter "studied" flags + challenge self-grades
//
// Default weights are the published FSRS-4.5 defaults; they're a sane
// starting point for one learner (qualitative behavior — lapses come
// back fast, recalls space out exponentially — is robust to small
// weight differences; per-user optimization is out of scope here).
// ============================================================

const W = [
  0.4072, 1.1829, 3.1262, 15.4722, 7.2102, 0.5316, 1.0651, 0.0234,
  1.616, 0.1544, 1.0824, 1.9813, 0.0953, 0.2975, 2.2042, 0.2407, 2.9466,
];
const DECAY = -0.5;
const FACTOR = Math.pow(0.9, 1 / DECAY) - 1;   // ≈ 0.2345679
const REQUEST_RETENTION = 0.9;
const MIN_S = 0.1;
const MAX_S = 36500;
const DAY_MS = 86400000;

export const Rating = { Again: 1, Hard: 2, Good: 3, Easy: 4 };

const clamp = (x, lo, hi) => Math.min(Math.max(x, lo), hi);
const initDifficulty = (g) => clamp(W[4] - Math.exp(W[5] * (g - 1)) + 1, 1, 10);
const initStability = (g) => Math.max(W[g - 1], MIN_S);
const retrievability = (elapsedDays, S) =>
  Math.pow(1 + FACTOR * elapsedDays / S, DECAY);

function nextIntervalDays(S) {
  const days = (S / FACTOR) * (Math.pow(REQUEST_RETENTION, 1 / DECAY) - 1);
  return clamp(Math.round(days), 1, MAX_S);
}
function nextDifficulty(D, g) {
  const next = D - W[6] * (g - 3);
  // mean-reversion toward the "easy" difficulty anchor (FSRS-4.5)
  return clamp(W[7] * initDifficulty(4) + (1 - W[7]) * next, 1, 10);
}
function nextStabilityRecall(D, S, R, g) {
  const hard = g === Rating.Hard ? W[15] : 1;
  const easy = g === Rating.Easy ? W[16] : 1;
  const s = S * (1 + Math.exp(W[8]) * (11 - D) * Math.pow(S, -W[9]) *
    (Math.exp(W[10] * (1 - R)) - 1) * hard * easy);
  return clamp(s, MIN_S, MAX_S);
}
function nextStabilityLapse(D, S, R) {
  const s = W[11] * Math.pow(D, -W[12]) * (Math.pow(S + 1, W[13]) - 1) *
    Math.exp(W[14] * (1 - R));
  return clamp(Math.min(s, S), MIN_S, MAX_S);   // a lapse never raises S
}

export function newCard() {
  return {
    due: null, stability: 0, difficulty: 0,
    reps: 0, lapses: 0, state: 'new', last: null,
  };
}

// Grade a card. Returns the NEW card state. `Again` re-queues the card
// inside the current session (~1 min) instead of pushing it a full day
// out, so a missed card is re-tested while you're still here.
export function schedule(card, rating) {
  card = card || newCard();
  const now = Date.now();
  let S = card.stability;
  let D = card.difficulty;
  if (card.state === 'new' || !S) {
    S = initStability(rating);
    D = initDifficulty(rating);
  } else {
    const elapsed = Math.max(0, (now - new Date(card.last).getTime()) / DAY_MS);
    const R = retrievability(elapsed, S);
    D = nextDifficulty(D, rating);
    S = rating === Rating.Again
      ? nextStabilityLapse(D, S, R)
      : nextStabilityRecall(D, S, R, rating);
  }
  const dueMs = rating === Rating.Again
    ? now + 60 * 1000
    : now + nextIntervalDays(S) * DAY_MS;
  return {
    due: new Date(dueMs).toISOString(),
    stability: S,
    difficulty: D,
    reps: (card.reps || 0) + 1,
    lapses: (card.lapses || 0) + (rating === Rating.Again ? 1 : 0),
    state: 'review',
    last: new Date(now).toISOString(),
  };
}

// A human-readable next-interval preview per rating (for the grade
// buttons, e.g. "Good · 4d"). Doesn't mutate or persist.
export function intervalPreview(card, rating) {
  const c = schedule(card, rating);
  if (rating === Rating.Again) return '<1d';
  const days = Math.max(1, Math.round((new Date(c.due).getTime() - Date.now()) / DAY_MS));
  if (days < 30) return days + 'd';
  if (days < 365) return Math.round(days / 30) + 'mo';
  return (days / 365).toFixed(1) + 'y';
}

export function isDue(card, nowMs) {
  const now = nowMs || Date.now();
  if (!card || card.state === 'new' || !card.due) return true;   // new ⇒ due
  return new Date(card.due).getTime() <= now;
}

// ---- FSRS card persistence ----
const SRS_KEY = 'dd:srs:v1';
const _loadSrs = () => {
  try { return JSON.parse(localStorage.getItem(SRS_KEY) || '{}') || {}; }
  catch (_) { return {}; }
};
const _saveSrs = (o) => {
  try { localStorage.setItem(SRS_KEY, JSON.stringify(o)); } catch (_) {}
};
const _deckKey = (slug, cid) => slug + '::' + cid;

export function loadDeck(slug, cid) {
  return _loadSrs()[_deckKey(slug, cid)] || {};   // { [cardIdx]: card }
}
export function saveCard(slug, cid, idx, card) {
  const all = _loadSrs();
  const dk = _deckKey(slug, cid);
  (all[dk] = all[dk] || {})[idx] = card;
  _saveSrs(all);
}
export function dueCount(slug, cid, nCards) {
  const deck = loadDeck(slug, cid);
  const now = Date.now();
  let n = 0;
  for (let i = 0; i < nCards; i++) if (isDue(deck[i], now)) n++;
  return n;
}
// Count of already-reviewed cards in a chapter that are due again now.
// Doesn't need the total card count — used for the sidebar "N due"
// badge (a never-opened chapter has an empty deck ⇒ 0).
export function deckDueCount(slug, cid) {
  const deck = loadDeck(slug, cid);
  const now = Date.now();
  let n = 0;
  for (const k in deck) if (isDue(deck[k], now)) n++;
  return n;
}

// ---- chapter / challenge progress ----
const PROG_KEY = 'dd:study:progress:v1';
const _loadProg = () => {
  try { return JSON.parse(localStorage.getItem(PROG_KEY) || '{}') || {}; }
  catch (_) { return {}; }
};
const _saveProg = (o) => {
  try { localStorage.setItem(PROG_KEY, JSON.stringify(o)); } catch (_) {}
};
const _slugProg = (p, slug) =>
  (p[slug] = p[slug] || { chapters: {}, challenges: {} });

export function markChapterStudied(slug, cid, studied = true) {
  const p = _loadProg();
  _slugProg(p, slug).chapters[cid] = studied;
  _saveProg(p);
}
export function isChapterStudied(slug, cid) {
  const p = _loadProg();
  return !!(p[slug] && p[slug].chapters && p[slug].chapters[cid]);
}
export function studiedCount(slug) {
  const p = _loadProg();
  if (!p[slug] || !p[slug].chapters) return 0;
  return Object.values(p[slug].chapters).filter(Boolean).length;
}
export function setChallengeGrade(slug, cid, idx, status) {  // 'got' | 'review'
  const p = _loadProg();
  _slugProg(p, slug).challenges[cid + '::' + idx] = status;
  _saveProg(p);
}
export function getChallengeGrade(slug, cid, idx) {
  const p = _loadProg();
  return (p[slug] && p[slug].challenges && p[slug].challenges[cid + '::' + idx]) || null;
}

// Wipe ALL local study state for one framework — FSRS decks (dd:srs:v1,
// keyed `slug::cid`) + chapter "studied" flags + challenge self-grades
// (dd:study:progress:v1, keyed by slug). Called by Wipe Synth so a wiped
// framework leaves NO phantom "N studied" / due-card badges in the Study
// sidebar. These stores are local-only (never reconciled with the
// server) and survive server wipes + hard refresh, so without this an
// externally-wiped framework keeps showing stale progress. Also prevents
// a re-synthesized chapter from inheriting a misaligned old deck (card
// indices won't match the regenerated flashcards.json).
export function forgetFramework(slug) {
  if (!slug) return;
  try {
    const all = _loadSrs();
    const prefix = slug + '::';
    let changed = false;
    for (const k of Object.keys(all)) {
      if (k.startsWith(prefix)) { delete all[k]; changed = true; }
    }
    if (changed) _saveSrs(all);
  } catch (_) {}
  try {
    const p = _loadProg();
    if (p[slug]) { delete p[slug]; _saveProg(p); }
  } catch (_) {}
}
