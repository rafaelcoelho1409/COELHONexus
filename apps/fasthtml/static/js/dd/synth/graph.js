// synth/graph.js — Cytoscape graph rendering + drawer ctx builder.
// Extracted from synth.js Step 7 (2026-06-05) using the DI pattern:
// _synthFieldPresent (the only cross-ref that would otherwise pull
// us back to synth.js) was moved to shared.js, so this module
// imports from shared.js — never from synth.js — and synth.js
// re-exports our public functions for main.js compat.
import * as Sy from '@dd/shared/state/synth.js';
import { escapeHtml } from '../shared/utils.js';
import { _synthFieldPresent } from './shared.js';

// instead of restarting at 0 when reconnecting. 0 = no known run.

// ============================================================
// Day 5 — Synth canvas parity. Mirrors planner's helpers so each
// shipped synth node lights up the same way Planner does today.
// The canvas appears under ?ui=graph; cards remain the default view.
// ============================================================

export function _setSynthStagePill(status, labelOverride) {
  const pill = document.getElementById('fw-synth-pill');
  const text = document.getElementById('fw-synth-pill-text');
  if (!pill || !text) return;
  const labels = {
    idle: 'Idle', working: 'Working', done: 'Completed',
    failed: 'Failed', cancelled: 'Cancelled',
  };
  pill.dataset.status = status;
  text.textContent = labelOverride || labels[status] || status;
}

// KPI extraction per synth node. Currently every field is empty
// because no synth nodes ship state yet — populated as each lands.
// Format mirrors _kpiForNode (planner side): returns 'k=v' string or
// empty. When synth nodes start emitting real `*_stats`, fill these.
export function _kpiForSynthNode(nodeId, values) {
  if (!values) return '';
  const stats = (key) => values[key] || null;
  switch (nodeId) {
    case 'outline_sdp':        {
      const s = stats('outline_stats');
      if (!s) return '';
      const parts = [];
      if (s.n_sections   !== undefined) parts.push(`sec=${s.n_sections}`);
      if (s.max_stage    !== undefined) parts.push(`depth=${s.max_stage}`);
      if (s.n_violations !== undefined) parts.push(`viol=${s.n_violations}`);
      return parts.join(' · ');
    }
    case 'digest_construct':   {
      const s = stats('digest_stats');
      if (!s) return '';
      const parts = [];
      if (s.n_sources !== undefined) parts.push(`src=${s.n_sources}`);
      if (s.n_sections !== undefined &&
          s.n_sections_covered !== undefined) {
        parts.push(`cov=${s.n_sections_covered}/${s.n_sections}`);
      }
      if (s.n_orphan_code_refs !== undefined) {
        parts.push(`orph=${s.n_orphan_code_refs}`);
      }
      if (s.n_empty_sections) parts.push(`empty=${s.n_empty_sections}`);
      return parts.join(' · ');
    }
    case 'sawc_write':         {
      const s = stats('sawc_stats');
      if (!s) return '';
      const parts = [];
      if (s.n_sections !== undefined && s.n_completed !== undefined) {
        parts.push(`sec=${s.n_completed}/${s.n_sections}`);
      }
      if (s.n_fallback) parts.push(`fb=${s.n_fallback}`);
      if (s.n_repairs) parts.push(`rep=${s.n_repairs}`);
      if (s.n_picker_fallbacks) {
        parts.push(`pfb=${s.n_picker_fallbacks}`);
      }
      // Ship #7 (2026-05-24): show refine_iter when sawc has looped
      // back from mgsr_replan (>1 means the CoRefine loop fired).
      const iter = values.refine_iter;
      if (iter !== undefined && iter > 1) {
        parts.push(`iter=${iter}`);
      }
      return parts.join(' · ');
    }
    case 'checklist_eval':     {
      const s = stats('checklist_stats');
      if (!s) return '';
      const parts = [];
      if (s.n_total !== undefined && s.n_passed !== undefined) {
        parts.push(`pass=${s.n_passed}/${s.n_total}`);
      }
      if (s.pass_rate !== undefined) {
        parts.push(`rate=${(s.pass_rate * 100).toFixed(0)}%`);
      }
      if (s.chapter_passed === true)  parts.push('✓');
      if (s.chapter_passed === false) parts.push('✗');
      if (s.n_failed_feedback) parts.push(`fb=${s.n_failed_feedback}`);
      return parts.join(' · ');
    }
    case 'mgsr_replan':        {
      const s = stats('mgsr_stats');
      if (!s) return '';
      const parts = [];
      if (s.halt !== undefined) {
        parts.push(s.halt ? '✓halt' : '↻loop');
      }
      if (s.halt_reason) parts.push(s.halt_reason);
      if (s.n_actions !== undefined) parts.push(`act=${s.n_actions}`);
      if (s.confidence !== undefined) {
        parts.push(`conf=${(s.confidence * 100).toFixed(0)}%`);
      }
      return parts.join(' · ');
    }
    case 'render_audit_write': {
      const s = stats('chapter_stats');
      if (!s) return '';
      const parts = [];
      if (s.audit_passed === true)  parts.push('audit=✓');
      if (s.audit_passed === false) parts.push('audit=✗');
      if (s.n_artifacts !== undefined) parts.push(`arts=${s.n_artifacts}`);
      if (s.n_code_refs !== undefined && s.n_resolved !== undefined &&
          s.n_code_refs > 0) {
        parts.push(`refs=${s.n_resolved}/${s.n_code_refs}`);
      }
      if (s.n_missing) parts.push(`miss=${s.n_missing}`);
      if (s.n_byte_drift) parts.push(`drift=${s.n_byte_drift}`);
      if (s.rendered_chars) {
        parts.push(`${(s.rendered_chars / 1000).toFixed(1)}k`);
      }
      return parts.join(' · ');
    }
  }
  return '';
}

export function _renderSynthGraph(values, nextNodes) {
  if (!Sy.synthGraph) return;
  // BUGFIX 2026-05-24: previously this routine inferred "currently
  // running" by finding the FIRST not-done node (`i === doneCount`).
  // That assumes monotonic progression, which CoRefine loopbacks break:
  // after `mgsr_replan → RETHINK → sawc_write` re-enters, sawc's output
  // field from iter 1 is still in the checkpoint values, so the loop
  // misclassifies sawc as 'done' and lights up the next un-output node
  // (typically render_audit_write) as 'running' even though Python is
  // actually re-executing sawc_write.
  //
  // Fix: when the caller passes `nextNodes` (= snap.next from LangGraph
  // state), use it as the authoritative "currently running" set. Any
  // node in nextNodes that the synth thread is actively running gets
  // status='running', overriding the field-presence heuristic.
  // The heuristic stays as a fallback for the pre-first-checkpoint
  // window (when nextNodes is empty/unknown).
  const nextSet = (Array.isArray(nextNodes) && nextNodes.length > 0)
    ? new Set(nextNodes) : null;
  const useAuthoritative = nextSet !== null && Sy.synthThreadId !== null;
  // 2026-05-25: per-node iter badge + global CoRefine chip.
  // refine_iter is a SynthState field bumped by sawc_write each pass;
  // it survives across the loopback because LangGraph checkpoints the
  // value. Default 0 → no badge displayed (first pass / no run yet).
  const refineIter = Number(values && values.refine_iter || 0);
  const maxIter    = 5;   // matches graph.py:_MAX_REFINE_ITER
  // A loopback is "actively firing" when sawc_write is in nextSet AND
  // there's already a sawc output (i.e. we've completed at least iter 1
  // and are re-entering). Same predicate the SAWC running-state uses.
  const isLooping = (
    refineIter >= 1 &&
    useAuthoritative &&
    nextSet.has('sawc_write')
  );
  let doneCount = 0;
  let anyRunning = false;
  for (let i = 0; i < Sy.SYNTH_NODE_ORDER.length; i++) {
    const nodeId = Sy.SYNTH_NODE_ORDER[i];
    const field = Sy.SYNTH_SUBSTEP_FIELDS[i];
    const present = _synthFieldPresent(values, field);
    const isImpl = Sy.synthImplemented.has(nodeId);
    let status;
    if (useAuthoritative && nextSet.has(nodeId)) {
      // Authoritative running signal — overrides field presence.
      status = 'running'; anyRunning = true;
    } else if (present) { status = 'done'; doneCount++; }
    else if (!isImpl)   { status = 'future'; }
    else if (i === doneCount && Sy.synthThreadId !== null) {
      // Pre-checkpoint fallback for the very first superstep.
      status = 'running'; anyRunning = true;
    } else              { status = 'pending'; }
    // Per-node iter badge on sawc_write only (the loop target). KPI text
    // gets concatenated with the existing KPI line when present.
    let kpiText = present ? _kpiForSynthNode(nodeId, values) : '';
    if (nodeId === 'sawc_write' && refineIter >= 1) {
      const badge = `iter ${refineIter}/${maxIter}`;
      kpiText = kpiText ? `${badge} · ${kpiText}` : badge;
    }
    Sy.synthGraph.setStatus(nodeId, status, kpiText);
  }
  // Drive the loopback edge state (amber arc — dashed dormant / solid
  // animated when firing). Cheap no-op when the graph has no loopback
  // edges (e.g. planner reuses StageGraph but has no cycle).
  if (typeof Sy.synthGraph.setLoopActive === 'function') {
    Sy.synthGraph.setLoopActive(isLooping);
  }
  _updateCoRefineChip(isLooping, refineIter, maxIter);
  const explicitStatus = (values && values.status) || null;
  const implCount = Sy.SYNTH_NODE_ORDER.filter(n => Sy.synthImplemented.has(n)).length;
  const progress = implCount ? doneCount + '/' + implCount : null;
  if (explicitStatus === 'failed')        _setSynthStagePill('failed');
  else if (explicitStatus === 'cancelled') _setSynthStagePill('cancelled');
  else if (anyRunning || Sy.synthThreadId !== null) {
    _setSynthStagePill('working',
      progress ? 'Working · ' + progress : null);
  } else if (doneCount > 0 && doneCount === implCount) {
    _setSynthStagePill('done');
  } else if (doneCount === 0) {
    _setSynthStagePill('idle');
  }
}

export function _buildSynthNodeCtx(nodeId, values) {
  const idx = Sy.SYNTH_NODE_ORDER.indexOf(nodeId);
  if (idx < 0) return null;
  const label = Sy.SYNTH_NODE_LABELS[idx] || nodeId;
  const thisField = Sy.SYNTH_SUBSTEP_FIELDS[idx];
  let status = 'pending';
  if (_synthFieldPresent(values, thisField)) status = 'done';
  else if (!Sy.synthImplemented.has(nodeId)) status = 'future';
  else if (Sy.synthThreadId) status = 'running';
  const kpiText = _kpiForSynthNode(nodeId, values);
  const kpis = {};
  if (kpiText) {
    // KPI text format is `k1=v1 · k2=v2 · k3=v3` (space-dot-space
    // separator). Older code only grabbed the first `k=v` because it
    // split on the FIRST `=` for the whole string, dropping multi-key
    // KPIs. Split on the separator first, then on `=` per pair.
    kpiText.split(' · ').forEach(pair => {
      const eqIdx = pair.indexOf('=');
      if (eqIdx > 0) {
        kpis[pair.slice(0, eqIdx).trim()] = pair.slice(eqIdx + 1).trim();
      }
    });
  }
  // Synth's SUBSTEP_RENDERERS is empty until nodes ship; same
  // pattern as planner — when a renderer lands, drawer gets the
  // rich KPI/table/outline view automatically.
  const renderer = Sy.SYNTH_SUBSTEP_RENDERERS[idx];
  const resultsHtml = (renderer && _synthFieldPresent(values, thisField))
    ? renderer(values)
    : null;
  const inputs = idx > 0 && _synthFieldPresent(values, Sy.SYNTH_SUBSTEP_FIELDS[idx - 1])
    ? JSON.stringify({ [Sy.SYNTH_SUBSTEP_FIELDS[idx - 1]]: values[Sy.SYNTH_SUBSTEP_FIELDS[idx - 1]] }, null, 2)
    : null;
  const outputs = _synthFieldPresent(values, thisField)
    ? JSON.stringify({ [thisField]: values[thisField] }, null, 2)
    : null;
  return { label, status, kpis, resultsHtml, inputs, outputs };
}

// In-memory event buffer keyed by step name. The SSE handler in
// pollSynthState pushes every event here AS IT ARRIVES, regardless of
// whether the drawer is currently open. When the user opens the
