// planner/graph.js — Cytoscape graph rendering + drawer ctx builder +
// drawer open/refresh.
//
// Extracted from planner.js Step 4 (2026-06-05 follow-up) via per-
// function grep + brace-counting. Today's Step 1 used line-range
// extraction and broke a function mid-body; this safer approach
// avoids that failure mode. _fieldPresent + _plannerStorageKey
// moved to planner/shared.js so this module never imports from
// planner.js (breaks the would-be cycle).
import * as Sa from '@dd/shared/state/api.js';
import * as Si from '@dd/shared/state/ingestion.js';
import * as Sp from '@dd/shared/state/planner.js';
import { escapeHtml } from '../shared/utils.js';
import { _fieldPresent, _plannerStorageKey } from './shared.js';
import { SUBSTEP_RENDERERS } from './renderers.js';
import { NodeDrawer } from './drawer.js';

export function _setPlannerStagePill(status, labelOverride) {
  const pill = document.getElementById('fw-planner-pill');
  const text = document.getElementById('fw-planner-pill-text');
  if (!pill || !text) return;
  const labels = {
    idle:     'Idle',
    working:  'Working',
    done:     'Completed',
    failed:   'Failed',
    cancelled:'Cancelled',
  };
  pill.dataset.status = status;
  text.textContent = labelOverride || labels[status] || status;
}

export function _kpiForNode(nodeId, values) {
  if (!values) return '';
  const stats = (key) => values[key] || null;
  switch (nodeId) {
    case 'corpus_load': {
      const s = stats('corpus_stats');
      return s && s.files ? `n=${s.files}` : '';
    }
    case 'embed_corpus': {
      const s = stats('embed_stats');
      if (!s) return '';
      if (s.dim) return `dim=${s.dim}`;
      if (s.files) return `n=${s.files}`;
      return '';
    }
    case 'off_topic': {
      const s = stats('off_topic_stats');
      return s && (s.kept !== undefined)
        ? `kept=${s.kept}/${(s.kept + (s.dropped || 0))}` : '';
    }
    case 'doc_distill': {
      const s = stats('doc_distill_stats');
      if (!s) return '';
      if (s.skipped) return `skip:N≤80`;
      return (s.n_distilled !== undefined)
        ? `n=${s.n_distilled}/${s.n_files || '?'}` : '';
    }
    case 'chapter_propose': {
      const s = stats('propose_stats');
      return s && (s.n_proposals !== undefined)
        ? `props=${s.n_proposals}` : '';
    }
    case 'chapter_assign': {
      const s = stats('assign_stats');
      return s && (s.n_assigned !== undefined)
        ? `assigned=${s.n_assigned}/${s.n_docs || '?'}` : '';
    }
    case 'chapter_select': {
      const s = stats('select_stats');
      return s && (s.n_chapters_out !== undefined)
        ? `ch=${s.n_chapters_out}` : '';
    }
    case 'plan_write': {
      const s = stats('plan_write_stats');
      return s && (s.n_chapters !== undefined)
        ? `ch=${s.n_chapters}` : '';
    }
  }
  return '';
}

export function _renderPlannerGraph(values) {
  if (!Sp.plannerGraph) return;
  let doneCount = 0;
  let anyRunning = false;
  let anyFailed = false;
  for (let i = 0; i < Sp.PLANNER_NODE_ORDER.length; i++) {
    const nodeId = Sp.PLANNER_NODE_ORDER[i];
    const field = Sp.PLANNER_SUBSTEP_FIELDS[i];
    const present = _fieldPresent(values, field);
    const isImpl = Sp.plannerImplemented.has(nodeId);
    let status;
    if (present) {
      status = 'done';
      doneCount++;
    } else if (!isImpl) {
      status = 'future';
    } else if (i === doneCount && Sp.plannerThreadId !== null) {
      status = 'running';
      anyRunning = true;
    } else {
      status = 'pending';
    }
    const kpi = present ? _kpiForNode(nodeId, values) : '';
    Sp.plannerGraph.setStatus(nodeId, status, kpi);
  }
  // Derive stage pill from aggregate state. Failed has priority,
  // then running, then all-done, else idle. The terminal SSE
  // handler overrides this with explicit done/failed/cancelled.
  // Progress count (N/8) is folded INTO the pill while working —
  // replaces the separate "Step N of 8" label that used to live in
  // the header actions cluster.
  const explicitStatus = (values && values.status) || null;
  const implCount = Sp.PLANNER_NODE_ORDER.filter(n => Sp.plannerImplemented.has(n)).length;
  const progress = implCount ? doneCount + '/' + implCount : null;
  if (explicitStatus === 'failed') {
    _setPlannerStagePill('failed');
    anyFailed = true;
  } else if (explicitStatus === 'cancelled') {
    _setPlannerStagePill('cancelled');
  } else if (anyRunning || Sp.plannerThreadId !== null) {
    _setPlannerStagePill('working',
      progress ? 'Working · ' + progress : null);
  } else if (
    doneCount > 0 && doneCount === implCount
  ) {
    _setPlannerStagePill('done');
  } else if (doneCount === 0) {
    _setPlannerStagePill('idle');
  }
  return { doneCount, anyRunning, anyFailed };
}

export function _buildPlannerNodeCtx(nodeId, values) {
  const idx = Sp.PLANNER_NODE_ORDER.indexOf(nodeId);
  if (idx < 0) return null;
  const label = Sp.PLANNER_NODE_LABELS[idx] || nodeId;
  const thisField = Sp.PLANNER_SUBSTEP_FIELDS[idx];
  let status = 'pending';
  if (_fieldPresent(values, thisField)) status = 'done';
  else if (!Sp.plannerImplemented.has(nodeId)) status = 'future';
  else if (Sp.plannerThreadId) status = 'running';
  // KPI strip for the sticky header — same compact format as the
  // node-label KPI badge but split into key/value chips.
  const kpiText = _kpiForNode(nodeId, values);
  const kpis = {};
  if (kpiText) {
    const eqIdx = kpiText.indexOf('=');
    if (eqIdx > 0) kpis[kpiText.slice(0, eqIdx)] = kpiText.slice(eqIdx + 1);
  }
  // PRIMARY content — the SAME rich HTML the legacy card body
  // showed. Custom per-substep renderer if this node has produced
  // output; otherwise the drawer renders a status-aware placeholder.
  const renderer = SUBSTEP_RENDERERS[idx];
  const resultsHtml = (renderer && _fieldPresent(values, thisField))
    ? renderer(values)
    : null;
  // Raw JSON kept as collapsed debug aids (only when present).
  const inputs = idx > 0 && _fieldPresent(values, Sp.PLANNER_SUBSTEP_FIELDS[idx - 1])
    ? JSON.stringify({ [Sp.PLANNER_SUBSTEP_FIELDS[idx - 1]]: values[Sp.PLANNER_SUBSTEP_FIELDS[idx - 1]] }, null, 2)
    : null;
  const outputs = _fieldPresent(values, thisField)
    ? JSON.stringify({ [thisField]: values[thisField] }, null, 2)
    : null;
  return { label, status, kpis, resultsHtml, inputs, outputs };
}

export async function _openPlannerNodeDrawer(nodeId) {
  let values = {};
  // Sp.plannerThreadId is set ONLY while a run is in flight — terminal
  // SSE handler nulls it on done/failed/cancelled. For a completed
  // thread we need the localStorage entry (same fallback the page-
  // refresh recovery uses) so the drawer can fetch /state and the
  // renderer can show the rich card body content.
  let tid = Sp.plannerThreadId;
  if (!tid && Si.activeSlug) {
    try { tid = localStorage.getItem(_plannerStorageKey(Si.activeSlug)); }
    catch (e) {}
  }
  if (tid) {
    try {
      const r = await fetch(Sa.API + '/planner/debug/graph/' + tid + '/state');
      if (r.ok) values = (await r.json()).values || {};
    } catch (e) { /* drawer opens with empty results */ }
  }
  const ctx = _buildPlannerNodeCtx(nodeId, values);
  if (ctx) NodeDrawer.open('planner', nodeId, ctx);
}

export function _refreshOpenPlannerDrawer(values) {
  if (NodeDrawer.openStage !== 'planner') return;
  const nodeId = NodeDrawer.openNodeId;
  if (!nodeId) return;
  const ctx = _buildPlannerNodeCtx(nodeId, values);
  if (ctx) NodeDrawer.updateContext(ctx);
}

