// ============================================================
// ingestion.js — Ingestion/progress: renderProgress, pollRun,
//   renderManifest*, loadManifestForSlug, triggerIngest
// ============================================================

import * as S from './state.js';
import { sleep, fmtBytes, fmtAge } from './utils.js';
import {
  showNotice, hideNotice, showToast, hideToast, refreshGenerateState,
} from './ui.js';
import { setProgressFramework } from './picker.js';
import { navigateToStage } from './nav.js';

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
  S.setCurrentManifestEntries(m.entries);
  if (summaryEl) {
    summaryEl.innerHTML =
      '<span><strong>' + (m.framework_name || S.activeSlug) + '</strong> · ' +
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
  renderManifestTo(S.pagesSummary, S.pageGrid, m);
  renderManifestTo(S.step2Summary, S.step2Grid, m);
}

export async function loadManifestForSlug(slug, opts = {}) {
  // `preserveActiveSlug` lets a caller render a framework's manifest
  // WITHOUT clobbering `S.activeSlug` — used by the Ingestion page when
  // the user opens a DONE framework's file list while a DIFFERENT
  // framework is currently being ingested in the background. The
  // bottom-bar "Ingesting" indicator + global running-dot must keep
  // pointing at the in-flight slug, so `activeSlug` stays where it is.
  const preserveActiveSlug = !!opts.preserveActiveSlug;
  if (!preserveActiveSlug) {
    S.setActiveSlug(slug);
  }
  // Page-refresh recovery for the planner step.
  const { _tryResumeActivePlanner } = await import('./planner.js');
  _tryResumeActivePlanner(slug).catch(() => {});
  // Same per-slug recovery for the synth step (Step 4).
  const { _tryResumeActiveSynth } = await import('./synth.js');
  _tryResumeActiveSynth(slug).catch(() => {});
  try {
    const r = await fetch(S.API + '/ingestion/' + slug + '/manifest');
    if (!r.ok) {
      const msg = '<div class="fw-empty">Manifest fetch failed (HTTP ' +
        r.status + ').</div>';
      if (S.pageGrid) S.pageGrid.innerHTML = msg;
      if (S.step2Grid) S.step2Grid.innerHTML = msg;
      return;
    }
    renderManifest(await r.json());
  } catch (e) {
    const msg = '<div class="fw-empty">' + String(e) + '</div>';
    if (S.pageGrid) S.pageGrid.innerHTML = msg;
    if (S.step2Grid) S.step2Grid.innerHTML = msg;
  }
}

// ============================================================
// Step 2: progress display + polling
// ============================================================
export function renderProgress(p) {
  if (!p) return;
  if (!S.progressTier) return;   // not on the ingestion page — no-op
  S.progressTier.textContent = p.tier || '—';
  S.progressStatus.textContent = p.status || '—';
  S.progressUrl.textContent = p.last_url || '';
  if (p.total && p.total > 0) {
    S.progressBar.classList.remove('indeterminate');
    const pct = Math.min(100, Math.round((p.current / p.total) * 100));
    S.progressFill.style.width = pct + '%';
    S.progressCounter.textContent =
      (p.current || 0) + ' / ' + p.total + ' (' + pct + '%)';
  } else {
    S.progressBar.classList.add('indeterminate');
    S.progressFill.style.width = '35%';
    S.progressCounter.textContent = (p.current || 0) + ' so far…';
  }
}

export async function pollRun(runId) {
  S.setPollAbort(false);
  S.setActiveRunId(runId);
  refreshGenerateState();   // disable Generate while this run is in flight
  // Progress UI only exists on the Ingestion page — guard so this
  // function can be safely awaited from other stages (which still
  // need the activeRunId state for the global running-dot indicator).
  if (S.progressBox) S.progressBox.style.display = '';
  if (S.cancelBtn) {
    S.cancelBtn.disabled = false;
    S.cancelBtn.innerHTML = 'Cancel ingestion';
  }
  if (S.activeSlug) setProgressFramework(S.activeSlug);
  while (!S.pollAbort && S.activeRunId === runId) {
    try {
      const r = await fetch(S.API + '/runs/' + runId);
      if (r.status === 404) { await sleep(800); continue; }
      const data = await r.json();
      renderProgress(data.progress);
      const st = data.progress?.status;
      if (st === 'done') {
        const completedSlug = S.activeSlug;
        S.setActiveRunId(null);
        refreshGenerateState();
        // Only refresh the file grid if the user is currently viewing
        // the framework that just completed. If they navigated to
        // another framework's view while this ingestion ran in the
        // background, don't clobber that view with the just-completed
        // framework's manifest.
        let urlSlug = null;
        try {
          urlSlug = new URL(window.location.href).searchParams.get('slug');
        } catch (_) {}
        if (!urlSlug || urlSlug === completedSlug) {
          await loadManifestForSlug(completedSlug);
        }
        const { loadLibrary } = await import('./library.js');
        await loadLibrary();
        // Stay on Step 2 (Ingestion) so the user sees the just-populated
        // file grid instead of auto-advancing to Planner. The stepper
        // unlocks Step 3+ via loadLibrary → syncStepLocks; the user
        // clicks through manually when they're ready.
        if (S.progressBox) S.progressBox.style.display = 'none';
        const { refreshPlannerStartState } = await import('./planner.js');
        refreshPlannerStartState();             // enable Step 3 Start button
        return;
      }
      if (st === 'failed' || st === 'cancelled') {
        const cancelledSlug = S.activeSlug;
        S.setActiveRunId(null);
        refreshGenerateState();
        // Hide the live progress box + restore Step 2 + Step 4 to their
        // initial pick-a-framework state.
        S.progressBox.style.display = 'none';
        S.step2Summary.innerHTML = '';
        S.step2Grid.innerHTML =
          '<div class="fw-empty">Pick a framework in the catalog or ' +
          'the sidebar to see its downloaded files.</div>';
        if (S.activeSlug === cancelledSlug) {
          S.setActiveSlug(null);
          if (S.pagesSummary) S.pagesSummary.innerHTML = '';
          if (S.pageGrid) S.pageGrid.innerHTML =
            '<div class="fw-empty">Pick an item from the sidebar or ' +
            'generate a new study.</div>';
          S.sidebarList.querySelectorAll('.fw-lib-item.active')
            .forEach(x => x.classList.remove('active'));
        }
        const { loadLibrary } = await import('./library.js');
        await loadLibrary();
        const { refreshPlannerStartState } = await import('./planner.js');
        refreshPlannerStartState();
        showToast('Ingestion ' + st + '. ' +
          (st === 'cancelled' ? 'Partial pages cleared from storage.' : ''));
        return;
      }
    } catch (e) {
      // transient — retry
    }
    await sleep(1500);
  }
}

S.cancelBtn?.addEventListener('click', async () => {
  if (!S.activeRunId) return;
  S.cancelBtn.disabled = true;
  S.cancelBtn.innerHTML =
    '<div class="fw-spinner" style="display:inline-block;' +
    'vertical-align:middle;margin-right:8px"></div>Cancelling…';
  if (S.progressStatus) S.progressStatus.textContent = 'cancelling';
  try {
    await fetch(S.API + '/runs/' + S.activeRunId + '/cancel', {method: 'POST'});
  } catch (e) {
    S.cancelBtn.disabled = false;
    S.cancelBtn.innerHTML = 'Cancel ingestion';
    showToast('Cancel request failed: ' + String(e));
  }
});

// ============================================================
// POST /runs — Generate / Refresh
// ============================================================
export async function triggerIngest(slug, refresh) {
  hideToast(); hideNotice();
  S.setActiveSlug(slug);
  try {
    const r = await fetch(S.API + '/runs', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({slug: slug, refresh: !!refresh}),
    });
    const data = await r.json();
    if (data.status === 'cached') {
      // Cached → the corpus already lives in MinIO; jump to Synth so
      // the user can run/inspect chapter synthesis. The Synth page's
      // own bootstrap will load the manifest + chapter state from URL.
      navigateToStage('synth', slug);
      return;
    }
    if (data.status === 'queued') {
      // Queued → land on the Ingestion stage carrying the run_id, so
      // the destination page can resume polling without a separate
      // /runs/active lookup.
      navigateToStage('ingestion', slug, data.run_id);
      return;
    }
    if (data.status === 'locked') {
      showToast(data.message || 'Another ingestion is already running for this framework.');
      return;
    }
    showToast('Unexpected response: ' + JSON.stringify(data));
  } catch (e) {
    showToast('Request failed: ' + String(e));
  }
}

S.generate?.addEventListener('click', () => {
  if (!S.selected) return;
  // Defense in depth — the button is `disabled` via refreshGenerateState
  // while a run is in flight, but a user could remove the attribute via
  // DevTools, and assistive-tech shortcuts can sometimes activate a
  // visually-disabled control. The runtime guard ensures we NEVER
  // POST /runs while activeRunId is set, even if the disabled attr
  // was bypassed; the backend's per-slug single-flight lock catches
  // the same-slug race, but cross-slug double-trigger would slip
  // through without this client gate.
  if (S.activeRunId) {
    showToast('Another ingestion is running — wait for it to finish.');
    return;
  }
  triggerIngest(S.selected, false);
});
