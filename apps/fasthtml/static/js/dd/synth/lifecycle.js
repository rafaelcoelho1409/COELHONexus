// synth/lifecycle.js — start / cancel / wipe / resume + cards-render
// orchestration. Extracted from synth.js Step 8 (2026-06-05 follow-up).
// All cross-refs to siblings (canvas.js, graph.js, polling.js,
// chstrip.js, shared.js) imported directly. Run-start timestamp
// (setSynthRunStartMs / getSynthRunStartMs) lives in shared.js so
// every consumer (lifecycle.js, polling.js) goes through the same
// canonical store with no cycle back to synth.js.
import * as Sa from '@dd/shared/state/api.js';
import * as Sc from '@dd/shared/state/catalog.js';
import * as Si from '@dd/shared/state/ingestion.js';
import * as Sy from '@dd/shared/state/synth.js';
import { sleep, escapeHtml, formatFieldValue } from '../shared/utils.js';
import {
  showConfirm, showNotice, showToast,
  refreshCrossStageBlocker, crossStageBlockerFor,
  fetchPipelineState, cascadeImpactText,
} from '../shared/ui.js';
import { startElapsed, stopElapsed, showElapsed, isElapsedRunning, fmtMs } from '../shared/timing.js';
import { $activePipeline } from '@nx/stores/pipeline.js';
import { _setSynthStagePill, _renderSynthGraph, _kpiForSynthNode } from './graph.js';
import { _resizeSynthCanvas, _resetSynthEventBuffer, _refreshOpenSynthDrawer, _updateCoRefineChip } from './canvas.js';
import { synthCardEl, _synthAllImplementedComplete, _genSynthThreadId, pollSynthState, pollStudyState } from './polling.js';
import { _showChStrip, _renderChStrip, _applyChStripTitles, _markChStripCell, _markChStripCellTime, _hydrateChStripFromChapters, _resetStudyState } from './chstrip.js';
import {
  _synthFieldPresent,
  setSynthRunStartMs,
  getSynthRunStartMs,
} from './shared.js';

export function renderSynthCards(values, nextNodes) {
  // Cards DOM was removed 2026-05-19 — Sy.synthCardsEl is null. The
  // per-card loop below now early-skips at `if (!c) continue;` but
  // `_renderSynthGraph` + `_refreshOpenSynthDrawer` at the tail MUST
  // still fire (they own the graph-canvas + drawer state). Previous
  // `if (!synthCardsEl) return;` short-circuit silently broke them.
  //
  // CoRefine fix (see _renderSynthGraph): `nextNodes` comes from
  // snap.next on /state polls. When set, treat any node in it as
  // the running one regardless of field presence; falls through to
  // field-presence + first-not-done heuristic otherwise.
  const nextSet = (Array.isArray(nextNodes) && nextNodes.length > 0)
    ? new Set(nextNodes) : null;
  const useAuthoritative = nextSet !== null && Sy.synthThreadId !== null;
  let doneCount = 0;
  for (let i = 0; i < Sy.SYNTH_SUBSTEP_FIELDS.length; i++) {
    const field = Sy.SYNTH_SUBSTEP_FIELDS[i];
    const nodeId = Sy.SYNTH_NODE_ORDER[i];
    const c = synthCardEl(i);
    if (!c) {
      // Without cards we can't count done state from the DOM, so
      // derive it from values directly to keep the "first not-done
      // → running" canvas logic intact.
      if (_synthFieldPresent(values, field)) doneCount++;
      continue;
    }
    const icon = c.querySelector('.fw-planner-card-icon');
    const body = c.querySelector('.fw-planner-card-body');
    const present = _synthFieldPresent(values, field);
    const cardData = c.dataset.substep || '';
    const isImplemented = Sy.synthImplemented.has(cardData);
    if (useAuthoritative && nextSet.has(nodeId)) {
      // Authoritative running signal — beats field-presence so CoRefine
      // loopbacks display the currently-re-executing node correctly.
      c.classList.add('running');
      c.classList.remove('done', 'failed', 'future');
      icon.textContent = '◐'; icon.dataset.status = 'running';
    } else if (present) {
      c.classList.add('done');
      c.classList.remove('running', 'failed', 'future');
      icon.textContent = '●'; icon.dataset.status = 'done';
      const renderer = Sy.SYNTH_SUBSTEP_RENDERERS[i];
      if (renderer) {
        body.innerHTML = renderer(values);
      } else {
        const v = values[field];
        body.innerHTML = '<pre>' + escapeHtml(formatFieldValue(v)) + '</pre>';
      }
      doneCount++;
    } else if (!isImplemented) {
      // Stub — render as future (⏳).
      c.classList.add('future');
      c.classList.remove('running', 'done', 'failed');
      icon.textContent = '⏳'; icon.dataset.status = 'future';
      body.innerHTML =
        '<div class="fw-empty">Substep not yet implemented — will be ' +
        'wired into the graph as its real logic lands.</div>';
    } else if (i === doneCount && Sy.synthThreadId !== null) {
      // First not-done IMPLEMENTED card while polling = currently running.
      c.classList.add('running');
      c.classList.remove('done', 'failed', 'future');
      icon.textContent = '◐'; icon.dataset.status = 'running';
    } else {
      c.classList.remove('running', 'done', 'failed', 'future');
      icon.textContent = '○'; icon.dataset.status = 'pending';
    }
  }
  // Mirror state into the Cytoscape canvas (no-op when ?ui=cards).
  // Drives node colors + KPI badges + the top-of-stage status pill.
  _renderSynthGraph(values, nextNodes);
  // Live-refresh drawer if open for a synth node (same pattern as
  // planner — _refreshOpenSynthDrawer is a no-op when not open).
  _refreshOpenSynthDrawer(values);
}

export function markSynthFailed(message) {
  let failedNodeId = null;
  for (let i = 0; i < Sy.SYNTH_SUBSTEP_FIELDS.length; i++) {
    const c = synthCardEl(i);
    if (!c) continue;
    if (c.classList.contains('running') ||
        (!c.classList.contains('done') && !c.classList.contains('failed') &&
         !c.classList.contains('future'))) {
      c.classList.remove('running');
      c.classList.add('failed', 'expanded');
      const icon = c.querySelector('.fw-planner-card-icon');
      icon.textContent = '✕';
      icon.dataset.status = 'failed';
      c.querySelector('.fw-planner-card-body').innerHTML =
        '<div class="fw-planner-error">' + escapeHtml(message) + '</div>';
      failedNodeId = Sy.SYNTH_NODE_ORDER[i];
      break;
    }
  }
  if (Sy.synthGraph && failedNodeId) Sy.synthGraph.setStatus(failedNodeId, 'failed');
  _setSynthStagePill('failed');
}

export function resetSynthCards() {
  Sy.SYNTH_SUBSTEP_FIELDS.forEach((_, i) => {
    const c = synthCardEl(i);
    if (!c) return;
    c.classList.remove('running', 'done', 'failed', 'expanded');
    const substep = c.dataset.substep || '';
    // Stubs go back to future (⏳); implemented nodes go to pending (○).
    const isImpl = Sy.synthImplemented.has(substep);
    c.classList.toggle('future', !isImpl);
    const icon = c.querySelector('.fw-planner-card-icon');
    icon.textContent = isImpl ? '○' : '⏳';
    icon.dataset.status = isImpl ? 'pending' : 'future';
    c.querySelector('.fw-planner-card-latency').textContent = '';
    c.querySelector('.fw-planner-card-body').innerHTML = isImpl
      ? '<div class="fw-empty">Output will appear here once the substep runs.</div>'
      : '<div class="fw-empty">Substep not yet implemented — will be ' +
        'wired into the graph as its real logic lands.</div>';
  });
  // Day 5: also reset the Cytoscape canvas + stage pill on Start.
  if (Sy.synthGraph) Sy.synthGraph.reset();
  // Only reset the pill to 'idle' when NO run is in flight. When this is
  // called from `_onStripCellClick` (user pinning a running chapter) or
  // `_maybeAttachCurrentChapterSSE` (auto-attaching to the orchestrator's
  // current chapter), the synth/study thread IDs are already set — pill
  // should stay 'working'. The OLD-commit version reset unconditionally,
  // relying on the async fetch's `renderSynthCards → _renderSynthGraph`
  // to flip it back to 'working' within ~100ms — but on a cold cache /
  // empty state, that update never lands and the pill sticks at 'Idle'
  // mid-run, which is what the user saw on browser-use's chapter 2.
  if (!Sy.synthThreadId && !Sy.studyThreadId) _setSynthStagePill('idle');
}

export function refreshSynthStartState() {
  if (!Sy.synthStartBtn) return;
  // States for the Start/Resume/Stop button:
  //  - running               → "Stop Synth"
  //  - idle, partial render   → "Resume Synth" (keeps completed chapters)
  //  - idle, ready (no/all)   → "Start Synth" enabled
  //  - idle, blocked          → "Start Synth" disabled
  // Until the first synth node ships, "ready" requires the server's
  // /synth/info implemented list to be non-empty — otherwise clicking
  // Start would just hit the 503 stub. Show the button but disabled
  // with a clarifying tooltip so the user sees the path is wired but
  // not yet active.
  const running = Sy.synthThreadId !== null || Sy.studyThreadId !== null;
  if (running) {
    Sy.synthStartBtn.removeAttribute('disabled');
    Sy.synthStartBtn.classList.add('btn-outline');
    Sy.synthStartBtn.classList.remove('btn-primary');
    Sy.synthStartBtn.innerHTML = 'Stop';
  } else {
    const hasNodes = Sy.synthImplemented && Sy.synthImplemented.size > 0;
    // Synth REQUIRES a planner plan — block Start until one exists for
    // this framework (mirrors the server-side _load_plan 404 guard, so
    // the disabled button and the API agree). See main.js initSynth /
    // _hydrateChStripFromChapters which set Sy.synthHasPlan.
    // CROSS-STAGE GATE — Planner and Synth must not run simultaneously
    // (LLM-resource contention). When a planner is in flight ANYWHERE
    // (any slug), Start Synth is disabled with an explanatory tooltip.
    // The server enforces this too via POST /synth's locked-response.
    const blocker = crossStageBlockerFor('synth');
    const ready = Si.activeSlug && Si.activeRunId === null
                  && hasNodes && Sy.synthHasPlan && !blocker;
    if (ready) {
      Sy.synthStartBtn.removeAttribute('disabled');
      Sy.synthStartBtn.removeAttribute('title');
    } else {
      Sy.synthStartBtn.setAttribute('disabled', 'disabled');
      if (!hasNodes) {
        Sy.synthStartBtn.setAttribute(
          'title',
          'Synth pipeline not yet implemented — substeps light up as nodes ship.',
        );
      } else if (!Si.activeSlug) {
        Sy.synthStartBtn.setAttribute('title', 'Pick a framework first.');
      } else if (!Sy.synthHasPlan) {
        Sy.synthStartBtn.setAttribute(
          'title',
          'Run the Planner first — Synth needs a chapter plan for this framework.',
        );
      } else if (blocker) {
        Sy.synthStartBtn.setAttribute('title', blocker.title);
      }
    }
    Sy.synthStartBtn.classList.add('btn-primary');
    Sy.synthStartBtn.classList.remove('btn-outline');
    // Start vs Resume: when SOME (but not all) chapters are already
    // synthesized, the next run RESUMES — the backend orchestrator skips
    // rendered chapters, so completed work is kept and only the unfinished
    // chapter(s) re-run. Count rendered cells from the chapter strip.
    const _cells = Sy.chstripCellsEl
      ? Sy.chstripCellsEl.querySelectorAll('.fw-chstrip-cell') : [];
    let _rendered = 0;
    _cells.forEach((c) => { if (c.dataset.status === 'done') _rendered++; });
    const _partial = _cells.length > 0 && _rendered > 0
                     && _rendered < _cells.length;
    Sy.synthStartBtn.innerHTML = _partial ? 'Resume' : 'Start';
    if (ready && _partial) {
      Sy.synthStartBtn.setAttribute('title',
        'Resume — keeps completed chapters; re-runs only the unfinished '
        + 'one(s). Use Wipe Synth to erase everything and start over.');
    }
  }
  if (Sy.synthWipeBtn) {
    if (Si.activeSlug && !running && Sy.synthImplemented.size > 0) {
      Sy.synthWipeBtn.removeAttribute('disabled');
      Sy.synthWipeBtn.setAttribute('title',
        "Delete this framework's synth cache " +
        '(MinIO chapter artifacts + Postgres checkpoints + browser state)');
    } else {
      Sy.synthWipeBtn.setAttribute('disabled', 'disabled');
      Sy.synthWipeBtn.setAttribute('title', running
        ? 'Cannot wipe while a synth run is in flight.'
        : (Sy.synthImplemented.size === 0
            ? 'Synth pipeline not yet implemented.'
            : 'Pick a framework first.'));
    }
  }
  // Framework chip + stage-pill aggregate state.
  setSynthFramework(Si.activeSlug);
  if (!running) {
    // Pill reflects "have any synth output for this slug?".
    // Read the chstrip the durable hydrate (_hydrateChStripFromChapters)
    // just painted: count cells with data-status='done'. All N/N done →
    // green 'Done'. Some K/N done → blue 'Working · K/N' (resumable
    // partial). Zero → genuinely idle.
    // Without this read, refreshSynthStartState would always force
    // 'idle' here AFTER _hydrateChStripFromChapters set 'done', and
    // the pill would flip to Idle on every page refresh of a
    // completed study.
    const _cells = Sy.chstripCellsEl
      ? Sy.chstripCellsEl.querySelectorAll('.fw-chstrip-cell') : [];
    let _nDone = 0;
    _cells.forEach((c) => { if (c.dataset.status === 'done') _nDone++; });
    if (_cells.length > 0 && _nDone === _cells.length) {
      _setSynthStagePill('done');
    } else if (_cells.length > 0 && _nDone > 0) {
      _setSynthStagePill('working',
        'Working · ' + _nDone + '/' + _cells.length);
    } else {
      _setSynthStagePill('idle');
    }
  }
  // Empty-state placeholder — hide the cards/canvas when no slug
  // is active so the panel doesn't show an inert pipeline UI.
  // _toggleStageEmpty lives in planner.js — dynamic import.
  import('@dd/planner/planner.js').then(m => m._toggleStageEmpty('synth', !Si.activeSlug));
}

export function setSynthFramework(slug) {
  if (!Sy.synthFwNameEl || !Sy.synthFwLogosEl) return;
  if (!slug) {
    Sy.synthFwNameEl.textContent = 'Pick a framework to start.';
    Sy.synthFwNameEl.classList.add('fw-planner-fw-name-empty');
    Sy.synthFwLogosEl.innerHTML = '';
    Sy.synthFwLogosEl.style.display = 'none';
    return;
  }
  const info = Si.frameworkInfo[slug] || {name: slug, logos: []};
  Sy.synthFwNameEl.textContent = info.name || slug;
  Sy.synthFwNameEl.classList.remove('fw-planner-fw-name-empty');
  if (info.logos && info.logos.length) {
    Sy.synthFwLogosEl.innerHTML = info.logos.map(u =>
      '<img class="fw-planner-fw-logo" src="' + u + '" alt="">'
    ).join('');
    Sy.synthFwLogosEl.style.display = '';
  } else {
    Sy.synthFwLogosEl.innerHTML = '';
    Sy.synthFwLogosEl.style.display = 'none';
  }
}

export function _synthStorageKey(slug) { return 'dd:synth:active:' + slug; }

export function _rememberActiveSynth(slug, tid) {
  try {
    localStorage.setItem(_synthStorageKey(slug), tid);
    localStorage.setItem(Sy._LAST_SYNTH_SLUG_KEY, slug);
  } catch (e) {}
}

export function _forgetActiveSynth(slug) {
  try { localStorage.removeItem(_synthStorageKey(slug)); } catch (e) {}
}

export function _studyStorageKey(slug) { return 'dd:study:active:' + slug; }

export function _rememberActiveStudy(slug, sid) {
  try {
    localStorage.setItem(_studyStorageKey(slug), sid);
    localStorage.setItem(Sy._LAST_SYNTH_SLUG_KEY, slug);
  } catch (e) {}
}

export function _forgetActiveStudy(slug) {
  try { localStorage.removeItem(_studyStorageKey(slug)); } catch (e) {}
}

export function _getActiveStudy(slug) {
  try { return localStorage.getItem(_studyStorageKey(slug)); }
  catch (e) { return null; }
}

export async function _tryResumeActiveSynth(slug) {
  Sy.setSynthThreadId(null);
  Sy.setSynthHasPlan(false);   // re-gated below by _refreshSynthPlanGate
  resetSynthCards();
  refreshSynthStartState();

  // STUDY mode recovery. Prefer the browser's remembered thread; if absent
  // (cleared storage / another tab), the live-run registry confirms a run is
  // live for this slug so a plain refresh still reconnects and restores the
  // running-chapter blue highlight + live graph. We ALWAYS read the registry
  // (cheap Redis GET) for its `started_ts` so the navbar timer SEEDS from the
  // real run start and continues — study_start's own ts can age out of the
  // 200-event SSE snapshot on a long book, so the registry is the durable seed.
  let sid = _getActiveStudy(slug);
  let startedTs = null;
  try {
    const ar = await fetch(Sa.API + '/synth/' + slug + '/active');
    if (ar.ok) {
      const ad = await ar.json();
      if (ad && ad.active && ad.study_thread_id) {
        if (!sid) { sid = ad.study_thread_id; _rememberActiveStudy(slug, sid); }
        startedTs = ad.started_ts || null;
      }
    }
  } catch (_) { /* offline / no live run → fall through to hydrate */ }
  if (sid) {
    // Seed + start the navbar timer immediately (study-thread events are
    // sparse — ~one per chapter boundary — so waiting for a fresh event
    // would leave it blank for minutes mid-chapter). /active already
    // confirmed liveness; the 5s-no-replay guard below stops it if the
    // registry turns out stale.
    setSynthRunStartMs(startedTs ? startedTs * 1000 : Date.now());
    startElapsed('synth', Math.max(0, Date.now() - getSynthRunStartMs()));
    _resetStudyState();
    Sy.setStudyThreadId(sid);
    _showChStrip(true);
    _setSynthStagePill('working', 'Resuming study…');
    refreshSynthStartState();
    pollStudyState(sid);
    setTimeout(() => {
      if (Sy.studyThreadId === sid && Sy.studyChapterIds.length === 0) {
        console.log('[study-recover] no replay events in 5s; forgetting',
                    sid);
        _forgetActiveStudy(slug);
        Sy.setStudyThreadId(null);
        _resetStudyState();
        // Stale registry (run already finished/crashed, nothing to replay) —
        // stop the live ticker and fall back to the durable strip + the
        // PERSISTED total (hydrate calls showElapsed with study_total_wall_ms).
        setSynthRunStartMs(0);
        stopElapsed('synth');
        _hydrateChStripFromChapters(slug).catch(() => {});
        refreshSynthStartState();
      }
    }, 5000);
    return true;
  }

  // No in-flight study → rebuild strip from durable MinIO render status.
  _resetStudyState();
  _hydrateChStripFromChapters(slug).catch(() => {});

  let tid = null;
  try { tid = localStorage.getItem(_synthStorageKey(slug)); }
  catch (e) { return false; }
  if (!tid) return false;
  try {
    const r = await fetch(Sa.API + '/synth/debug/graph/' + tid + '/state');
    if (!r.ok) {
      _forgetActiveSynth(slug);
      return false;
    }
    const data = await r.json();
    const values = data.values || {};
    const nextNodes = Array.isArray(data.next) ? data.next : null;
    const status = values.status;
    const allImplDone = _synthAllImplementedComplete(values);
    const effectivelyDone = (
      status === 'failed' || status === 'cancelled' ||
      (status === 'done' && allImplDone) ||
      allImplDone
    );
    if (effectivelyDone) {
      renderSynthCards(values, nextNodes);
      return false;
    }
    // VIEW-ONLY (mirrors _tryResumeActivePlanner). A "running" synth
    // checkpoint is NOT proof of a live task — a crashed/interrupted run
    // leaves status stuck at "running" with no live process. So we paint
    // the partial progress as a STATIC snapshot and never set
    // synthThreadId (which would show "Working" + "Cancel"), never start
    // pollSynthState, and never auto-POST /resume (which would silently
    // restart synth compute just by navigating to the page). Continuing
    // is always an explicit Start Synth click (smart-resume).
    renderSynthCards(values, nextNodes);   // static view of partial progress
    _setSynthStagePill('idle');            // accurate — nothing running now
    refreshSynthStartState();              // Start Synth stays enabled
    return false;
  } catch (e) {
    _forgetActiveSynth(slug);
    return false;
  }
}

export async function recoverActiveSynth() {
  if (!Si.activeSlug) {
    let lastSlug = null;
    try { lastSlug = localStorage.getItem(Sy._LAST_SYNTH_SLUG_KEY); }
    catch (e) {}
    const keys = [];
    try {
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && k.startsWith('dd:synth:active:')) keys.push(k);
      }
    } catch (e) { return; }
    if (!keys.length) {
      try {
        const r = await fetch(Sa.API + '/synth/recent');
        if (r.ok) {
          const data = await r.json();
          const recent = (data && data.recent) || [];
          for (const item of recent) {
            try {
              localStorage.setItem(
                _synthStorageKey(item.slug), item.thread_id,
              );
            } catch (e) {}
          }
          if (recent.length) {
            try {
              localStorage.setItem(Sy._LAST_SYNTH_SLUG_KEY, recent[0].slug);
            } catch (e) {}
          }
        }
      } catch (e) {}
      return;
    }
  } else {
    await _tryResumeActiveSynth(Si.activeSlug).catch(() => {});
  }
}

export async function startSynth() {
  if (!Si.activeSlug || Sy.synthThreadId || Sy.studyThreadId) return;
  if (!Sy.synthImplemented || !Sy.synthImplemented.size) {
    showToast('Synth pipeline not yet implemented. UI is ready; ' +
              'substeps light up as nodes ship.');
    return;
  }
  // PRE-DISPATCH cross-stage check — refresh the global blocker so a
  // planner started in another tab is caught here rather than only at
  // the server. Cheap one round-trip.
  try { await refreshCrossStageBlocker(); } catch (_) {}
  const preBlocker = crossStageBlockerFor('synth');
  if (preBlocker) {
    showNotice(preBlocker.notice);
    refreshSynthStartState();
    return;
  }
  resetSynthCards();
  _resetSynthEventBuffer();
  _resetStudyState();
  // Phase 3: announce the live pipeline. The thread_id isn't known yet
  // (the POST hasn't returned); subscribers that need it can read it
  // later from Sy.synthThreadId. The atom carries enough to drive
  // cross-stage gating + topbar status.
  $activePipeline.set({ stage: 'synth', slug: Si.activeSlug, run_id: null });

  // STUDY MODE — Start Synth always fans out across ALL chapters.
  try {
    const budget = (Sy.synthBudgetSel && Sy.synthBudgetSel.value) || '5';
    const url = Sa.API + '/synth/' + Si.activeSlug +
      '?mode=quality' +
      '&budget=' + encodeURIComponent(budget);
    const r = await fetch(url, {method: 'POST'});
    if (!r.ok) {
      const txt = await r.text();
      markSynthFailed('HTTP ' + r.status + ': ' + txt.slice(0, 400));
      return;
    }
    const data = await r.json();
    // LOCKED RESPONSE — server-side single-flight gate rejected our
    // dispatch (cross-stage planner running OR cross-slug synth
    // running). Surface the explanatory message inline and refresh
    // the global blocker so the disabled-button state catches up.
    if (data && data.status === 'locked') {
      try { await refreshCrossStageBlocker(); } catch (_) {}
      refreshSynthStartState();
      showNotice(data.message ||
        ('Synth blocked: another ' + (data.stage || 'pipeline') +
         ' run is in flight (' + (data.slug || '?') + ').'));
      return;
    }
    const sid = data.study_thread_id;
    const chapterIds = data.chapter_ids || [];
    if (!sid) {
      markSynthFailed('Server did not return a study_thread_id.');
      return;
    }
    Sy.setStudyThreadId(sid);
    _rememberActiveStudy(Si.activeSlug, sid);
    // Mark run start + start the navbar timer (a refresh recovers the start
    // from the registry's started_ts instead). Fresh start begins ~now.
    setSynthRunStartMs(Date.now());
    startElapsed('synth', 0);
    _renderChStrip(chapterIds);
    _applyChStripTitles(Si.activeSlug);   // upgrade ids → real titles
    _showChStrip(true);
    _setSynthStagePill('working',
      'Working · 0/' + chapterIds.length);
    refreshSynthStartState();
    pollStudyState(sid);
  } catch (e) {
    markSynthFailed('Request failed: ' + String(e));
  }
}

// Safety-net timeout (ms) — if no SSE `terminal` arrives within this
// window the button auto-resets so the user is never stuck waiting.
// Cancel watchers poll every ~1s; 5s = one watcher tick + a couple
// seconds for the in-flight await to unwind. Backend keeps draining on
// its own after we reset the UI (cancel flag has TTL=1h), so it's safe
// to release the user from the spinner sooner. (Moved from synth.js
// 2026-06-07 — was a module-private const there, so cancelSynth here
// hit ReferenceError on every click and never reached the cancel POST.)
const CANCEL_TIMEOUT_MS = 5000;


export async function cancelSynth() {
  // Phase 3: clear the atom immediately so the cross-stage Start gate
  // releases on click, not when the cancel watcher lands on the server.
  $activePipeline.set(null);

  const tid = Sy.studyThreadId || Sy.synthThreadId;
  if (!tid) return;
  Sy.synthStartBtn.setAttribute('disabled', 'disabled');
  Sy.synthStartBtn.innerHTML =
    '<div class="fw-spinner" style="display:inline-block;' +
    'vertical-align:middle;margin-right:8px"></div>Cancelling…';

  // Safety-net timer. If the SSE terminal event doesn't fire within
  // CANCEL_TIMEOUT_MS (e.g., the chapter watcher missed the flag, the
  // pod restarted mid-cancel, or the SSE connection closed before the
  // terminal event arrived), we forcibly reset the UI so the user isn't
  // stuck. CRITICAL: this MUST clear synthThreadId AND studyThreadId
  // — otherwise refreshSynthStartState keeps `running=true` and the
  // Wipe button stays disabled even after the button looks like
  // "Start Synth". This was the cause of the "Wipe button does
  // nothing" bug. Backend cancel flag stays set (TTL=1h) so the
  // worker still drains on its own watcher tick — the state we're
  // clearing is purely browser-side.
  const safetyTimer = setTimeout(() => {
    if (Sy.synthStartBtn && Sy.synthStartBtn.innerHTML.includes('Cancelling')) {
      // Clear all thread refs so `running` flips to false everywhere.
      Sy.setSynthThreadId(null);
      Sy.setStudyThreadId(null);
      // Forget per-slug persistence too, so a page reload doesn't try
      // to re-attach to the cancelled study.
      if (Si.activeSlug) {
        try { _forgetActiveStudy(Si.activeSlug); } catch (_) {}
        try { _forgetActiveSynth(Si.activeSlug); } catch (_) {}
      }
      // Now flip the visuals — refreshSynthStartState will see no
      // running threads → enable Wipe + reset the Start button cleanly.
      refreshSynthStartState();
      // Re-sync the strip + Start/Resume label from server render status.
      if (Si.activeSlug) _hydrateChStripFromChapters(Si.activeSlug).catch(() => {});
      showToast(
        'Stop sent. Cleanup is still finishing in the background. '
        + 'Previously-completed chapters are preserved — click Resume Synth '
        + 'to continue from the unfinished chapter, or Wipe Synth to erase '
        + 'everything and start over.'
      );
    }
  }, CANCEL_TIMEOUT_MS);

  try {
    const r = await fetch(Sa.API + '/synth/' + tid + '/cancel', {method: 'POST'});
    if (r.ok) {
      const data = await r.json().catch(() => ({}));
      const n = (data.propagated_to || []).length;
      if (n > 0) {
        console.log('[cancelSynth] cancel propagated to '
          + n + ' chapter thread(s); the in-flight node will abort. '
          + 'Previously-completed node outputs are preserved.');
      }
      // Refresh the cross-stage blocker — once the lock releases via
      // the task's CAD-finally, Planner's Start button should unblock.
      try { await refreshCrossStageBlocker(); } catch (_) {}
      // Don't reset the button here — wait for the SSE `terminal` event
      // which signals the watcher actually fired and the task cancelled.
      // The safetyTimer above is the fallback if that never happens.
    } else {
      clearTimeout(safetyTimer);
      Sy.synthStartBtn.removeAttribute('disabled');
      Sy.synthStartBtn.innerHTML = 'Stop';
      showToast('Stop request failed: HTTP ' + r.status);
    }
  } catch (e) {
    clearTimeout(safetyTimer);
    Sy.synthStartBtn.removeAttribute('disabled');
    Sy.synthStartBtn.innerHTML = 'Stop';
    showToast('Stop request failed: ' + String(e));
  }
}

export async function wipeSynth(slug) {
  if (!slug) return {error: 'no slug'};
  let result = {};
  try {
    const r = await fetch(Sa.API + '/synth/' + slug + '/wipe',
      {method: 'DELETE'});
    result = r.ok ? (await r.json()) : {http_status: r.status};
  } catch (e) { result = {error: String(e)}; }
  _forgetActiveSynth(slug);
  _forgetActiveStudy(slug);  // study-orchestrator resume key — else a wiped
                             // slug re-opens the finished study's SSE on
                             // reload and replays its snapshot (see study_done)
  if (Si.activeSlug === slug) {
    Sy.setSynthThreadId(null);
    _resetStudyState();      // clears study-level state + HIDES the chapter strip
    resetSynthCards();
    refreshSynthStartState();
    // Re-populate the chapter strip from the planner's plan-latest.json.
    // Wipe Synth deletes the SYNTH cache (rendered chapter outputs +
    // LangGraph checkpoints) — it does NOT touch the planner. The chapter
    // list shown in the "red box" comes from the planner via
    // GET /synth/{slug}/study/chapters; after wiping synth, those
    // chapters are still listed (none rendered yet). Without this re-
    // hydrate the strip stays hidden and the user can't pick chapters to
    // re-synthesize without a page reload.
    _hydrateChStripFromChapters(slug).catch((e) => {
      console.warn('[ddWipeSynth] chapter strip rehydrate failed:', e);
    });
  }
  console.log('[ddWipeSynth]', slug, result);
  return result;
}

export async function loadSynthInfo() {
  try {
    const r = await fetch(Sa.API + '/synth/info');
    if (!r.ok) return;
    const data = await r.json();
    Sy.setSynthImplemented(new Set(data.implemented || []));
    renderSynthCards({});
    refreshSynthStartState();
  } catch (e) { /* silent — defaults to all "future" */ }
}

