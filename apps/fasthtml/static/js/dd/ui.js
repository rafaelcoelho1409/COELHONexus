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
    const md = (typeof marked !== 'undefined')
      ? marked.parse(raw)
      : '<pre>' + raw.replace(/&/g, '&amp;').replace(/</g, '&lt;') + '</pre>';
    S.drawerBody.innerHTML = '<article class="fw-markdown">' + md + '</article>';
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

// ============================================================
// Stepper navigation
// ============================================================
export function renderStepper() {
  S.steps.forEach((s, i) => {
    const n = i + 1;
    s.classList.remove('active', 'completed');
    if (n === S.currentStep) s.classList.add('active');
    else if (n <= S.farthestStep) s.classList.add('completed');
  });
  S.connectors.forEach((c, i) => {
    c.classList.toggle('complete', i + 1 < S.farthestStep);
  });
}

// showStep is reassigned by study.js to hook Step 5 navigation.
// We export a reference-holder object so the reassignment is visible
// to all callers.
export const stepFn = { showStep: null };

export function showStep(n) {
  // Delegate to the current implementation (may be wrapped by study.js)
  stepFn.showStep(n);
}

// The "real" showStep logic — study.js wraps this.
export function _showStepImpl(n) {
  if (n > S.farthestStep) return;
  S.setCurrentStep(n);
  S.panels.forEach((p, i) => p.classList.toggle('active', i + 1 === n));
  // Sticky bar appears on Step 1 whenever a tile is selected; Generate
  // enablement is controlled by `refreshGenerateState()`.
  S.stickyBar.classList.toggle('visible', n === 1 && S.selected !== null);
  // Step 3 — Cytoscape latches container dimensions at init time;
  // the canvas was initialized while the panel was display:none so
  // its viewport is 0x0 until we explicitly tell it to resize after
  // the panel becomes visible. Idempotent — no-op when ?ui=cards.
  if (n === 3 && S.plannerGraph) {
    // Dynamic import to avoid circular dependency at module parse time
    import('./planner.js').then(m => m._resizePlannerCanvas());
  }
  if (n === 4 && S.synthGraph) {
    import('./synth.js').then(m => m._resizeSynthCanvas());
  }
  // Step 2 — only show the live progress box during an active run;
  // pull the canonical manifest into the file list otherwise.
  if (n === 2) {
    if (S.activeRunId !== null) {
      S.progressBox.style.display = '';
      S.step2Summary.innerHTML = '';
      S.step2Grid.innerHTML =
        '<div class="fw-empty">Ingestion in progress — materials will ' +
        'appear here when it completes.</div>';
    } else {
      S.progressBox.style.display = 'none';
      if (S.activeSlug) {
        import('./ingestion.js').then(m => m.loadManifestForSlug(S.activeSlug));
      }
    }
  }
  // Step 3 — Planner. Refresh start-button enablement.
  if (n === 3) {
    import('./planner.js').then(m => m.refreshPlannerStartState());
  }
  renderStepper();
}

// Initialize the reference
stepFn.showStep = _showStepImpl;

export function syncStepLocks() {
  // Steps 2-5 unlock when EITHER an ingestion is running OR the library
  // has at least one finalized framework.
  const hasLibrary =
    S.sidebarList.querySelectorAll('.fw-lib-item').length > 0;
  const ingestActive = S.activeRunId !== null;
  if (hasLibrary || ingestActive) {
    S.setFarthestStep(Math.max(S.farthestStep, 5));
  } else {
    S.setFarthestStep(1);
    if (S.currentStep !== 1) {
      S.setCurrentStep(1);
      S.panels.forEach((p, i) => p.classList.toggle('active', i + 1 === 1));
      S.stickyBar.classList.toggle('visible', S.selected !== null);
    }
  }
  renderStepper();
}

export function refreshGenerateState() {
  // Disable Start Ingestion + every sidebar Refresh button while an
  // ingestion is in flight.
  const ingestActive = S.activeRunId !== null;
  if (!S.selected || ingestActive) {
    S.generate.setAttribute('disabled', 'disabled');
  } else {
    S.generate.removeAttribute('disabled');
  }
  document.querySelectorAll('.fw-lib-refresh, .fw-lib-delete').forEach(b => {
    if (ingestActive) {
      b.setAttribute('disabled', 'disabled');
    } else {
      b.removeAttribute('disabled');
    }
  });
}

export function advance() {
  if (S.currentStep >= 4) return;
  S.setFarthestStep(Math.max(S.farthestStep, S.currentStep + 1));
  showStep(S.currentStep + 1);
}

export function jumpTo(step) {
  S.setFarthestStep(Math.max(S.farthestStep, step));
  showStep(step);
}

S.steps.forEach((s, i) => s.addEventListener('click', () => {
  const target = i + 1;
  if (target <= S.farthestStep) showStep(target);
}));
