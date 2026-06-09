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
import { buildExplorer } from './explorer.js';

// ============================================================
// Manifest render — populates the summary line + drives the split-pane
// explorer (left rail tree + right pane preview). The 2026-06-08
// redesign replaced the flat `#fw-step2-grid` list with the explorer;
// callers can keep using `renderManifest(m)` unchanged.
// ============================================================

function _renderSummary(summaryEl, m) {
  if (!summaryEl) return;
  if (!m || !m.entries) { summaryEl.innerHTML = ''; return; }
  summaryEl.innerHTML =
    '<span><strong>' + (m.framework_name || Si.activeSlug) +
    '</strong> · ' + (m.entries.length) + ' pages · ' +
    fmtBytes(m.total_bytes || 0) + '</span>' +
    '<span>' + (m.tier_kind || '') + ' · ' +
    fmtAge(m.ingested_at) + '</span>';
}

// Back-compat shim. The legacy signature was
// `renderManifestTo(summaryEl, gridEl, m)`; the grid is now driven by
// the explorer, so we only honour the summary half. Callers like
// `planner/lifecycle.js` that imported this no-op on Planner/Synth
// pages (no summary element rendered) and that's intentional.
export function renderManifestTo(summaryEl, _gridElIgnored, m) {
  if (!m || !m.entries) {
    if (summaryEl) summaryEl.innerHTML = '';
    return;
  }
  So.setCurrentManifestEntries(m.entries);
  _renderSummary(summaryEl, m);
}

export function renderManifest(m) {
  // Summary line (legacy ID kept for library/sidebar.js reset code).
  _renderSummary(Si.step2Summary, m);
  // Explorer split-pane: only present on the Ingestion route, so the
  // build is a no-op everywhere else.
  if (document.getElementById('fw-explorer-tree')) {
    buildExplorer(m);
  } else if (m && m.entries) {
    // Non-explorer pages still want the drawer's source-index map for
    // citation pop-ups (Study) — keep currentManifestEntries in sync.
    So.setCurrentManifestEntries(m.entries);
  }
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
