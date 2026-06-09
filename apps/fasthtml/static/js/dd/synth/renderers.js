// synth/renderers.js — SUBSTEP_RENDERERS for the 7 synth nodes
// (2026-06-08). Same shape as planner/renderers.js: each function
// takes the LangGraph state values dict and returns an HTML string
// for the Overview tab body. The drawer wraps the output in
// `.fw-node-drawer-results`; renderers emit their own KPI cards,
// distribution rows, tables, and metadata footer.
//
// Pattern (every renderer):
//   1. Headline KPI grid (4 cards — labels + values + sub-text)
//   2. Domain-specific visualization (table / list / coverage bar)
//   3. Metadata footer (model, hash, store_path, cache_hit)
//
// Data sources (`values.<stats_dict>`) — verified live 2026-06-08:
//   outline_sdp        → values.outline_stats
//   digest_construct   → values.digest_stats
//   sawc_write         → values.sawc_stats
//   sawc_derive        → values.derive_stats
//   checklist_eval     → values.checklist_stats
//   mgsr_replan        → values.mgsr_stats
//   render_audit_write → values.chapter_stats
import { escapeHtml } from '../shared/utils.js';

// ============================================================
// Shared helpers — copied from planner/renderers.js so the synth
// module stays self-contained. Tiny enough to duplicate.
// ============================================================
function _kpiCard(label, value, sub) {
  return (
    '<div class="fw-stat-card">' +
      '<div class="fw-stat-card-label">' + escapeHtml(label) + '</div>' +
      '<div class="fw-stat-card-value">' + escapeHtml(value) + '</div>' +
      (sub
        ? '<div class="fw-stat-card-sub">' + escapeHtml(sub) + '</div>'
        : '') +
    '</div>'
  );
}

function _kpiGrid(cards) {
  return '<div class="fw-stat-grid">' + cards.join('') + '</div>';
}

function _footer(parts) {
  return (
    '<div class="fw-stat-foot">' +
      parts.filter(Boolean).join(' · ') +
    '</div>'
  );
}

function _hashCode(hash) {
  if (!hash) return '—';
  return (
    'hash <code style="font-family:JetBrains Mono,monospace;' +
    'font-size:0.72rem">' + escapeHtml(String(hash).slice(0, 12)) + '</code>'
  );
}

function _pathCode(path) {
  if (!path) return null;
  return (
    'path <code style="font-family:JetBrains Mono,monospace;' +
    'font-size:0.72rem">' + escapeHtml(String(path)) + '</code>'
  );
}

function _wallMs(ms) {
  return (ms || 0) + ' ms';
}

function _empty(msg) {
  return '<div class="fw-empty">' + escapeHtml(msg) + '</div>';
}

// ============================================================
// 0. outline_sdp — Section-Defined Plan outline construction.
//   Headline KPIs: sections, depth, repairs, violations.
//   Below: violations table (when any), sample headings list.
// ============================================================
function renderOutline(values) {
  const s = values.outline_stats || {};
  if (!s.n_sections && !s.cache_hit) return _empty('no outline stats reported');

  const cards = _kpiGrid([
    _kpiCard('Sections', String(s.n_sections || 0),
      'final outline'),
    _kpiCard('Depth',    String(s.max_stage || 0),
      'staged review levels (' + (s.n_stages || 0) + ' stages)'),
    _kpiCard('Repairs',  String(s.n_repairs || 0),
      (s.n_removed_edges || 0) + ' edges pruned'),
    _kpiCard('Violations', String(s.n_violations || 0),
      s.n_violations ? 'schema-flagged' : 'clean'),
  ]);

  // Violation list — each entry is a {kind, detail} dict from the
  // backend, render the worst N inline so the user spots the failure
  // mode without opening Raw I/O.
  let viol = '';
  const vlist = Array.isArray(s.violations) ? s.violations : [];
  if (vlist.length) {
    const rows = vlist.slice(0, 12).map(v => {
      const kind = (v && typeof v === 'object' && v.kind) || 'issue';
      const detail = (v && typeof v === 'object' && v.detail) || JSON.stringify(v);
      return (
        '<tr style="border-bottom:1px solid var(--border)">' +
          '<td style="padding:5px 10px;font-family:JetBrains Mono,' +
            'monospace;font-size:0.72rem;color:var(--accent);' +
            'white-space:nowrap;vertical-align:top">' +
            escapeHtml(String(kind)) +
          '</td>' +
          '<td style="padding:5px 10px;font-size:0.82rem;' +
            'color:var(--text)">' + escapeHtml(String(detail)) +
          '</td>' +
        '</tr>'
      );
    }).join('');
    viol =
      '<div class="fw-stat-dist" style="margin-top:14px">' +
        '<div class="fw-stat-dist-title">Violations (' +
          vlist.length + ' total, showing ' +
          Math.min(12, vlist.length) + ')</div>' +
        '<div style="max-height:280px;overflow-y:auto;' +
          'border:1px solid var(--border);border-radius:4px">' +
          '<table style="width:100%;border-collapse:collapse;' +
            'font-family:Raleway"><tbody>' + rows + '</tbody></table>' +
        '</div>' +
      '</div>';
  }

  const foot = _footer([
    'N=' + (s.n_samples || 1) + ' samples · USC vote',
    'vault hashes referenced ' + (s.n_vault_hashes || 0),
    s.truncated ? '<strong style="color:var(--accent)">corpus truncated</strong>' : null,
    s.cache_hit ? '<strong>cache HIT</strong>' : 'cold',
    _wallMs(s.wall_ms),
    _hashCode(s.manifest_hash),
  ]);
  return cards + viol + foot;
}

// ============================================================
// 1. digest_construct — per-section evidence + code-ref aggregation.
//   Headline KPIs: sources, section coverage, orphans, merges.
//   Below: per-section coverage bar OR over-spread sources list.
// ============================================================
function renderDigest(values) {
  const s = values.digest_stats || {};
  if (!s.n_sections && !s.cache_hit) return _empty('no digest stats reported');

  const covered = s.n_sections_covered || 0;
  const total = s.n_sections || 0;
  const covPct = total ? Math.round(covered / total * 100) + '%' : '—';

  const cards = _kpiGrid([
    _kpiCard('Sources',  String(s.n_sources || 0),
      'evidence pool'),
    _kpiCard('Coverage', covered + '/' + total,
      covPct + ' of sections'),
    _kpiCard('Orphan refs', String(s.n_orphan_code_refs || 0),
      'code blocks no section claimed'),
    _kpiCard('Empty',    String(s.n_empty_sections || 0),
      s.n_merged_sections
        ? (s.n_merged_sections + ' sections merged')
        : null),
  ]);

  // Over-spread sources — when one doc is split across too many
  // sections (signals that the planner over-clustered).
  let overTbl = '';
  const over = Array.isArray(s.over_spread_sources) ? s.over_spread_sources : [];
  if (over.length) {
    const rows = over.slice(0, 10).map(o => {
      const src = (o && o.source) || (typeof o === 'string' ? o : '?');
      const n = (o && o.n_sections) || '?';
      return (
        '<tr style="border-bottom:1px solid var(--border)">' +
          '<td style="padding:5px 10px;font-family:JetBrains Mono,' +
            'monospace;font-size:0.72rem;color:var(--text-muted)">' +
            escapeHtml(String(src).split('/').slice(-2).join('/')) +
          '</td>' +
          '<td style="padding:5px 10px;font-size:0.78rem;' +
            'text-align:right;font-variant-numeric:tabular-nums">' +
            'spread across ' + n + ' sections' +
          '</td>' +
        '</tr>'
      );
    }).join('');
    overTbl =
      '<div class="fw-stat-dist" style="margin-top:14px">' +
        '<div class="fw-stat-dist-title">Over-spread sources (' +
          over.length + ')</div>' +
        '<div style="max-height:240px;overflow-y:auto;' +
          'border:1px solid var(--border);border-radius:4px">' +
          '<table style="width:100%;border-collapse:collapse">' +
            '<tbody>' + rows + '</tbody>' +
          '</table>' +
        '</div>' +
      '</div>';
  }

  const foot = _footer([
    'avg ' + (s.avg_sources_per_section || 0).toFixed(1) +
      ' src/section · ' +
      (s.avg_sections_per_source || 0).toFixed(1) + ' sections/src',
    'vault hashes ' + (s.n_total_vault_hashes || 0),
    s.n_pydantic_fail
      ? '<strong style="color:var(--accent)">' + s.n_pydantic_fail +
        ' Pydantic fails</strong>'
      : null,
    s.cache_hit ? '<strong>cache HIT</strong>' : 'cold',
    _wallMs(s.wall_ms),
    _hashCode(s.manifest_hash),
  ]);
  return cards + overTbl + foot;
}

// ============================================================
// 2. sawc_write — Section-Aware Writer-Critic. Per-section drafts,
//   critic picks, fallbacks. Shows CoRefine iter when looping.
// ============================================================
function renderSawc(values) {
  const s = values.sawc_stats || {};
  if (!s.n_sections && !s.cache_hit) return _empty('no sawc stats reported');

  const iter = Number(values.refine_iter || 0);
  const completed = s.n_completed || 0;
  const total = s.n_sections || 0;
  const fallbacks = s.n_fallback || 0;
  const repairs = s.n_repairs || 0;
  const pickerFb = s.n_picker_fallbacks || 0;

  const cards = _kpiGrid([
    _kpiCard('Completed', completed + '/' + total,
      total
        ? (Math.round(completed / total * 100) + '% sections')
        : null),
    _kpiCard('Iteration', iter > 0 ? ('iter ' + iter + '/5') : 'first pass',
      iter > 1 ? 'CoRefine loop active' : 'no replan yet'),
    _kpiCard('Fallbacks', String(fallbacks),
      pickerFb ? (pickerFb + ' picker fallbacks') : null),
    _kpiCard('Repairs',   String(repairs),
      repairs ? 'auto-fix passes' : 'clean'),
  ]);

  // Quick stats row — subtopics / citations / avg explanation length.
  let micro = '';
  const totSub = s.total_subtopics;
  const totCit = s.total_citations;
  const avgExp = s.avg_explanation_words;
  const avgSub = s.avg_subtopics_per_section;
  if (totSub !== undefined || totCit !== undefined) {
    micro =
      '<div class="fw-stat-dist" style="margin-top:14px">' +
        '<div class="fw-stat-dist-title">Content density</div>' +
        '<div class="fw-stat-dist-row">' +
          (totSub !== undefined
            ? '<div class="fw-stat-dist-cell">' +
                '<div class="fw-stat-dist-key">subtopics</div>' +
                '<div class="fw-stat-dist-val">' + totSub + '</div>' +
              '</div>'
            : '') +
          (avgSub !== undefined
            ? '<div class="fw-stat-dist-cell">' +
                '<div class="fw-stat-dist-key">avg/section</div>' +
                '<div class="fw-stat-dist-val">' +
                  (avgSub || 0).toFixed(1) + '</div>' +
              '</div>'
            : '') +
          (totCit !== undefined
            ? '<div class="fw-stat-dist-cell">' +
                '<div class="fw-stat-dist-key">citations</div>' +
                '<div class="fw-stat-dist-val">' + totCit + '</div>' +
              '</div>'
            : '') +
          (avgExp !== undefined
            ? '<div class="fw-stat-dist-cell">' +
                '<div class="fw-stat-dist-key">avg words/expl</div>' +
                '<div class="fw-stat-dist-val">' +
                  (avgExp || 0).toFixed(0) + '</div>' +
              '</div>'
            : '') +
        '</div>' +
      '</div>';
  }

  const foot = _footer([
    'stages ' + (s.n_stages || 0),
    'drafts fired ' + (s.n_total_drafts_fired || 0),
    'critic picks ' + (s.n_critic_picks || 0),
    s.cache_hit ? '<strong>cache HIT</strong>' : 'cold',
    _wallMs(s.wall_ms),
    _hashCode(s.manifest_hash),
  ]);
  return cards + micro + foot;
}

// ============================================================
// 3. sawc_derive — analogical-prompted derived code generation. Lots
//   of attempts per subtopic; promoted ones land in the chapter.
// ============================================================
function renderDerive(values) {
  const s = values.derive_stats || {};
  if (s.enabled === false) {
    return _kpiGrid([
      _kpiCard('Status', 'DISABLED',
        'KD_SAWC_DERIVE flag off'),
      _kpiCard('Chapter', String(s.chapter_id || '—'), null),
      _kpiCard('Wall',   _wallMs(s.wall_ms),
        'no LLM calls'),
      _kpiCard('Subtopics', String(s.n_subtopics_total || 0),
        'all kept verbatim'),
    ]) + _footer([
      'sawc_derive is the optional analogical-code expansion pass — ' +
        'enable with KD_SAWC_DERIVE=true to promote derived examples.',
    ]);
  }
  if (!s.n_subtopics_total) {
    return _empty('no derive stats reported');
  }

  const promoted = s.n_promoted || 0;
  const subTotal = s.n_subtopics_total || 0;
  const promPct = subTotal
    ? Math.round(promoted / subTotal * 100) + '%' : '—';
  const rejAst = s.n_rejected_ast || 0;
  const rejLen = s.n_rejected_len || 0;
  const rotFail = s.n_rotator_fail || 0;
  const thin = s.n_candidates_thin || 0;

  const cards = _kpiGrid([
    _kpiCard('Promoted', String(promoted),
      promPct + ' of ' + subTotal + ' subtopics'),
    _kpiCard('AST reject', String(rejAst),
      'parse failures'),
    _kpiCard('Len reject', String(rejLen),
      'too-short candidates'),
    _kpiCard('Rotator fail', String(rotFail),
      thin ? (thin + ' thin candidates') : 'clean'),
  ]);

  // Attempt log — useful sample of which subtopics tried derivation.
  let attemptTbl = '';
  const attempts = Array.isArray(s.attempts) ? s.attempts : [];
  if (attempts.length) {
    const rows = attempts.slice(0, 12).map(a => {
      const sub = (a && a.subtopic_id) || '?';
      const verdict = (a && a.verdict) || (a && a.status) || '—';
      const dot = verdict === 'promoted'
        ? '<span style="color:#2a8b46">●</span>'
        : verdict === 'rejected_ast' || verdict === 'rejected_len'
          ? '<span style="color:var(--error-text)">●</span>'
          : '<span style="color:var(--text-muted)">●</span>';
      return (
        '<tr style="border-bottom:1px solid var(--border)">' +
          '<td style="padding:5px 10px;width:24px">' + dot + '</td>' +
          '<td style="padding:5px 10px;font-family:JetBrains Mono,' +
            'monospace;font-size:0.72rem;color:var(--text-muted)">' +
            escapeHtml(String(sub)) + '</td>' +
          '<td style="padding:5px 10px;font-size:0.78rem">' +
            escapeHtml(String(verdict)) + '</td>' +
        '</tr>'
      );
    }).join('');
    attemptTbl =
      '<div class="fw-stat-dist" style="margin-top:14px">' +
        '<div class="fw-stat-dist-title">Attempt log (showing ' +
          Math.min(12, attempts.length) + ' of ' + attempts.length + ')</div>' +
        '<div style="max-height:280px;overflow-y:auto;' +
          'border:1px solid var(--border);border-radius:4px">' +
          '<table style="width:100%;border-collapse:collapse">' +
            '<tbody>' + rows + '</tbody>' +
          '</table>' +
        '</div>' +
      '</div>';
  }

  const foot = _footer([
    'chapter ' + escapeHtml(String(s.chapter_id || '?')),
    'schema ' + escapeHtml(String(s.schema_version || '?')),
    _wallMs(s.wall_ms),
  ]);
  return cards + attemptTbl + foot;
}

// ============================================================
// 4. checklist_eval — per-criterion LLM judge with pre-gate.
//   Headline KPIs: pass rate, chapter verdict, pre-gate vs LLM.
// ============================================================
function renderChecklist(values) {
  const s = values.checklist_stats || {};
  if (!s.n_total && !s.cache_hit) return _empty('no checklist stats reported');

  const passed = s.n_passed || 0;
  const total = s.n_total || 0;
  const rate = s.pass_rate !== undefined
    ? (s.pass_rate * 100).toFixed(0) + '%'
    : (total ? Math.round(passed / total * 100) + '%' : '—');
  const chapterOk = s.chapter_passed === true;
  const chapterFail = s.chapter_passed === false;

  const cards = _kpiGrid([
    _kpiCard('Pass rate', rate,
      passed + ' / ' + total + ' criteria'),
    _kpiCard('Chapter verdict',
      chapterOk ? '✓ PASS' : chapterFail ? '✕ FAIL' : '—',
      chapterOk ? 'all criteria met'
        : chapterFail ? 'below threshold' : 'pending'),
    _kpiCard('Pre-gate',
      (s.n_pregate_passed || 0) + '/' + (s.n_pregate_total || 0),
      'deterministic checks'),
    _kpiCard('LLM judged',
      (s.n_llm_passed || 0) + '/' + (s.n_llm_total || 0),
      s.judge_repaired ? (s.judge_repaired + ' repaired') : null),
  ]);

  // Failed criteria — surface what needs fixing.
  let failTbl = '';
  const failed = Array.isArray(s.names_failed) ? s.names_failed : [];
  if (failed.length) {
    const rows = failed.slice(0, 20).map(n =>
      '<tr style="border-bottom:1px solid var(--border)">' +
        '<td style="padding:5px 10px;width:24px;color:var(--error-text)">●</td>' +
        '<td style="padding:5px 10px;font-size:0.82rem">' +
          escapeHtml(String(n)) + '</td>' +
      '</tr>',
    ).join('');
    failTbl =
      '<div class="fw-stat-dist" style="margin-top:14px">' +
        '<div class="fw-stat-dist-title">Failed criteria (' +
          failed.length + ')</div>' +
        '<div style="max-height:280px;overflow-y:auto;' +
          'border:1px solid var(--border);border-radius:4px">' +
          '<table style="width:100%;border-collapse:collapse">' +
            '<tbody>' + rows + '</tbody>' +
          '</table>' +
        '</div>' +
      '</div>';
  }

  const foot = _footer([
    'feedback gen ' + (s.n_failed_feedback || 0),
    'judge ' + escapeHtml(String(s.deployment_judge || '—')),
    s.judge_wall_ms !== undefined ? ('judge ' + s.judge_wall_ms + ' ms') : null,
    s.cache_hit ? '<strong>cache HIT</strong>' : 'cold',
    _wallMs(s.wall_ms),
    _hashCode(s.manifest_hash),
  ]);
  return cards + failTbl + foot;
}

// ============================================================
// 5. mgsr_replan — Meta-Generation Self-Refine decision: halt or loop.
//   Headline KPIs: halt/loop, reason, confidence, action count.
// ============================================================
function renderMgsr(values) {
  const s = values.mgsr_stats || {};
  if (s.halt === undefined && !s.cache_hit) {
    return _empty('no mgsr stats reported');
  }

  const halt = s.halt === true;
  const conf = s.confidence !== undefined
    ? (s.confidence * 100).toFixed(0) + '%'
    : '—';
  const rate = s.pass_rate !== undefined
    ? (s.pass_rate * 100).toFixed(0) + '%'
    : '—';

  const cards = _kpiGrid([
    _kpiCard('Decision', halt ? '✓ HALT' : '↻ LOOP',
      halt ? 'chapter accepted' : 're-enter sawc_write'),
    _kpiCard('Reason',
      String(s.halt_reason || '—'),
      s.trivial_pass ? 'trivial-pass shortcut' : null),
    _kpiCard('Confidence', conf,
      'judge confidence'),
    _kpiCard('Actions',
      String(s.n_actions || 0),
      (s.n_failed_criteria || 0) + ' failed criteria addressed'),
  ]);

  // Decision card — a sentence-form summary of why MGSR landed where it did.
  const decisionPara =
    '<div class="fw-stat-dist" style="margin-top:14px">' +
      '<div class="fw-stat-dist-title">Decision summary</div>' +
      '<div style="padding:12px 14px;font-size:0.88rem;line-height:1.6;' +
        'border:1px solid var(--border);border-radius:4px;' +
        'background:rgba(0,0,0,0.015)">' +
        (halt
          ? 'Chapter <strong>halted</strong> at iter ' +
            (values.refine_iter || 1) + '/5 with confidence ' + conf +
            (s.chapter_passed === true ? '. Chapter passed checklist gate.' : '') +
            ' Reason: <code style="font-family:JetBrains Mono,monospace;' +
            'font-size:0.78rem">' + escapeHtml(String(s.halt_reason || '?')) +
            '</code>.'
          : 'Chapter <strong>looping back</strong> to sawc_write (iter ' +
            (values.refine_iter || 1) + '/5). ' +
            'Pass rate ' + rate + '; ' + (s.n_actions || 0) +
            ' refine action(s) queued.') +
      '</div>' +
    '</div>';

  const foot = _footer([
    s.cache_hit ? '<strong>cache HIT</strong>' : 'cold',
    _wallMs(s.wall_ms),
    _hashCode(s.manifest_hash),
  ]);
  return cards + decisionPara + foot;
}

// ============================================================
// 6. render_audit_write — final chapter materialization + byte audit.
//   Headline KPIs: audit verdict, artifacts, refs resolved, drift.
// ============================================================
function renderRenderAudit(values) {
  const s = values.chapter_stats || {};
  if (!s.n_artifacts && !s.cache_hit) {
    return _empty('no render/audit stats reported');
  }

  const ok = s.audit_passed === true;
  const fail = s.audit_passed === false;
  const refs = s.n_code_refs || 0;
  const resolved = s.n_resolved || 0;
  const missing = s.n_missing || 0;
  const drift = s.n_byte_drift || 0;
  const chars = s.rendered_chars || 0;

  const cards = _kpiGrid([
    _kpiCard('Audit',
      ok ? '✓ PASS' : fail ? '✕ FAIL' : '—',
      drift ? (drift + ' byte drift')
        : missing ? (missing + ' missing refs') : 'clean'),
    _kpiCard('Code refs',
      resolved + '/' + refs,
      refs
        ? (Math.round(resolved / refs * 100) + '% resolved')
        : 'none cited'),
    _kpiCard('Output size',
      (chars / 1000).toFixed(1) + 'k chars',
      (s.n_sections || 0) + ' sections, ' +
      (s.n_subtopics_total || 0) + ' subtopics'),
    _kpiCard('Artifacts',
      String(s.n_artifacts || 0),
      (s.n_citations_total || 0) + ' citations'),
  ]);

  // Vault stats — how much of the upstream vault was used.
  const vaultLine =
    '<div class="fw-stat-dist" style="margin-top:14px">' +
      '<div class="fw-stat-dist-title">Vault usage</div>' +
      '<div class="fw-stat-dist-row">' +
        '<div class="fw-stat-dist-cell">' +
          '<div class="fw-stat-dist-key">files loaded</div>' +
          '<div class="fw-stat-dist-val">' +
            (s.n_vault_files_loaded || 0) + '</div>' +
        '</div>' +
        '<div class="fw-stat-dist-cell">' +
          '<div class="fw-stat-dist-key">files skipped</div>' +
          '<div class="fw-stat-dist-val">' +
            (s.n_vault_files_skipped || 0) + '</div>' +
        '</div>' +
        '<div class="fw-stat-dist-cell">' +
          '<div class="fw-stat-dist-key">entries</div>' +
          '<div class="fw-stat-dist-val">' +
            (s.n_vault_entries || 0) + '</div>' +
        '</div>' +
        '<div class="fw-stat-dist-cell">' +
          '<div class="fw-stat-dist-key">orphan</div>' +
          '<div class="fw-stat-dist-val">' +
            (s.n_orphan_unused || 0) + '</div>' +
        '</div>' +
        '<div class="fw-stat-dist-cell">' +
          '<div class="fw-stat-dist-key">sentinels</div>' +
          '<div class="fw-stat-dist-val">' +
            (s.sentinels_in_output || 0) + '</div>' +
        '</div>' +
      '</div>' +
    '</div>';

  const foot = _footer([
    'template ' + escapeHtml(String(s.template_version || '?')),
    _pathCode(s.readme_path),
    s.cache_hit ? '<strong>cache HIT</strong>' : 'cold',
    _wallMs(s.wall_ms),
    _hashCode(s.manifest_hash),
  ]);
  return cards + vaultLine + foot;
}

// ============================================================
// Export keyed by SYNTH_SUBSTEP_FIELDS index (same shape as planner).
// ============================================================
export const SYNTH_RENDERERS = {
  0: renderOutline,
  1: renderDigest,
  2: renderSawc,
  3: renderDerive,
  4: renderChecklist,
  5: renderMgsr,
  6: renderRenderAudit,
};
