// shared/ui/pipeline.js — pipeline-aware helpers used by the
// destructive-action confirm dialogs (cascade impact text) and the
// proactive Start-button gating (cross-stage blocker). Extracted from
// shared/ui.js Step 3 (2026-06-05 follow-up).
import * as Sa from '@dd/shared/state/api.js';

// ---- pipeline-state probe + cascade-message helper ----------------
// Single shared fetch the three wipe / delete handlers use to label
// their confirm dialogs with accurate cascade impact. Falls back to
// "everything is cached" (the conservative show-all-warnings shape)
// if the endpoint fails — better to over-warn than to silently delete
// downstream artifacts the user didn't realize were there.
export async function fetchPipelineState(slug) {
  if (!slug) return null;
  try {
    const r = await fetch(Sa.API + '/pipeline/' + encodeURIComponent(slug) +
                           '/state');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return await r.json();
  } catch (e) {
    console.warn('[fetchPipelineState]', slug, e);
    return {
      slug, ingestion: true, planner: true, synth: true, study: true,
    };
  }
}

// ---- cross-stage proactive gate -----------------------------------
// Planner and Synth must NOT run simultaneously: they fight for the
// same free-tier LLM rotator pool and degrade each other's output
// quality. The server enforces this with locked-response gates at
// POST /planner and POST /synth, but the UI should ALSO disable the
// Start buttons proactively so the user sees the constraint before
// clicking (and so a click that races a remote start gets a clear
// message instead of looking like a silent no-op).
//
// `GET /pipeline/active` returns `{planner: {slug, thread_id} | null,
// synth: {slug, thread_id} | null}` — the cached result is what the
// Start-state refreshers read synchronously to decide whether to
// disable the button + which "running on X" tooltip to show.
//
// Cache lifecycle: refreshCrossStageBlocker() is called from
// initPlanner / initSynth on page load, after every Start / Cancel
// click, and could be polled (not done today — the rare-cross-tab
// case is fine to catch at click-time via the server's locked
// response). Fallback on fetch error: both `null` (no blocker) so a
// transient network blip doesn't lock the user out.
let _crossStageBlocker = { planner: null, synth: null };

export async function fetchActivePipelineStage() {
  try {
    const r = await fetch(Sa.API + '/pipeline/active');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return await r.json();
  } catch (e) {
    console.warn('[fetchActivePipelineStage]', e);
    return { planner: null, synth: null };
  }
}

export async function refreshCrossStageBlocker() {
  _crossStageBlocker = await fetchActivePipelineStage();
  return _crossStageBlocker;
}

export function getCrossStageBlocker() {
  return _crossStageBlocker;
}

// Build the "you cannot start because the OTHER stage is running"
// blocking message for either Planner or Synth Start buttons. Returns
// null when nothing is blocking, otherwise an object with `title` for
// the tooltip + `notice` for an inline toast. `mySlug` lets us avoid
// blocking a Planner run for slug X while a Synth IS running but it's
// on slug X too — wait, no. The constraint is "Planner + Synth never
// concurrent, ANY slug". So mySlug is unused for blocking but kept
// for parity with future per-slug rules.
export function crossStageBlockerFor(myStage) {
  const b = _crossStageBlocker || {};
  const other = myStage === 'planner' ? 'synth' : 'planner';
  const otherLock = b[other];
  if (!otherLock || !otherLock.slug) return null;
  const otherLabel = other.charAt(0).toUpperCase() + other.slice(1);
  return {
    stage: other,
    slug: otherLock.slug,
    thread_id: otherLock.thread_id,
    title: otherLabel + ' is running on ' + otherLock.slug +
           ' — Planner and Synth share LLM resources and cannot run ' +
           'at the same time. Wait for it to finish or cancel it first.',
    notice: otherLabel + ' is running on ' + otherLock.slug + '. ' +
            'Planner and Synth share the same LLM resources and ' +
            'cannot run at the same time without degrading each ' +
            "other's quality — wait for the other stage to finish " +
            'or cancel it before starting this one.',
  };
}

// Build the cascade-impact tail for a confirm message. ``fromStage`` is
// the stage being wiped — only downstream stages from that point are
// listed. Returns a string that READS NICELY appended to the destructive
// action description ("Wipe planner cache for X? Deletes...") so the
// user sees the same shape regardless of which button they clicked.
export function cascadeImpactText(state, fromStage) {
  if (!state) return '';
  // Downstream order — what gets cascaded for each entry-point.
  const downstream = {
    ingestion: ['planner', 'synth', 'study'],
    planner:   ['synth', 'study'],
    synth:     ['study'],
  }[fromStage] || [];
  const cached = downstream.filter(s => state[s]);
  if (cached.length === 0) return '';
  const labels = {
    planner: 'cached Planner artifacts',
    synth:   'cached Synth chapter outputs',
    study:   'rendered Study chapters',
  };
  const parts = cached.map(s => labels[s]);
  let list;
  if (parts.length === 1) list = parts[0];
  else if (parts.length === 2) list = parts.join(' and ');
  else list = parts.slice(0, -1).join(', ') + ', and ' + parts.slice(-1);
  return ' Cascades downstream — will ALSO delete the ' + list +
         ' for this framework.';
}
