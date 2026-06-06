// study/search.js — Cmd-K / Ctrl-K cross-chapter search. Local-first
// + vanilla: lazily fetch every rendered chapter's README on first
// open, build a tiny in-memory index (title + headings + body), and
// match queries against it. No framework, no CDN, no backend index.
// A result deep-links to its chapter and scrolls to the matched
// heading. Extracted from study.js Step 2 (2026-06-05 follow-up)
// using per-function brace counting. No DI needed — chapters.js +
// sidebar.js don't import search.js, so direct imports break no cycle.
import * as Si from '@dd/shared/state/ingestion.js';
import * as Ss from '@dd/shared/state/study.js';
import { escapeHtml } from '../shared/utils.js';
import { _loadStudyArtifact } from './shared.js';
import { _slugifyHeading } from './readme.js';
import { openStudyChapter } from './chapters.js';
import { _switchStudyTab } from './sidebar.js';

let _searchIndex = null;
let _searchBuilt = false;
let _searchSel = 0;
let _searchHits = [];

async function _buildSearchIndex() {
  if (_searchBuilt) return;
  _searchBuilt = true;
  _searchIndex = [];
  const slug = Si.activeSlug;
  for (const ch of Ss.studyChapters) {
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

export async function openSearch() {
  if (!Si.activeSlug || !Ss.studyChapters.length) return;
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
export function closeSearch() {
  if (_searchOverlay) _searchOverlay.classList.remove('open');
}

// ⌘K / Ctrl-K opens search (study page only); the 🔍 button does too.
document.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
    if (!Ss.studyGridEl) return;   // not on the study page
    e.preventDefault();
    openSearch();
  }
});
(() => {
  const btn = document.querySelector('#fw-study-search-btn');
  if (btn) btn.addEventListener('click', openSearch);
})();
