// shared/library/recovery.js — page-load recovery paths for in-flight
// ingestion + planner runs. Extracted from library.js Step 7
// (2026-06-05). Self-contained: no cross-refs back to library.js or
// renderSidebar. Imports state + nav helpers same as the original.
import * as Sa from '@dd/shared/state/api.js';
import * as Sc from '@dd/shared/state/catalog.js';
import * as Si from '@dd/shared/state/ingestion.js';
import * as Sp from '@dd/shared/state/planner.js';
import { fmtAge } from '../utils.js';
import { showNotice, refreshGenerateState } from '../ui.js';
import { updatePickerTrigger, setProgressFramework } from '@dd/catalog/picker.js';
import { renderProgress, pollRun } from '@dd/ingestion/polling.js';
import { _plannerStorageKey } from '@dd/planner/shared.js';

export async function recoverActiveRuns() {
  try {
    const r = await fetch(Sa.API + '/runs/active');
    if (!r.ok) return;
    const data = await r.json();
    const runs = data.active || [];
    if (runs.length === 0) return;
    // Resume the first active run (single-flight lock is per-slug so
    // multiple concurrent runs across different slugs are theoretically
    // possible; we surface the first one — the others remain protected
    // by their own locks, user will see them when they finish).
    const run = runs[0];
    Si.setActiveSlug(run.slug);
    Si.setActiveRunId(run.run_id);
    refreshGenerateState();   // disables Start + sidebar refresh/delete
    // Paint the header `Library ▾` button with the recovered slug's
    // display name + logo. Server-side render didn't get a slug in
    // the URL (user clicked the Ingestion nav tab, not the link from
    // Catalog with `?slug=...`), so without this the button keeps
    // showing the placeholder "Library" while the sidebar + progress
    // box correctly reflect the in-flight framework.
    updatePickerTrigger(run.slug).catch(() => {});
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
  if (Si.activeSlug) return;     // some other path already activated
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
    const r = await fetch(Sa.API + '/planner/recent');
    if (!r.ok) return;
    const data = await r.json();
    const recent = (data && data.recent) || [];
    for (const item of recent) {
      try {
        localStorage.setItem(_plannerStorageKey(item.slug), item.thread_id);
      } catch (e) {}
    }
    if (recent.length) {
      try { localStorage.setItem(Sp._LAST_PLANNER_SLUG_KEY, recent[0].slug); }
      catch (e) {}
    }
  } catch (e) {
    console.warn('[planner-recover] /planner/recent failed:', e);
  }
}

