// ============================================================
// library.js — Sidebar library list, page-reload recovery for
//   active ingestion & planner runs, planner info bootstrap
// ============================================================

import * as Sa from '@dd/shared/state/api.js';
import * as Sc from '@dd/shared/state/catalog.js';
import * as Si from '@dd/shared/state/ingestion.js';
import * as Sp from '@dd/shared/state/planner.js';
import { fmtAge, fmtBytes } from './utils.js';
import {
  showNotice, showToast, showConfirm, refreshGenerateState,
  fetchPipelineState, cascadeImpactText,
} from './ui.js';
import {
  ensureFrameworkInfo,
  setProgressFramework,
  updatePickerTrigger,
} from '../catalog/picker.js';
import {
  renderManifest, loadManifestForSlug, renderProgress, pollRun,
  triggerIngest,
} from '../ingestion/ingestion.js';
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
// renderSidebar extracted to ./library/sidebar.js (Step 2,
// 2026-06-05 follow-up). Re-imported + re-exported so loadLibrary
// below + consumers using `import { renderSidebar } from
// '../shared/library.js'` resolve without churn.
export { renderSidebar } from './library/sidebar.js';
import { renderSidebar } from './library/sidebar.js';
export async function loadLibrary() {
  try {
    const r = await fetch(Sa.API + '/ingestion');
    if (!r.ok) {
      Sc.setIngestedSlugs(new Set());
      renderSidebar([]); return;
    }
    const items = await r.json();
    // Record which slugs are already ingested so the Catalog tab can
    // green-badge their tiles (markIngestedTiles in picker.js reads
    // this). Always set it — even when the picker list isn't on this
    // page (e.g. the Catalog tab dropped the Library dropdown).
    Sc.setIngestedSlugs(new Set(
      (Array.isArray(items) ? items : [])
        .map(it => it && it.slug)
        .filter(Boolean)
    ));
    renderSidebar(items);
  } catch (e) {
    Sc.setIngestedSlugs(new Set());
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
// Recovery functions extracted to ./library/recovery.js (Step 7,
// 2026-06-05). Re-exported so main.js's existing
// `import { recoverActiveRuns, recoverActivePlanner } from
// '../shared/library.js'` keeps resolving.
export {
  recoverActiveRuns,
  recoverActivePlanner,
} from './library/recovery.js';
export async function loadPlannerInfo() {
  try {
    const r = await fetch(Sa.API + '/planner/info');
    if (!r.ok) return;
    const data = await r.json();
    Sp.setPlannerImplemented(new Set(data.implemented || []));
    // Mode dropdown removed 2026-05-18 — the unified LITA-pattern
    // planner is the only mode now (see PLANNER-ARCHITECTURE-2026-05-17
    // .md). Server still returns `modes` for backwards compatibility
    // but the client no longer renders the picker.
    // Re-render the cards now that we know which are implemented vs
    // future — turns unimplemented stubs into the "⏳ future" state.
    const { renderPlannerCards } = await import('@dd/planner/planner.js');
    renderPlannerCards({});
  } catch (e) { /* silent — defaults to all "pending" */ }
}

// Helper — planner localStorage key (mirrors the monolith's
// _plannerStorageKey). Used by recoverActivePlanner to seed
// localStorage from the server's recent list.
function _plannerStorageKey(slug) {
  return 'dd:planner:active:' + slug;
}
