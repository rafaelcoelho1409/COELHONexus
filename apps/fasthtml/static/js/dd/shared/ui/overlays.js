// shared/ui/overlays.js — generic page overlays: notice banner,
// toast, confirm modal. Extracted from shared/ui.js Step 3
// (2026-06-05 follow-up). Pure DOM + state/overlays.js; no pipeline
// awareness.
import * as So from '@dd/shared/state/overlays.js';

// ---- notice + toast ----
export function showNotice(text) {
  if (!So.noticeEl) return;
  So.noticeText.textContent = text;
  So.noticeEl.style.display = '';
  setTimeout(() => { So.noticeEl.style.display = 'none'; }, 8000);
}
export function hideNotice() { if (So.noticeEl) So.noticeEl.style.display = 'none'; }
export function showToast(text) {
  if (!So.toastEl) return;
  So.toastText.textContent = text;
  So.toastEl.style.display = '';
}
export function hideToast() { if (So.toastEl) So.toastEl.style.display = 'none'; }
So.toastClose?.addEventListener('click', hideToast);

// ---- in-page confirm modal (replacement for browser confirm()) ----
export function showConfirm(title, message, confirmLabel) {
  if (!So.modalEl) return Promise.resolve(false);
  So.modalTitleEl.textContent = title;
  So.modalMessageEl.textContent = message;
  So.modalConfirmBtn.textContent = confirmLabel || 'Confirm';
  So.modalEl.classList.add('visible');
  return new Promise(resolve => { So.set_modalResolver(resolve); });
}
export function closeModal(result) {
  if (!So.modalEl) return;
  So.modalEl.classList.remove('visible');
  const r = So._modalResolver;
  So.set_modalResolver(null);
  if (r) r(result);
}
So.modalConfirmBtn?.addEventListener('click', () => closeModal(true));
So.modalCancelBtn?.addEventListener('click', () => closeModal(false));
So.modalEl?.addEventListener('click', (e) => {
  // Click on the backdrop (outside the box) cancels.
  if (e.target === So.modalEl) closeModal(false);
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && So.modalEl?.classList.contains('visible')) {
    closeModal(false);
  }
});
