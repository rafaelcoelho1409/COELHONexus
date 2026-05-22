// ============================================================
// library.js — Sidebar library list, page-reload recovery for
//   active ingestion & planner runs, planner info bootstrap
// ============================================================

import * as S from './state.js';
import { fmtAge, fmtBytes } from './utils.js';
import {
  showNotice, showToast, showConfirm,
  refreshGenerateState, showStep, renderStepper,
  syncStepLocks,
} from './ui.js';
import { setProgressFramework } from './picker.js';
import {
  renderManifest, loadManifestForSlug, renderProgress, pollRun,
  triggerIngest,
} from './ingestion.js';

// ============================================================
// Sidebar — library list
// ============================================================
export function renderSidebar(items) {
  // Augment frameworkInfo from the library list so recovery + sidebar
  // clicks can label the loading box even for frameworks that aren't
  // in the catalog tile set (or were ingested via the audit endpoint).
  if (items) {
    items.forEach(it => {
      if (it.slug && !S.frameworkInfo[it.slug]) {
        // Prefer `logos` array from the catalog (multi-logo stack);
        // fall back to the single `logo` for everyday entries.
        const logos = (it.logos && it.logos.length)
          ? it.logos
          : (it.logo ? [it.logo] : []);
        S.frameworkInfo[it.slug] = {
          name: it.framework_name || it.slug,
          logos,
        };
      }
    });
  }
  if (!items || items.length === 0) {
    S.sidebarList.innerHTML =
      '<div class="fw-sidebar-empty">' +
      'No ingested frameworks yet. Pick one in the catalog and click Start Ingestion.' +
      '</div>';
    return;
  }
  const html = items.map(it => {
    const isActive = (it.slug === S.activeSlug) ? ' active' : '';
    const logo = it.logo
      ? '<img class="fw-lib-logo" src="' + it.logo + '" alt="">'
      : '';
    return '<div class="fw-lib-item' + isActive + '" data-slug="' + it.slug + '">' +
      logo +
      '<div style="flex:1;min-width:0">' +
      '<div class="fw-lib-name">' + (it.framework_name || it.slug) + '</div>' +
      '<div class="fw-lib-meta">' + (it.page_count || 0) + ' pages · ' +
      fmtAge(it.ingested_at) + '</div>' +
      '</div>' +
      '<button class="fw-lib-refresh" data-slug="' + it.slug +
      '" title="Refresh (re-download)">↻</button>' +
      '<button class="fw-lib-delete" data-slug="' + it.slug +
      '" title="Delete this ingestion">🗑</button>' +
      '</div>';
  }).join('');
  S.sidebarList.innerHTML = html;
  S.sidebarList.querySelectorAll('.fw-lib-item').forEach(el => {
    el.addEventListener('click', async ev => {
      if (ev.target.closest('.fw-lib-refresh, .fw-lib-delete')) return;
      const slug = el.dataset.slug;
      S.sidebarList.querySelectorAll('.fw-lib-item').forEach(
        x => x.classList.remove('active'));
      el.classList.add('active');
      await loadManifestForSlug(slug);
      // Library click swaps the ACTIVE FRAMEWORK without changing the
      // user's current step. All 5 steps stay reachable for the
      // newly-selected slug; Study (5) shows its own empty-state until
      // that framework has rendered chapters.
      S.setFarthestStep(Math.max(S.farthestStep, 5));
      renderStepper();
      const { refreshPlannerStartState } = await import('./planner.js');
      refreshPlannerStartState();
      const { refreshSynthStartState } = await import('./synth.js');
      if (typeof refreshSynthStartState === 'function') {
        refreshSynthStartState();
      }
    });
  });
  S.sidebarList.querySelectorAll('.fw-lib-refresh').forEach(b => {
    b.addEventListener('click', ev => {
      ev.stopPropagation();
      triggerIngest(b.dataset.slug, true);
    });
  });
  // Newly-rendered refresh buttons must pick up the current ingest state
  // (a re-render from loadLibrary() during an active run would otherwise
  // give them a fresh enabled state).
  refreshGenerateState();
  S.sidebarList.querySelectorAll('.fw-lib-delete').forEach(b => {
    b.addEventListener('click', async ev => {
      ev.stopPropagation();
      const slug = b.dataset.slug;
      const row = b.closest('.fw-lib-item');
      const displayName = row.querySelector('.fw-lib-name')?.textContent || slug;

      const ok = await showConfirm(
        'Delete ingestion',
        'Permanently delete "' + displayName + '"? ' +
        'Wipes the manifest + every page body from MinIO. ' +
        'This cannot be undone.',
        'Delete'
      );
      if (!ok) return;

      // Replace 🗑 with spinner + lock the row so a stray click can't
      // re-fire delete or jump to another framework mid-DELETE.
      const refresh = row.querySelector('.fw-lib-refresh');
      const originalLabel = b.innerHTML;
      b.innerHTML = '<div class="fw-spinner"></div>';
      b.setAttribute('disabled', 'disabled');
      if (refresh) refresh.setAttribute('disabled', 'disabled');
      row.style.pointerEvents = 'none';
      row.style.opacity = '0.7';

      try {
        const r = await fetch(S.API + '/ingestion/' + slug, {method: 'DELETE'});
        if (!r.ok) throw new Error('HTTP ' + r.status);

        // Clear Step 3 if the deleted framework was the one being viewed.
        if (S.activeSlug === slug) {
          S.setActiveSlug(null);
          if (S.pageGrid) S.pageGrid.innerHTML =
            '<div class="fw-empty">Pick an item from the sidebar or ' +
            'generate a new study.</div>';
          if (S.pagesSummary) S.pagesSummary.innerHTML = '';
        }
        // Remove the row in place — snappier than a full library reload.
        row.remove();
        if (S.sidebarList.querySelectorAll('.fw-lib-item').length === 0) {
          S.sidebarList.innerHTML =
            '<div class="fw-sidebar-empty">' +
            'No ingested frameworks yet. Pick one in the catalog and ' +
            'click Start Ingestion.' +
            '</div>';
        }
        syncStepLocks();   // library may now be empty → lock Steps 2+3
      } catch (e) {
        // Restore on failure so the user can try again.
        b.innerHTML = originalLabel;
        b.removeAttribute('disabled');
        if (refresh) refresh.removeAttribute('disabled');
        row.style.pointerEvents = '';
        row.style.opacity = '';
        showToast('Delete failed: ' + String(e));
      }
    });
  });
}

export async function loadLibrary() {
  try {
    const r = await fetch(S.API + '/ingestion');
    if (!r.ok) { renderSidebar([]); syncStepLocks(); return; }
    renderSidebar(await r.json());
  } catch (e) {
    renderSidebar([]);
  }
  syncStepLocks();   // unlock/lock Steps 2+3 based on library presence
}

// ============================================================
// Page-reload recovery — restore active-ingestion state from Redis.
// ============================================================
// Without this, refreshing the page mid-ingestion wipes the in-memory
// activeRunId/activeSlug → the loading box vanishes and the user can
// re-click Start Ingestion (which the backend single-flight lock would
// deny with "locked", but the UX is jarring). With this, the UI
// re-attaches to any still-running run on page load: resumes polling,
// restores the progress display, blocks the Generate button.
export async function recoverActiveRuns() {
  try {
    const r = await fetch(S.API + '/runs/active');
    if (!r.ok) return;
    const data = await r.json();
    const runs = data.active || [];
    if (runs.length === 0) return;
    // Resume the first active run (single-flight lock is per-slug so
    // multiple concurrent runs across different slugs are theoretically
    // possible; we surface the first one — the others remain protected
    // by their own locks, user will see them when they finish).
    const run = runs[0];
    S.setActiveSlug(run.slug);
    S.setActiveRunId(run.run_id);
    S.setFarthestStep(Math.max(S.farthestStep, 2));
    refreshGenerateState();   // disables Start + sidebar refresh/delete
    showStep(2);              // reveal the live progress box
    setProgressFramework(run.slug);
    // Paint the last-known progress immediately so the UI is populated
    // before the first poll tick lands.
    if (run.progress) renderProgress(run.progress);
    pollRun(run.run_id);      // resume the poll loop
    showNotice(
      'Resumed in-flight ingestion of ' + run.slug + ' (started ' +
      fmtAge(run.progress?.updated_at) + ').'
    );
  } catch (e) { /* silent — nothing to recover */ }
}

// Page-load auto-resume for planner runs. Mirrors recoverActiveRuns
// (ingestion side) but driven by localStorage instead of a backend
// active-runs endpoint, because the planner's active thread_id is
// generated client-side. Activates the most recent slug with a
// surviving /state so a plain page reload (no framework click)
// restores the cached substep cards.
export async function recoverActivePlanner() {
  // Page-load behaviour (per user UX rule): NEVER auto-activate a
  // framework on reload — the user lands on Catalog (Step 1) and
  // must click a library item to pick a framework. The previous
  // behaviour (auto-pick the first cached slug + jump to Step 3)
  // was confusing because the sidebar wouldn't show any item as
  // active even though the Planner panel had data.
  //
  // This function now ONLY hydrates the planner localStorage from
  // the server-side /planner/recent endpoint (useful for browsers
  // that wipe localStorage like Brave / Safari private mode). The
  // hydrated entries make _tryResumeActivePlanner(slug) work later
  // when the user explicitly clicks a library item.
  if (S.activeSlug) return;     // some other path already activated
  const keys = [];
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && k.startsWith('dd:planner:active:')) keys.push(k);
    }
  } catch (e) { return; }
  if (keys.length) return;   // localStorage already populated; nothing to do
  // localStorage empty — try to seed it from the server's recent list.
  try {
    const r = await fetch(S.API + '/planner/recent');
    if (!r.ok) return;
    const data = await r.json();
    const recent = (data && data.recent) || [];
    for (const item of recent) {
      try {
        localStorage.setItem(_plannerStorageKey(item.slug), item.thread_id);
      } catch (e) {}
    }
    if (recent.length) {
      try { localStorage.setItem(S._LAST_PLANNER_SLUG_KEY, recent[0].slug); }
      catch (e) {}
    }
  } catch (e) {
    console.warn('[planner-recover] /planner/recent failed:', e);
  }
}

export async function loadPlannerInfo() {
  try {
    const r = await fetch(S.API + '/planner/info');
    if (!r.ok) return;
    const data = await r.json();
    S.setPlannerImplemented(new Set(data.implemented || []));
    // Mode dropdown removed 2026-05-18 — the unified LITA-pattern
    // planner is the only mode now (see PLANNER-ARCHITECTURE-2026-05-17
    // .md). Server still returns `modes` for backwards compatibility
    // but the client no longer renders the picker.
    // Re-render the cards now that we know which are implemented vs
    // future — turns unimplemented stubs into the "⏳ future" state.
    const { renderPlannerCards } = await import('./planner.js');
    renderPlannerCards({});
  } catch (e) { /* silent — defaults to all "pending" */ }
}

// Helper — planner localStorage key (mirrors the monolith's
// _plannerStorageKey). Used by recoverActivePlanner to seed
// localStorage from the server's recent list.
function _plannerStorageKey(slug) {
  return 'dd:planner:active:' + slug;
}
