// planner/lifecycle.js — start / cancel / wipe / resume + cards
// rendering + storage-key helpers + cross-stage gating.
//
// Extracted from planner.js Step 6 (2026-06-05 follow-up). All cross-
// refs resolve to already-extracted siblings (graph.js, polling.js,
// drawer.js, renderers.js, shared.js) — no DI needed.
import * as Sa from '@dd/shared/state/api.js';
import * as Sc from '@dd/shared/state/catalog.js';
import * as Si from '@dd/shared/state/ingestion.js';
import * as Sp from '@dd/shared/state/planner.js';
// Sy + _resizeSynthCanvas needed for `_toggleStageEmpty('synth', false)` —
// when initSynth calls this via the planner module, the synth canvas
// needs the same post-reveal Cytoscape re-fit the planner canvas gets.
// Direct imports are safe: state/synth.js is a leaf module and
// synth/canvas.js only imports state/synth.js + ../shared/stagegraph.js
// + ./graph.js (also synth-local) — no cycle back into planner.
import * as Sy from '@dd/shared/state/synth.js';
import { _resizeSynthCanvas } from '../synth/canvas.js';
import { sleep, fmtBytes, fmtAge, escapeHtml, formatFieldValue } from '../shared/utils.js';
import {
  showConfirm, showNotice, showToast, refreshGenerateState,
  fetchPipelineState, cascadeImpactText,
  refreshCrossStageBlocker, crossStageBlockerFor,
} from '../shared/ui.js';
import { loadManifestForSlug, renderManifest } from '../ingestion/ingestion.js';
import {
  startElapsed, stopElapsed, showElapsed, isElapsedRunning,
} from '../shared/timing.js';
import { $activePipeline } from '@nx/stores/pipeline.js';
import {
  _setPlannerStagePill,
  _renderPlannerGraph,
  _refreshOpenPlannerDrawer,
} from './graph.js';
// `refreshPlannerStartState` defined directly in this file (moved from
// planner.js, 2026-06-06). The original split left it in planner.js
// while lifecycle.js + polling.js tried to import it from graph.js (a
// non-existent export). Moving the function HERE eliminates both the
// dead import AND the would-be circular dependency lifecycle ↔ planner:
// all of refreshPlannerStartState's runtime deps (Sc / Si / Sp /
// crossStageBlockerFor / setPlannerFramework / _toggleStageEmpty) are
// already imported above. polling.js → lifecycle.js for this symbol is
// a non-cyclic edge.
import { _resizePlannerCanvas } from './canvas.js';
import { pollPlannerState, _setPlannerRunStartMs } from './polling.js';
import { SUBSTEP_RENDERERS } from './renderers.js';
import { _fieldPresent, _plannerStorageKey } from './shared.js';

export function refreshPlannerStartState() {
  if (!Sp.plannerStartBtn) return;   // not on the planner page
  // Three states for the Start/Cancel button:
  //  - idle, ready    → "Start Planner" enabled
  //  - idle, blocked  → "Start Planner" disabled (no slug, ingest active,
  //                     or no ingested corpus yet)
  //  - running        → button becomes "Cancel Planner" (always enabled
  //                     during a run; same behavior pattern as Step 2's
  //                     ingestion cancel)
  const running = Sp.plannerThreadId !== null;
  if (running) {
    Sp.plannerStartBtn.removeAttribute('disabled');
    Sp.plannerStartBtn.classList.add('btn-outline');
    Sp.plannerStartBtn.classList.remove('btn-primary');
    Sp.plannerStartBtn.innerHTML = 'Stop';
  } else {
    // CORPUS-FIRST GATE — the planner needs an ingested corpus. Mirrors
    // the server-side read_framework_manifest 404 so the disabled button
    // and the API agree. Sc.ingestedSlugs is populated by loadLibrary.
    const hasCorpus = Sc.ingestedSlugs.has(Si.activeSlug);
    // CROSS-STAGE GATE — Planner and Synth must not run simultaneously
    // (LLM-resource contention). When a synth is in flight ANYWHERE
    // (any slug), Start Planner is disabled with an explanatory
    // tooltip; the server enforces this too via POST /planner's
    // locked-response path.
    const blocker = crossStageBlockerFor('planner');
    const ready = Si.activeSlug && Si.activeRunId == null && hasCorpus
                  && !blocker;
    if (ready) {
      Sp.plannerStartBtn.removeAttribute('disabled');
      Sp.plannerStartBtn.removeAttribute('title');
    } else {
      Sp.plannerStartBtn.setAttribute('disabled', 'disabled');
      if (!Si.activeSlug) {
        Sp.plannerStartBtn.setAttribute('title', 'Pick a framework first.');
      } else if (!hasCorpus) {
        Sp.plannerStartBtn.setAttribute('title',
          'Ingest this framework first — the planner needs its corpus.');
      } else if (blocker) {
        Sp.plannerStartBtn.setAttribute('title', blocker.title);
      } else {
        Sp.plannerStartBtn.removeAttribute('title');
      }
    }
    Sp.plannerStartBtn.classList.add('btn-primary');
    Sp.plannerStartBtn.classList.remove('btn-outline');
    Sp.plannerStartBtn.innerHTML = 'Start';
  }
  // Wipe button — enabled whenever a slug is active and no run is
  // currently in flight (wiping mid-run would corrupt LangGraph state).
  if (Sp.plannerWipeBtn) {
    if (Si.activeSlug && !running) {
      Sp.plannerWipeBtn.removeAttribute('disabled');
      Sp.plannerWipeBtn.setAttribute('title',
        "Delete this framework's planner cache " +
        '(MinIO embeddings + Postgres checkpoints + browser state)');
    } else {
      Sp.plannerWipeBtn.setAttribute('disabled', 'disabled');
      Sp.plannerWipeBtn.setAttribute('title', running
        ? 'Cannot wipe while a planner run is in flight.'
        : 'Pick a framework first.');
    }
  }
  // Framework chip — logo(s) + catalog name. Mirrors the Step 2
  // progress framework strip; same `frameworkInfo` source.
  setPlannerFramework(Si.activeSlug);
  // Empty-state placeholder — show "pick a framework" when no slug
  // is active, hide the cards/canvas in that case so the user isn't
  // confused by an inert pipeline UI dangling from prior context.
  _toggleStageEmpty('planner', !Si.activeSlug);
}

export function _toggleStageEmpty(stage, showEmpty) {
  const emptyEl  = document.getElementById('fw-' + stage + '-empty');
  const graphEl  = document.getElementById('fw-' + stage + '-graph');
  if (!emptyEl) return;
  if (showEmpty) {
    emptyEl.style.display = '';
    if (graphEl) graphEl.style.display = 'none';
  } else {
    emptyEl.style.display = 'none';
    if (graphEl) graphEl.style.display = 'flex';
    // Re-fit Cytoscape now that the wrapper has real dimensions.
    if (stage === 'planner' && Sp.plannerGraph) _resizePlannerCanvas();
    if (stage === 'synth'   && Sy.synthGraph)   _resizeSynthCanvas();
  }
}

export function setPlannerFramework(slug) {
  if (!Sp.plannerFwNameEl || !Sp.plannerFwLogosEl) return;
  if (!slug) {
    Sp.plannerFwNameEl.textContent = 'Pick a framework to start.';
    Sp.plannerFwNameEl.classList.add('fw-planner-fw-name-empty');
    Sp.plannerFwLogosEl.innerHTML = '';
    Sp.plannerFwLogosEl.style.display = 'none';
    return;
  }
  const info = Si.frameworkInfo[slug] || {name: slug, logos: []};
  Sp.plannerFwNameEl.textContent = info.name || slug;
  Sp.plannerFwNameEl.classList.remove('fw-planner-fw-name-empty');
  if (info.logos && info.logos.length) {
    Sp.plannerFwLogosEl.innerHTML = info.logos.map(u =>
      '<img class="fw-planner-fw-logo" src="' + u + '" alt="">'
    ).join('');
    Sp.plannerFwLogosEl.style.display = '';
  } else {
    Sp.plannerFwLogosEl.innerHTML = '';
    Sp.plannerFwLogosEl.style.display = 'none';
  }
}

export function cardEl(idx) {
  // Cards DOM removed 2026-05-19. Always null in the new graph-only
  // UI; the cards-rendering loops short-circuit cleanly via
  // `if (!c) continue;` while still calling `_renderPlannerGraph`
  // + `_refreshOpenPlannerDrawer` at the tail.
  if (!Sp.plannerCardsEl) return null;
  return Sp.plannerCardsEl.querySelector(
    '.fw-planner-card[data-idx="' + idx + '"]');
}

export function resetPlannerCards() {
  Sp.PLANNER_SUBSTEP_FIELDS.forEach((_, i) => {
    const c = cardEl(i);
    if (!c) return;
    c.classList.remove('running', 'done', 'failed', 'expanded');
    const icon = c.querySelector('.fw-planner-card-icon');
    icon.textContent = '○';
    icon.dataset.status = 'pending';
    c.querySelector('.fw-planner-card-latency').textContent = '';
    c.querySelector('.fw-planner-card-body').innerHTML =
      '<div class="fw-empty">Output will appear here once the substep runs.</div>';
  });
  // Day 2: also reset the Cytoscape canvas + stage pill so a fresh
  // Start Planner click presents a clean visual baseline.
  if (Sp.plannerGraph) Sp.plannerGraph.reset();
  _setPlannerStagePill('idle');
}

export function renderPlannerCards(values) {
  // values = the latest checkpoint's accumulated state
  let doneCount = 0;
  for (let i = 0; i < Sp.PLANNER_SUBSTEP_FIELDS.length; i++) {
    const field = Sp.PLANNER_SUBSTEP_FIELDS[i];
    const c = cardEl(i);
    const present = _fieldPresent(values, field);
    if (!c) {
      // Cards DOM removed 2026-05-19. Still count done so the
      // tail-end `_renderPlannerGraph` has accurate progress.
      if (present) doneCount++;
      continue;
    }
    const icon = c.querySelector('.fw-planner-card-icon');
    const body = c.querySelector('.fw-planner-card-body');
    // Substep name = the PLANNER_SUBSTEPS index → graph node name.
    // Lookup the implementation flag for visual treatment.
    const cardData = c.dataset.substep || '';
    const isImplemented = Sp.plannerImplemented.has(cardData);
    if (present) {
      c.classList.add('done');
      c.classList.remove('running', 'failed', 'future');
      icon.textContent = '●'; icon.dataset.status = 'done';
      const renderer = SUBSTEP_RENDERERS[i];
      if (renderer) {
        body.innerHTML = renderer(values);
      } else {
        const v = values[field];
        body.innerHTML = '<pre>' + escapeHtml(formatFieldValue(v)) + '</pre>';
      }
      doneCount++;
    } else if (!isImplemented) {
      // Substep stub — not wired into the runtime graph. Render as
      // "future" so the user sees it's a planned step, not a failure.
      c.classList.add('future');
      c.classList.remove('running', 'done', 'failed');
      icon.textContent = '⏳'; icon.dataset.status = 'future';
      body.innerHTML =
        '<div class="fw-empty">Substep not yet implemented — will be ' +
        'wired into the graph as its real logic lands.</div>';
    } else if (i === doneCount && Sp.plannerThreadId !== null) {
      // First not-done IMPLEMENTED card while polling = currently running
      c.classList.add('running');
      c.classList.remove('done', 'failed', 'future');
      icon.textContent = '◐'; icon.dataset.status = 'running';
    } else {
      c.classList.remove('running', 'done', 'failed', 'future');
      icon.textContent = '○'; icon.dataset.status = 'pending';
    }
  }
  // Day 2: mirror the same state into the Cytoscape canvas. No-op
  // when ?ui=cards (Sp.plannerGraph is null). Drives node colors,
  // KPI badges, and the top-of-stage status pill (which now also
  // carries the N/8 progress count while working).
  _renderPlannerGraph(values);
  // Drawer live-refresh: if the user has the drawer open for a
  // planner node, re-hydrate its Results panel with the latest
  // SUBSTEP_RENDERERS output. Lets the drawer evolve in lockstep
  // with the card body without forcing the user to re-click.
  _refreshOpenPlannerDrawer(values);
}

export function markPlannerFailed(message) {
  // Find the first card still running (or first pending) and flag it.
  let failedNodeId = null;
  for (let i = 0; i < Sp.PLANNER_SUBSTEP_FIELDS.length; i++) {
    const c = cardEl(i);
    if (!c) continue;
    if (c.classList.contains('running') ||
        (!c.classList.contains('done') && !c.classList.contains('failed'))) {
      c.classList.remove('running');
      c.classList.add('failed', 'expanded');
      const icon = c.querySelector('.fw-planner-card-icon');
      icon.textContent = '✕';
      icon.dataset.status = 'failed';
      c.querySelector('.fw-planner-card-body').innerHTML =
        '<div class="fw-planner-error">' + escapeHtml(message) + '</div>';
      failedNodeId = Sp.PLANNER_NODE_ORDER[i];
      break;
    }
  }
  // Day 2: mirror to canvas + flip stage pill to failed.
  if (Sp.plannerGraph && failedNodeId) {
    Sp.plannerGraph.setStatus(failedNodeId, 'failed');
  }
  _setPlannerStagePill('failed');
}

export async function wipePlanner(slug) {
  if (!slug) return {error: 'no slug'};
  let result = {};
  try {
    const r = await fetch(Sa.API + '/planner/' + slug + '/wipe',
      {method: 'DELETE'});
    result = r.ok ? (await r.json()) : {http_status: r.status};
  } catch (e) {
    result = {error: String(e)};
  }
  _forgetActivePlanner(slug);
  if (Si.activeSlug === slug) {
    Sp.setPlannerThreadId(null);
    resetPlannerCards();
    refreshPlannerStartState();
    // Clear the navbar row-3 total. Backend deletes the
    // planner-timing-latest.json blob via the planner/{slug}/ MinIO prefix
    // sweep, but the navbar text was painted from a prior live ticker or
    // a /planner/{slug}/timing response — neither auto-clears on wipe.
    stopElapsed('planner');
    showElapsed('planner', 0);
  }
  console.log('[ddWipePlanner]', slug, result);
  return result;
}

export function _rememberActivePlanner(slug, tid) {
  try {
    localStorage.setItem(_plannerStorageKey(slug), tid);
    localStorage.setItem(Sp._LAST_PLANNER_SLUG_KEY, slug);
  } catch (e) { /* private mode etc — silently ignore */ }
}

export function _forgetActivePlanner(slug) {
  try { localStorage.removeItem(_plannerStorageKey(slug)); }
  catch (e) { /* ignore */ }
}

export function _allImplementedComplete(values) {
  if (!values) return false;
  if (!Sp.plannerImplemented || !Sp.plannerImplemented.size) return false;
  for (let i = 0; i < Sp.PLANNER_NODE_ORDER.length; i++) {
    const step = Sp.PLANNER_NODE_ORDER[i];
    if (!Sp.plannerImplemented.has(step)) continue;
    const field = Sp.PLANNER_SUBSTEP_FIELDS[i];
    if (!_fieldPresent(values, field)) return false;
  }
  return true;
}

export async function _tryResumeActivePlanner(slug) {
  // Tear down any prior session FIRST so a switch from framework A
  // (which had cached planner state) to framework B doesn't leave
  // A's KPI grids on B's cards. Sp.plannerThreadId !== new tid implies
  // the previous SSE loop should self-exit on its next message
  // (see the guard inside pollPlannerState). We also reset the
  // visual state so a slug with no localStorage entry shows pending
  // cards instead of inheriting the previous slug's render.
  Sp.setPlannerThreadId(null);
  resetPlannerCards();
  refreshPlannerStartState();

  // LIVE-RUN RECONNECT (2026-05-29). A server-side registry
  // (`dd:planner:current:{slug}`, set by start_planner, cleared on
  // terminal/cancel/wipe) is the authoritative "is a run live NOW?" signal
  // — a checkpoint with status="running" is NOT (a crashed/pod-restarted
  // run leaves it stuck forever). If the registry says active, reconnect to
  // the live SSE: the snapshot replay repaints the current running step and
  // fresh events resume the live progress + timer. This is READ-ONLY — it
  // does NOT POST /resume (only an explicit Start does), so a stale entry
  // can never restart compute; worst case it shows the last step until the
  // 1h registry TTL lapses (timer won't tick without fresh events).
  try {
    const ar = await fetch(Sa.API + '/planner/' + slug + '/active');
    if (ar.ok) {
      const a = await ar.json();
      if (a && a.active && a.thread_id) {
        _setPlannerRunStartMs(a.started_ts
          ? a.started_ts * 1000
          : Date.now());
        Sp.setPlannerThreadId(a.thread_id);
        try { localStorage.setItem(_plannerStorageKey(slug), a.thread_id); }
        catch (_) {}
        _setPlannerStagePill('working');
        refreshPlannerStartState();      // Start → Cancel
        pollPlannerState(a.thread_id);    // snapshot replay + live SSE
        return true;
      }
    }
  } catch (_) { /* fall through to view-only recovery */ }

  // Persisted planner total → navbar (finished/cached runs survive refresh).
  // Skip if a live run is currently ticking so we don't clobber it.
  if (!isElapsedRunning('planner')) {
    fetch(Sa.API + '/planner/' + slug + '/timing')
      .then(r => (r.ok ? r.json() : null))
      .then(d => {
        if (d && !isElapsedRunning('planner')) {
          showElapsed('planner', Number(d.total_wall_ms || 0));
        }
      })
      .catch(() => {});
  }

  // Source-of-truth lookup: ask the server for the authoritative latest
  // thread_id for this slug BEFORE trusting localStorage. The previous
  // implementation read localStorage first, which broke two real cases:
  //
  //   (1) Fresh browser / tab / private window: localStorage is empty,
  //       so we'd return false and the planner graph stayed at the
  //       initial all-pending visual even though /planner/recent had a
  //       fully-completed thread for this slug.
  //
  //   (2) Stale localStorage: an older partial run wrote a thread_id
  //       that's no longer the most-recent. /state for that stale tid
  //       returns a values dict with only the fields the partial run
  //       got to — so SOME nodes paint green and the rest stay pending,
  //       which is exactly the bug reported for the Pipeline page on
  //       2026-06-08 ("not all graph nodes showing green").
  //
  // /planner/recent is keyed by slug at the server (latest checkpoint
  // per thread per framework), so its answer overrides localStorage on
  // a mismatch. We also write it back to localStorage so subsequent
  // page-load fast paths (no network) stay accurate.
  let tid = null;
  try {
    const rr = await fetch(Sa.API + '/planner/recent');
    if (rr.ok) {
      const rd = await rr.json();
      const recent = (rd && rd.recent) || [];
      const hit = recent.find(it => it.slug === slug);
      if (hit && hit.thread_id) {
        tid = hit.thread_id;
        try { localStorage.setItem(_plannerStorageKey(slug), tid); }
        catch (_) {}
      }
    }
  } catch (_) { /* network down — fall through to localStorage */ }
  if (!tid) {
    try { tid = localStorage.getItem(_plannerStorageKey(slug)); }
    catch (e) { return false; }
  }
  if (!tid) return false;
  try {
    const r = await fetch(Sa.API + '/planner/debug/graph/' + tid + '/state');
    if (!r.ok) {
      _forgetActivePlanner(slug);
      return false;
    }
    const data = await r.json();
    const values = data.values || {};
    const status = values.status;
    // Terminal means "no more work to do":
    //   - failed/cancelled: explicit user/system halt, regardless of
    //     how many nodes ran
    //   - done AND all currently-wired nodes have committed: full
    //     completion under the current IMPLEMENTED set
    // CRITICAL: status="done" ALONE isn't enough. If new nodes were
    // added to IMPLEMENTED after the run finished, the thread shows
    // status="done" but missing the new node's field. Treating that as
    // terminal would skip the auto-/resume that needs to run the new
    // node — exactly the cluster-not-syncing bug.
    const allImplDone = _allImplementedComplete(values);
    const effectivelyDone = (
      status === 'failed' || status === 'cancelled' ||
      (status === 'done' && allImplDone) ||
      allImplDone
    );
    if (effectivelyDone) {
      // Terminal (or all-impl-done) — paint final state, don't subscribe.
      // KEEP localStorage entry so subsequent page refreshes can still
      // recover the cached cards. Entry only clears on explicit
      // Wipe Planner OR when a new run on this slug overwrites it.
      renderPlannerCards(values);
      return false;
    }
    // Non-terminal checkpoint ("running" / incomplete). This runs on
    // EVERY navigation to a framework's Planner page, so it is strictly
    // VIEW-ONLY. A checkpoint with status="running" is NOT proof of a
    // live task: a crashed/interrupted/pod-restarted run leaves the
    // status stuck at "running" forever (see planner.py /resume
    // docstring), and there is no backend liveness signal to tell a
    // live run from a dead one.
    //
    // So we paint the partial progress as a STATIC snapshot and DO NOT:
    //   - set Sp.plannerThreadId — which would flip the pill to
    //     "Working · N/9" (via _renderPlannerGraph) and the button to
    //     "Cancel", falsely implying a live run;
    //   - start pollPlannerState (live polling);
    //   - auto-POST /resume — which previously kicked off REAL compute
    //     just by visiting the page. THAT is the bug this fixes: a
    //     stale "running" checkpoint (e.g. FastMCP) showed "Working 5/9"
    //     and silently restarted the planner with no Start click.
    //
    // Live progress only ever runs in the session that explicitly
    // clicked Start Planner (startPlanner → smart-resume → pollPlannerState).
    // To continue an incomplete plan, the user clicks Start Planner: it
    // finds this thread via /planner/recent and POSTs /resume. Resuming
    // is therefore always an explicit, intentional action.
    renderPlannerCards(values);     // static view of the partial progress
    _setPlannerStagePill('idle');   // accurate — nothing is running now
    refreshPlannerStartState();     // Start Planner stays enabled (resume)
    return false;
  } catch (e) {
    _forgetActivePlanner(slug);
    return false;
  }
}

export async function startPlanner() {
  // Visible feedback on gate-fail. Previously the function returned
  // silently when the slug was missing or another run was already in
  // flight — which on mobile looks identical to "click did nothing"
  // and hides the actual reason. A toast keeps the UX honest.
  if (!Si.activeSlug) {
    showToast('Pick a framework from the Library picker first.');
    return;
  }
  if (Sp.plannerThreadId) {
    showToast('A planner run is already in flight for this framework.');
    return;
  }
  if (!Sc.ingestedSlugs.has(Si.activeSlug)) {
    showToast(
      'Ingest ' + Si.activeSlug + ' first — the planner needs its corpus.');
    return;
  }
  const blocker = crossStageBlockerFor('planner');
  if (blocker) {
    showNotice(blocker.notice);
    return;
  }
  resetPlannerCards();

  // Smart resume: if a thread already exists for this slug, reuse its
  // thread_id and POST /resume instead of /planner/{slug}. LangGraph's
  // ainvoke(None, config) on the expanded graph automatically skips
  // already-checkpointed nodes and runs only the new downstream ones.
  // Net: adding a 4th planner node + clicking Start Planner on a slug
  // that has steps 1-3 cached → only step 4 actually executes.
  let tid = null;
  let isResume = false;
  try {
    const r = await fetch(Sa.API + '/planner/recent');
    if (r.ok) {
      const data = await r.json();
      const found = ((data && data.recent) || [])
        .find(item => item.slug === Si.activeSlug);
      if (found && found.thread_id) {
        tid = found.thread_id;
        isResume = true;
      }
    }
  } catch (e) { /* fall through to fresh thread */ }

  if (!tid) tid = _genPlannerThreadId(Si.activeSlug);
  // PRE-DISPATCH cross-stage check — refresh the global blocker so a
  // synth started in another tab (after this page's last cache update)
  // is caught here rather than only at the server. This catches the
  // race "synth started 30 seconds ago in tab 2 → user clicks Start
  // Planner in tab 1". Cheap, one round-trip.
  try { await refreshCrossStageBlocker(); } catch (_) {}
  const preBlocker = crossStageBlockerFor('planner');
  if (preBlocker) {
    showNotice(preBlocker.notice);
    refreshPlannerStartState();   // re-apply disabled state
    return;
  }
  Sp.setPlannerThreadId(tid);
  _rememberActivePlanner(Si.activeSlug, tid);   // page-refresh recovery
  // Phase 3: announce the live pipeline so subscribers (topbar status
  // dots, cross-stage Start gates) react without polling.
  $activePipeline.set({ stage: 'planner', slug: Si.activeSlug, run_id: tid });
  // Mark the run start for the navbar timer (a refresh reconnect recovers
  // this from the registry's started_ts instead). A fresh start begins ~now.
  _setPlannerRunStartMs(Date.now());
  refreshPlannerStartState();   // button flips to "Cancel Planner"
  // Kick off polling in parallel with the main POST so the user sees
  // cards advance progressively.
  pollPlannerState(tid);
  try {
    // Mode is fixed to "llm" (the unified LITA-pattern planner) —
    // the dropdown was removed; the server still defaults `mode=llm`
    // if omitted, so we don't even need to pass it.
    const url = isResume
      ? Sa.API + '/planner/' + tid + '/resume'
      : Sa.API + '/planner/' + Si.activeSlug +
        '?mode=llm&thread_id=' + encodeURIComponent(tid);
    const r = await fetch(url, {method: 'POST'});
    if (!r.ok) {
      const txt = await r.text();
      markPlannerFailed('HTTP ' + r.status + ': ' + txt.slice(0, 400));
      Sp.setPlannerThreadId(null);
      refreshPlannerStartState();
      return;
    }
    const data = await r.json();
    // LOCKED RESPONSE — the server-side single-flight gate (cross-stage
    // synth running OR same-stage planner running for another slug)
    // rejected our dispatch. Roll back the local thread_id state, show
    // the server's explanatory message inline, and refresh the global
    // blocker so the disabled-button state catches up.
    if (data && data.status === 'locked') {
      Sp.setPlannerThreadId(null);
      try { await refreshCrossStageBlocker(); } catch (_) {}
      refreshPlannerStartState();
      showNotice(data.message ||
        ('Planner blocked: another ' + (data.stage || 'pipeline') +
         ' run is in flight (' + (data.slug || '?') + ').'));
      return;
    }
    // POST now returns immediately with status="running" — the
    // background graph task runs server-side and the polling loop
    // (pollPlannerState above) owns terminal-state detection +
    // resetting Sp.plannerThreadId / the button.
  } catch (e) {
    markPlannerFailed('Request failed: ' + String(e));
    Sp.setPlannerThreadId(null);
    refreshPlannerStartState();
  }
}

export async function cancelPlanner() {
  if (!Sp.plannerThreadId) return;
  const tid = Sp.plannerThreadId;
  // Phase 3: clear the atom now; the terminal SSE handler will fire too
  // but clearing here ensures the cross-stage Start gate releases the
  // moment the user clicks Cancel, not when the cancel watcher lands.
  $activePipeline.set(null);
  // Spinner + "Cancelling…" — mirrors the Step 2 ingestion cancel UX.
  Sp.plannerStartBtn.setAttribute('disabled', 'disabled');
  Sp.plannerStartBtn.innerHTML =
    '<div class="fw-spinner" style="display:inline-block;' +
    'vertical-align:middle;margin-right:8px"></div>Cancelling…';

  // Safety-net timer (mirrors Synth's pattern). The intended path is:
  // cancel watcher (1s poll) fires → graph.ainvoke raises CancelledError
  // → startPlanner's POST returns with status='cancelled' → its finally
  // block flips the button back. If anything in that chain stalls (pod
  // restart, network drop, ainvoke stuck inside a non-cancellable
  // await), the button used to spin forever. 5s gives the happy path
  // room; after that we force-reset the UI so the user isn't stuck.
  // Backend cancel flag stays set (TTL=1h) so the worker still drains
  // on its own — the state we're clearing here is purely browser-side.
  const PLANNER_CANCEL_TIMEOUT_MS = 5000;
  const safetyTimer = setTimeout(() => {
    if (Sp.plannerStartBtn
        && Sp.plannerStartBtn.innerHTML.includes('Cancelling')) {
      Sp.setPlannerThreadId(null);
      if (Si.activeSlug) {
        try { _forgetActivePlanner(Si.activeSlug); } catch (_) {}
      }
      refreshPlannerStartState();
      showToast(
        'Cancel sent. Cleanup is still finishing in the background; '
        + 'Start Planner / Wipe Planner are usable now.'
      );
    }
  }, PLANNER_CANCEL_TIMEOUT_MS);

  try {
    // Fire-and-forget — the cancel watcher on the server detects the
    // Redis flag within ~1s, raises CancelledError inside graph.ainvoke,
    // and the in-flight POST /planner/{slug} returns with
    // status='cancelled'. THAT response triggers the UI cleanup
    // (refreshPlannerStartState in startPlanner's finally).
    await fetch(Sa.API + '/planner/' + tid + '/cancel', {method: 'POST'});
    // Refresh the cross-stage blocker — once cancel propagates and
    // the lock releases (task finally fires), Synth's Start button
    // should unblock. Fire-and-forget on this tab; the Synth tab
    // re-checks on its own page load + click anyway.
    try { await refreshCrossStageBlocker(); } catch (_) {}
  } catch (e) {
    // If the cancel POST itself fails, restore the button so the user
    // can retry. The startPlanner POST is still in flight either way.
    clearTimeout(safetyTimer);
    Sp.plannerStartBtn.removeAttribute('disabled');
    Sp.plannerStartBtn.innerHTML = 'Stop';
    showToast('Cancel request failed: ' + String(e));
  }
}

