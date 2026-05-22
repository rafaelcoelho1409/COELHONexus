// Study viewer — chapter S.sidebar, tabs, flashcards, artifact loading.
import * as S from './state.js';
import { escapeHtml } from './utils.js';
import { stepFn } from './ui.js';

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
  S.studyChapterListEl.innerHTML = S.studyChapters.map(ch => {
    const status = !ch.rendered
      ? 'not-rendered'
      : (ch.audit_passed ? 'rendered' : 'audit-failed');
    const icon = !ch.rendered
      ? '○'
      : (ch.audit_passed ? '●' : '✕');
    const cls = [
      'fw-study-chapter',
      ch.id === S.studyActiveChapter ? 'active' : '',
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

export async function _loadStudyReadme(slug, cid) {
  if (!S.studyReadmeEl) return;
  S.studyReadmeEl.innerHTML =
    '<div class="fw-empty">Loading chapter…</div>';
  try {
    const raw = await _loadStudyArtifact(slug, cid, 'README.md');
    const md = (typeof marked !== 'undefined')
      ? marked.parse(raw)
      : ('<pre>' + escapeHtml(raw) + '</pre>');
    S.studyReadmeEl.innerHTML = md;
    // Apply syntax highlighting if highlight.js is loaded.
    if (typeof hljs !== 'undefined') {
      S.studyReadmeEl.querySelectorAll('pre code').forEach(block => {
        try { hljs.highlightElement(block); } catch (_) {}
      });
    }
  } catch (e) {
    S.studyReadmeEl.innerHTML =
      '<div class="fw-empty">Failed to load README.md: ' +
      escapeHtml(String(e)) + '</div>';
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
    S.studyChallengesEl.innerHTML = headerHtml + itemsHtml;
  } catch (e) {
    S.studyChallengesEl.innerHTML =
      '<div class="fw-empty">Failed to load challenges.md: ' +
      escapeHtml(String(e)) + '</div>';
  }
}

export function _renderFlashcard() {
  if (!S.studyFlashcardsEl) return;
  if (!S.studyCards.length) {
    S.studyFlashcardsEl.innerHTML =
      '<div class="fw-empty">No flashcards for this chapter.</div>';
    return;
  }
  const card = S.studyCards[S.studyCardIdx];
  const total = S.studyCards.length;
  S.studyFlashcardsEl.innerHTML =
    '<div class="fw-study-cards-progress">' +
      'Card ' + (S.studyCardIdx + 1) + ' of ' + total +
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
        (S.studyCardIdx === 0 ? ' disabled' : '') + '>← Prev</button>' +
      '<button type="button" id="fw-study-card-flip">Flip</button>' +
      '<button type="button" id="fw-study-card-next"' +
        (S.studyCardIdx === total - 1 ? ' disabled' : '') + '>Next →</button>' +
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
    if (S.studyCardIdx > 0) { S.studyCardIdx--; _renderFlashcard(); }
  });
  if (nextBtn) nextBtn.addEventListener('click', () => {
    if (S.studyCardIdx < S.studyCards.length - 1) {
      S.studyCardIdx++; _renderFlashcard();
    }
  });
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
    S.setStudyCardIdx(0);
    _renderFlashcard();
  } catch (e) {
    S.studyFlashcardsEl.innerHTML =
      '<div class="fw-empty">Failed to load flashcards.json: ' +
      escapeHtml(String(e)) + '</div>';
  }
}

export async function openStudyChapter(cid) {
  if (!S.activeSlug || !cid) return;
  const ch = S.studyChapters.find(c => c.id === cid);
  if (!ch) return;
  if (!ch.rendered) {
    _renderStudyChapterHead(ch);
    S.studyReadmeEl.innerHTML =
      '<div class="fw-empty">This chapter has not been synthesized yet. ' +
      'Run Synth (Step 4) on this chapter first.</div>';
    S.studyChallengesEl.innerHTML =
      '<div class="fw-empty">No challenges available — chapter not synthesized.</div>';
    S.studyFlashcardsEl.innerHTML =
      '<div class="fw-empty">No flashcards available — chapter not synthesized.</div>';
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
  _setStudyStagePill('done', 'Reading · ' + (ch.title || cid));
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
        'this framework yet. Run Synth (Step 4) to generate content.</div>';
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
    _switchStudyTab(btn.dataset.tab || 'readme');
  });
});

// Chapter S.sidebar: event delegation for chapter clicks. Picking a
// chapter closes the side window so the materials get the full width.
if (S.studyChapterListEl) {
  S.studyChapterListEl.addEventListener('click', ev => {
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

// Hook into showStep so navigating to Step 5 triggers the load. We wrap
// the mutable `stepFn.showStep` indirection (ui.js) rather than the
// imported binding — ES modules forbid reassigning imports.
const _origShowStep = stepFn.showStep;
stepFn.showStep = function(n) {
  _origShowStep(n);
  // The chapter side window is position:fixed, so it would bleed over
  // other S.steps if left open — always close it when not on Step 5,
  // and start Step 5 content-first (closed) too.
  closeStudySide();
  if (n === 5) {
    refreshStudyVisibility();
    setStudyFramework(S.activeSlug);
    if (S.activeSlug && S.activeSlug !== S.studyLoadedSlug) {
      loadStudyChapters(S.activeSlug);
    }
  }
};
