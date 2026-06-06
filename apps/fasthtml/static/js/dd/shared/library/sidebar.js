// shared/library/sidebar.js — `renderSidebar(items)` — the framework
// library list rendered in the topbar dropdown + the framework picker
// popover. Extracted from library.js Step 2 (2026-06-05 follow-up).
// loadLibrary imports it back; recovery + planner-info don't touch it.
// ============================================================
// library.js — Sidebar library list, page-reload recovery for
//   active ingestion & planner runs, planner info bootstrap
// ============================================================

// Relative paths are scoped to this file's location at
// `shared/library/sidebar.js` — same-directory siblings (utils, ui, nav)
// live at `shared/` so they're `../`, NOT `./`. Cross-package refs use
// the importmap aliases (`@dd/...`) — same style as `library/recovery.js`.
import * as Sa from '@dd/shared/state/api.js';
import * as Sc from '@dd/shared/state/catalog.js';
import * as Si from '@dd/shared/state/ingestion.js';
import * as Sp from '@dd/shared/state/planner.js';
import { fmtAge, fmtBytes } from '../utils.js';
import {
  showNotice, showToast, showConfirm, refreshGenerateState,
  fetchPipelineState, cascadeImpactText,
} from '../ui.js';
import {
  ensureFrameworkInfo,
  setProgressFramework,
  updatePickerTrigger,
} from '@dd/catalog/picker.js';
import {
  renderManifest, loadManifestForSlug, renderProgress, pollRun,
  triggerIngest,
} from '@dd/ingestion/ingestion.js';
import { currentStage, navigateToStage } from '../nav.js';

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
    Si.sidebarList.querySelectorAll('.fw-lib-refresh, .fw-lib-delete')
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
  // module-load cached Si.sidebarList. The picker popover (which
  // hosts #fw-sidebar-list) is rendered in the title row and is
  // guaranteed to exist on every DD page, but re-querying makes
  // this resilient to any future restructuring.
  const list = document.querySelector('#fw-sidebar-list') || Si.sidebarList;
  if (!list) return;
  // Defensive: accept only arrays. A backend error envelope
  // (e.g. {"detail": "..."}) would otherwise NOT trip the empty
  // branch (no .length === 0) but WOULD throw on .map() below.
  if (!Array.isArray(items)) items = [];
  // Augment frameworkInfo from the library list so recovery + sidebar
  // clicks can label the loading box even for frameworks that aren't
  // in the catalog tile set (or were ingested via the audit endpoint).
  items.forEach(it => {
    if (it && it.slug && !Si.frameworkInfo[it.slug]) {
      // Prefer `logos` array from the catalog (multi-logo stack);
      // fall back to the single `logo` for everyday entries.
      const logos = (it.logos && it.logos.length)
        ? it.logos
        : (it.logo ? [it.logo] : []);
      Si.frameworkInfo[it.slug] = {
        name: it.framework_name || it.slug,
        logos,
      };
    }
  });
  // In-progress pseudo-entry: when a run is active AND its slug isn't
  // in the finalized library list yet, prepend a placeholder row so the
  // user can navigate back into the Ingestion page from any stage
  // without losing track of what's being extracted. No refresh /
  // delete buttons (you can't refresh a running run; deleting mid-run
  // would leak the lock). Click routes to /ingestion regardless of
  // current stage so the live progress box is visible. The pseudo-
  // entry disappears naturally when loadLibrary() refreshes after the
  // run finalizes — the slug then exists in `items` and the real
  // entry replaces it. We kick off ensureFrameworkInfo before
  // building the markup so the placeholder shows the catalog display
  // name + logo instead of the raw slug.
  let pseudo = '';
  if (Si.activeRunId && Si.activeSlug &&
      !items.some(it => it && it.slug === Si.activeSlug)) {
    const slug = Si.activeSlug;
    // Fire-and-forget hydration: if cache is empty the first render
    // shows the slug, then a re-render after resolve fills in the name.
    // We re-render by calling renderSidebar(items) once the promise
    // resolves — items hasn't mutated so this is cheap.
    if (!Si.frameworkInfo[slug] || Si.frameworkInfo[slug].name === slug) {
      ensureFrameworkInfo(slug).then(info => {
        if (info && info.name && info.name !== slug) renderSidebar(items);
      }).catch(() => {});
    }
    const info = Si.frameworkInfo[slug] || {name: slug, logos: []};
    const logoUrl = info.logos && info.logos.length ? info.logos[0] : '';
    const logo = logoUrl
      ? '<img class="fw-lib-logo" src="' + logoUrl + '" alt="">'
      : '';
    pseudo = '<div class="fw-lib-item fw-lib-item-ingesting active"' +
      ' data-slug="' + slug + '">' +
      logo +
      '<div style="flex:1;min-width:0">' +
      '<div class="fw-lib-name">' + (info.name || slug) + '</div>' +
      '<div class="fw-lib-meta">' +
      '<span class="fw-spinner fw-lib-spinner"></span>' +
      '<span>Ingesting…</span>' +
      '</div>' +
      '</div>' +
      '</div>';
  }
  if (items.length === 0 && !pseudo) {
    list.innerHTML =
      '<div class="fw-sidebar-empty">' +
      'No ingested frameworks yet. Pick one in the catalog and click Start Ingestion.' +
      '</div>';
    return;
  }
  const html = items.map(it => {
    const isActive = (it.slug === Si.activeSlug) ? ' active' : '';
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
  list.innerHTML = pseudo + html;
  list.querySelectorAll('.fw-lib-item').forEach(el => {
    el.addEventListener('click', ev => {
      if (ev.target.closest('.fw-lib-refresh, .fw-lib-delete')) return;
      const slug = el.dataset.slug;
      // The in-progress pseudo-entry has no real corpus yet, so the
      // only useful destination is the Ingestion page where its
      // progress box lives — override stage-route logic.
      if (el.classList.contains('fw-lib-item-ingesting')) {
        navigateToStage('ingestion', slug, Si.activeRunId || undefined);
        return;
      }
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

      // Probe what's actually cached downstream so the confirm dialog
      // tells the user the real cascade impact. The DELETE /ingestion
      // backend endpoint already wipes planner + synth + study server-
      // side; we don't need to call them separately, only to LABEL the
      // user-visible cascade accurately.
      const state = await fetchPipelineState(slug);
      const cascade = cascadeImpactText(state, 'ingestion');
      const ok = await showConfirm(
        'Delete framework',
        'Permanently delete "' + displayName + '"? Full wipe — removes ' +
        'the ingested corpus, raw monolith, and synth vault sentinels.' +
        cascade + ' This cannot be undone.',
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
        const r = await fetch(Sa.API + '/ingestion/' + slug, {method: 'DELETE'});
        if (!r.ok) throw new Error('HTTP ' + r.status);

        // Reset every step's per-slug view to its initial empty state when
        // the deleted framework was the one being viewed. The user lands
        // on the "pick a framework" message exactly as if nothing was
        // ever selected — same effect as a fresh page load with the
        // sidebar item gone.
        if (Si.activeSlug === slug) {
          Si.setActiveSlug(null);
          // Step 2 (Ingestion) — the file grid the user is most likely
          // looking at when they delete.
          if (Si.step2Summary) Si.step2Summary.innerHTML = '';
          if (Si.step2Grid) Si.step2Grid.innerHTML =
            '<div class="fw-empty">Pick a framework in the catalog or ' +
            'the sidebar to see its downloaded files.</div>';
          // Hide the live progress box if a previous run left it open.
          if (Si.progressBox) Si.progressBox.style.display = 'none';
          // Legacy Step 3 page-grid (element may be absent post 2026-05-19
          // Planner canvas swap — guards no-op when missing).
          if (Si.pageGrid) Si.pageGrid.innerHTML =
            '<div class="fw-empty">Pick an item from the sidebar or ' +
            'generate a new study.</div>';
          if (Si.pagesSummary) Si.pagesSummary.innerHTML = '';
          // Drop any sidebar "active" highlight — the deleted row is
          // about to disappear, but other rows shouldn't linger as
          // selected for a slug that no longer exists.
          Si.sidebarList.querySelectorAll('.fw-lib-item.active')
            .forEach(x => x.classList.remove('active'));
          // Reset Planner + Synth canvases to their "pick a framework"
          // empty state. Dynamic import keeps this off the library.js
          // module-load critical path AND avoids a circular dep with
          // planner.js (which imports from library.js).
          try {
            const { _toggleStageEmpty } = await import('@dd/planner/planner.js');
            _toggleStageEmpty('planner', true);
            _toggleStageEmpty('synth', true);
          } catch (_) { /* canvases may not be initialized — safe to skip */ }
        }
        // Remove the row in place — snappier than a full library reload.
        row.remove();
        if (Si.sidebarList.querySelectorAll('.fw-lib-item').length === 0) {
          Si.sidebarList.innerHTML =
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

