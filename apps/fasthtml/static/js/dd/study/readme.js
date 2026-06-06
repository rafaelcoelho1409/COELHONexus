// study/readme.js — chapter README renderer (slug-heading anchor IDs,
// scroll-spy TOC, post-process markdown, fetch + insert flow).
// Extracted from study.js Step 8 (2026-06-05). DI break:
// _loadStudyArtifact moved to shared.js so this module imports from
// shared.js — never from study.js — and study.js re-exports our
// public functions so main.js's import resolution stays unchanged.
import * as Sa from '@dd/shared/state/api.js';
import * as Si from '@dd/shared/state/ingestion.js';
import * as So from '@dd/shared/state/overlays.js';
import * as Ss from '@dd/shared/state/study.js';
import { escapeHtml } from '../shared/utils.js';
import { openDrawer } from '../shared/ui.js';
import { _loadStudyArtifact } from './shared.js';

let _scrollSpyObserver = null;

export function _slugifyHeading(text, i) {
  const base = (text || '').toLowerCase()
    .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 40);
  return 'sec-' + i + (base ? '-' + base : '');
}

// Build the right-rail table of contents from the rendered headings and
// wire IntersectionObserver scroll-spy to highlight the section in view.
// Hidden when a chapter has fewer than 2 headings (nothing to navigate).
export function _buildReadmeToc() {
  const toc = document.querySelector('#fw-study-toc');
  if (!toc || !Ss.studyReadmeEl) return;
  const heads = Array.from(Ss.studyReadmeEl.querySelectorAll('h2, h3'));
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
  const scrollRoot = Ss.studyReadmeEl.closest('.page') || null;
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
} from '../shared/content_renderer.js';

// ---- "Sources for this section" → open the ingested page in the drawer ----
// The right-side file drawer (ui.js) is index-based over the ingestion
// manifest (So.currentManifestEntries). We lazily fetch that manifest — the
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
  if (_srcIndex && _srcIndexSlug === Si.activeSlug) return _srcIndex;
  const map = new Map();
  try {
    const r = await fetch(Sa.API + '/ingestion/' + Si.activeSlug + '/manifest');
    if (r.ok) {
      const entries = (await r.json()).entries || [];
      So.setCurrentManifestEntries(entries);   // drawer prev/next walk these
      entries.forEach((e, i) => {
        const bn = _entryBasename(e).toLowerCase();
        if (bn) map.set(bn, i);
      });
    }
  } catch (_) { /* leave the map empty → clicks no-op */ }
  _srcIndex = map;
  _srcIndexSlug = Si.activeSlug;
  return _srcIndex;
}
async function _openSourceFile(basename) {
  if (!basename || !Si.activeSlug) return;
  const map = await _ensureSourceIndex();
  const k = basename.trim().toLowerCase();
  let idx = map.get(k);
  if (idx == null && k.endsWith('.md')) idx = map.get(k.slice(0, -3));
  if (idx == null && !k.endsWith('.md')) idx = map.get(k + '.md');
  if (idx == null) return;   // unknown source — silently ignore
  openDrawer(idx);
}

// Source-file links inside "Sources for this section" boxes — open the
// raw ingested page in the same right-side drawer the Ingestion page
// uses. Lives here so it can see the readme-private _openSourceFile
// (was a latent ReferenceError when invoked from study.js).
if (Ss.studyReadmeEl) {
  const onSourceActivate = (ev) => {
    const code = ev.target.closest('.fw-source-file');
    if (!code) return;
    if (ev.type === 'keydown' && ev.key !== 'Enter' && ev.key !== ' ') return;
    ev.preventDefault();
    _openSourceFile(code.dataset.basename);
  };
  Ss.studyReadmeEl.addEventListener('click', onSourceActivate);
  Ss.studyReadmeEl.addEventListener('keydown', onSourceActivate);
}

// Post-process the rendered README DOM:
//   1) Drop the inline "## Contents" list + its trailing `---` divider —
//      the sticky right-rail TOC (scroll-spy) makes it redundant.
//   2) Fold every "Sources for this section:" citation block into a
//      collapsed <details> so the prose stays scannable; sources on demand.
//      Each source's .md basename becomes a clickable link that opens the
//      ingested page in the same drawer the Ingestion page uses.
function _postProcessReadme() {
  const root = Ss.studyReadmeEl;
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
  if (!Ss.studyReadmeEl) return;
  Ss.studyReadmeEl.innerHTML =
    '<div class="fw-empty">Loading chapter…</div>';
  try {
    const raw = await _loadStudyArtifact(slug, cid, 'README.md');
    await _renderMarkdownInto(Ss.studyReadmeEl, raw, {
      // Post-process hook: fold "Sources for this section" lists into
      // collapsed <details> and drop the inline ## Contents block (the
      // sticky right-rail TOC makes it redundant). Runs BEFORE block
      // renderers so the TOC scroll-spy sees the final heading set.
      postProcess: () => { _postProcessReadme(); _buildReadmeToc(); },
    });
  } catch (e) {
    Ss.studyReadmeEl.innerHTML =
      '<div class="fw-empty">Failed to load README.md: ' +
      escapeHtml(String(e)) + '</div>';
    const toc = document.querySelector('#fw-study-toc');
    if (toc) { toc.innerHTML = ''; toc.style.display = 'none'; }
  }
}

