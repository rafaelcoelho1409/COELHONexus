// synth/polling.js — SSE polling: synth-level + study-orchestrator.
// Extracted from synth.js Step 7 (2026-06-05 follow-up). DI for
// cross-refs back to synth.js (rendering / status / strip update
// helpers). The bandit-routed sleeper state lives on the deps.
import * as Sa from '@dd/shared/state/api.js';
import * as Si from '@dd/shared/state/ingestion.js';
import * as Sy from '@dd/shared/state/synth.js';
import { sleep, escapeHtml, formatFieldValue } from '../shared/utils.js';
import { showToast } from '../shared/ui.js';
import { startElapsed, stopElapsed, showElapsed, fmtMs } from '../shared/timing.js';
import { _setSynthStagePill, _renderSynthGraph } from './graph.js';
import {
  _refreshOpenSynthDrawer,
  _bufferSynthEvent,
  _resetSynthEventBuffer,
  _getNodeDrawerRef,
} from './canvas.js';
import {
  _showChStrip,
  _renderChStrip,
  _applyChStripTitles,
  _resetStudyState,
  _hydrateChStripFromChapters,
} from './chstrip.js';
import { _synthFieldPresent, setSynthRunStartMs } from './shared.js';
import { deps } from './polling_deps.js';

// Book-harmonize indicator (2026-06-08) — single source of truth for
// the post-study cross-chapter coherence pass UI row that sits under
// the chapter strip. Called by the SSE handlers in pollStudyState.
// Status: 'idle' / 'running' / 'skipped' / 'done'. Sets data-status so
// CSS can drive the icon + color.
function _updateBookHarmonize(status, label) {
  const row = document.getElementById('fw-book-harmonize');
  if (!row) return;
  row.dataset.status = status || 'idle';
  const txt = document.getElementById('fw-bh-status-text');
  if (txt) txt.textContent = label || '—';
}

export function synthCardEl(idx) {
  if (!Sy.synthCardsEl) return null;
  return Sy.synthCardsEl.querySelector(
    '.fw-planner-card[data-idx="' + idx + '"]');
}

export function _synthStepIdx(stepName) {
  return Sy.SYNTH_SUBSTEP_FIELDS.findIndex((_, i) =>
    synthCardEl(i)?.dataset.substep === stepName);
}

export function _synthAllImplementedComplete(values) {
  if (!Sy.synthImplemented || !Sy.synthImplemented.size) return false;
  for (let i = 0; i < Sy.SYNTH_NODE_ORDER.length; i++) {
    const step = Sy.SYNTH_NODE_ORDER[i];
    if (!Sy.synthImplemented.has(step)) continue;
    const field = Sy.SYNTH_SUBSTEP_FIELDS[i];
    if (!_synthFieldPresent(values, field)) return false;
  }
  return true;
}

export function _synthLiveProgressEl(stepName, idx) {
  const c = synthCardEl(idx);
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

export function _markSynthCardRunning(stepName) {
  // CRITICAL: the early-return-on-`idx < 0` that used to live here was a
  // killer bug — `_synthStepIdx` queries `synthCardEl(i)?.dataset.substep`,
  // and `synthCardsEl` has been `null` since cards DOM was removed
  // 2026-05-19, so `idx` is ALWAYS -1, which made this function a complete
  // no-op for the Cytoscape graph update path (the entire user-facing
  // "graph nodes light up live" behavior). The comment below already
  // documents the intent ("Must run BEFORE the legacy card guard") but the
  // guard was at the top, so it never did. Moved 2026-06-07.
  //
  // Graph-only UI (cards DOM removed): flip the Cytoscape node to
  // 'running' FIRST, unconditionally — it's the sole live "Working"
  // indicator now.
  if (Sy.synthGraph) {
    // Don't downgrade an already-finished node (SSE snapshot replay on
    // refresh re-delivers old `start` events for done steps).
    let cur = null;
    try { cur = Sy.synthGraph.cy.getElementById(stepName).data('status'); }
    catch (_) {}
    if (cur !== 'done' && cur !== 'failed') {
      Sy.synthGraph.setStatus(stepName, 'running');
      // Per-node pill text suppressed during study mode (2026-06-07) —
      // the study-level `chapter_running` handler owns the pill text
      // as `Working · X/N` (X = chapter ordinal, N = total chapters);
      // letting per-node `Working · stepIdx/7` overwrite it every time
      // a node fires would clobber the chapter-level view the user
      // asked for. Single-chapter runs (no studyThreadId) still get
      // the per-node ordinal so the pill stays informative there.
      if (!Sy.studyThreadId) {
        const stepIdx = Sy.SYNTH_NODE_ORDER.indexOf(stepName);
        const implCount = Sy.SYNTH_NODE_ORDER.filter(n => Sy.synthImplemented.has(n)).length;
        const progress = (stepIdx >= 0 && implCount)
          ? (stepIdx + '/' + implCount) : null;
        _setSynthStagePill('working',
          progress ? 'Working · ' + progress : null);
      }
    }
  }
  // Legacy card path — no-op in the graph-only UI (synthCardEl is null).
  // The `idx < 0` short-circuit lives here now, AFTER the graph update.
  const idx = _synthStepIdx(stepName);
  if (idx < 0) return;
  const c = synthCardEl(idx);
  if (!c) return;
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

export function _renderSynthLiveProgress(stepName, ev) {
  const idx = _synthStepIdx(stepName);
  if (idx < 0) return;
  const c = synthCardEl(idx);
  if (c && c.classList.contains('done')) return;
  const el = _synthLiveProgressEl(stepName, idx);
  if (!el) return;
  let text = '';
  // Generic lifecycle fallbacks — every node SHOULD emit start/done at
  // minimum.
  if (ev.kind === 'start')      text = '· starting ' + stepName + '…';
  else if (ev.kind === 'done')  text = '✓ done (' + (ev.wall_ms || 0) + ' ms)';
  else if (ev.kind === 'error') text = '✕ ' + (ev.error || 'failed');
  // outline_sdp — SurveyGen-I SDP per-event progress
  if (stepName === 'outline_sdp') {
    if (ev.kind === 'start') {
      text = '· loading sources for ' + (ev.chapter_title || ev.chapter_id || 'chapter') +
             ' (' + (ev.n_sources || 0) + ' sources)';
    } else if (ev.kind === 'sources_loaded') {
      text = '· sources loaded: ' + (ev.n_bodies || 0) + '/' + (ev.n_sources || 0) +
             ' bodies, ' + ((ev.bytes || 0) / 1000).toFixed(1) + 'k chars, ' +
             (ev.n_vault_hashes || 0) + ' code refs' +
             (ev.truncated ? ' (truncated)' : '');
    } else if (ev.kind === 'sample_done') {
      // Per-sample event (one per concurrent LLM draft). `sample_idx`
      // is 0-based; show 1-based for the user.
      const idx = (ev.sample_idx ?? 0) + 1;
      const tot = ev.n_total || 0;
      const dep = ev.deployment ? ' [' + ev.deployment + ']' : '';
      if (ev.ok) {
        text = '· sample ' + idx + '/' + tot + ' done (' +
               (ev.n_sections || '?') + ' sections, ' +
               (ev.wall_ms || 0) + ' ms)' + dep;
      } else {
        text = '· sample ' + idx + '/' + tot + ' FAILED: ' +
               (ev.error || 'unknown');
      }
    } else if (ev.kind === 'samples_drafted') {
      text = '· drafted ' + (ev.n_samples || 0) + '/' +
             (ev.n_requested || 0) + ' candidate outlines';
    } else if (ev.kind === 'samples_validated') {
      text = '· validated ' + (ev.n_candidates || 0) + ' candidate(s)' +
             (ev.n_pydantic_fail ? ', ' + ev.n_pydantic_fail + ' pydantic-rejected' : '');
    } else if (ev.kind === 'usc_voted') {
      text = '· USC picked candidate #' + (ev.chosen_index || 0) +
             ' (' + (ev.n_initial_violations || 0) + ' initial violations)';
    } else if (ev.kind === 'repair_attempt') {
      text = '· repair attempt ' + (ev.attempt || 0) +
             ' (' + (ev.n_violations || 0) + ' violations)';
    } else if (ev.kind === 'done') {
      text = '✓ done — ' + (ev.n_sections || 0) + ' sections, ' +
             'depth=' + (ev.max_stage || 0) + ', ' +
             'repairs=' + (ev.n_repairs || 0) + ', ' +
             'violations=' + (ev.n_violations || 0) +
             ' (' + (ev.wall_ms || 0) + ' ms)';
    }
  }
  // digest_construct — per-source LLM-assigned routing (LLMxMapReduce-V3
  // pattern). N parallel source digests with one `source_done` event per
  // completion, plus lifecycle events.
  if (stepName === 'digest_construct') {
    if (ev.kind === 'start') {
      text = '· starting digests for ' + (ev.chapter_title || ev.chapter_id || 'chapter') +
             ' (' + (ev.n_sources || 0) + ' sources × ' +
             (ev.n_sections || 0) + ' sections)';
    } else if (ev.kind === 'outline_loaded') {
      text = '· outline loaded: ' + (ev.n_sources || 0) + ' source(s), ' +
             (ev.n_total_vault_hashes || 0) + ' code refs, ' +
             (((ev.total_bytes || 0) / 1000).toFixed(1)) + 'k chars';
    } else if (ev.kind === 'source_done') {
      const idx = (ev.sample_idx ?? 0) + 1;
      const tot = ev.n_total || 0;
      const dep = ev.deployment ? ' [' + ev.deployment + ']' : '';
      const src = (ev.source_key || '').split('/').pop();
      if (ev.ok) {
        text = '· source ' + idx + '/' + tot + ' done · ' + src + ' · ' +
               (ev.n_contributions || 0) + ' contribs, ' +
               (ev.wall_ms || 0) + ' ms' + dep;
      } else {
        text = '· source ' + idx + '/' + tot + ' FAILED · ' + src +
               ': ' + (ev.error || 'unknown');
      }
    } else if (ev.kind === 'digests_aggregated') {
      text = '· aggregated ' + (ev.n_digests_ok || 0) + '/' +
             (ev.n_total || 0) + ' digests' +
             (ev.n_pydantic_fail
                ? ', ' + ev.n_pydantic_fail + ' pydantic-rejected'
                : '');
    } else if (ev.kind === 'done') {
      text = '✓ done — ' + (ev.n_sources || 0) + ' sources, ' +
             'cov=' + (ev.n_sections_covered || 0) + '/' +
             (ev.n_sections || 0) + ', ' +
             'empty=' + (ev.n_empty_sections || 0) + ', ' +
             'orph=' + (ev.n_orphan_code_refs || 0) +
             ' (' + (ev.wall_ms || 0) + ' ms)';
    }
  }
  // sawc_write — Section-Aware Writer-Critic (SurveyGen-I §3.2
  // + MAMM-Refine). Stage-parallel; N=3 best-of-N per section; per-
  // section critic-pick. Emits 6 event kinds so the live progress
  // stream has steady cadence across the stage loop.
  if (stepName === 'sawc_write') {
    if (ev.kind === 'start') {
      text = '· starting writes for ' + (ev.chapter_title || ev.chapter_id || 'chapter') +
             ' (' + (ev.n_sections || 0) + ' sections × 3 drafts = ' +
             (ev.n_total_drafts || 0) + ' draft calls + critic picks across ' +
             (ev.n_stages || 0) + ' stages)';
    } else if (ev.kind === 'stage_start') {
      const sids = (ev.section_ids || []).join(', ');
      text = '· stage ' + (ev.stage_idx ?? '?') + ' starting (' +
             (ev.n_sections_in_stage || 0) + ' sections in parallel: ' +
             sids + ')';
    } else if (ev.kind === 'section_draft_done') {
      const di = (ev.draft_idx ?? 0) + 1;
      const tot = ev.n_total || 3;
      const sid = ev.section_id || '?';
      const dep = ev.deployment ? ' [' + ev.deployment + ']' : '';
      if (ev.ok) {
        text = '· ' + sid + ' draft ' + di + '/' + tot + ' done · ' +
               (ev.n_paragraphs || 0) + ' paras, ' +
               (ev.n_citations || 0) + ' cites, ' +
               (ev.wall_ms || 0) + ' ms' +
               (ev.n_violations ? ', ' + ev.n_violations + ' viol' : '') + dep;
      } else {
        text = '· ' + sid + ' draft ' + di + '/' + tot + ' FAILED: ' +
               (ev.error || 'unknown');
      }
    } else if (ev.kind === 'section_picked') {
      const sid = ev.section_id || '?';
      const fb = ev.fallback ? ' [fallback=' + ev.fallback + ']' : '';
      const dep = ev.deployment_critic ? ' [' + ev.deployment_critic + ']' : '';
      if (ev.chosen_idx === -1) {
        text = '· ' + sid + ' all 3 drafts failed → placeholder';
      } else {
        text = '· ' + sid + ' picked draft ' + ev.chosen_idx +
               ' (score=' + (ev.structural_score || 0).toFixed(2) +
               (ev.n_violations ? ', ' + ev.n_violations + ' viol' : '') +
               ')' + fb + dep;
      }
    } else if (ev.kind === 'section_done') {
      const sid = ev.section_id || '?';
      const fb = ev.fallback ? ' [' + ev.fallback + ']' : '';
      text = '· ' + sid + ' written — ' + (ev.n_paragraphs || 0) + ' paras, ' +
             (ev.n_code_refs || 0) + ' refs, ' +
             (ev.n_citations || 0) + ' cites, ' +
             ((ev.total_chars || 0) / 1000).toFixed(1) + 'k chars, ' +
             (ev.wall_ms || 0) + ' ms' + fb;
    } else if (ev.kind === 'stage_done') {
      text = '✓ stage ' + (ev.stage_idx ?? '?') + ' complete: ' +
             (ev.n_completed || 0) + ' sections written, ' +
             (ev.n_failed || 0) + ' failed (' +
             (ev.wall_ms || 0) + ' ms)';
    } else if (ev.kind === 'done') {
      text = '✓ done — ' + (ev.n_completed || 0) + '/' +
             (ev.n_sections || 0) + ' sections, ' +
             (ev.n_fallback || 0) + ' fallbacks, ' +
             (ev.n_repairs || 0) + ' repairs, ' +
             (ev.total_drafts_fired || 0) + ' drafts fired' +
             ' (' + (ev.wall_ms || 0) + ' ms)';
    }
  }
  // checklist_eval — 12 binary criteria (7 deterministic pre-gates +
  // 5 LLM-judge). Fast node (1 LLM call total). Emits 4 event kinds.
  if (stepName === 'checklist_eval') {
    if (ev.kind === 'start') {
      text = '· starting checklist for ' + (ev.chapter_title || ev.chapter_id || 'chapter') +
             ' (' + (ev.n_total_criteria || 0) + ' criteria, threshold ' +
             ((ev.pass_threshold || 0.8) * 100).toFixed(0) + '%)';
    } else if (ev.kind === 'pregates_done') {
      const failed = ev.names_failed || [];
      text = '· pre-gates: ' + (ev.n_passed || 0) + '/' +
             (ev.n_pregate || 0) + ' passed' +
             (failed.length
                ? ' · failed: ' + failed.slice(0, 3).join(', ') +
                  (failed.length > 3 ? ` (+${failed.length - 3})` : '')
                : '');
    } else if (ev.kind === 'judge_request') {
      text = '· LLM judge: dispatching (' +
             ((ev.chapter_chars || 0) / 1000).toFixed(1) + 'k chars chapter' +
             (ev.truncated ? ', truncated' : '') + ')…';
    } else if (ev.kind === 'judge_done') {
      const failed = ev.names_failed || [];
      const dep = ev.deployment ? ' [' + ev.deployment + ']' : '';
      const rep = ev.repaired ? ' (repaired)' : '';
      text = '· LLM judge done: ' + (ev.n_passed || 0) + '/' +
             (ev.n_llm || 0) + ' passed' + rep +
             (failed.length
                ? ' · failed: ' + failed.slice(0, 3).join(', ') +
                  (failed.length > 3 ? ` (+${failed.length - 3})` : '')
                : '') +
             ' (' + (ev.wall_ms || 0) + ' ms)' + dep;
    } else if (ev.kind === 'done') {
      const passMark = ev.chapter_passed ? '✓ PASSED' : '✗ FAILED';
      text = '✓ done — ' + passMark + ' — ' +
             (ev.n_passed || 0) + '/' + (ev.n_total || 0) +
             ' criteria (' + ((ev.pass_rate || 0) * 100).toFixed(0) + '%), ' +
             (ev.n_failed_feedback || 0) + ' feedback notes' +
             ' (' + (ev.wall_ms || 0) + ' ms)';
    }
  }
  // render_audit_write — Final node. Zero LLM calls. Renders README.md
  // via Jinja2 + runs SHA-256 round-trip audit on code refs. 5 event kinds.
  if (stepName === 'render_audit_write') {
    if (ev.kind === 'start') {
      text = '· starting render for ' + (ev.chapter_title || ev.chapter_id || 'chapter') +
             ' (' + (ev.n_sections || 0) + ' sections · mgsr ' +
             (ev.mgsr_halt_reason || '?') + ')';
    } else if (ev.kind === 'inputs_loaded') {
      text = '· vaults loaded: ' + (ev.n_vault_files_loaded || 0) + '/' +
             (ev.n_sources || 0) + ' source vaults' +
             (ev.n_vault_files_skipped
               ? ', ' + ev.n_vault_files_skipped + ' skipped'
               : '') +
             ' · ' + (ev.n_vault_entries || 0) + ' total vault entries';
    } else if (ev.kind === 'rendered') {
      const auditMark = ev.audit_passed ? '✓' : '✗';
      text = '· rendered chapter (' +
             ((ev.chapter_chars || 0) / 1000).toFixed(1) + 'k chars, ' +
             (ev.n_sections_rendered || 0) + ' sections) · ' +
             'audit=' + auditMark + ' refs=' +
             (ev.n_code_refs_resolved || 0) + '/' +
             ((ev.n_code_refs_resolved || 0) +
              (ev.n_code_refs_missing || 0)) +
             (ev.n_code_refs_missing
               ? ' · miss=' + ev.n_code_refs_missing : '') +
             (ev.n_code_refs_drift
               ? ' · drift=' + ev.n_code_refs_drift : '') +
             (ev.sentinels_in_output
               ? ' · sent=' + ev.sentinels_in_output : '');
    } else if (ev.kind === 'artifacts_written') {
      const names = (ev.artifact_names || []).join(', ');
      text = '· wrote ' + (ev.n_artifacts || 0) + ' artifacts (' +
             ((ev.total_bytes || 0) / 1000).toFixed(1) + 'k bytes total) — ' +
             names;
    } else if (ev.kind === 'done') {
      const mark = ev.audit_passed ? '✓ AUDIT PASSED' : '✗ AUDIT FAILED';
      text = '✓ done — ' + mark + ' · ' +
             (ev.n_artifacts || 0) + ' artifacts, ' +
             ((ev.rendered_chars || 0) / 1000).toFixed(1) + 'k chars rendered' +
             (ev.n_missing ? ' · ' + ev.n_missing + ' missing refs' : '') +
             (ev.n_byte_drift ? ' · ' + ev.n_byte_drift + ' drift' : '') +
             (ev.sentinels_in_output
               ? ' · ' + ev.sentinels_in_output + ' unresolved sentinels'
               : '') +
             ' (' + (ev.wall_ms || 0) + ' ms)';
    }
  }
  // mgsr_replan — Memory-Guided Structure Replanner (SurveyGen-I +
  // CoRefine). Fast path = trivial_pass (no LLM call) when chapter
  // already passed checklist. Slow path = 1 LLM call emitting typed
  // replan actions on the outline DAG. 5 event kinds.
  if (stepName === 'mgsr_replan') {
    if (ev.kind === 'start') {
      const fmtRate = ((ev.pass_rate || 0) * 100).toFixed(0);
      text = '· starting replan for ' + (ev.chapter_title || ev.chapter_id || 'chapter') +
             ' (pass=' + fmtRate + '%, ' +
             (ev.n_failed_criteria || 0) + ' failed criteria)';
    } else if (ev.kind === 'trivial_pass') {
      text = '· chapter already passed (' +
             ((ev.pass_rate || 0) * 100).toFixed(0) +
             '%) — halting trivially, no LLM call';
    } else if (ev.kind === 'llm_request') {
      text = '· LLM replan: dispatching (' +
             (ev.n_failed_criteria || 0) + ' failed criteria)…';
    } else if (ev.kind === 'llm_done') {
      const dep = ev.deployment ? ' [' + ev.deployment + ']' : '';
      const rep = ev.repaired ? ' (repaired)' : '';
      const halt = ev.halt ? 'halt' : 'continue';
      if (ev.error) {
        text = '· LLM replan FAILED — fallback halt (' +
               (ev.wall_ms || 0) + ' ms)';
      } else {
        text = '· LLM replan done: ' + halt + ', ' +
               (ev.n_actions || 0) + ' actions, conf=' +
               ((ev.confidence || 0) * 100).toFixed(0) + '%' +
               rep + ' (' + (ev.wall_ms || 0) + ' ms)' + dep;
      }
    } else if (ev.kind === 'done') {
      const mark = ev.halt ? '✓ HALT' : '↻ LOOP';
      text = '✓ done — ' + mark + ' (' + (ev.halt_reason || '?') + '), ' +
             (ev.n_actions || 0) + ' actions, conf=' +
             ((ev.confidence || 0) * 100).toFixed(0) + '%' +
             ' (' + (ev.wall_ms || 0) + ' ms)';
    }
  }
  if (text) el.textContent = text;
}

export async function _refreshSynthCardsFromState(threadId, expectedField) {
  const maxAttempts = expectedField ? 6 : 1;
  for (let i = 0; i < maxAttempts; i++) {
    try {
      const r = await fetch(Sa.API + '/synth/debug/graph/' + threadId + '/state');
      if (r.ok) {
        const data = await r.json();
        const values = data.values || {};
        // data.next is LangGraph's snap.next — the authoritative set of
        // nodes currently scheduled / executing. Passing it through lets
        // _renderSynthGraph distinguish CoRefine-loop re-entries from
        // truly completed steps (see comment there).
        const nextNodes = Array.isArray(data.next) ? data.next : null;
        if (!expectedField || _synthFieldPresent(values, expectedField)) {
          deps.renderSynthCards?.(values, nextNodes);
          return;
        }
      }
    } catch (e) { /* transient */ }
    await sleep(250 + 150 * i);
  }
}

export async function pollStudyState(sid) {
  const url = Sa.API + '/synth/' + sid + '/events';
  let es;
  try {
    es = new EventSource(url);
  } catch (e) {
    deps.markSynthFailed?.('Study EventSource open failed: ' + String(e));
    _resetStudyState();
    deps.refreshSynthStartState?.();
    return;
  }
  // Helper: open per-chapter SSE for the currently-active chapter if
  // we haven't already. Debounced (120 ms).
  let _studyAttachTimer = null;
  const _maybeAttachCurrentChapterSSE = async () => {
    // If the user pinned to a specific chapter (clicked its strip cell),
    // do NOT yank the canvas back to the orchestrator's current chapter.
    if (Sy.studyPinnedChapterId &&
        Sy.studyPinnedChapterId !== Sy.studyCurrentChapterId) return;
    const chTid = Sy.studyCurrentChapterThreadId;
    if (!chTid) return;
    if (Sy.synthThreadId === chTid) return;
    deps.resetSynthCards?.();
    _resetSynthEventBuffer();
    const _ndr = _getNodeDrawerRef();
    if (_ndr && _ndr.reset) {
      _ndr.reset();
    }
    Sy.setSynthThreadId(chTid);
    Sy.set_synthLiveEventReceived(false);
    // Immediately paint the running chapter's CURRENT node progress from its
    // checkpoint — otherwise, attaching mid-node (e.g. during a long
    // sawc_write) leaves the canvas on the empty default graph until the next
    // live event fires. Mirrors the strip-cell-click path. Then stream live.
    try {
      const r = await fetch(Sa.API + '/synth/debug/graph/' + chTid + '/state');
      if (r.ok && Sy.synthThreadId === chTid) {
        const data = await r.json();
        deps.renderSynthCards?.(
          data.values || {},
          Array.isArray(data.next) ? data.next : null,
        );
      }
    } catch (_) { /* fall back to live events only */ }
    if (Sy.synthThreadId !== chTid) return;   // switched chapters during fetch
    pollSynthState(chTid);
    deps._highlightStripCell?.(Sy.studyCurrentChapterId);
  };
  const _scheduleAttachCurrent = () => {
    if (_studyAttachTimer) clearTimeout(_studyAttachTimer);
    _studyAttachTimer = setTimeout(() => {
      _studyAttachTimer = null;
      _maybeAttachCurrentChapterSSE();
    }, 120);
  };

  es.onmessage = (msg) => {
    if (Sy.studyThreadId !== sid) {
      try { es.close(); } catch (_) {}
      return;
    }
    let ev;
    try { ev = JSON.parse(msg.data); } catch (_) { return; }

    if (ev.step === 'study' && ev.kind === 'study_start') {
      const ids = ev.chapter_ids || [];
      _renderChStrip(ids);
      _applyChStripTitles(Si.activeSlug);   // upgrade ids → real titles
      _showChStrip(true);
      _setSynthStagePill('working', 'Working · 0/' + ids.length);
      return;
    }
    if (ev.step === 'study' && ev.kind === 'chapter_running') {
      const cid = ev.chapter_id;
      const chTid = ev.chapter_thread_id;
      Sy.setStudyCurrentChapterId(cid);
      Sy.setStudyCurrentChapterThreadId(chTid || null);
      if (cid && chTid) {
        Sy.studyChapterThreads.set(cid, chTid);
        // Stash on the cell dataset too.
        const cell = Sy.chstripCellsEl && Sy.chstripCellsEl.querySelector(
          '.fw-chstrip-cell[data-chapter-id="' + cid.replace(/"/g, '\\"') + '"]'
        );
        if (cell) cell.dataset.chapterThreadId = chTid;
      }
      deps._markChStripCell?.(cid, 'running');
      // Chapter-level progress only (per 2026-06-07 user request):
      // `Working · X/N` where X = chapter ordinal currently being
      // synthesized, N = total chapters in this study. Per-node ordinal
      // (which the old commit's per-chapter `_markSynthCardRunning`
      // would briefly overwrite this with) is suppressed during study
      // mode — see `_markSynthCardRunning` / `_renderSynthGraph` guards.
      const _pos   = ev.position || '?';
      const _total = ev.n_total || Sy.studyChapterIds.length || '?';
      _setSynthStagePill('working', 'Working · ' + _pos + '/' + _total);
      try {
        document.dispatchEvent(new CustomEvent('dd:synth:chapter-running', {
          detail: { chapter_id: cid, thread_id: chTid || null },
        }));
      } catch (_) {}
      _scheduleAttachCurrent();
      return;
    }
    if (ev.step === 'study' && ev.kind === 'chapter_done') {
      const cid = ev.chapter_id;
      const status = ev.status || 'done';
      deps._markChStripCell?.(cid, status);
      deps._markChStripCellTime?.(cid, ev.wall_ms);   // per-chapter wall on the cell
      if (status === 'failed') {
        showToast('Chapter ' + cid + ' failed: ' +
          (ev.error || 'unknown error') + ' — continuing.');
      }
      if (cid === Sy.studyCurrentChapterId) {
        Sy.setStudyCurrentChapterId(null);
        Sy.setStudyCurrentChapterThreadId(null);
      }
      Sy.setSynthThreadId(null);
      try {
        document.dispatchEvent(new CustomEvent('dd:synth:chapter-done', {
          detail: { chapter_id: cid, status: status },
        }));
      } catch (_) {}
      // NOTE: Step-5 auto-refresh moved to `chapter_ready` so the Study
      // page reloads its chapter list THE INSTANT each chapter becomes
      // readable, not after the whole book finishes.
      return;
    }
    // Bundle 6 (2026-05-25) — Streaming chapter delivery.
    // `chapter_ready` fires the moment a chapter's render_audit_write
    // completes successfully — the chapter is now readable in MinIO. We:
    //   - lock the cell to the `done` visual (idempotent with chapter_done)
    //   - surface a toast (only for the user actively waiting on Study)
    //   - reload the Step-5 Study list so the new chapter appears
    //     immediately, instead of after ~2h when the orchestrator emits
    //     its final `done` event.
    if (ev.step === 'study' && ev.kind === 'chapter_ready') {
      const cid = ev.chapter_id;
      deps._markChStripCell?.(cid, 'done');
      try {
        const studyPanel = document.querySelector('#fw-step-5-panel');
        if (studyPanel && studyPanel.classList.contains('active') &&
            Si.activeSlug) {
          import('@dd/study/study.js').then(m => m.loadStudyChapters(Si.activeSlug)).catch(() => {});
        }
      } catch (_) {}
      // "Chapter X ready to read (Y/N)" toast removed 2026-06-07 per
      // user request — the chstrip cell flipping to ✓ + the pill
      // updating already convey readiness.
      return;
    }
    // Ship #7 (2026-05-24): book_harmonize study-level events.
    // 2026-06-08: also updates the persistent #fw-book-harmonize row in
    // the chapter strip (toasts are transient; the row sticks so the
    // user sees the final outcome after the toast fades).
    if (ev.step === 'study' && ev.kind === 'book_harmonize_start') {
      _setSynthStagePill('working', 'Harmonizing chapters…');
      _updateBookHarmonize('running',
        (ev.n_chapters || '?') + ' chapters');
      showToast(`Cross-chapter harmonization started (${ev.n_chapters || '?'} chapters)`);
      return;
    }
    if (ev.step === 'study' && ev.kind === 'book_harmonize_skipped') {
      console.log('[book-harmonize] skipped:', ev.reason);
      _updateBookHarmonize('skipped',
        ev.reason === 'fewer_than_2_rendered_chapters'
          ? 'Skipped — needs ≥2 chapters'
          : 'Skipped'
      );
      return;
    }
    if (ev.step === 'study' && ev.kind === 'book_harmonize_done') {
      const patched = ev.n_chapters_patched || 0;
      const overwritten = ev.n_chapters_overwritten || 0;
      const issues = ev.n_chapters_with_issues || 0;
      const cache = ev.cache_hit ? ' (cache)' : '';
      let label;
      if (overwritten > 0) {
        label = `${overwritten}/${issues} patched${cache}`;
        showToast(
          `Harmonized ${overwritten}/${issues} chapter(s) for cross-chapter consistency${cache}`
        );
      } else if (issues === 0) {
        label = `Verified, no patches${cache}`;
        showToast(`Cross-chapter coherence verified, no patches needed${cache}`);
      } else {
        label = `${issues} issues, 0 patched${cache}`;
      }
      _updateBookHarmonize('done', label);
      return;
    }
    if (ev.step === 'study' && ev.kind === 'study_done') {
      if (_studyAttachTimer) {
        clearTimeout(_studyAttachTimer);
        _studyAttachTimer = null;
      }
      // Freeze the navbar total at the authoritative cumulative wall-clock.
      stopElapsed('synth', Number(ev.total_wall_ms || 0) || undefined);
      setSynthRunStartMs(0);   // run ended — don't seed a future ticker
      Sy.setStudyCurrentChapterId(null);
      Sy.setStudyCurrentChapterThreadId(null);
      const ok = ev.n_completed || 0;
      const tot = ev.n_total || Sy.studyChapterIds.length;
      const fail = ev.n_failed || 0;
      const final = ev.final_status || 'done';
      if (final === 'cancelled') {
        showToast('Study cancelled: ' + ok + '/' + tot + ' chapters done.');
        _setSynthStagePill('cancelled');
      } else if (fail > 0) {
        showToast('Study finished with ' + fail + ' failed chapter(s); ' +
          ok + '/' + tot + ' succeeded.');
        _setSynthStagePill('done', 'Done (' + ok + '/' + tot + ')');
      } else {
        showToast('All ' + tot + ' chapters synthesized. ' +
          'Open Step 5 to study.');
        _setSynthStagePill('done', 'Done (' + ok + '/' + tot + ')');
      }
      // Study finished (or cancelled) — forget the resume key + thread.
      // Without this the key lingers and a page reload re-opens this
      // finished study's SSE, replaying its cached Redis snapshot
      // (chapter_ready + study_done) and re-marking every chapter "Done"
      // — a phantom "cached study" that survives hard refresh even after
      // the artifacts/checkpoints are wiped. The durable strip state is
      // rebuilt from MinIO render status on reload (_hydrateChStripFrom-
      // Chapters), not from this ephemeral replay, so dropping it is safe.
      if (Si.activeSlug) { try { deps._forgetActiveStudy?.(Si.activeSlug); } catch (_) {} }
      Sy.setStudyThreadId(null);
      // Re-sync strip + Start/Resume button from authoritative server render
      // status now the run ended: a cancelled/partial run → "Resume Synth"
      // (keeps completed chapters); a full run → "Start Synth".
      if (Si.activeSlug) _hydrateChStripFromChapters(Si.activeSlug).catch(() => {});
      return;
    }
    if (ev.step === 'synth' && ev.kind === 'terminal') {
      try { es.close(); } catch (_) {}
      if (Si.activeSlug) deps._forgetActiveStudy?.(Si.activeSlug);
      Sy.setStudyThreadId(null);
      deps.refreshSynthStartState?.();
      return;
    }
  };
  es.onerror = () => {
    if (Sy.studyThreadId !== sid) {
      try { es.close(); } catch (_) {}
    }
  };
}

export async function pollSynthState(threadId) {
  const url = Sa.API + '/synth/' + threadId + '/events';
  let es;
  try {
    es = new EventSource(url);
  } catch (e) {
    deps.markSynthFailed?.('EventSource open failed: ' + String(e));
    Sy.setSynthThreadId(null);
    deps.refreshSynthStartState?.();
    return;
  }
  es.onmessage = async (msg) => {
    if (Sy.synthThreadId !== threadId) {
      try { es.close(); } catch (_) {}
      return;
    }
    let ev;
    try { ev = JSON.parse(msg.data); } catch (_) { return; }
    // SSE replay window (last 200 events from Redis snapshot) fires on
    // every connect — for a DONE chapter this re-delivers ~14 historical
    // start/done pairs in rapid succession, and each used to trigger
    // `_refreshSynthCardsFromState` → /state fetch → renderSynthCards
    // → setStatus on every node, causing the visible KPI flicker the
    // user reported (2026-06-07). Live events stay (<20s old); replays
    // (older than 20s OR no ts) are processed for buffering + drawer
    // but skip the state-refresh fetches.
    const _isLive = ev.ts && (Date.now() / 1000 - ev.ts) < 20;
    if (_isLive) {
      Sy.set_synthLiveEventReceived(true);
    }
    if (ev.step === 'synth' && ev.kind === 'terminal') {
      // REPLAY-SAFE: when the SSE snapshot replays a `terminal` event
      // for a chapter the user is just VIEWING (clicked a done cell),
      // this used to fire the live-run cleanup path —
      // `setSynthThreadId(null)` + `refreshSynthStartState()` →
      // pill resets to 'Idle' ~1s after the user clicked, which is
      // exactly the "Completed then flips to Idle" symptom the user
      // reported. Live terminal events still get the cleanup; replays
      // (old ts) just close the stream and leave the painted state.
      if (!_isLive) {
        try { es.close(); } catch (_) {}
        return;
      }
      await _refreshSynthCardsFromState(threadId, 'status');
      const status = ev.status || 'done';
      if (status === 'failed') {
        if (!Sy.studyThreadId) deps.markSynthFailed?.(ev.error || 'Synth failed.');
      } else if (status === 'cancelled') {
        if (!Sy.studyThreadId) {
          showToast('Synth cancelled. Checkpoints up to the cancel point are preserved.');
          _setSynthStagePill('cancelled');
        }
      } else if (status === 'not_implemented') {
        // Router stub — no run happened.
      } else {
        if (!Sy.studyThreadId) _setSynthStagePill('done');
      }
      try { es.close(); } catch (_) {}
      if (!Sy.studyThreadId) {
        Sy.setSynthThreadId(null);
        deps.refreshSynthStartState?.();
      }
      try {
        document.dispatchEvent(new CustomEvent('dd:synth:terminal', {
          detail: { status: status, thread_id: threadId },
        }));
      } catch (_) {}
      return;
    }
    if (ev.step) {
      _bufferSynthEvent(ev);
      if (ev.kind === 'start') {
        _markSynthCardRunning(ev.step);
        // Replay events skip the state refresh — the inline /state
        // fetch on click already painted the chapter's authoritative
        // final state, and re-fetching for every replayed start/done
        // pair produced visible KPI flicker on done-chapter clicks.
        if (!_isLive) return;
        const stepIdx = Sy.SYNTH_NODE_ORDER.indexOf(ev.step);
        if (stepIdx > 0) {
          const prevStep = Sy.SYNTH_NODE_ORDER[stepIdx - 1];
          const prevField = Sy.SYNTH_STEP_TO_FIELD[prevStep];
          await _refreshSynthCardsFromState(threadId, prevField);
          _markSynthCardRunning(ev.step);
        }
      }
      if (ev.kind === 'done' && _isLive) {
        const field = Sy.SYNTH_STEP_TO_FIELD[ev.step];
        await _refreshSynthCardsFromState(threadId, field);
        try {
          document.dispatchEvent(new CustomEvent('dd:synth:node-done', {
            detail: { step: ev.step, field, thread_id: threadId },
          }));
        } catch (_) {}
      }
      _renderSynthLiveProgress(ev.step, ev);
      // Day 5: route to NodeDrawer if open for this synth node.
      const _ndrLive = _getNodeDrawerRef();
      if (_ndrLive && _ndrLive.isOpenFor('synth', ev.step)) {
        _ndrLive.appendEvent(ev);
      }
    }
  };
  es.onerror = () => {
    if (Sy.synthThreadId !== threadId) {
      try { es.close(); } catch (_) {}
    }
  };
}

export function _genSynthThreadId(slug) {
  // Canonical synth thread_id format — MUST match server-side
  // _make_thread_id in routers/v1/docs_distiller/synth.py.
  const uuid = (typeof crypto !== 'undefined' && crypto.randomUUID)
    ? crypto.randomUUID()
    : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
        const r = Math.random() * 16 | 0;
        return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
      });
  return 'docs-distiller/synth/' + slug + '/' + uuid;
}
