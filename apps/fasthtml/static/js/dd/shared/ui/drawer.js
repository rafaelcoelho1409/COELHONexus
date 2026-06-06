// shared/ui/drawer.js — slide-out right-anchored file-content drawer.
// Same renderer pipeline as the Study chapter view (marked +
// DOMPurify + lazy hljs + mermaid + KaTeX + ANSI). Extracted from
// shared/ui.js Step 3 (2026-06-05 follow-up).
import * as Sa from '@dd/shared/state/api.js';
import * as Si from '@dd/shared/state/ingestion.js';
import * as So from '@dd/shared/state/overlays.js';
import { fmtBytes } from '../utils.js';

export function openDrawer(idx) {
  if (!So.currentManifestEntries || So.currentManifestEntries.length === 0) return;
  if (idx < 0 || idx >= So.currentManifestEntries.length) return;
  So.setDrawerIdx(idx);
  So.drawerEl.classList.add('visible');
  renderDrawerContent();
}
export function closeDrawer() {
  So.drawerEl.classList.remove('visible');
  document.querySelectorAll('.fw-page-card.viewing').forEach(
    c => c.classList.remove('viewing')
  );
}
export function drawerStep(delta) {
  const next = So.drawerIdx + delta;
  if (next < 0 || next >= So.currentManifestEntries.length) return;
  So.setDrawerIdx(next);
  renderDrawerContent();
}
export async function renderDrawerContent() {
  const e = So.currentManifestEntries[So.drawerIdx];
  if (!e || !Si.activeSlug) { closeDrawer(); return; }
  So.drawerName.textContent = e.title || e.slug;
  So.drawerMeta.textContent =
    (e.tier || '') + ' · ' + fmtBytes(e.bytes) + ' · ' +
    (So.drawerIdx + 1) + ' of ' + So.currentManifestEntries.length;
  if (So.drawerIdx === 0) So.drawerPrev.setAttribute('disabled', 'disabled');
  else So.drawerPrev.removeAttribute('disabled');
  if (So.drawerIdx >= So.currentManifestEntries.length - 1) So.drawerNext.setAttribute('disabled', 'disabled');
  else So.drawerNext.removeAttribute('disabled');
  // Highlight the currently-viewing card across both step grids.
  // `data-idx` on rendered cards is the ARRAY POSITION (see
  // ingestion.js renderManifestTo for the why) — match it against
  // `So.drawerIdx` (also an array position), NOT `e.idx` (the entry's
  // storage idx, which would mis-target a different card whenever the
  // manifest was reordered post-fetch).
  document.querySelectorAll('.fw-page-card.viewing').forEach(
    c => c.classList.remove('viewing')
  );
  document.querySelectorAll(
    '.fw-page-card[data-idx="' + So.drawerIdx + '"]'
  ).forEach(c => c.classList.add('viewing'));
  So.drawerBody.innerHTML = '<div class="fw-empty">Loading…</div>';
  try {
    const r = await fetch(Sa.API + '/ingestion/' + Si.activeSlug +
                           '/pages/' + e.idx);
    if (!r.ok) {
      So.drawerBody.innerHTML =
        '<div class="fw-empty">Failed to load (HTTP ' + r.status + ')</div>';
      return;
    }
    const data = await r.json();
    const raw = data.body || '';
    // Rich render — same pipeline as the Study page: marked parse +
    // DOMPurify sanitize + lazy hljs + mermaid + KaTeX + ANSI terminal
    // blocks. The <article.fw-markdown> wrapper preserves the existing
    // typography styles.
    So.drawerBody.innerHTML = '<article class="fw-markdown"></article>';
    const article = So.drawerBody.querySelector('article');
    const { renderMarkdownInto } = await import('../content_renderer.js');
    await renderMarkdownInto(article, raw, {});
    So.drawerBody.scrollTop = 0;
  } catch (err) {
    So.drawerBody.innerHTML = '<div class="fw-empty">' + String(err) + '</div>';
  }
}
So.drawerPrev?.addEventListener('click', () => drawerStep(-1));
So.drawerNext?.addEventListener('click', () => drawerStep(1));
So.drawerClose?.addEventListener('click', closeDrawer);
document.addEventListener('keydown', (e) => {
  if (!So.drawerEl?.classList.contains('visible')) return;
  // Don't hijack arrows when the user is typing in an input/textarea
  const tag = (document.activeElement?.tagName || '').toLowerCase();
  if (tag === 'input' || tag === 'textarea') return;
  if (e.key === 'Escape') closeDrawer();
  else if (e.key === 'ArrowLeft') drawerStep(-1);
  else if (e.key === 'ArrowRight') drawerStep(1);
});
// Click delegation — opens the drawer from any .fw-page-card in any grid
document.addEventListener('click', (e) => {
  const card = e.target.closest('.fw-page-card');
  if (!card) return;
  const idx = parseInt(card.dataset.idx, 10);
  if (Number.isFinite(idx)) openDrawer(idx);
});
