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

// ---- pipeline-state probe + cascade-message helper ----------------
// Single shared fetch the three wipe / delete handlers use to label
// their confirm dialogs with accurate cascade impact. Falls back to
// "everything is cached" (the conservative show-all-warnings shape)
// if the endpoint fails — better to over-warn than to silently delete
// downstream artifacts the user didn't realize were there.
export async function fetchPipelineState(slug) {
  if (!slug) return null;
  try {
    const r = await fetch(S.API + '/pipeline/' + encodeURIComponent(slug) +
                           '/state');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return await r.json();
  } catch (e) {
    console.warn('[fetchPipelineState]', slug, e);
    return {
      slug, ingestion: true, planner: true, synth: true, study: true,
    };
  }
}

// ---- cross-stage proactive gate -----------------------------------
// Planner and Synth must NOT run simultaneously: they fight for the
// same free-tier LLM rotator pool and degrade each other's output
// quality. The server enforces this with locked-response gates at
// POST /planner and POST /synth, but the UI should ALSO disable the
// Start buttons proactively so the user sees the constraint before
// clicking (and so a click that races a remote start gets a clear
// message instead of looking like a silent no-op).
//
// `GET /pipeline/active` returns `{planner: {slug, thread_id} | null,
// synth: {slug, thread_id} | null}` — the cached result is what the
// Start-state refreshers read synchronously to decide whether to
// disable the button + which "running on X" tooltip to show.
//
// Cache lifecycle: refreshCrossStageBlocker() is called from
// initPlanner / initSynth on page load, after every Start / Cancel
// click, and could be polled (not done today — the rare-cross-tab
// case is fine to catch at click-time via the server's locked
// response). Fallback on fetch error: both `null` (no blocker) so a
// transient network blip doesn't lock the user out.
let _crossStageBlocker = { planner: null, synth: null };

export async function fetchActivePipelineStage() {
  try {
    const r = await fetch(S.API + '/pipeline/active');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return await r.json();
  } catch (e) {
    console.warn('[fetchActivePipelineStage]', e);
    return { planner: null, synth: null };
  }
}

export async function refreshCrossStageBlocker() {
  _crossStageBlocker = await fetchActivePipelineStage();
  return _crossStageBlocker;
}

export function getCrossStageBlocker() {
  return _crossStageBlocker;
}

// Build the "you cannot start because the OTHER stage is running"
// blocking message for either Planner or Synth Start buttons. Returns
// null when nothing is blocking, otherwise an object with `title` for
// the tooltip + `notice` for an inline toast. `mySlug` lets us avoid
// blocking a Planner run for slug X while a Synth IS running but it's
// on slug X too — wait, no. The constraint is "Planner + Synth never
// concurrent, ANY slug". So mySlug is unused for blocking but kept
// for parity with future per-slug rules.
export function crossStageBlockerFor(myStage) {
  const b = _crossStageBlocker || {};
  const other = myStage === 'planner' ? 'synth' : 'planner';
  const otherLock = b[other];
  if (!otherLock || !otherLock.slug) return null;
  const otherLabel = other.charAt(0).toUpperCase() + other.slice(1);
  return {
    stage: other,
    slug: otherLock.slug,
    thread_id: otherLock.thread_id,
    title: otherLabel + ' is running on ' + otherLock.slug +
           ' — Planner and Synth share LLM resources and cannot run ' +
           'at the same time. Wait for it to finish or cancel it first.',
    notice: otherLabel + ' is running on ' + otherLock.slug + '. ' +
            'Planner and Synth share the same LLM resources and ' +
            'cannot run at the same time without degrading each ' +
            "other's quality — wait for the other stage to finish " +
            'or cancel it before starting this one.',
  };
}

// Build the cascade-impact tail for a confirm message. ``fromStage`` is
// the stage being wiped — only downstream stages from that point are
// listed. Returns a string that READS NICELY appended to the destructive
// action description ("Wipe planner cache for X? Deletes...") so the
// user sees the same shape regardless of which button they clicked.
export function cascadeImpactText(state, fromStage) {
  if (!state) return '';
  // Downstream order — what gets cascaded for each entry-point.
  const downstream = {
    ingestion: ['planner', 'synth', 'study'],
    planner:   ['synth', 'study'],
    synth:     ['study'],
  }[fromStage] || [];
  const cached = downstream.filter(s => state[s]);
  if (cached.length === 0) return '';
  const labels = {
    planner: 'cached Planner artifacts',
    synth:   'cached Synth chapter outputs',
    study:   'rendered Study chapters',
  };
  const parts = cached.map(s => labels[s]);
  let list;
  if (parts.length === 1) list = parts[0];
  else if (parts.length === 2) list = parts.join(' and ');
  else list = parts.slice(0, -1).join(', ') + ', and ' + parts.slice(-1);
  return ' Cascades downstream — will ALSO delete the ' + list +
         ' for this framework.';
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
  // Highlight the currently-viewing card across both step grids.
  // `data-idx` on rendered cards is the ARRAY POSITION (see
  // ingestion.js renderManifestTo for the why) — match it against
  // `S.drawerIdx` (also an array position), NOT `e.idx` (the entry's
  // storage idx, which would mis-target a different card whenever the
  // manifest was reordered post-fetch).
  document.querySelectorAll('.fw-page-card.viewing').forEach(
    c => c.classList.remove('viewing')
  );
  document.querySelectorAll(
    '.fw-page-card[data-idx="' + S.drawerIdx + '"]'
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
  // Bottom-bar labels — only present on the Catalog stage
  // (#fw-sticky-bar in _StickyBar). TWO parallel groups, BOTH visible
  // when a run is in flight:
  //
  //   Selected:   <tile the user clicked>      (always — that's the
  //                                              picker's source of truth)
  //   Ingesting:  <active framework>           (only when activeRunId set;
  //                                              hidden via display:none
  //                                              the rest of the time)
  //
  // Selected stays current with the user's last tile click so they can
  // queue up the NEXT ingestion mentally while the current one runs;
  // Ingesting reflects the pipeline reality. Both names hydrate async
  // via the same singleton ensureFrameworkInfo / catalog tile cache.
  const nameEl = document.querySelector('#fw-selected-name');
  if (nameEl) {
    if (!S.selected) nameEl.textContent = '';
    else if (S.frameworkInfo[S.selected]) {
      nameEl.textContent = S.frameworkInfo[S.selected].name || S.selected;
    }
  }
  const ingLabel = document.querySelector('#fw-ingesting-label');
  const ingNameEl = document.querySelector('#fw-ingesting-name');
  if (ingLabel && ingNameEl) {
    if (ingestActive && S.activeSlug) {
      ingLabel.style.display = '';
      const cached = S.frameworkInfo[S.activeSlug];
      ingNameEl.textContent = (cached && cached.name) || S.activeSlug;
      // Hydrate from the resolver if not cached. Same singleton fetch
      // the progress card + picker trigger use, so one round-trip
      // populates all three UI surfaces.
      const cachedSlug = S.activeSlug;
      const cachedRunId = S.activeRunId;
      import('./picker.js').then(({ ensureFrameworkInfo }) => {
        ensureFrameworkInfo(cachedSlug).then((info) => {
          // Re-check state — the run may have finished or switched
          // frameworks between the kick-off and the hydrate response.
          if (S.activeRunId === cachedRunId && S.activeSlug === cachedSlug
              && info && info.name && ingNameEl) {
            ingNameEl.textContent = info.name;
          }
        }).catch(() => {});
      }).catch(() => {});
    } else {
      ingLabel.style.display = 'none';
      ingNameEl.textContent = '';
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

