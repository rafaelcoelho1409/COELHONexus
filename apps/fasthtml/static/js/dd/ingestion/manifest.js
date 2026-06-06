// ingestion/manifest.js — manifest rendering + fetch (renderManifestTo,
// renderManifest, loadManifestForSlug). Extracted from ingestion.js
// Step 8 (2026-06-05). Self-contained — no cross-refs to progress /
// pollRun / triggerIngest. polling.js imports loadManifestForSlug back.
// ============================================================
// ingestion.js — Ingestion/progress: renderProgress, pollRun,
//   renderManifest*, loadManifestForSlug, triggerIngest
// ============================================================

import * as Sa from '@dd/shared/state/api.js';
import * as Sc from '@dd/shared/state/catalog.js';
import * as Si from '@dd/shared/state/ingestion.js';
import * as So from '@dd/shared/state/overlays.js';
import { sleep, fmtBytes, fmtAge } from '../shared/utils.js';
import {
  showNotice, hideNotice, showToast, hideToast, refreshGenerateState,
} from '../shared/ui.js';
import { setProgressFramework } from '../catalog/picker.js';
import { navigateToStage } from '../shared/nav.js';

// ============================================================
// Step 3: render manifest entries into the page grid
// ============================================================

export function renderManifestTo(summaryEl, gridEl, m) {
  // gridEl may be null — Step 3 became the Planner (page grid removed
  // 2026-05-19), so `#fw-page-grid` no longer exists. Guard both targets
  // so a missing element silently no-ops instead of throwing (which would
  // abort the Step 2 render that follows in renderManifest).
  if (!m || !m.entries) {
    if (gridEl) gridEl.innerHTML =
      '<div class="fw-empty">Manifest unavailable.</div>';
    if (summaryEl) summaryEl.innerHTML = '';
    return;
  }
  // Track the current entry list so the drawer's prev/next + click
  // delegation walk the same list the user is looking at.
  So.setCurrentManifestEntries(m.entries);
  if (summaryEl) {
    summaryEl.innerHTML =
      '<span><strong>' + (m.framework_name || Si.activeSlug) + '</strong> · ' +
      (m.entries.length) + ' pages · ' + fmtBytes(m.total_bytes || 0) + '</span>' +
      '<span>' + (m.tier_kind || '') + ' · ' + fmtAge(m.ingested_at) + '</span>';
  }
  // `data-idx` MUST be the ARRAY POSITION in `m.entries`, NOT the
  // entry's stored `e.idx` field. The two diverged once `reorder_by_
  // url_list` shipped (Tier 2/3/4 fetch in completion order but the
  // manifest is sorted by URL post-fetch — entries.array_position !=
  // entry.idx). The drawer's openDrawer(idx) + drawerStep(±1) +
  // currentManifestEntries[idx] semantics all assume array position;
  // study.js's source-index map (study.js:_ensureSourceIndex) also
  // stores array positions — using `i` keeps every caller aligned.
  // The entry's storage idx is still available as `e.idx` inside the
  // drawer (renderDrawerContent reads it for the `/pages/{e.idx}`
  // fetch URL), so backend addressing is unaffected.
  if (gridEl) gridEl.innerHTML = m.entries.map((e, i) =>
    '<div class="fw-page-card" data-idx="' + i + '">' +
    '<div class="fw-page-title">' + (e.title || e.slug) + '</div>' +
    '<div class="fw-page-meta">' + (e.tier || '') + ' · ' + fmtBytes(e.bytes) + '</div>' +
    '</div>'
  ).join('');
}

// Backward-compat wrapper — historical callers target Step 3.
export function renderManifest(m) {
  renderManifestTo(Si.pagesSummary, Si.pageGrid, m);
  renderManifestTo(Si.step2Summary, Si.step2Grid, m);
}

export async function loadManifestForSlug(slug, opts = {}) {
  // `preserveActiveSlug` lets a caller render a framework's manifest
  // WITHOUT clobbering `Si.activeSlug` — used by the Ingestion page when
  // the user opens a DONE framework's file list while a DIFFERENT
  // framework is currently being ingested in the background. The
  // bottom-bar "Ingesting" indicator + global running-dot must keep
  // pointing at the in-flight slug, so `activeSlug` stays where it is.
  const preserveActiveSlug = !!opts.preserveActiveSlug;
  if (!preserveActiveSlug) {
    Si.setActiveSlug(slug);
  }
  // FETCH + RENDER FIRST — this is the user-visible critical path. The
  // OLD reference (commit f5bff8e) did the planner/synth resume calls
  // BEFORE the fetch. That worked when the modules were flat siblings
  // (every import resolved in the local dir), but in the per-stage
  // layout the dynamic `import('@dd/planner/planner.js')` triggers a
  // fresh fetch of planner.js + all of its transitive deps — and ANY
  // runtime issue in that subtree throws here, aborting the fetch
  // entirely and leaving step2Grid pinned at the "Ingestion in
  // progress" CASE-C placeholder. Renaming this to "render first,
  // resume second" makes the manifest grid robust to module-init
  // failures upstream.
  try {
    const r = await fetch(Sa.API + '/ingestion/' + slug + '/manifest');
    if (!r.ok) {
      const msg = '<div class="fw-empty">Manifest fetch failed (HTTP ' +
        r.status + ').</div>';
      if (Si.pageGrid) Si.pageGrid.innerHTML = msg;
      if (Si.step2Grid) Si.step2Grid.innerHTML = msg;
    } else {
      renderManifest(await r.json());
    }
  } catch (e) {
    const msg = '<div class="fw-empty">' + String(e) + '</div>';
    if (Si.pageGrid) Si.pageGrid.innerHTML = msg;
    if (Si.step2Grid) Si.step2Grid.innerHTML = msg;
  }
  // Best-effort page-refresh recovery for planner + synth. Each dynamic
  // import + invocation is independently guarded so a failure in one
  // can't poison the other or the manifest render above.
  try {
    const { _tryResumeActivePlanner } = await import('@dd/planner/planner.js');
    _tryResumeActivePlanner(slug).catch(() => {});
  } catch (e) { console.warn('[loadManifestForSlug] planner resume failed:', e); }
  try {
    const { _tryResumeActiveSynth } = await import('@dd/synth/synth.js');
    _tryResumeActiveSynth(slug).catch(() => {});
  } catch (e) { console.warn('[loadManifestForSlug] synth resume failed:', e); }
}

// ============================================================
// Step 2: progress display + polling
// ============================================================
