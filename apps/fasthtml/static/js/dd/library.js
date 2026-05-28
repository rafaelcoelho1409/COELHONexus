// ============================================================
// library.js — Sidebar library list, page-reload recovery for
//   active ingestion & planner runs, planner info bootstrap
// ============================================================

import * as S from './state.js';
import { fmtAge, fmtBytes } from './utils.js';
import {
  showNotice, showToast, showConfirm, refreshGenerateState,
} from './ui.js';
import { setProgressFramework } from './picker.js';
import {
  renderManifest, loadManifestForSlug, renderProgress, pollRun,
  triggerIngest,
} from './ingestion.js';
import { currentStage, navigateToStage } from './nav.js';

// ============================================================
// Sidebar action lock — disable every refresh + delete button while ANY
// action (refresh OR delete on any row) is in flight. Without this guard
// a user can fire a second delete mid-DELETE, leak Redis locks, or kick
// off concurrent ingestions for two different slugs. Unlock defers to
// refreshGenerateState so the activeRunId-based lock takes over once a
// queued ingestion is running.
// ============================================================
function _setSidebarActionsLocked(locked) {
  if (locked) {
    S.sidebarList.querySelectorAll('.fw-lib-refresh, .fw-lib-delete')
      .forEach(b => b.setAttribute('disabled', 'disabled'));
  } else {
    // Hand the final state to refreshGenerateState, which keeps buttons
    // disabled while activeRunId is set and re-enables them otherwise.
    refreshGenerateState();
  }
}

// ============================================================
// Sidebar — library list
// ============================================================
export function renderSidebar(items) {
  // Defensive: re-query the list element instead of trusting the
  // module-load cached S.sidebarList. The picker popover (which
  // hosts #fw-sidebar-list) is rendered in the title row and is
  // guaranteed to exist on every DD page, but re-querying makes
  // this resilient to any future restructuring.
  const list = document.querySelector('#fw-sidebar-list') || S.sidebarList;
  if (!list) return;
  // Defensive: accept only arrays. A backend error envelope
  // (e.g. {"detail": "..."}) would otherwise NOT trip the empty
  // branch (no .length === 0) but WOULD throw on .map() below.
  if (!Array.isArray(items)) items = [];
  // Augment frameworkInfo from the library list so recovery + sidebar
  // clicks can label the loading box even for frameworks that aren't
  // in the catalog tile set (or were ingested via the audit endpoint).
  items.forEach(it => {
    if (it && it.slug && !S.frameworkInfo[it.slug]) {
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
  if (items.length === 0) {
    list.innerHTML =
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
  list.innerHTML = html;
  list.querySelectorAll('.fw-lib-item').forEach(el => {
    el.addEventListener('click', ev => {
      if (ev.target.closest('.fw-lib-refresh, .fw-lib-delete')) return;
      const slug = el.dataset.slug;
      // Stage-route navigation: clicking a library item keeps the user
      // on their current stage but swaps the framework. From Catalog
      // (no slug context) we jump to Planner — the natural next step
      // after picking an ingested framework.
      const here = currentStage();
      const dest = (here === 'catalog') ? 'planner' : here;
      navigateToStage(dest, slug);
    });
  });
  list.querySelectorAll('.fw-lib-refresh').forEach(b => {
    b.addEventListener('click', async ev => {
      ev.stopPropagation();
      // Lock all sidebar actions + swap the ↻ icon with a spinner so the
      // user has unambiguous "we're working on it" feedback. The lock
      // covers the window between this click and POST /runs returning;
      // after that, refreshGenerateState (called inside triggerIngest)
      // keeps the lock based on activeRunId for the rest of the run.
      const originalLabel = b.innerHTML;
      b.innerHTML = '<div class="fw-spinner"></div>';
      _setSidebarActionsLocked(true);
      try {
        await triggerIngest(b.dataset.slug, true);
      } finally {
        // Restore the icon either way. The disabled state is now
        // governed by activeRunId via refreshGenerateState — keeps the
        // lock during a queued ingestion, releases it on cached/locked/
        // error (no active run).
        b.innerHTML = originalLabel;
        _setSidebarActionsLocked(false);
      }
    });
  });
  // Newly-rendered refresh buttons must pick up the current ingest state
  // (a re-render from loadLibrary() during an active run would otherwise
  // give them a fresh enabled state).
  refreshGenerateState();
  list.querySelectorAll('.fw-lib-delete').forEach(b => {
    b.addEventListener('click', async ev => {
      ev.stopPropagation();
      const slug = b.dataset.slug;
      const row = b.closest('.fw-lib-item');
      const displayName = row.querySelector('.fw-lib-name')?.textContent || slug;

      const ok = await showConfirm(
        'Delete framework',
        'Permanently delete "' + displayName + '"? Full wipe — removes ' +
        'the ingested corpus, raw monolith, synth vault sentinels, and ' +
        'any planner/synth artifacts for this framework. ' +
        'This cannot be undone.',
        'Delete'
      );
      if (!ok) return;

      // Replace 🗑 with spinner + lock EVERY sidebar action button across
      // every row (not just this one) so a stray click can't fire a
      // second DELETE / refresh while this one is in flight.
      const originalLabel = b.innerHTML;
      b.innerHTML = '<div class="fw-spinner"></div>';
      _setSidebarActionsLocked(true);
      row.style.pointerEvents = 'none';
      row.style.opacity = '0.7';

      try {
        const r = await fetch(S.API + '/ingestion/' + slug, {method: 'DELETE'});
        if (!r.ok) throw new Error('HTTP ' + r.status);

        // Reset every step's per-slug view to its initial empty state when
        // the deleted framework was the one being viewed. The user lands
        // on the "pick a framework" message exactly as if nothing was
        // ever selected — same effect as a fresh page load with the
        // sidebar item gone.
        if (S.activeSlug === slug) {
          S.setActiveSlug(null);
          // Step 2 (Ingestion) — the file grid the user is most likely
          // looking at when they delete.
          if (S.step2Summary) S.step2Summary.innerHTML = '';
          if (S.step2Grid) S.step2Grid.innerHTML =
            '<div class="fw-empty">Pick a framework in the catalog or ' +
            'the sidebar to see its downloaded files.</div>';
          // Hide the live progress box if a previous run left it open.
          if (S.progressBox) S.progressBox.style.display = 'none';
          // Legacy Step 3 page-grid (element may be absent post 2026-05-19
          // Planner canvas swap — guards no-op when missing).
          if (S.pageGrid) S.pageGrid.innerHTML =
            '<div class="fw-empty">Pick an item from the sidebar or ' +
            'generate a new study.</div>';
          if (S.pagesSummary) S.pagesSummary.innerHTML = '';
          // Drop any sidebar "active" highlight — the deleted row is
          // about to disappear, but other rows shouldn't linger as
          // selected for a slug that no longer exists.
          S.sidebarList.querySelectorAll('.fw-lib-item.active')
            .forEach(x => x.classList.remove('active'));
          // Reset Planner + Synth canvases to their "pick a framework"
          // empty state. Dynamic import keeps this off the library.js
          // module-load critical path AND avoids a circular dep with
          // planner.js (which imports from library.js).
          try {
            const { _toggleStageEmpty } = await import('./planner.js');
            _toggleStageEmpty('planner', true);
            _toggleStageEmpty('synth', true);
          } catch (_) { /* canvases may not be initialized — safe to skip */ }
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
      } catch (e) {
        // Restore on failure so the user can try again. The row stays —
        // only the icon + lock + visual fade get reverted.
        b.innerHTML = originalLabel;
        row.style.pointerEvents = '';
        row.style.opacity = '';
        showToast('Delete failed: ' + String(e));
      } finally {
        // Release the global sidebar lock. On success the row is gone
        // (so nothing to restore on it); on failure the previous catch
        // already restored this row's icon + opacity. Either way, OTHER
        // rows' buttons need their lock dropped.
        _setSidebarActionsLocked(false);
      }
    });
  });
}

export async function loadLibrary() {
  try {
    const r = await fetch(S.API + '/ingestion');
    if (!r.ok) {
      S.setIngestedSlugs(new Set());
      renderSidebar([]); return;
    }
    const items = await r.json();
    // Record which slugs are already ingested so the Catalog tab can
    // green-badge their tiles (markIngestedTiles in picker.js reads
    // this). Always set it — even when the picker list isn't on this
    // page (e.g. the Catalog tab dropped the Library dropdown).
    S.setIngestedSlugs(new Set(
      (Array.isArray(items) ? items : [])
        .map(it => it && it.slug)
        .filter(Boolean)
    ));
    renderSidebar(items);
  } catch (e) {
    S.setIngestedSlugs(new Set());
    renderSidebar([]);
  }
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
    refreshGenerateState();   // disables Start + sidebar refresh/delete
    // pollRun (below) reveals + drives the live progress box on the
    // ingestion page; no stepper navigation needed (per-stage routes).
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
