// planner/polling.js — SSE polling + live-progress rendering +
// stage-pill status. Extracted from planner.js Step 5 (2026-06-05
// follow-up) using per-function grep + brace-counting + DI for
// cross-refs back to planner.js (renderPlannerCards, markPlannerFailed,
// cardEl). _plannerRunStartMs module-state moved here too.
import * as Sa from '@dd/shared/state/api.js';
import * as Sp from '@dd/shared/state/planner.js';
import { sleep } from '../shared/utils.js';
import { showToast } from '../shared/ui.js';
import { startElapsed, stopElapsed } from '../shared/timing.js';
import { _setPlannerStagePill } from './graph.js';
import { NodeDrawer } from './drawer.js';
import { _fieldPresent } from './shared.js';
import { deps } from './polling_deps.js';
// $activePipeline cleared on planner terminal SSE — was referenced
// without an import (would crash with ReferenceError mid-run).
import { $activePipeline } from '@nx/stores/pipeline.js';
// `refreshPlannerStartState` accessed via the DI registry (deps.*) —
// see polling_deps.js for why. Tried a direct circular import from
// './planner.js' / './lifecycle.js' (function declaration → hoisted →
// live binding) but it empirically failed in browsers despite working
// in Node — the symptom was the Cancel→Start flip never firing on
// terminal SSE events. Using the existing DI mechanism eliminates the
// cycle entirely. The registry is populated synchronously by planner.js
// (registerPollingDeps call) before any SSE handler can fire.
const refreshPlannerStartState = () => deps.refreshPlannerStartState?.();

let _plannerRunStartMs = 0;

export async function pollPlanner(threadId) {
  Sp.setPlannerPollAbort(false);
  while (!Sp.plannerPollAbort && Sp.plannerThreadId === threadId) {
    try {
      // thread_id has slashes (docs-distiller/{slug}/{uuid}). Don't
      // encode — the FastAPI `:path` converter accepts slashes; the
      // smoke test in /history confirmed unencoded paths round-trip.
      const r = await fetch(
        Sa.API + '/planner/debug/graph/' + threadId + '/state');
      if (r.status === 404) { await sleep(700); continue; }
      if (!r.ok) { await sleep(1500); continue; }
      const data = await r.json();
      const values = data.values || {};
      deps.renderPlannerCards?.(values);
      if (values.status === 'done') {
        Sp.setPlannerThreadId(null);
        refreshPlannerStartState();
        return;
      }
      if (values.status === 'failed') {
        deps.markPlannerFailed?.(values.error || 'Planner failed.');
        Sp.setPlannerThreadId(null);
        refreshPlannerStartState();
        return;
      }
    } catch (e) { /* transient — retry */ }
    await sleep(1000);
  }
}

export function _genPlannerThreadId(slug) {
  // Client-side UUID v4 — uses crypto.randomUUID where available,
  // falls back to a sufficient-quality polyfill for older browsers.
  const uuid = (typeof crypto !== 'undefined' && crypto.randomUUID)
    ? crypto.randomUUID()
    : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
        const r = Math.random() * 16 | 0;
        return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
      });
  return 'docs-distiller/' + slug + '/' + uuid;
}

export function _liveProgressEl(stepName, idx) {
  const c = deps.cardEl?.(idx);
  if (!c) return null;
  const body = c.querySelector('.fw-planner-card-body');
  if (!body) return null;
  let el = body.querySelector('.fw-planner-card-live');
  if (!el) {
    el = document.createElement('div');
    el.className = 'fw-planner-card-live';
    el.style.cssText =
      'font-family:JetBrains Mono,monospace;font-size:0.78rem;' +
      'color:var(--text-muted);padding:8px 12px;border-top:1px dashed var(--border);' +
      'margin-top:8px';
    body.appendChild(el);
  }
  return el;
}

export function _stepIdx(stepName) {
  return Sp.PLANNER_SUBSTEP_FIELDS.findIndex((_, i) =>
    deps.cardEl?.(i)?.dataset.substep === stepName);
}

export function _markCardRunning(stepName) {
  const idx = _stepIdx(stepName);
  if (idx < 0) return;
  // Graph-only UI (cards DOM removed 2026-05-19): flip the Cytoscape
  // node to 'running' FIRST, unconditionally. This is the sole live
  // "Working" indicator now — the burgundy border + active-edge
  // animation kick in immediately on the SSE `start` event (without
  // waiting for the next /state refresh). Must run BEFORE the legacy
  // card guard below, which early-returns when no card element exists
  // (always, post-2026-05-19) and previously suppressed this update.
  if (Sp.plannerGraph) {
    // Don't downgrade an already-finished node. SSE snapshot replay on
    // page refresh re-delivers old `start` events for done steps; without
    // this guard they'd flip a completed node back to 'running' (the
    // graph-only equivalent of the old card `.done` guard).
    let cur = null;
    try { cur = Sp.plannerGraph.cy.getElementById(stepName).data('status'); }
    catch (_) {}
    if (cur !== 'done' && cur !== 'failed') {
      Sp.plannerGraph.setStatus(stepName, 'running');
      // Pill carries the in-flight step's ordinal so the user sees a
      // crisp "Working · 3/8" without waiting for the next state poll.
      const stepIdx = Sp.PLANNER_NODE_ORDER.indexOf(stepName);
      const implCount = Sp.PLANNER_NODE_ORDER.filter(n => Sp.plannerImplemented.has(n)).length;
      const progress = (stepIdx >= 0 && implCount)
        ? (stepIdx + '/' + implCount) : null;
      _setPlannerStagePill('working',
        progress ? 'Working · ' + progress : null);
    }
  }
  // Legacy card path — no-op in the graph-only UI (cardEl is null), but
  // kept so a future cards-mode reintroduction still works.
  const c = deps.cardEl?.(idx);
  if (!c) return;
  // Don't downgrade an already-completed card. Without this guard, SSE
  // snapshot replay during page-refresh recovery would re-process the
  // original `start` event for an already-done step and flip its
  // spinner back to running, hiding the KPI grid behind a stale
  // "filtering N files…" live-progress line.
  if (c.classList.contains('done')) return;
  c.classList.add('running');
  c.classList.remove('failed', 'future');
  const icon = c.querySelector('.fw-planner-card-icon');
  if (icon) { icon.textContent = '◐'; icon.dataset.status = 'running'; }
  const body = c.querySelector('.fw-planner-card-body');
  if (body && body.querySelector('.fw-empty')) {
    body.innerHTML = '';
  }
}

export function _renderLiveProgress(stepName, ev) {
  const idx = _stepIdx(stepName);
  if (idx < 0) return;
  const c = deps.cardEl?.(idx);
  // Same reason as _markCardRunning: skip live-text rewrites for
  // cards already marked done by the LangGraph state snapshot.
  if (c && c.classList.contains('done')) return;
  const el = _liveProgressEl(stepName, idx);
  if (!el) return;
  let text = '';
  if (stepName === 'corpus_load') {
    if (ev.kind === 'start')      text = '· reading manifest…';
    else if (ev.kind === 'done')  text = '✓ ' + (ev.files||0).toLocaleString() + ' files, ' + ((ev.total_bytes||0)/1024|0) + ' KB';
  } else if (stepName === 'embed_corpus') {
    if (ev.kind === 'start')             text = '· starting NIM embed (' + (ev.files||0) + ' files)…';
    else if (ev.kind === 'chunks_prepared') text = '· ' + (ev.chunks_total||0).toLocaleString() + ' chunks prepared (' + (ev.docs_chunked||0) + '/' + (ev.docs_total||0) + ' docs split)';
    else if (ev.kind === 'batch')        text = '· embedding chunk ' + (ev.chunks_done||0).toLocaleString() + ' / ' + (ev.chunks_total||0).toLocaleString();
    else if (ev.kind === 'done')         text = '✓ ' + (ev.files||0).toLocaleString() + ' vectors @ ' + (ev.dim||'?') + '-D (' + (ev.cache_hit ? 'cache hit' : ((ev.wall_ms||0) + ' ms cold') ) + ')';
  } else if (stepName === 'off_topic') {
    if (ev.kind === 'start')              text = '· filtering ' + (ev.files||0).toLocaleString() + ' files…';
    else if (ev.kind === 'anchors_embedded') text = '· anchors embedded (pos + neg) · LLM-as-Judge routing via ParetoBandit/dd-grader';
    else if (ev.kind === 'llm_progress')  text = '· LLM judged ' + (ev.judged||0).toLocaleString() + ' / ' + (ev.total||0).toLocaleString() + ' (keep ' + (ev.llm_keep||0) + ', drop ' + (ev.llm_drop||0) + (ev.llm_err ? ', err ' + ev.llm_err : '') + ')';
    else if (ev.kind === 'done')          text = '✓ kept ' + (ev.kept||0).toLocaleString() + '/' + (ev.total||0).toLocaleString() + ' (' + (ev.wall_ms||0) + ' ms)';
  } else if (stepName === 'cluster') {
    if (ev.kind === 'start')              text = '· clustering ' + (ev.n_docs||0).toLocaleString() + ' docs…';
    else if (ev.kind === 'umap_start')    text = '· UMAP ' + (ev.in_dim||'?') + '-D → ' + (ev.out_dim||'?') + '-D (cosine metric, ' + (ev.n_docs||0) + ' docs)';
    else if (ev.kind === 'hdbscan_start') text = '· HDBSCAN density clustering on ' + (ev.reduced_dim||'?') + '-D embeddings';
    else if (ev.kind === 'done')          text = '✓ ' + (ev.n_clusters||0) + ' clusters · ' + (ev.n_noise||0) + ' noise · ' + (ev.n_boundary||0) + ' boundary (' + (ev.wall_ms||0) + ' ms)';
  } else if (stepName === 'refine') {
    if (ev.kind === 'start')              text = '· reading cluster state…';
    else if (ev.kind === 'context_prepared') text = '· prepared c-TF-IDF context for ' + (ev.n_clusters||0) + ' clusters; LLM-judging ' + (ev.n_boundary||0) + ' boundary docs…';
    else if (ev.kind === 'llm_progress')  text = '· LLM judged ' + (ev.judged||0).toLocaleString() + ' / ' + (ev.total||0).toLocaleString() + ' (reassigned ' + (ev.changed||0) + ', null ' + (ev.null||0) + (ev.err ? ', err ' + ev.err : '') + ')';
    else if (ev.kind === 'done')          text = '✓ ' + (ev.n_changed||0) + ' reassigned · ' + (ev.n_null||0) + ' sent to noise (' + (ev.wall_ms||0) + ' ms)';
  } else if (stepName === 'label') {
    if (ev.kind === 'start')                 text = '· preparing label context…';
    else if (ev.kind === 'context_prepared') text = '· c-TF-IDF + rep-doc context ready for ' + (ev.n_clusters||0) + ' clusters; round 1 USC labeling…';
    else if (ev.kind === 'llm_progress')     text = '· ' + (ev.round || 'round1') + ': labeled ' + (ev.judged||0) + ' / ' + (ev.total||0) + ' (unanimous ' + (ev.unanimous||0) + ', USC ' + (ev.usc||0) + (ev.err ? ', err ' + ev.err : '') + ')';
    else if (ev.kind === 'round2_start')     text = '· round 2: re-labeling ' + (ev.n_round2||0) + ' USC-split clusters with sibling context…';
    else if (ev.kind === 'done')             text = '✓ ' + (ev.n_clusters||0) + ' clusters named' + (ev.n_round2 ? ' (' + ev.n_round2 + ' via round 2)' : '') + ' · ' + (ev.wall_ms||0) + ' ms';
  } else if (stepName === 'reduce') {
    if (ev.kind === 'start')                 text = '· reading cluster + refine + label artifacts…';
    else if (ev.kind === 'context_prepared') text = '· prepared context for ' + (ev.n_clusters_in||0) + ' input clusters; generating N=3 outline samples…';
    else if (ev.kind === 'samples_generated') text = '· ' + (ev.n_samples||0) + ' samples generated; USC voting…';
    else if (ev.kind === 'usc_voted')        text = '· USC vote done; running self-refine pass (feedback → refine)…';
    else if (ev.kind === 'refined')          text = '· self-refine done; validating coverage…';
    else if (ev.kind === 'repair_attempt')   text = '· repair attempt ' + (ev.attempt||0) + ': missing ' + (ev.missing||0) + ', dup ' + (ev.duplicate||0) + ', unknown ' + (ev.unknown||0);
    else if (ev.kind === 'done')             text = '✓ ' + (ev.n_chapters||0) + ' chapters' + (ev.n_repairs ? ' (' + ev.n_repairs + ' repair' + (ev.n_repairs > 1 ? 's' : '') + ')' : '') + (ev.forced_repair ? ' [forced]' : '') + ' · ' + (ev.wall_ms||0) + ' ms';
  } else if (stepName === 'doc_distill') {
    if (ev.kind === 'start')           text = '· distilling ' + (ev.n_files||0) + ' docs… (skip≤' + (ev.pass_through_threshold||80) + ')';
    else if (ev.kind === 'done' && ev.skipped) text = '✓ skipped (small N pass-through, ' + (ev.wall_ms||0) + ' ms)';
    else if (ev.kind === 'done')       text = '✓ ' + (ev.n_distilled||0) + ' distilled' + (ev.n_failed ? ' · ' + ev.n_failed + ' failed' : '') + ' (' + (ev.cache_hit ? 'cache hit' : ((ev.wall_ms||0) + ' ms')) + ')';
  } else if (stepName === 'chapter_propose') {
    if (ev.kind === 'start')           text = '· loading distillates + extracting structural seeds…';
    else if (ev.kind === 'sampling')   text = '· firing N=' + (ev.n_samples||3) + ' proposals (' + (ev.n_heading_seeds||0) + ' heading + ' + (ev.n_namespace_seeds||0) + ' namespace seeds)…';
    else if (ev.kind === 'done')       text = '✓ ' + (ev.n_proposals||0) + ' chapters proposed' + (ev.titles ? ': ' + (ev.titles||[]).slice(0,3).join(', ') + (ev.titles.length>3 ? '…' : '') : '') + ' (' + (ev.cache_hit ? 'cache hit' : ((ev.wall_ms||0) + ' ms')) + ')';
  } else if (stepName === 'chapter_assign') {
    if (ev.kind === 'start')           text = '· scoring ' + (ev.n_docs||0) + ' docs against ' + (ev.n_proposals||0) + ' chapter proposals…';
    else if (ev.kind === 'done')       text = '✓ ' + (ev.n_assigned||0) + ' assigned' + (ev.n_failed ? ' · ' + ev.n_failed + ' failed' : '') + ' (' + (ev.cache_hit ? 'cache hit' : ((ev.wall_ms||0) + ' ms')) + ')';
  } else if (stepName === 'chapter_select') {
    if (ev.kind === 'start')           text = '· greedy coverage over ' + (ev.n_proposals||0) + ' proposals · ' + (ev.n_docs||0) + ' docs' + (ev.n_pinned ? ' · ' + ev.n_pinned + ' pinned' : '') + '…';
    else if (ev.kind === 'done')       text = '✓ ' + (ev.n_chapters||0) + ' chapters selected' + (ev.n_pruned ? ' · ' + ev.n_pruned + ' pruned' : '') + ' · ' + Math.round((ev.coverage||0)*100) + '% coverage (' + (ev.wall_ms||0) + ' ms)';
  } else if (stepName === 'plan_write') {
    if (ev.kind === 'start')           text = '· hashing inputs… (manifest ' + ((ev.manifest_hash||'').slice(0,8)) + ')';
    else if (ev.kind === 'loaded')     text = '· loaded ' + (ev.n_chapters_in||0) + ' chapters · ' + (ev.n_clusters||0) + ' clusters · ' + (ev.n_docs||0) + ' docs';
    else if (ev.kind === 'sanitized')  text = '· sanitized · ' + (ev.n_chapters||0) + ' chapters · ' + (ev.n_sources||0) + ' sources' + (ev.n_dropped ? ' · ' + ev.n_dropped + ' empty dropped' : '') + (ev.n_unassigned ? ' · ' + ev.n_unassigned + ' unassigned' : '');
    else if (ev.kind === 'done')       text = '✓ ' + (ev.n_chapters||0) + ' chapters · ' + (ev.n_sources||0) + ' sources persisted (' + ((ev.cache_hit) ? 'cache hit' : ((ev.wall_ms||0) + ' ms')) + ')';
  }
  if (text) el.textContent = text;
}

export async function _refreshCardsFromState(threadId, expectedField) {
  const maxAttempts = expectedField ? 6 : 1;
  for (let i = 0; i < maxAttempts; i++) {
    try {
      const r = await fetch(Sa.API + '/planner/debug/graph/' + threadId + '/state');
      if (r.ok) {
        const data = await r.json();
        const values = data.values || {};
        if (!expectedField || _fieldPresent(values, expectedField)) {
          deps.renderPlannerCards?.(values);
          return;
        }
      }
    } catch (e) { /* transient */ }
    await sleep(250 + 150 * i);   // ~250ms / 400 / 550 / 700 / 850 / 1000
  }
}

export function _setPlannerRunStartMs(ms) { _plannerRunStartMs = ms || 0; }

export async function pollPlannerState(threadId) {
  // 2026-canonical pattern: Server-Sent Events instead of HTTP polling.
  // Backend pub/sub channel (Redis) is bridged by the FastAPI
  // /planner/{thread_id}/events endpoint which streams text/event-stream.
  // Each event carries {step, kind, ts, ...}; we route to the matching
  // substep card and render either a live progress sub-line or
  // (on "done") fetch the full state and let renderPlannerCards
  // redraw the card with KPI grids.
  //
  // Name kept for back-compat with existing callers (startPlanner).
  const url = Sa.API + '/planner/' + threadId + '/events';
  let es;
  try {
    es = new EventSource(url);
  } catch (e) {
    deps.markPlannerFailed?.('EventSource open failed: ' + String(e));
    Sp.setPlannerThreadId(null);
    refreshPlannerStartState();
    return;
  }
  es.onmessage = async (msg) => {
    if (Sp.plannerThreadId !== threadId) {
      try { es.close(); } catch (_) {}
      return;
    }
    let ev;
    try { ev = JSON.parse(msg.data); } catch (_) { return; }
    // Only "fresh" events (within the last ~20 seconds) count for
    // orphan-detect. Without this, the Redis snapshot replay of an
    // old run's events (e.g. a previous cluster start that errored)
    // would suppress the auto-/resume needed to actually run the
    // step now.
    if (ev.ts && (Date.now() / 1000 - ev.ts) < 20) {
      Sp.set_liveEventReceived(true);
      // Live navbar wall-clock — idempotent, starts ticking on the FIRST
      // fresh event (so a dead run's stale snapshot replay never starts it).
      // Seed from the run's real start so a refresh reconnect continues the
      // timer instead of restarting at 0.
      const base = _plannerRunStartMs
        ? Math.max(0, Date.now() - _plannerRunStartMs)
        : 0;
      startElapsed('planner', base);
    }

    // Planner-level terminal event: end the stream + reset UI.
    if (ev.step === 'planner' && ev.kind === 'terminal') {
      // Freeze the navbar total at the authoritative wall-clock (carried in
      // the terminal event — a separate post-terminal event would be missed
      // because the stream closes here).
      stopElapsed('planner', Number(ev.total_wall_ms || 0) || undefined);
      _plannerRunStartMs = 0;   // run ended — don't seed a future ticker

      // Pull the final state once so the cards reflect the very last
      // checkpoint. status field is set by aupdate_state right before
      // the terminal SSE event is emitted, so retry-by-status is the
      // race-safe expected field.
      await _refreshCardsFromState(threadId, 'status');
      const status = ev.status || 'done';
      if (status === 'failed') {
        deps.markPlannerFailed?.(ev.error || 'Planner failed.');
      } else if (status === 'cancelled') {
        showToast('Planner cancelled. Checkpoints up to the cancel point are preserved.');
        _setPlannerStagePill('cancelled');
      } else {
        // Day 2: explicit done → flip pill so the at-a-glance
        // indicator transitions out of 'working' even before the
        // user navigates away. _renderPlannerGraph's aggregate
        // logic also sets this, but the explicit signal is
        // race-safer (covers the all-impl-done detection edge).
        _setPlannerStagePill('done');
      }
      try { es.close(); } catch (_) {}
      Sp.setPlannerThreadId(null);
      // Phase 3: clear the live-pipeline atom on terminal so subscribers
      // unwind. Use .set(null) explicitly (not .set(undefined)) so the
      // subscriber's "clear data-* attribute" branch fires.
      const cur = $activePipeline.get();
      if (cur && cur.stage === 'planner') $activePipeline.set(null);
      // Intentionally NOT calling _forgetActivePlanner here — the
      // localStorage entry stays so a page refresh can still recover
      // the completed cards via the same thread_id. The entry only
      // clears on explicit Wipe Planner or on the next Start Planner
      // on this slug (which overwrites it).
      refreshPlannerStartState();
      // Broadcast for the unified Pipeline page's optional auto-chain
      // handler. Pure DOM event — no coupling to synth code from here.
      // Listener reads the active slug from the .fw-picker dataset.
      try {
        document.dispatchEvent(new CustomEvent('dd:planner:terminal', {
          detail: { status: status },
        }));
      } catch (_) {}
      return;
    }

    // Per-step lifecycle.
    if (ev.step) {
      if (ev.kind === 'start') {
        _markCardRunning(ev.step);
        // Previous step's checkpoint is necessarily committed by the
        // time the NEXT step starts (graph is sequential), so refresh
        // state to paint the previous card's full KPI grid. Skip for
        // the first step (no previous).
        const stepIdx = Sp.PLANNER_NODE_ORDER.indexOf(ev.step);
        if (stepIdx > 0) {
          const prevStep = Sp.PLANNER_NODE_ORDER[stepIdx - 1];
          const prevField = Sp.STEP_TO_FIELD[prevStep];
          await _refreshCardsFromState(threadId, prevField);
          // _markCardRunning was called BEFORE the state refresh; if
          // renderPlannerCards happens to have flipped this card back
          // to pending (because its field isn't in state yet), re-mark
          // it running here so the spinner stays correct.
          _markCardRunning(ev.step);
        }
      }
      _renderLiveProgress(ev.step, ev);
      // Day 3: route the same event into NodeDrawer if it's open for
      // this node. The drawer's rAF batching + sticky-bottom log
      // turns the SSE stream into a live activity tail.
      if (NodeDrawer.isOpenFor('planner', ev.step)) {
        NodeDrawer.appendEvent(ev);
      }
    }
  };
  es.onerror = (_e) => {
    // Browser auto-reconnects EventSource on transient errors; we
    // only intervene if the run was already torn down server-side.
    if (Sp.plannerThreadId !== threadId) {
      try { es.close(); } catch (_) {}
    }
  };
}

