// ============================================================
// ui.js — Shared UI components: notice, toast, modal, drawer,
//         stepper, step navigation, generate-button state
// ============================================================

import * as S from './state.js';
import { fmtBytes } from './utils.js';

// ---- notice + toast ----
export function showNotice(text) {
  if (!S.noticeEl) return;
  S.noticeText.textContent = text;
  S.noticeEl.style.display = '';
  setTimeout(() => { S.noticeEl.style.display = 'none'; }, 8000);
}
export function hideNotice() { if (S.noticeEl) S.noticeEl.style.display = 'none'; }
export function showToast(text) {
  if (!S.toastEl) return;
  S.toastText.textContent = text;
  S.toastEl.style.display = '';
}
export function hideToast() { if (S.toastEl) S.toastEl.style.display = 'none'; }
S.toastClose?.addEventListener('click', hideToast);

// ---- in-page confirm modal (replacement for browser confirm()) ----
export function showConfirm(title, message, confirmLabel) {
  if (!S.modalEl) return Promise.resolve(false);
  S.modalTitleEl.textContent = title;
  S.modalMessageEl.textContent = message;
  S.modalConfirmBtn.textContent = confirmLabel || 'Confirm';
  S.modalEl.classList.add('visible');
  return new Promise(resolve => { S.set_modalResolver(resolve); });
}
export function closeModal(result) {
  if (!S.modalEl) return;
  S.modalEl.classList.remove('visible');
  const r = S._modalResolver;
  S.set_modalResolver(null);
  if (r) r(result);
}
S.modalConfirmBtn?.addEventListener('click', () => closeModal(true));
S.modalCancelBtn?.addEventListener('click', () => closeModal(false));
S.modalEl?.addEventListener('click', (e) => {
  // Click on the backdrop (outside the box) cancels.
  if (e.target === S.modalEl) closeModal(false);
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && S.modalEl?.classList.contains('visible')) {
    closeModal(false);
  }
});

// ---- file-content drawer (slide-out, right-anchored) ----
export function openDrawer(idx) {
  if (!S.currentManifestEntries || S.currentManifestEntries.length === 0) return;
  if (idx < 0 || idx >= S.currentManifestEntries.length) return;
  S.setDrawerIdx(idx);
  S.drawerEl.classList.add('visible');
  renderDrawerContent();
}
export function closeDrawer() {
  S.drawerEl.classList.remove('visible');
  document.querySelectorAll('.fw-page-card.viewing').forEach(
    c => c.classList.remove('viewing')
  );
}
export function drawerStep(delta) {
  const next = S.drawerIdx + delta;
  if (next < 0 || next >= S.currentManifestEntries.length) return;
  S.setDrawerIdx(next);
  renderDrawerContent();
}
export async function renderDrawerContent() {
  const e = S.currentManifestEntries[S.drawerIdx];
  if (!e || !S.activeSlug) { closeDrawer(); return; }
  S.drawerName.textContent = e.title || e.slug;
  S.drawerMeta.textContent =
    (e.tier || '') + ' · ' + fmtBytes(e.bytes) + ' · ' +
    (S.drawerIdx + 1) + ' of ' + S.currentManifestEntries.length;
  if (S.drawerIdx === 0) S.drawerPrev.setAttribute('disabled', 'disabled');
  else S.drawerPrev.removeAttribute('disabled');
  if (S.drawerIdx >= S.currentManifestEntries.length - 1) S.drawerNext.setAttribute('disabled', 'disabled');
  else S.drawerNext.removeAttribute('disabled');
  // Highlight the currently-viewing card across both step grids
  document.querySelectorAll('.fw-page-card.viewing').forEach(
    c => c.classList.remove('viewing')
  );
  document.querySelectorAll(
    '.fw-page-card[data-idx="' + e.idx + '"]'
  ).forEach(c => c.classList.add('viewing'));
  S.drawerBody.innerHTML = '<div class="fw-empty">Loading…</div>';
  try {
    const r = await fetch(S.API + '/ingestion/' + S.activeSlug +
                           '/pages/' + e.idx);
    if (!r.ok) {
      S.drawerBody.innerHTML =
        '<div class="fw-empty">Failed to load (HTTP ' + r.status + ')</div>';
      return;
    }
    const data = await r.json();
    const raw = data.body || '';
    // Rich render — same pipeline as the Study page: marked parse +
    // DOMPurify sanitize + lazy hljs + mermaid + KaTeX + ANSI terminal
    // blocks. The <article.fw-markdown> wrapper preserves the existing
    // typography styles.
    S.drawerBody.innerHTML = '<article class="fw-markdown"></article>';
    const article = S.drawerBody.querySelector('article');
    const { renderMarkdownInto } = await import('./content_renderer.js');
    await renderMarkdownInto(article, raw, {});
    S.drawerBody.scrollTop = 0;
  } catch (err) {
    S.drawerBody.innerHTML = '<div class="fw-empty">' + String(err) + '</div>';
  }
}
S.drawerPrev?.addEventListener('click', () => drawerStep(-1));
S.drawerNext?.addEventListener('click', () => drawerStep(1));
S.drawerClose?.addEventListener('click', closeDrawer);
document.addEventListener('keydown', (e) => {
  if (!S.drawerEl?.classList.contains('visible')) return;
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

// NOTE: the wizard-era stepper machinery (renderStepper / showStep /
// _showStepImpl / stepFn / syncStepLocks / advance / jumpTo + the
// `.fw-step` click handler) was removed 2026-05-28 — per-stage routes
// (/docs-distiller/<stage>) replaced the single-page stepper, so stage
// navigation is now real <a href> links + main.js stage init.

export function refreshGenerateState() {
  // Disable Start Ingestion + every sidebar Refresh button while an
  // ingestion is in flight. The Start Ingestion button (#fw-generate)
  // only exists on /docs-distiller (catalog) — null-guarded so this
  // function can be safely called from any stage page (e.g. by
  // renderSidebar after the library list re-renders, which would
  // otherwise throw on non-catalog pages and bubble up to the
  // loadLibrary catch block — rendering the popover empty).
  const ingestActive = S.activeRunId !== null;
  if (S.generate) {
    if (!S.selected || ingestActive) {
      S.generate.setAttribute('disabled', 'disabled');
    } else {
      S.generate.removeAttribute('disabled');
    }
  }
  document.querySelectorAll('.fw-lib-refresh, .fw-lib-delete').forEach(b => {
    if (ingestActive) {
      b.setAttribute('disabled', 'disabled');
    } else {
      b.removeAttribute('disabled');
    }
  });
}

