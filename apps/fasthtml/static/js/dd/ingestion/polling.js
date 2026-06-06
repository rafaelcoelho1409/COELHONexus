// ingestion/polling.js — live progress + SSE / poll loop
// (renderProgress, pollRun, triggerIngest). Extracted from
// ingestion.js Step 8 (2026-06-05). pollRun's done-handler calls
// loadManifestForSlug from manifest.js.
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
import { renderManifest, loadManifestForSlug } from './manifest.js';

export function renderProgress(p) {
  if (!p) return;
  if (!Si.progressTier) return;   // not on the ingestion page — no-op
  Si.progressTier.textContent = p.tier || '—';
  Si.progressStatus.textContent = p.status || '—';
  Si.progressUrl.textContent = p.last_url || '';
  if (p.total && p.total > 0) {
    Si.progressBar.classList.remove('indeterminate');
    const pct = Math.min(100, Math.round((p.current / p.total) * 100));
    Si.progressFill.style.width = pct + '%';
    Si.progressCounter.textContent =
      (p.current || 0) + ' / ' + p.total + ' (' + pct + '%)';
  } else {
    Si.progressBar.classList.add('indeterminate');
    Si.progressFill.style.width = '35%';
    Si.progressCounter.textContent = (p.current || 0) + ' so far…';
  }
}

export async function pollRun(runId) {
  Si.setPollAbort(false);
  Si.setActiveRunId(runId);
  refreshGenerateState();   // disable Generate while this run is in flight
  // Progress UI only exists on the Ingestion page — guard so this
  // function can be safely awaited from other stages (which still
  // need the activeRunId state for the global running-dot indicator).
  if (Si.progressBox) Si.progressBox.style.display = '';
  if (Si.cancelBtn) {
    Si.cancelBtn.disabled = false;
    Si.cancelBtn.innerHTML = 'Cancel ingestion';
  }
  if (Si.activeSlug) setProgressFramework(Si.activeSlug);
  while (!Si.pollAbort && Si.activeRunId === runId) {
    try {
      const r = await fetch(Sa.API + '/runs/' + runId);
      if (r.status === 404) { await sleep(800); continue; }
      const data = await r.json();
      renderProgress(data.progress);
      const st = data.progress?.status;
      if (st === 'done') {
        const completedSlug = Si.activeSlug;
        Si.setActiveRunId(null);
        refreshGenerateState();
        // Hide the progress box IMMEDIATELY when the backend says done.
        // The OLD reference put this after loadManifestForSlug +
        // loadLibrary, but those involve dynamic module imports that
        // can throw inside the outer try/catch (transient — retry),
        // swallowing the throw and leaving the box visible forever.
        // Hiding first guarantees the user sees the transition even
        // if a downstream import is slow or fails.
        if (Si.progressBox) Si.progressBox.style.display = 'none';
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
          try { await loadManifestForSlug(completedSlug); }
          catch (e) { console.warn('[pollRun done] loadManifest failed:', e); }
        }
        try {
          const { loadLibrary } = await import('@dd/shared/library.js');
          await loadLibrary();
        } catch (e) { console.warn('[pollRun done] loadLibrary failed:', e); }
        // Stay on Step 2 (Ingestion) so the user sees the just-populated
        // file grid instead of auto-advancing to Planner. The stepper
        // unlocks Step 3+ via loadLibrary → syncStepLocks; the user
        // clicks through manually when they're ready.
        try {
          const { refreshPlannerStartState } = await import('@dd/planner/planner.js');
          refreshPlannerStartState();             // enable Step 3 Start button
        } catch (e) { console.warn('[pollRun done] planner import failed:', e); }
        return;
      }
      if (st === 'failed' || st === 'cancelled') {
        const cancelledSlug = Si.activeSlug;
        Si.setActiveRunId(null);
        refreshGenerateState();
        // Hide the live progress box + restore Step 2 + Step 4 to their
        // initial pick-a-framework state.
        Si.progressBox.style.display = 'none';
        Si.step2Summary.innerHTML = '';
        Si.step2Grid.innerHTML =
          '<div class="fw-empty">Pick a framework in the catalog or ' +
          'the sidebar to see its downloaded files.</div>';
        if (Si.activeSlug === cancelledSlug) {
          Si.setActiveSlug(null);
          if (Si.pagesSummary) Si.pagesSummary.innerHTML = '';
          if (Si.pageGrid) Si.pageGrid.innerHTML =
            '<div class="fw-empty">Pick an item from the sidebar or ' +
            'generate a new study.</div>';
          Si.sidebarList.querySelectorAll('.fw-lib-item.active')
            .forEach(x => x.classList.remove('active'));
        }
        const { loadLibrary } = await import('@dd/shared/library.js');
        await loadLibrary();
        const { refreshPlannerStartState } = await import('@dd/planner/planner.js');
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

Si.cancelBtn?.addEventListener('click', async () => {
  if (!Si.activeRunId) return;
  Si.cancelBtn.disabled = true;
  Si.cancelBtn.innerHTML =
    '<div class="fw-spinner" style="display:inline-block;' +
    'vertical-align:middle;margin-right:8px"></div>Cancelling…';
  if (Si.progressStatus) Si.progressStatus.textContent = 'cancelling';
  try {
    await fetch(Sa.API + '/runs/' + Si.activeRunId + '/cancel', {method: 'POST'});
  } catch (e) {
    Si.cancelBtn.disabled = false;
    Si.cancelBtn.innerHTML = 'Cancel ingestion';
    showToast('Cancel request failed: ' + String(e));
  }
});

// ============================================================
// POST /runs — Generate / Refresh
// ============================================================
export async function triggerIngest(slug, refresh) {
  hideToast(); hideNotice();
  Si.setActiveSlug(slug);
  try {
    const r = await fetch(Sa.API + '/runs', {
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

Sc.generate?.addEventListener('click', () => {
  if (!Sc.selected) return;
  // Defense in depth — the button is `disabled` via refreshGenerateState
  // while a run is in flight, but a user could remove the attribute via
  // DevTools, and assistive-tech shortcuts can sometimes activate a
  // visually-disabled control. The runtime guard ensures we NEVER
  // POST /runs while activeRunId is set, even if the disabled attr
  // was bypassed; the backend's per-slug single-flight lock catches
  // the same-slug race, but cross-slug double-trigger would slip
  // through without this client gate.
  if (Si.activeRunId) {
    showToast('Another ingestion is running — wait for it to finish.');
    return;
  }
  triggerIngest(Sc.selected, false);
});
