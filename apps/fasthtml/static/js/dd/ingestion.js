// ============================================================
// ingestion.js — Ingestion/progress: renderProgress, pollRun,
//   renderManifest*, loadManifestForSlug, triggerIngest
// ============================================================

import * as S from './state.js';
import { sleep, fmtBytes, fmtAge } from './utils.js';
import {
  showNotice, hideNotice, showToast, hideToast,
  refreshGenerateState, jumpTo, showStep, renderStepper,
  syncStepLocks,
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
  if (gridEl) gridEl.innerHTML = m.entries.map(e =>
    '<div class="fw-page-card" data-idx="' + e.idx + '">' +
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

export async function loadManifestForSlug(slug) {
  S.setActiveSlug(slug);
  // Page-refresh recovery for the planner step.
  const { _tryResumeActivePlanner } = await import('./planner.js');
  _tryResumeActivePlanner(slug).catch(() => {});
  // Same per-slug recovery for the synth step (Step 4).
  const { _tryResumeActiveSynth } = await import('./synth.js');
  _tryResumeActiveSynth(slug).catch(() => {});
  // If the user switches frameworks while ALREADY on the Study stage,
  // the showStep(5) navigation hook won't fire — so refresh the Study
  // view in place.
  if (S.currentStep === 5) {
    const study = await import('./study.js');
    study.setStudyFramework(slug);
    study.refreshStudyVisibility();
    if (slug !== S.studyLoadedSlug) study.loadStudyChapters(slug);
  }
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
        S.setActiveRunId(null);
        refreshGenerateState();
        await loadManifestForSlug(S.activeSlug);
        const { loadLibrary } = await import('./library.js');
        await loadLibrary();
        // Stay on Step 2 (Ingestion) so the user sees the just-populated
        // file grid instead of auto-advancing to Planner. The stepper
        // unlocks Step 3+ via loadLibrary → syncStepLocks; the user
        // clicks through manually when they're ready.
        S.progressBox.style.display = 'none';   // hide the 100% progress bar
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
  triggerIngest(S.selected, false);
});
