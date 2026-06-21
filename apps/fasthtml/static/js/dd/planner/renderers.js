// planner/renderers.js — SUBSTEP_RENDERERS, the per-substep card-body
// + drawer-detail HTML renderers. Extracted from planner.js Step 6
// (2026-06-05) — ~640 LOC of pure data with zero reverse refs back
// to planner.js, so extraction is clean. Re-exported through
// planner.js so existing consumers keep working.
import * as Sp from '@dd/shared/state/planner.js';
import { escapeHtml, fmtBytes } from '../shared/utils.js';

// Per-substep custom body renderers. Each returns an HTML string for
// the card body. Keyed by substep idx (matches Sp.PLANNER_SUBSTEP_FIELDS).
// Substeps without an entry here fall back to formatFieldValue/JSON.
export const SUBSTEP_RENDERERS = {
  // corpus_load — KPI-card Sc.grid + percentile distribution + meta footer.
  // Design follows 2026 dashboard best practices: 4 headline KPI cards
  // (one visual element max per card), then a compact percentile row,
  // then a metadata footer line. Avoids the "Christmas Tree" effect.
  0: function renderCorpusLoad(values) {
    const s = values.corpus_stats || {};
    if (!s.total_files) {
      return '<div class="fw-empty">no corpus stats reported</div>';
    }
    const rate = s.load_ms
      ? Math.round(s.total_files / s.load_ms * 1000)
      : 0;
    const ts = s.ingested_at
      ? new Date(s.ingested_at * 1000).toISOString().replace('T',' ').slice(0, 16) + ' UTC'
      : '—';

    // 4 KPI cards
    const kpi = (label, value, sub) =>
      '<div class="fw-stat-card">' +
        '<div class="fw-stat-card-label">' + escapeHtml(label) + '</div>' +
        '<div class="fw-stat-card-value">' + escapeHtml(value) + '</div>' +
        (sub ? '<div class="fw-stat-card-sub">' + escapeHtml(sub) + '</div>' : '') +
      '</div>';

    const cards =
      kpi('Files',        s.total_files.toLocaleString(), null) +
      kpi('Total bytes',  fmtBytes(s.total_bytes),        null) +
      kpi('Median page',  fmtBytes(s.median_bytes),       null) +
      kpi('Load time',    s.load_ms + ' ms',
                          rate ? rate.toLocaleString() + ' files/s' : null);

    // Compact distribution row — percentiles inline (log-scale not needed
    // when bytes span 3 orders of magnitude; raw numbers tell the story).
    const dist =
      '<div class="fw-stat-dist">' +
        '<div class="fw-stat-dist-title">Page size distribution</div>' +
        '<div class="fw-stat-dist-row">' +
          ['min', 'p10', 'median', 'p90', 'max'].map((k, i) => {
            const val = [s.min_bytes, s.p10_bytes, s.median_bytes,
                         s.p90_bytes, s.max_bytes][i];
            return '<div class="fw-stat-dist-cell">' +
                     '<div class="fw-stat-dist-key">' + k + '</div>' +
                     '<div class="fw-stat-dist-val">' + fmtBytes(val) + '</div>' +
                   '</div>';
          }).join('') +
        '</div>' +
      '</div>';

    // Footer — tier + ingested timestamp
    const foot =
      '<div class="fw-stat-foot">' +
        'Tier <strong>' + escapeHtml(s.tier_kind || '—') + '</strong>' +
        ' · ingested <strong>' + escapeHtml(ts) + '</strong>' +
      '</div>';

    return '<div class="fw-stat-grid">' + cards + '</div>' + dist + foot;
  },

  // embed_corpus — one-shot NIM pass; KPI cards show files / dim /
  // cache_hit / wall_ms / blob path. Cache-hit runs report ~10 ms
  // (just the HEAD + read); cold runs show the full embedding wall.
  1: function renderEmbedCorpus(values) {
    const s = values.embed_stats || {};
    if (!s.files) {
      return '<div class="fw-empty">no embed stats reported</div>';
    }
    const kpi = (label, value, sub) =>
      '<div class="fw-stat-card">' +
        '<div class="fw-stat-card-label">' + escapeHtml(label) + '</div>' +
        '<div class="fw-stat-card-value">' + escapeHtml(value) + '</div>' +
        (sub ? '<div class="fw-stat-card-sub">' + escapeHtml(sub) + '</div>' : '') +
      '</div>';

    const cacheLabel = s.cache_hit ? 'HIT' : 'cold';
    const cacheSub   = s.cache_hit
      ? 'reused stored vectors'
      : 'NIM embedding pass';
    const blobKB = s.blob_bytes
      ? Math.round(s.blob_bytes / 1024).toLocaleString() + ' KB blob'
      : null;

    const cards =
      kpi('Files',     s.files.toLocaleString(), null) +
      kpi('Dimensions', String(s.dim || 0),       'per-doc vector') +
      kpi('Cache',     cacheLabel,                cacheSub) +
      kpi('Wall time', (s.wall_ms || 0) + ' ms',  blobKB);

    const truncatedLine = (s.truncated_count !== undefined && s.truncated_count > 0)
      ? ' · truncated <strong>' + s.truncated_count.toLocaleString() + '</strong>'
      : '';

    const foot =
      '<div class="fw-stat-foot">' +
        'NIM <strong>nvidia/llama-nemotron-embed-1b-v2</strong>' +
        ' · hash <strong>' + escapeHtml(s.manifest_hash || '—') + '</strong>' +
        truncatedLine +
        ' · path <code style="font-family:JetBrains Mono,monospace;font-size:0.72rem">' +
          escapeHtml(s.store_path || '—') + '</code>' +
      '</div>';

    return '<div class="fw-stat-grid">' + cards + '</div>' + foot;
  },

  // off_topic — pure LLM-as-Judge (no cosine cleave). Every doc is
  // routed through the ParetoBandit-driven dd-grader cells: the bandit
  // picks the top-K best deployments by UCB score, calls each via
  // direct litellm, and submits reward signals so future calls learn
  // which deployments are reliable. KPI cards show keep/drop split +
  // bandit telemetry (deployments used + average reward). The verdict
  // sample table shows per-page judgments with the model that answered.
  2: function renderOffTopic(values) {
    const s = values.off_topic_stats || {};
    if (s.kept === undefined && s.dropped === undefined) {
      return '<div class="fw-empty">no off_topic stats reported</div>';
    }
    const kept     = s.kept    || 0;
    const dropped  = s.dropped || 0;
    const total    = s.total   || (kept + dropped);
    const dropPct  = total ? Math.round(dropped / total * 100) : 0;
    const elapsed  = s.elapsed_ms || 0;
    const judged   = s.llm_judged || 0;
    const lkeep    = s.llm_kept || 0;
    const ldrop    = s.llm_dropped || 0;
    const lerr     = s.llm_errors || 0;
    const depUsage = s.deployment_usage || [];

    const kpi = (label, value, sub) =>
      '<div class="fw-stat-card">' +
        '<div class="fw-stat-card-label">' + escapeHtml(label) + '</div>' +
        '<div class="fw-stat-card-value">' + escapeHtml(value) + '</div>' +
        (sub ? '<div class="fw-stat-card-sub">' + escapeHtml(sub) + '</div>' : '') +
      '</div>';

    const topDep = depUsage[0]
      ? (depUsage[0].deployment.split('/').pop() + ' · ' + depUsage[0].calls + ' calls')
      : '—';
    const cards =
      kpi('Kept',    kept.toLocaleString(),    'of ' + total.toLocaleString()) +
      kpi('Dropped', dropped.toLocaleString(), dropPct + '% off-topic') +
      kpi('LLM judged', judged.toLocaleString(),
          '+keep ' + lkeep + ' · -drop ' + ldrop +
          (lerr ? ' · err ' + lerr : '')) +
      kpi('Top deployment', topDep,
          depUsage.length > 1 ? '+' + (depUsage.length - 1) + ' more' : null);

    // LLM verdict table — focused on the NEW telemetry that matters
    // now (which model answered + latency), since cosine margin is
    // no longer a decision input. ALL decisions rendered into a
    // scrollable container (sticky header) so the operator can
    // inspect every per-page verdict without clicking through pages.
    // Sortable columns: click any header to sort asc; click again to
    // toggle desc. Sort state survives re-renders via module scope.
    Sp.set_lastOffTopicValues(values);
    const decisions = (s.judge_decisions || []).slice();
    // Apply current sort state.
    const sortCol = Sp._offTopicSort.col;
    const sortDir = Sp._offTopicSort.dir === 'desc' ? -1 : 1;
    const _key = d => {
      if (sortCol === 'verdict')    return (d.verdict || '');
      if (sortCol === 'deployment') return ((d.deployment || '').split('/').pop() || '');
      if (sortCol === 'latency')    return (d.latency_s !== undefined && d.latency_s !== null) ? d.latency_s : -1;
      if (sortCol === 'page')       return ((d.key || '').split('/').pop() || '');
      return 0;   // 'index' / null: keep original order
    };
    if (sortCol) {
      decisions.sort((a, b) => {
        const ka = _key(a); const kb = _key(b);
        if (ka < kb) return -1 * sortDir;
        if (ka > kb) return 1 * sortDir;
        return 0;
      });
    }
    let table = '';
    if (decisions.length) {
      const rows = decisions.map(d => {
        const keep = d.verdict === 'KEEP';
        const dot = keep
          ? '<span style="color:#2a8b46">●</span>'
          : '<span style="color:var(--error-text)">●</span>';
        const errBadge = d.error
          ? '<span title="' + escapeHtml(d.error) + '" style="margin-left:4px;font-size:0.7rem;color:var(--accent)">!</span>'
          : '';
        const leaf = (d.key || '').split('/').pop();
        const depShort = (d.deployment || '?').split('/').pop();
        const lat = (d.latency_s !== undefined && d.latency_s !== null)
          ? d.latency_s.toFixed(2) + 's' : '—';
        return '<tr>' +
          '<td style="padding:3px 8px 3px 0">' + dot + errBadge + '</td>' +
          '<td style="padding:3px 8px 3px 0;font-size:0.78rem;font-weight:600">' +
            escapeHtml(d.verdict || '—') + '</td>' +
          '<td style="padding:3px 8px 3px 0;font-family:JetBrains Mono,monospace;font-size:0.72rem;color:var(--text-muted)">' +
            escapeHtml(depShort) + '</td>' +
          '<td style="padding:3px 8px 3px 0;font-family:JetBrains Mono,monospace;font-size:0.72rem;color:var(--text-muted)">' +
            lat + '</td>' +
          '<td style="padding:3px 0;font-size:0.78rem;color:var(--text-muted)">' +
            escapeHtml(leaf) + '</td>' +
        '</tr>';
      }).join('');
      const headStyle =
        'position:sticky;top:0;background:var(--card);' +
        'text-align:left;padding:8px 12px;font-size:0.7rem;' +
        'color:var(--text-muted);text-transform:uppercase;' +
        'border-bottom:1px solid var(--border);z-index:2;cursor:pointer;' +
        'user-select:none';
      const _arrow = (col) => {
        if (Sp._offTopicSort.col !== col) return ' <span style="opacity:0.3">↕</span>';
        return Sp._offTopicSort.dir === 'desc'
          ? ' <span style="color:var(--text)">↓</span>'
          : ' <span style="color:var(--text)">↑</span>';
      };
      const th = (col, label) =>
        '<th data-sort-col="' + col + '" style="' + headStyle + '">' +
          escapeHtml(label) + _arrow(col) +
        '</th>';
      table =
        '<div class="fw-stat-dist" style="margin-top:14px">' +
          '<div class="fw-stat-dist-title">LLM verdict (' +
            decisions.length + ' decisions, click column headers to sort)</div>' +
          '<div style="max-height:340px;overflow-y:auto;border:1px solid var(--border);border-radius:4px;background:var(--card)">' +
            '<table data-table="off-topic-verdicts" style="width:100%;border-collapse:collapse;font-family:Source Sans 3">' +
              '<thead><tr>' +
                th('index',      'In') +
                th('verdict',    'Verdict') +
                th('deployment', 'Deployment') +
                th('latency',    'Latency') +
                th('page',       'Page') +
              '</tr></thead>' +
              '<tbody>' + rows + '</tbody>' +
            '</table>' +
          '</div>' +
        '</div>';
    }

    // Bandit deployment breakdown — show all that answered with reward avg.
    let depRow = '';
    if (depUsage.length) {
      const drows = depUsage.slice(0, 10).map(d => {
        const r = (d.reward_avg !== undefined && d.reward_avg !== null)
          ? d.reward_avg.toFixed(3) : '—';
        return '<tr>' +
          '<td style="padding:3px 12px 3px 0;font-size:0.78rem">' +
            escapeHtml((d.deployment || '?').split('/').pop()) + '</td>' +
          '<td style="padding:3px 12px 3px 0;font-family:JetBrains Mono,monospace;font-size:0.78rem">' +
            d.calls + ' calls</td>' +
          '<td style="padding:3px 0;font-family:JetBrains Mono,monospace;font-size:0.78rem;color:var(--text-muted)">' +
            'reward avg ' + r + '</td>' +
          '</tr>';
      }).join('');
      depRow =
        '<div class="fw-stat-dist" style="margin-top:14px">' +
          '<div class="fw-stat-dist-title">Bandit deployment usage (top ' +
            Math.min(10, depUsage.length) + ')</div>' +
          '<table style="width:100%;border-collapse:collapse;font-family:Source Sans 3">' +
            '<tbody>' + drows + '</tbody>' +
          '</table>' +
        '</div>';
    }

    const embedModel = s.embed_model || 'nvidia/llama-nemotron-embed-1b-v2';
    const router = s.judge_router || 'bandit/dd-grader';
    const foot =
      '<div class="fw-stat-foot">' +
        'embed <strong>' + escapeHtml(embedModel) + '</strong>' +
        ' · judge <strong>' + escapeHtml(router) + '</strong>' +
        ' · LLM judge ' + judged + ' calls (concurrency ' +
          (s.judge_concurrency || '?') + ')' +
        ' · coherence ' + (s.domain_coherence || 0).toFixed(3) +
        ' · ' + elapsed + ' ms total' +
      '</div>';

    return '<div class="fw-stat-grid">' + cards + '</div>' + table + depRow + foot;
  },

  // 2026-05-27 P4 — LLM-first renderers replacing the legacy
  // cluster/refine/label/reduce path. PLANNER_NODE_ORDER indices
  // 3-6 are now doc_distill/chapter_propose/chapter_assign/
  // chapter_select. plan_write moved 7→8 to match the new 9-slot
  // PLANNER_SUBSTEP_FIELDS ordering.

  // doc_distill — per-doc summary + key terms via parallel rotator.
  // Skip-pass for N ≤ 80 (pass-through to chapter_propose's raw-body
  // path). KPI cards show distill success/failure + cache + wall.
  3: function renderDocDistill(values) {
    const s = values.doc_distill_stats || {};
    if (!s.n_files && !s.skipped) {
      return '<div class="fw-empty">no doc_distill stats reported</div>';
    }
    const kpi = (label, value, sub) =>
      '<div class="fw-stat-card">' +
        '<div class="fw-stat-card-label">' + escapeHtml(label) + '</div>' +
        '<div class="fw-stat-card-value">' + escapeHtml(value) + '</div>' +
        (sub ? '<div class="fw-stat-card-sub">' + escapeHtml(sub) + '</div>' : '') +
      '</div>';

    if (s.skipped === 'pass_through_small_n') {
      const cards =
        kpi('Skipped', 'PASS-THROUGH', 'small-N optimization') +
        kpi('Files',   String(s.n_files || 0), 'no LLM call needed') +
        kpi('Reason',  'N ≤ 80',               'proposer ingests raw bodies') +
        kpi('Wall',    (s.wall_ms || 0) + ' ms', 'cheap');
      return '<div class="fw-stat-grid">' + cards + '</div>' +
        '<div class="fw-stat-foot">' +
          'doc_distill bypassed; chapter_propose reads doc bodies directly. ' +
          'Triggered only when relevant_files ≤ 80 — keeps small corpora fast.' +
        '</div>';
    }
    const n = s.n_files || 0;
    const distilled = s.n_distilled || 0;
    const failed = s.n_failed || 0;
    const successPct = n ? Math.round(distilled / n * 100) : 0;
    const cards =
      kpi('Distilled', distilled.toLocaleString(),
          successPct + '% of ' + n.toLocaleString()) +
      kpi('Failed',    String(failed),
          failed ? 'rate-limited (429) or parse-fail' : 'clean run') +
      kpi('Cache',     s.cache_hit ? 'HIT' : 'cold',
          s.cache_hit ? 'reused stored distillates' : 'fresh distillation') +
      kpi('Wall',      (s.wall_ms || 0) + ' ms',
          n && s.wall_ms ? Math.round(n / s.wall_ms * 1000) + ' docs/s' : null);
    const foot =
      '<div class="fw-stat-foot">' +
        'hash <code style="font-family:JetBrains Mono,monospace;font-size:0.72rem">' +
          escapeHtml((s.manifest_hash || '').slice(0, 12)) + '</code>' +
        ' · per-doc summary + 5 key terms · concurrency 8' +
        (failed
          ? ' · <strong style="color:var(--accent)">' + failed +
              ' docs skipped, downstream still works on ' + distilled + '</strong>'
          : '') +
      '</div>';
    return '<div class="fw-stat-grid">' + cards + '</div>' + foot;
  },

  // chapter_propose — long-context LLM proposes 6-15 candidate chapters
  // from distillates + structural seeds (markdown headings + file-tree
  // namespaces). N=3 parallel samples + USC vote picks the best.
  4: function renderChapterPropose(values) {
    const s = values.propose_stats || {};
    const titles = s.titles || [];
    if (!s.n_proposals && !titles.length) {
      return '<div class="fw-empty">no chapter_propose stats reported</div>';
    }
    const kpi = (label, value, sub) =>
      '<div class="fw-stat-card">' +
        '<div class="fw-stat-card-label">' + escapeHtml(label) + '</div>' +
        '<div class="fw-stat-card-value">' + escapeHtml(value) + '</div>' +
        (sub ? '<div class="fw-stat-card-sub">' + escapeHtml(sub) + '</div>' : '') +
      '</div>';

    const samplesValid = s.n_samples_valid !== undefined
      ? s.n_samples_valid + '/3' : '?';
    const cards =
      kpi('Proposals',   String(s.n_proposals || 0),
          'candidate chapters') +
      kpi('Samples OK',  samplesValid,
          'USC-voted winner: idx ' + (s.chosen_idx ?? '?')) +
      kpi('From docs',   (s.n_files || 0).toLocaleString(),
          'distillates + structural seeds') +
      kpi('Wall',        (s.wall_ms || 0) + ' ms',
          s.cache_hit ? 'cache HIT' : 'cold');

    const titlesList = titles.length
      ? '<div class="fw-stat-dist" style="margin-top:14px">' +
          '<div class="fw-stat-dist-title">Proposed chapters (chosen sample)</div>' +
          '<ol style="margin:8px 0 0;padding:0 0 0 20px;font-size:0.85rem;color:var(--text)">' +
            titles.map(t =>
              '<li style="padding:3px 0">' + escapeHtml(t) + '</li>',
            ).join('') +
          '</ol>' +
        '</div>'
      : '';
    const foot =
      '<div class="fw-stat-foot">' +
        'hash <code style="font-family:JetBrains Mono,monospace;font-size:0.72rem">' +
          escapeHtml((s.manifest_hash || '').slice(0, 12)) + '</code>' +
        ' · long-context LLM call via FGTS-VA · ' +
        '<strong>N=3 samples + USC vote</strong>' +
      '</div>';
    return '<div class="fw-stat-grid">' + cards + '</div>' + titlesList + foot;
  },

  // chapter_assign — per-doc LLM scores membership against each proposal
  // (confidence 0-1, multi-assignment allowed). Concurrent rotator calls;
  // chapter_select consumes the matrix downstream.
  5: function renderChapterAssign(values) {
    const s = values.assign_stats || {};
    if (!s.n_docs) {
      return '<div class="fw-empty">no chapter_assign stats reported</div>';
    }
    const kpi = (label, value, sub) =>
      '<div class="fw-stat-card">' +
        '<div class="fw-stat-card-label">' + escapeHtml(label) + '</div>' +
        '<div class="fw-stat-card-value">' + escapeHtml(value) + '</div>' +
        (sub ? '<div class="fw-stat-card-sub">' + escapeHtml(sub) + '</div>' : '') +
      '</div>';

    const assigned = s.n_assigned || 0;
    const failed = s.n_failed || 0;
    const cards =
      kpi('Assigned',   assigned.toLocaleString(),
          'of ' + (s.n_docs || 0).toLocaleString() + ' docs') +
      kpi('Proposals',  String(s.n_proposals || 0),
          'each doc scored against all') +
      kpi('Failed',     String(failed),
          failed ? 'rate-limited or parse-fail' : 'clean run') +
      kpi('Wall',       (s.wall_ms || 0) + ' ms',
          s.cache_hit ? 'cache HIT' : 'cold');

    // Coverage breakdown — per-proposal count of docs with confidence ≥0.5.
    let cov = '';
    const cc = s.coverage_count || {};
    const proposalsList = (values.propose_stats || {}).titles || [];
    const covEntries = Object.entries(cc)
      .map(([idx, n]) => ({ idx: parseInt(idx), n: parseInt(n) }))
      .sort((a, b) => b.n - a.n);
    if (covEntries.length) {
      const maxN = covEntries[0].n || 1;
      const rows = covEntries.map(e => {
        const title = proposalsList[e.idx] || ('proposal #' + e.idx);
        const pct = Math.max(2, Math.round(e.n / maxN * 100));
        return '<tr style="border-bottom:1px solid var(--border)">' +
          '<td style="padding:6px 8px;font-family:JetBrains Mono,monospace;font-size:0.72rem;color:var(--text-muted);width:40px">' +
            '[' + e.idx + ']' +
          '</td>' +
          '<td style="padding:6px 8px;font-size:0.85rem">' + escapeHtml(title) + '</td>' +
          '<td style="padding:6px 8px;width:80px;text-align:right;font-variant-numeric:tabular-nums">' +
            e.n + ' docs' +
          '</td>' +
          '<td style="padding:6px 8px;width:140px">' +
            '<div style="width:' + pct + '%;height:10px;background:var(--accent,#4a7);border-radius:2px"></div>' +
          '</td>' +
          '</tr>';
      }).join('');
      cov =
        '<div class="fw-stat-dist" style="margin-top:14px">' +
          '<div class="fw-stat-dist-title">Coverage per proposal (docs with confidence ≥0.5)</div>' +
          '<div style="max-height:300px;overflow-y:auto;border:1px solid var(--border);border-radius:4px">' +
            '<table style="width:100%;border-collapse:collapse;font-family:Source Sans 3">' +
              '<tbody>' + rows + '</tbody>' +
            '</table>' +
          '</div>' +
        '</div>';
    }
    const foot =
      '<div class="fw-stat-foot">' +
        'hash <code style="font-family:JetBrains Mono,monospace;font-size:0.72rem">' +
          escapeHtml((s.manifest_hash || '').slice(0, 12)) + '</code>' +
        ' · per-doc rotator call · concurrency 8' +
      '</div>';
    return '<div class="fw-stat-grid">' + cards + '</div>' + cov + foot;
  },

  // chapter_select — pure-algorithm greedy coverage. Picks minimum
  // chapter set covering ≥95% of docs above confidence threshold, then
  // prunes <3-doc chapters unless structurally pinned.
  6: function renderChapterSelect(values) {
    const s = values.select_stats || {};
    if (!s.n_chapters_out && !(s.chapter_titles || []).length) {
      return '<div class="fw-empty">no chapter_select stats reported</div>';
    }
    const kpi = (label, value, sub) =>
      '<div class="fw-stat-card">' +
        '<div class="fw-stat-card-label">' + escapeHtml(label) + '</div>' +
        '<div class="fw-stat-card-value">' + escapeHtml(value) + '</div>' +
        (sub ? '<div class="fw-stat-card-sub">' + escapeHtml(sub) + '</div>' : '') +
      '</div>';

    const out = s.n_chapters_out || 0;
    const propIn = s.n_proposals_in || 0;
    const pruned = s.n_pruned || 0;
    const cov = s.coverage_fraction !== undefined
      ? Math.round(s.coverage_fraction * 100) + '%' : '?';
    const cards =
      kpi('Selected', String(out),
          'from ' + propIn + ' proposals') +
      kpi('Pruned',   String(pruned),
          pruned ? '<3 docs, unpinned' : 'all kept') +
      kpi('Coverage', cov,
          (s.n_assigned_docs || 0) + ' of ' +
          (s.n_total_docs || 0) + ' docs') +
      kpi('Wall',     (s.wall_ms || 0) + ' ms', 'pure algorithm');

    const titles = s.chapter_titles || [];
    const sizes  = s.chapter_sizes  || [];
    let list = '';
    if (titles.length) {
      const maxSize = Math.max(...sizes, 1);
      const rows = titles.map((t, i) => {
        const n = sizes[i] || 0;
        const pct = Math.max(2, Math.round(n / maxSize * 100));
        return '<tr style="border-bottom:1px solid var(--border)">' +
          '<td style="padding:6px 8px;font-family:JetBrains Mono,monospace;font-size:0.72rem;color:var(--text-muted);width:50px;text-align:right">' +
            'ch-' + (i + 1).toString().padStart(2, '0') +
          '</td>' +
          '<td style="padding:6px 8px;font-size:0.9rem;font-weight:500">' +
            escapeHtml(t) +
          '</td>' +
          '<td style="padding:6px 8px;width:80px;text-align:right;font-variant-numeric:tabular-nums">' +
            n + ' docs' +
          '</td>' +
          '<td style="padding:6px 8px;width:140px">' +
            '<div style="width:' + pct + '%;height:10px;background:var(--accent,#4a7);border-radius:2px"></div>' +
          '</td>' +
          '</tr>';
      }).join('');
      list =
        '<div class="fw-stat-dist" style="margin-top:14px">' +
          '<div class="fw-stat-dist-title">Final chapter set (' +
            titles.length + ', balanced)</div>' +
          '<div style="max-height:380px;overflow-y:auto;border:1px solid var(--border);border-radius:4px">' +
            '<table style="width:100%;border-collapse:collapse;font-family:Source Sans 3">' +
              '<tbody>' + rows + '</tbody>' +
            '</table>' +
          '</div>' +
        '</div>';
    }
    const foot =
      '<div class="fw-stat-foot">' +
        'hash <code style="font-family:JetBrains Mono,monospace;font-size:0.72rem">' +
          escapeHtml((s.manifest_hash || '').slice(0, 12)) + '</code>' +
        ' · greedy coverage (≥95% target, &lt;3-doc prune) · no LLM' +
      '</div>';
    return '<div class="fw-stat-grid">' + cards + '</div>' + list + foot;
  },

  // order_chapters — N=2 LLM rank-sample + Borda aggregation. Picks
  // foundational chapters first, then orders the rest. KPI cards show
  // input chapter count + sample count + foundational pick + wall.
  // Below: side-by-side "before vs after" ordering list.
  7: function renderOrderChapters(values) {
    const s = values.order_chapters_stats || {};
    if (!s.n_chapters && !s.cache_hit) {
      return '<div class="fw-empty">no order_chapters stats reported</div>';
    }
    const kpi = (label, value, sub) =>
      '<div class="fw-stat-card">' +
        '<div class="fw-stat-card-label">' + escapeHtml(label) + '</div>' +
        '<div class="fw-stat-card-value">' + escapeHtml(value) + '</div>' +
        (sub ? '<div class="fw-stat-card-sub">' + escapeHtml(sub) + '</div>' : '') +
      '</div>';

    const order = Array.isArray(s.order) ? s.order : [];
    const foundational = Array.isArray(s.foundational) ? s.foundational : [];
    const proposalTitles =
      ((values.propose_stats || {}).titles) ||
      ((values.chapter_proposals_ref && Array.isArray(s.proposal_titles))
        ? s.proposal_titles
        : []);

    const cards =
      kpi('Chapters', String(s.n_chapters || order.length),
          'ranked by panel') +
      kpi('Samples',  String(s.n_samples || 0),
          'independent rank votes') +
      kpi('Foundational', String(foundational.length),
          foundational.length
            ? 'pinned to the front'
            : 'no pin (panel chose flat)') +
      kpi('Wall', (s.wall_ms || 0) + ' ms',
          s.cache_hit ? 'cache HIT' : 'Borda aggregate');

    // Before-vs-after ordering. Without proposal titles we still show
    // the position swap; with them it reads as a real chapter rename.
    let orderTbl = '';
    if (order.length) {
      const rows = order.map((origIdx, newPos) => {
        const title = proposalTitles[origIdx]
          ? proposalTitles[origIdx]
          : 'proposal #' + origIdx;
        const moved = origIdx !== newPos;
        return (
          '<tr style="border-bottom:1px solid var(--border)">' +
            '<td style="padding:6px 10px;font-family:JetBrains Mono,' +
              'monospace;font-size:0.72rem;color:var(--text-muted);' +
              'width:60px;text-align:right">' +
              'pos ' + (newPos + 1) + '</td>' +
            '<td style="padding:6px 10px;font-size:0.85rem;' +
              'font-weight:' + (moved ? '600' : '500') + ';' +
              'color:' + (moved ? 'var(--primary)' : 'var(--text)') + '">' +
              escapeHtml(title) + '</td>' +
            '<td style="padding:6px 10px;font-family:JetBrains Mono,' +
              'monospace;font-size:0.72rem;color:var(--text-muted);' +
              'text-align:right;width:80px">' +
              'was #' + origIdx + (moved ? ' ↑↓' : '') + '</td>' +
          '</tr>'
        );
      }).join('');
      orderTbl =
        '<div class="fw-stat-dist" style="margin-top:14px">' +
          '<div class="fw-stat-dist-title">Final ordering ' +
            '(' + order.length + ' chapters)</div>' +
          '<div style="max-height:340px;overflow-y:auto;' +
            'border:1px solid var(--border);border-radius:4px">' +
            '<table style="width:100%;border-collapse:collapse;' +
              'font-family:Source Sans 3"><tbody>' + rows + '</tbody></table>' +
          '</div>' +
        '</div>';
    }

    // Deployment usage — which rotator deployments answered.
    let depRow = '';
    const depUsage = Array.isArray(s.deployment_usage) ? s.deployment_usage : [];
    if (depUsage.length) {
      const drows = depUsage.slice(0, 8).map(d =>
        '<tr><td style="padding:3px 12px 3px 0;font-size:0.78rem">' +
          escapeHtml((d.deployment || '?').split('/').pop()) + '</td>' +
        '<td style="padding:3px 0;font-family:JetBrains Mono,monospace;' +
          'font-size:0.78rem;color:var(--text-muted)">' +
          d.calls + ' calls</td></tr>',
      ).join('');
      depRow =
        '<div class="fw-stat-dist" style="margin-top:14px">' +
          '<div class="fw-stat-dist-title">Bandit deployment usage</div>' +
          '<table style="width:100%;border-collapse:collapse;font-family:Source Sans 3">' +
            '<tbody>' + drows + '</tbody>' +
          '</table>' +
        '</div>';
    }

    const foot =
      '<div class="fw-stat-foot">' +
        'prompt <strong>' + escapeHtml(s.prompt_version || '?') + '</strong>' +
        ' · panel <strong>N=' + (s.n_samples || 0) + ' + Borda</strong>' +
      '</div>';
    return '<div class="fw-stat-grid">' + cards + '</div>' + orderTbl + depRow + foot;
  },

  // plan_write — consumer-facing final plan with hydrated `sources`.
  // KPI cards: chapters / sources / unassigned / wall_ms.
  // Below: the final outline with title, description, per-chapter
  // source count + first-N source paths (so a developer can sanity-
  // check which docs ended up where). Last card of the pipeline.
  // 2026-05-27 P4 — re-keyed 7 → 8 to match the LLM-first 9-slot
  // PLANNER_SUBSTEP_FIELDS (index 7 is now order_chapters, which
  // renders via KPI-only on the graph; no rich drawer panel).
  8: function renderPlanWrite(values) {
    const s = values.plan_write_stats || {};
    const plan = s.plan || {};
    const chapters = (plan.chapters || []).slice();
    if (!chapters.length) {
      // Two cases: (a) plan_path missing entirely — node hasn't run
      // yet; (b) plan_path set but stats not yet refreshed from the
      // checkpoint commit (race window between SSE `done` and the
      // /state poll catching the latest checkpoint). Show a neutral
      // running-style message instead of the error-looking
      // placeholders previously rendered.
      if (values.plan_path) {
        return '<div class="fw-empty">plan persisted at <code style="font-family:JetBrains Mono,monospace">' +
          escapeHtml(values.plan_path) +
          '</code> — refreshing chapter details…</div>';
      }
      return '<div class="fw-empty">waiting for plan_write to commit…</div>';
    }

    const kpi = (label, value, sub) =>
      '<div class="fw-stat-card">' +
        '<div class="fw-stat-card-label">' + escapeHtml(label) + '</div>' +
        '<div class="fw-stat-card-value">' + escapeHtml(value) + '</div>' +
        (sub ? '<div class="fw-stat-card-sub">' + escapeHtml(sub) + '</div>' : '') +
      '</div>';

    const nSources = s.n_sources || (plan.stats || {}).n_sources || 0;
    const nUnassigned = s.n_unassigned || (plan.stats || {}).n_unassigned || 0;
    const nDropped = s.n_dropped || (plan.stats || {}).n_dropped || 0;
    const corpusN = (plan.provenance || {}).corpus_doc_count || 0;
    const cards =
      kpi('Chapters', String(chapters.length),
          'final ordered outline') +
      kpi('Sources',  String(nSources),
          corpusN ? 'of ' + corpusN + ' corpus docs' : 'hydrated from refine') +
      kpi('Unassigned', String(nUnassigned),
          nDropped ? nDropped + ' empty chapters dropped' : 'none dropped') +
      kpi('Wall', (s.wall_ms || 0) + ' ms',
          s.cache_hit ? 'cache HIT' : 'cold');

    const sortedChapters = chapters.slice().sort(
      (a, b) => (a.order || 0) - (b.order || 0),
    );
    const headStyle =
      'position:sticky;top:0;background:var(--card);' +
      'text-align:left;padding:10px 12px;font-size:0.7rem;' +
      'color:var(--text-muted);text-transform:uppercase;' +
      'border-bottom:1px solid var(--border);z-index:2';
    const chapterRows = sortedChapters.map(ch => {
      const srcs = (ch.sources || []).slice();
      const previewSrcs = srcs.slice(0, 4).map(p => {
        const tail = p.split('/').slice(-2).join('/');
        return '<div style="font-family:JetBrains Mono,monospace;font-size:0.7rem;color:var(--text-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%">' +
          escapeHtml(tail) + '</div>';
      }).join('');
      const moreSrcs = srcs.length > 4
        ? '<div style="font-family:JetBrains Mono,monospace;font-size:0.7rem;color:var(--text-muted);font-style:italic">… ' +
            (srcs.length - 4) + ' more</div>'
        : '';
      return '<tr style="border-bottom:1px solid var(--border)">' +
        '<td style="padding:8px 12px 8px 0;font-family:JetBrains Mono,monospace;font-size:0.78rem;color:var(--text-muted);vertical-align:top">' +
          (ch.order || '?') + '</td>' +
        '<td style="padding:8px 12px 8px 0;vertical-align:top;width:32%">' +
          '<div style="font-weight:700;font-size:0.95rem">' +
            escapeHtml(ch.title || '?') + '</div>' +
          '<div style="font-family:JetBrains Mono,monospace;font-size:0.7rem;color:var(--text-muted);margin-top:4px">' +
            escapeHtml(ch.id || '') + ' · ' + (ch.n_sources || srcs.length) + ' sources' +
          '</div>' +
        '</td>' +
        '<td style="padding:8px 12px 8px 0;vertical-align:top;font-size:0.85rem;color:var(--text-muted)">' +
          escapeHtml(ch.description || '') +
        '</td>' +
        '<td style="padding:8px 0;vertical-align:top">' +
          previewSrcs + moreSrcs +
        '</td>' +
        '</tr>';
    }).join('');
    const table =
      '<div class="fw-stat-dist" style="margin-top:14px">' +
        '<div class="fw-stat-dist-title">Final plan (' +
          sortedChapters.length + ' chapters, hydrated sources)</div>' +
        '<div style="max-height:460px;overflow-y:auto;border:1px solid var(--border);border-radius:4px;background:var(--card)">' +
          '<table style="width:100%;border-collapse:collapse;font-family:Source Sans 3">' +
            '<thead><tr>' +
              '<th style="' + headStyle + ';padding-left:8px;width:40px">#</th>' +
              '<th style="' + headStyle + '">Chapter</th>' +
              '<th style="' + headStyle + '">Description</th>' +
              '<th style="' + headStyle + ';width:34%">Sources (sample)</th>' +
            '</tr></thead>' +
            '<tbody>' + chapterRows + '</tbody>' +
          '</table>' +
        '</div>' +
      '</div>';

    const prov = plan.provenance || {};
    const provLine =
      '<div class="fw-stat-foot">' +
        'wrote <code style="font-family:JetBrains Mono,monospace;font-size:0.72rem">' +
          escapeHtml(s.store_path || values.plan_path || '') + '</code>' +
        ' · hash <code style="font-family:JetBrains Mono,monospace;font-size:0.72rem">' +
          escapeHtml((s.manifest_hash || plan.manifest_hash || '').slice(0, 12)) + '</code>' +
        ' · upstream prompts ' +
        escapeHtml(JSON.stringify(prov.prompt_versions || {})) +
      '</div>';

    return '<div class="fw-stat-grid">' + cards + '</div>' + table + provLine;
  },
};
