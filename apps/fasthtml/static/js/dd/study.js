// Study viewer — chapter sidebar, tabs, flashcards, artifact loading.
import { API, activeSlug } from './state.js';
import { escapeHtml } from './utils.js';
import { showStep } from './ui.js';

function _setStudySideOpen(open) {
  if (studySideEl) studySideEl.classList.toggle('open', open);
  if (studySideBackdrop) studySideBackdrop.classList.toggle('open', open);
  if (studyTocToggle) studyTocToggle.setAttribute('aria-expanded', String(!!open));
}
function openStudySide()  { _setStudySideOpen(true); }
function closeStudySide() { _setStudySideOpen(false); }
function toggleStudySide() {
  _setStudySideOpen(!(studySideEl && studySideEl.classList.contains('open')));
}
if (studyTocToggle) studyTocToggle.addEventListener('click', toggleStudySide);
if (studySideClose) studySideClose.addEventListener('click', closeStudySide);
if (studySideBackdrop) studySideBackdrop.addEventListener('click', closeStudySide);
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && studySideEl &&
      studySideEl.classList.contains('open')) {
    closeStudySide();
  }
});

// Per-framework state
let studyChapters    = [];     // [{id, title, rendered, audit_passed, ...}]
let studyActiveChapter = null; // current selected chapter id
let studyActiveTab   = 'readme';
let studyCards       = [];     // [{q, a}, ...]
let studyCardIdx     = 0;
let studyLoadedSlug  = null;   // last slug we loaded chapters for
let studyLoadedCid   = null;   // last chapter we loaded artifacts for

function _setStudyStagePill(status, label) {
  if (!studyPill || !studyPillText) return;
  const map = {
    idle:    'Idle',
    working: 'Loading',
    done:    'Ready',
    failed:  'Failed',
    cancelled: 'Cancelled',
  };
  studyPill.dataset.status = status;
  studyPillText.textContent = label || map[status] || status;
}

function setStudyFramework(slug) {
  if (!studyFwName || !studyFwLogos) return;
  if (!slug) {
    studyFwName.textContent = 'Pick a framework with synthesized chapters.';
    studyFwName.classList.add('fw-planner-fw-name-empty');
    studyFwLogos.innerHTML = '';
    studyFwLogos.style.display = 'none';
    return;
  }
  const info = frameworkInfo[slug] || {name: slug, logos: []};
  studyFwName.textContent = info.name || slug;
  studyFwName.classList.remove('fw-planner-fw-name-empty');
  if (info.logos && info.logos.length) {
    studyFwLogos.innerHTML = info.logos.map(u =>
      '<img class="fw-planner-fw-logo" src="' + u + '" alt="">'
    ).join('');
    studyFwLogos.style.display = '';
  } else {
    studyFwLogos.innerHTML = '';
    studyFwLogos.style.display = 'none';
  }
}

function _renderStudySidebar() {
  if (!studyChapterListEl) return;
  if (!studyChapters.length) {
    studyChapterListEl.innerHTML =
      '<div class="fw-empty" style="font-size:0.8rem;padding:8px 4px">' +
      'No chapters in this framework\'s plan. Run Planner first.' +
      '</div>';
    return;
  }
  studyChapterListEl.innerHTML = studyChapters.map(ch => {
    const status = !ch.rendered
      ? 'not-rendered'
      : (ch.audit_passed ? 'rendered' : 'audit-failed');
    const icon = !ch.rendered
      ? '○'
      : (ch.audit_passed ? '●' : '✕');
    const cls = [
      'fw-study-chapter',
      ch.id === studyActiveChapter ? 'active' : '',
    ].filter(Boolean).join(' ');
    const title = ch.title || ch.id;
    return (
      '<button type="button" class="' + cls + '" ' +
      'data-chapter-id="' + escapeHtml(ch.id) + '" ' +
      'data-rendered="' + ch.rendered + '">' +
        '<span class="fw-study-chapter-icon" data-status="' + status + '">' +
          icon + '</span>' +
        '<span class="fw-study-chapter-title">' +
          escapeHtml(title) + '</span>' +
      '</button>'
    );
  }).join('');
}

function _renderStudyChapterHead(ch) {
  if (!studyChapterHeadEl) return;
  if (!ch) {
    studyChapterHeadEl.classList.remove('visible');
    studyChapterHeadEl.innerHTML = '';
    return;
  }
  const auditBadge = ch.rendered
    ? (ch.audit_passed
        ? '<span class="badge pass">Audit ✓</span>'
        : '<span class="badge fail">Audit ✗</span>')
    : '<span class="badge">Not rendered</span>';
  studyChapterHeadEl.innerHTML =
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
  studyChapterHeadEl.classList.add('visible');
}

function _switchStudyTab(tab) {
  studyActiveTab = tab;
  studyTabBtns.forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tab);
  });
  document.querySelectorAll('.fw-study-pane').forEach(pane => {
    pane.classList.toggle('active', pane.dataset.tab === tab);
  });
}

async function _loadStudyArtifact(slug, cid, name) {
  const url = API + '/synth/' + slug + '/study/' + cid + '/artifact/' + name;
  const r = await fetch(url);
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return await r.text();
}

async function _loadStudyReadme(slug, cid) {
  if (!studyReadmeEl) return;
  studyReadmeEl.innerHTML =
    '<div class="fw-empty">Loading chapter…</div>';
  try {
    const raw = await _loadStudyArtifact(slug, cid, 'README.md');
    const md = (typeof marked !== 'undefined')
      ? marked.parse(raw)
      : ('<pre>' + escapeHtml(raw) + '</pre>');
    studyReadmeEl.innerHTML = md;
    // Apply syntax highlighting if highlight.js is loaded.
    if (typeof hljs !== 'undefined') {
      studyReadmeEl.querySelectorAll('pre code').forEach(block => {
        try { hljs.highlightElement(block); } catch (_) {}
      });
    }
  } catch (e) {
    studyReadmeEl.innerHTML =
      '<div class="fw-empty">Failed to load README.md: ' +
      escapeHtml(String(e)) + '</div>';
  }
}

async function _loadStudyChallenges(slug, cid) {
  if (!studyChallengesEl) return;
  studyChallengesEl.innerHTML =
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
    const itemsHtml = items.map(it => (
      '<details class="fw-study-challenge">' +
        '<summary>' +
          '<span class="fw-study-challenge-num">' + it.num + '.</span>' +
          '<span class="fw-study-challenge-text">' + escapeHtml(it.text) + '</span>' +
        '</summary>' +
        '<div class="fw-study-challenge-hint">' +
          'Pause and think before checking your answer against the chapter. ' +
          'The README explains each concept with the same vocabulary used here.' +
        '</div>' +
      '</details>'
    )).join('');
    studyChallengesEl.innerHTML = headerHtml + itemsHtml;
  } catch (e) {
    studyChallengesEl.innerHTML =
      '<div class="fw-empty">Failed to load challenges.md: ' +
      escapeHtml(String(e)) + '</div>';
  }
}

function _renderFlashcard() {
  if (!studyFlashcardsEl) return;
  if (!studyCards.length) {
    studyFlashcardsEl.innerHTML =
      '<div class="fw-empty">No flashcards for this chapter.</div>';
    return;
  }
  const card = studyCards[studyCardIdx];
  const total = studyCards.length;
  studyFlashcardsEl.innerHTML =
    '<div class="fw-study-cards-progress">' +
      'Card ' + (studyCardIdx + 1) + ' of ' + total +
    '</div>' +
    '<div class="fw-study-card-wrap">' +
      '<div class="fw-study-card" id="fw-study-card">' +
        '<div class="fw-study-card-face front">' +
          '<span class="label">Question</span>' +
          '<div class="body">' + _mdInline(card.q) + '</div>' +
        '</div>' +
        '<div class="fw-study-card-face back">' +
          '<span class="label">Answer</span>' +
          '<div class="body">' + _mdInline(card.a) + '</div>' +
        '</div>' +
      '</div>' +
    '</div>' +
    '<div class="fw-study-cards-actions">' +
      '<button type="button" id="fw-study-card-prev"' +
        (studyCardIdx === 0 ? ' disabled' : '') + '>← Prev</button>' +
      '<button type="button" id="fw-study-card-flip">Flip</button>' +
      '<button type="button" id="fw-study-card-next"' +
        (studyCardIdx === total - 1 ? ' disabled' : '') + '>Next →</button>' +
    '</div>' +
    '<div class="fw-study-cards-hint">' +
      'Click the card or hit Flip to reveal the answer.' +
    '</div>';
  // Bind handlers
  const cardEl = document.querySelector('#fw-study-card');
  const prevBtn = document.querySelector('#fw-study-card-prev');
  const flipBtn = document.querySelector('#fw-study-card-flip');
  const nextBtn = document.querySelector('#fw-study-card-next');
  if (cardEl) cardEl.addEventListener('click', () => {
    cardEl.classList.toggle('flipped');
  });
  if (flipBtn) flipBtn.addEventListener('click', () => {
    if (cardEl) cardEl.classList.toggle('flipped');
  });
  if (prevBtn) prevBtn.addEventListener('click', () => {
    if (studyCardIdx > 0) { studyCardIdx--; _renderFlashcard(); }
  });
  if (nextBtn) nextBtn.addEventListener('click', () => {
    if (studyCardIdx < studyCards.length - 1) {
      studyCardIdx++; _renderFlashcard();
    }
  });
}

// Tiny inline-markdown helper for flashcard faces — just handles
// `code` spans + **bold** + line breaks. marked.parse() would wrap
// everything in <p> which fights the flex-center layout.
function _mdInline(text) {
  let s = escapeHtml(text || '');
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/\n/g, '<br>');
  return s;
}

async function _loadStudyFlashcards(slug, cid) {
  if (!studyFlashcardsEl) return;
  studyFlashcardsEl.innerHTML =
    '<div class="fw-empty">Loading flashcards…</div>';
  try {
    const raw = await _loadStudyArtifact(slug, cid, 'flashcards.json');
    studyCards = JSON.parse(raw) || [];
    studyCardIdx = 0;
    _renderFlashcard();
  } catch (e) {
    studyFlashcardsEl.innerHTML =
      '<div class="fw-empty">Failed to load flashcards.json: ' +
      escapeHtml(String(e)) + '</div>';
  }
}

async function openStudyChapter(cid) {
  if (!activeSlug || !cid) return;
  const ch = studyChapters.find(c => c.id === cid);
  if (!ch) return;
  if (!ch.rendered) {
    _renderStudyChapterHead(ch);
    studyReadmeEl.innerHTML =
      '<div class="fw-empty">This chapter has not been synthesized yet. ' +
      'Run Synth (Step 4) on this chapter first.</div>';
    studyChallengesEl.innerHTML =
      '<div class="fw-empty">No challenges available — chapter not synthesized.</div>';
    studyFlashcardsEl.innerHTML =
      '<div class="fw-empty">No flashcards available — chapter not synthesized.</div>';
    return;
  }
  studyActiveChapter = cid;
  studyLoadedCid = cid;
  _renderStudySidebar();   // re-render to update active highlight
  _renderStudyChapterHead(ch);
  _setStudyStagePill('working', 'Loading…');
  // Fire all three loads in parallel
  await Promise.all([
    _loadStudyReadme(activeSlug, cid),
    _loadStudyChallenges(activeSlug, cid),
    _loadStudyFlashcards(activeSlug, cid),
  ]);
  _setStudyStagePill('done', 'Reading · ' + (ch.title || cid));
}

async function loadStudyChapters(slug) {
  if (!studyChapterListEl) return;
  studyChapters = [];
  studyActiveChapter = null;
  studyLoadedCid = null;
  _setStudyStagePill('working', 'Loading chapters…');
  studyChapterListEl.innerHTML =
    '<div class="fw-empty" style="font-size:0.8rem;padding:8px 4px">' +
    'Loading chapters…</div>';
  try {
    const r = await fetch(API + '/synth/' + slug + '/study/chapters');
    if (!r.ok) {
      studyChapterListEl.innerHTML =
        '<div class="fw-empty" style="font-size:0.8rem;padding:8px 4px">' +
        'Failed to load chapters (HTTP ' + r.status + ').</div>';
      _setStudyStagePill('failed', 'Failed');
      return;
    }
    const data = await r.json();
    studyChapters = (data.chapters || []).sort(
      (a, b) => (a.order || 0) - (b.order || 0)
    );
    studyLoadedSlug = slug;
    _renderStudySidebar();
    // Auto-open the first rendered chapter (if any) so the user
    // immediately sees content instead of an empty pane.
    const firstReady = studyChapters.find(c => c.rendered);
    if (firstReady) {
      await openStudyChapter(firstReady.id);
    } else {
      _setStudyStagePill('idle',
        'No rendered chapters yet — run Synth first.');
      studyReadmeEl.innerHTML =
        '<div class="fw-empty">No chapters have been synthesized for ' +
        'this framework yet. Run Synth (Step 4) to generate content.</div>';
    }
  } catch (e) {
    studyChapterListEl.innerHTML =
      '<div class="fw-empty" style="font-size:0.8rem;padding:8px 4px">' +
      'Network error loading chapters.</div>';
    _setStudyStagePill('failed', 'Failed');
  }
}

// Tab buttons: simple click delegation
studyTabBtns.forEach(btn => {
  btn.addEventListener('click', () => {
    _switchStudyTab(btn.dataset.tab || 'readme');
  });
});

// Chapter sidebar: event delegation for chapter clicks. Picking a
// chapter closes the side window so the materials get the full width.
if (studyChapterListEl) {
  studyChapterListEl.addEventListener('click', ev => {
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
// can re-trigger after activeSlug changes.
function refreshStudyVisibility() {
  if (!studyEmptyEl || !studyGridEl) return;
  if (!activeSlug) {
    studyEmptyEl.style.display = '';
    studyGridEl.style.display = 'none';
    return;
  }
  studyEmptyEl.style.display = 'none';
  studyGridEl.style.display = '';
}

// Hook into showStep so navigating to Step 5 triggers the load. If
// the framework changed since last load, refresh. If the same, no-op.
const _origShowStep = showStep;
// eslint-disable-next-line no-func-assign
showStep = function(n) {
  _origShowStep(n);
  // The chapter side window is position:fixed, so it would bleed over
  // other steps if left open — always close it when not on Step 5,
  // and start Step 5 content-first (closed) too.
  closeStudySide();
  if (n === 5) {
    refreshStudyVisibility();
    setStudyFramework(activeSlug);
    if (activeSlug && activeSlug !== studyLoadedSlug) {
      loadStudyChapters(activeSlug);
    }
  }
};
