import * as Sa from '@dd/shared/state/api.js';
import * as Sy from '@dd/shared/state/synth.js';

function _num(v) {
  const n = Number(v || 0);
  return Number.isFinite(n) ? n : 0;
}

function _fmtInt(v) {
  return _num(v).toLocaleString('en-US');
}

function _esc(v) {
  return String(v ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function _splitProviderModel(model) {
  const raw = String(model || '');
  if (!raw) return { provider: 'unknown', name: 'unknown' };
  const lower = raw.toLowerCase();
  if (lower.startsWith('meta-llama/')) return { provider: 'groq', name: raw };
  const idx = raw.indexOf('/');
  if (idx > 0) return { provider: raw.slice(0, idx), name: raw.slice(idx + 1) };
  if (lower.startsWith('mistral')) return { provider: 'mistral', name: raw };
  if (lower.startsWith('llama-')) return { provider: 'groq', name: raw };
  if (lower.startsWith('openai/')) return { provider: 'openai', name: raw.slice(7) };
  return { provider: 'implicit', name: raw };
}

function _emptyPayload(stage) {
  return {
    stage,
    total: { calls: 0, tokens_in: 0, tokens_out: 0, reasoning_tokens: 0 },
    by_node: {},
  };
}

export function mergeDdCounterPayloads(stage, payloads) {
  const merged = _emptyPayload(stage);
  for (const payload of payloads || []) {
    if (!payload || !payload.total) continue;
    merged.total.calls += _num(payload.total.calls);
    merged.total.tokens_in += _num(payload.total.tokens_in);
    merged.total.tokens_out += _num(payload.total.tokens_out);
    merged.total.reasoning_tokens += _num(payload.total.reasoning_tokens);
    for (const [nodeId, node] of Object.entries(payload.by_node || {})) {
      const dst = merged.by_node[nodeId] || {
        calls: 0, tokens_in: 0, tokens_out: 0, reasoning_tokens: 0,
        by_model: {},
      };
      dst.calls += _num(node.calls);
      dst.tokens_in += _num(node.tokens_in);
      dst.tokens_out += _num(node.tokens_out);
      dst.reasoning_tokens += _num(node.reasoning_tokens);
      for (const [model, stats] of Object.entries(node.by_model || {})) {
        const m = dst.by_model[model] || {
          calls: 0, tokens_in: 0, tokens_out: 0, reasoning_tokens: 0,
        };
        m.calls += _num(stats.calls);
        m.tokens_in += _num(stats.tokens_in);
        m.tokens_out += _num(stats.tokens_out);
        m.reasoning_tokens += _num(stats.reasoning_tokens);
        dst.by_model[model] = m;
      }
      merged.by_node[nodeId] = dst;
    }
  }
  return merged;
}

function _aggregateByModel(byNode) {
  const merged = {};
  for (const node of Object.values(byNode || {})) {
    for (const [model, stats] of Object.entries(node.by_model || {})) {
      const m = merged[model] || {
        calls: 0, tokens_in: 0, tokens_out: 0, reasoning_tokens: 0,
      };
      m.calls += _num(stats.calls);
      m.tokens_in += _num(stats.tokens_in);
      m.tokens_out += _num(stats.tokens_out);
      m.reasoning_tokens += _num(stats.reasoning_tokens);
      merged[model] = m;
    }
  }
  return merged;
}

function _modelRows(payload) {
  return Object.entries(_aggregateByModel((payload && payload.by_node) || {}))
    .map(([model, stats]) => {
      const split = _splitProviderModel(model);
      return {
        raw: model,
        provider: split.provider,
        name: split.name,
        calls: _num(stats.calls),
        tokens_in: _num(stats.tokens_in),
        tokens_out: _num(stats.tokens_out),
        reasoning_tokens: _num(stats.reasoning_tokens),
      };
    })
    .filter(r => r.calls > 0)
    .sort((a, b) => b.calls - a.calls);
}

function _nodeChips(payload) {
  return Object.entries((payload && payload.by_node) || {})
    .filter(([, node]) => _num(node.calls) > 0)
    .sort((a, b) => _num(b[1].calls) - _num(a[1].calls))
    .map(([nodeId, node]) =>
      '<span class="dd-llm-rail-nodechip" title="' +
        _esc(nodeId) + ': ' + _fmtInt(node.calls) + ' calls · ' +
        _fmtInt(node.tokens_in) + ' in / ' + _fmtInt(node.tokens_out) + ' out">' +
        '<span class="dd-llm-rail-nodechip-name">' + _esc(nodeId) + '</span>' +
        '<span class="dd-llm-rail-nodechip-count">' + _fmtInt(node.calls) + '</span>' +
      '</span>'
    ).join('');
}

function _kpiGrid(payload) {
  const total = (payload && payload.total) || {};
  const reasoning = _num(total.reasoning_tokens);
  return (
    '<div class="dd-llm-rail-kpis">' +
      '<span class="dd-llm-rail-kpi"><b>' + _fmtInt(total.calls) +
        '</b><small>calls</small></span>' +
      '<span class="dd-llm-rail-kpi"><b>' + _fmtInt(total.tokens_in) +
        '</b><small>input tokens</small></span>' +
      '<span class="dd-llm-rail-kpi"><b>' + _fmtInt(total.tokens_out) +
        '</b><small>output tokens</small></span>' +
      (reasoning > 0
        ? '<span class="dd-llm-rail-kpi"><b>' + _fmtInt(reasoning) +
          '</b><small>reasoning</small></span>'
        : '') +
    '</div>'
  );
}

function _modelTable(payload) {
  const rows = _modelRows(payload);
  if (!rows.length) return '';
  return (
    '<div class="dd-llm-rail-table-wrap">' +
      '<table class="dd-llm-rail-table">' +
        '<thead><tr><th>provider</th><th>model</th><th>calls</th>' +
        '<th>input tokens</th><th>output tokens</th><th>reasoning</th></tr></thead>' +
        '<tbody>' +
          rows.map(r =>
            '<tr>' +
              '<td title="' + _esc(r.raw) + '">' + _esc(r.provider) + '</td>' +
              '<td title="' + _esc(r.raw) + '">' + _esc(r.name) + '</td>' +
              '<td>' + _fmtInt(r.calls) + '</td>' +
              '<td>' + _fmtInt(r.tokens_in) + '</td>' +
              '<td>' + _fmtInt(r.tokens_out) + '</td>' +
              '<td>' + _fmtInt(r.reasoning_tokens) + '</td>' +
            '</tr>'
          ).join('') +
        '</tbody>' +
      '</table>' +
    '</div>'
  );
}

function _summaryBody(payload) {
  const chips = _nodeChips(payload);
  return (
    _kpiGrid(payload) +
    (chips ? '<div class="dd-llm-rail-nodechips">' + chips + '</div>' : '') +
    _modelTable(payload)
  );
}

function _renderSummaryCard(title, subtitle, payload) {
  if (!_num(((payload || {}).total || {}).calls)) {
    return '<div class="dd-llm-rail-empty">No LLM usage recorded yet.</div>';
  }
  return (
    '<div class="dd-llm-rail-summary">' +
      '<div class="dd-llm-rail-title">' + _esc(title) + '</div>' +
      (subtitle
        ? '<div class="dd-llm-rail-sub">' + _esc(subtitle) + '</div>'
        : '') +
      _summaryBody(payload) +
    '</div>'
  );
}

function _renderChapterCard(entry) {
  const payload = entry.payload;
  const hasCalls = _num(((payload || {}).total || {}).calls) > 0;
  const status = entry.status || (entry.rendered ? 'done' : 'running');
  const subtitle = entry.rendered
    ? 'Rendered chapter'
    : 'In progress';
  return (
    '<details class="dd-llm-rail-card"' + (status === 'running' ? ' open' : '') + '>' +
      '<summary>' +
        '<div>' +
          '<div class="dd-llm-rail-title">' + _esc(entry.title || entry.id) + '</div>' +
          '<div class="dd-llm-rail-sub">' + _esc(subtitle) + '</div>' +
        '</div>' +
        '<span class="dd-llm-rail-status" data-status="' + _esc(status) + '">' +
          _esc(status) +
        '</span>' +
      '</summary>' +
      '<div class="dd-llm-rail-card-body">' +
        (hasCalls
          ? _summaryBody(payload)
          : '<div class="dd-llm-rail-empty">No LLM usage recorded for this chapter yet.</div>') +
      '</div>' +
    '</details>'
  );
}

async function _fetchJson(url) {
  const r = await fetch(url);
  if (!r.ok) return null;
  return await r.json();
}

async function _fetchCounters(stage, threadId) {
  if (!threadId) return null;
  const path = stage === 'planner' ? 'planner' : 'synth';
  return await _fetchJson(
    Sa.API + '/' + path + '/debug/graph/' + threadId + '/llm-counters'
  );
}

async function _latestPlannerThread(slug) {
  const data = await _fetchJson(Sa.API + '/planner/recent');
  const recent = (data && data.recent) || [];
  const hit = recent.find(it => it.slug === slug && it.thread_id);
  return hit ? hit.thread_id : null;
}

async function _plannerState(slug) {
  const threadId = await _latestPlannerThread(slug);
  if (!threadId) return null;
  const [payload, snap] = await Promise.all([
    _fetchCounters('planner', threadId),
    _fetchJson(Sa.API + '/planner/debug/graph/' + threadId + '/state'),
  ]);
  return {
    threadId,
    payload,
    complete: ((snap && snap.values) || {}).status === 'done',
  };
}

async function _synthChapterEntries(slug) {
  const data = await _fetchJson(Sa.API + '/synth/' + slug + '/study/chapters');
  const chapters = ((data && data.chapters) || []).map(ch => ({
    id: ch.id,
    title: ch.title || ch.id,
    order: Number(ch.order || 0),
    rendered: !!ch.rendered,
    thread_id: ch.thread_id || null,
    status: ch.rendered ? 'done' : 'pending',
  }));
  const byId = new Map(chapters.map(ch => [ch.id, ch]));

  for (const [id, threadId] of Sy.studyChapterThreads.entries()) {
    if (!id || !threadId) continue;
    const known = byId.get(id) || {
      id,
      title: id,
      order: chapters.length + byId.size + 1,
      rendered: false,
      thread_id: null,
      status: 'running',
    };
    known.thread_id = threadId;
    known.rendered = !!known.rendered;
    known.status = Sy.studyChapterStatus.get(id) || known.status || 'running';
    byId.set(id, known);
  }

  if (Sy.studyCurrentChapterId && Sy.studyCurrentChapterThreadId) {
    const current = byId.get(Sy.studyCurrentChapterId) || {
      id: Sy.studyCurrentChapterId,
      title: Sy.studyCurrentChapterId,
      order: chapters.length + 1,
      rendered: false,
      thread_id: null,
      status: 'running',
    };
    current.thread_id = Sy.studyCurrentChapterThreadId;
    current.status = 'running';
    current.rendered = false;
    byId.set(current.id, current);
  }

  const entries = Array.from(byId.values())
    .filter(ch => ch.thread_id || ch.rendered)
    .sort((a, b) => a.order - b.order);

  const payloads = await Promise.all(entries.map(async entry => ({
    entry,
    payload: await _fetchCounters('synth', entry.thread_id).catch(() => null),
  })));

  const merged = mergeDdCounterPayloads(
    'synth',
    payloads.map(item => item.payload).filter(Boolean),
  );
  const allRendered = chapters.length > 0 && chapters.every(ch => ch.rendered);

  return {
    allRendered,
    chapters: payloads.map(item => ({
      ...item.entry,
      payload: item.payload || _emptyPayload('synth'),
    })),
    totalPayload: allRendered ? merged : null,
  };
}

let _refreshTimer = null;
let _refreshInFlight = false;
let _refreshPending = false;
let _drawerBound = false;

function _setDrawerSections(mode) {
  const plannerSection = document.getElementById('fw-llm-drawer-planner-section');
  const synthChaptersSection = document.getElementById('fw-llm-drawer-synth-chapters-section');
  const synthTotalSection = document.getElementById('fw-llm-drawer-synth-total-section');
  if (plannerSection) plannerSection.style.display = mode === 'planner' ? '' : 'none';
  if (synthChaptersSection) synthChaptersSection.style.display = mode === 'synth' ? '' : 'none';
  if (synthTotalSection) synthTotalSection.style.display = mode === 'synth' ? '' : 'none';
}

function _openLlmDrawer(mode) {
  const drawer = document.getElementById('fw-llm-drawer');
  const name = document.getElementById('fw-llm-drawer-name');
  const meta = document.getElementById('fw-llm-drawer-meta');
  if (!drawer || !name || !meta) return;
  _setDrawerSections(mode);
  if (mode === 'planner') {
    name.textContent = 'Planner LLM usage';
    meta.textContent = 'Latest planner run, grouped by node and provider/model.';
  } else {
    name.textContent = 'Synth LLM usage';
    meta.textContent = 'Per chapter while running, plus a final combined total after all chapters finish.';
  }
  drawer.classList.add('visible');
}

function _closeLlmDrawer() {
  document.getElementById('fw-llm-drawer')?.classList.remove('visible');
}

function _bindDrawerControls() {
  if (_drawerBound) return;
  _drawerBound = true;
  document.getElementById('fw-planner-llm-open')
    ?.addEventListener('click', () => _openLlmDrawer('planner'));
  document.getElementById('fw-synth-llm-open')
    ?.addEventListener('click', () => _openLlmDrawer('synth'));
  document.getElementById('fw-llm-drawer-close')
    ?.addEventListener('click', _closeLlmDrawer);
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') _closeLlmDrawer();
  });
}

export async function refreshDdPipelineLlmTotals(slug) {
  if (!slug) return;
  const plannerEl = document.getElementById('fw-planner-llm-totals');
  const synthChaptersEl = document.getElementById('fw-synth-llm-chapters');
  const synthTotalEl = document.getElementById('fw-synth-llm-total');
  if (!plannerEl && !synthChaptersEl && !synthTotalEl) return;
  if (_refreshInFlight) {
    _refreshPending = true;
    return;
  }
  _refreshInFlight = true;
  try {
    let plannerState = null;
    if (plannerEl) {
      plannerEl.innerHTML = '<div class="dd-llm-rail-empty">Loading planner usage...</div>';
      try {
        plannerState = await _plannerState(slug);
        plannerEl.innerHTML = plannerState
          ? _renderSummaryCard('Latest planner run', 'Updated from graph checkpoints', plannerState.payload)
          : '<div class="dd-llm-rail-empty">Planner has no recorded LLM usage yet.</div>';
      } catch {
        plannerEl.innerHTML = '<div class="dd-llm-rail-empty">Planner usage unavailable.</div>';
      }
    } else {
      plannerState = await _plannerState(slug).catch(() => null);
    }

    let synth = null;
    if (synthChaptersEl || synthTotalEl) {
      synth = await _synthChapterEntries(slug).catch(() => null);
      if (synthChaptersEl) {
        synthChaptersEl.innerHTML = synth && synth.chapters.length
          ? synth.chapters.map(_renderChapterCard).join('')
          : '<div class="dd-llm-rail-empty">Synth chapter usage appears here once chapter threads emit LLM activity.</div>';
      }
      if (synthTotalEl) {
        synthTotalEl.innerHTML = synth && synth.allRendered && synth.totalPayload
          ? _renderSummaryCard('All chapters combined', 'Shown only after the full study finishes', synth.totalPayload)
          : '<div class="dd-llm-rail-empty">Synth total appears after all chapters are done.</div>';
      }
    }

    if (plannerState && plannerState.complete && synth && synth.allRendered && synth.totalPayload) {
      _renderPipelineToolbarTotal(mergeDdCounterPayloads(
        'pipeline',
        [plannerState.payload, synth.totalPayload].filter(Boolean),
      ));
    } else {
      _renderPipelineToolbarTotal(null);
    }
  } finally {
    _refreshInFlight = false;
    if (_refreshPending) {
      _refreshPending = false;
      refreshDdPipelineLlmTotals(slug).catch(() => {});
    }
  }
}

function _scheduleRefresh(slug, delayMs) {
  if (!slug) return;
  if (_refreshTimer) clearTimeout(_refreshTimer);
  _refreshTimer = setTimeout(() => {
    _refreshTimer = null;
    refreshDdPipelineLlmTotals(slug).catch(() => {});
  }, delayMs);
}

function _renderPipelineToolbarTotal(payload) {
  const host = document.getElementById('fw-pipeline-total');
  const callsEl = document.getElementById('fw-pipeline-total-calls');
  const inEl = document.getElementById('fw-pipeline-total-in');
  const outEl = document.getElementById('fw-pipeline-total-out');
  if (!host || !callsEl || !inEl || !outEl) return;
  if (!payload || !_num(((payload || {}).total || {}).calls)) {
    host.style.display = 'none';
    return;
  }
  const total = payload.total || {};
  callsEl.textContent = _fmtInt(total.calls) + ' calls';
  inEl.textContent = _fmtInt(total.tokens_in) + ' input tokens';
  outEl.textContent = _fmtInt(total.tokens_out) + ' output tokens';
  host.style.display = 'inline-flex';
}

function _hidePipelineToolbarTotal() {
  _renderPipelineToolbarTotal(null);
}

export function installDdPipelineLlmTotals(slug) {
  if (!slug) return;
  _bindDrawerControls();
  _scheduleRefresh(slug, 0);
  document.addEventListener('dd:planner:terminal', () => _scheduleRefresh(slug, 80));
  document.addEventListener('dd:planner:node-done', () => _scheduleRefresh(slug, 80));
  document.addEventListener('dd:synth:node-done', () => _scheduleRefresh(slug, 80));
  document.addEventListener('dd:synth:chapter-running', () => _scheduleRefresh(slug, 40));
  document.addEventListener('dd:synth:chapter-done', () => _scheduleRefresh(slug, 80));
  document.addEventListener('dd:synth:terminal', () => _scheduleRefresh(slug, 120));
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') _scheduleRefresh(slug, 0);
  });
  document.addEventListener('click', (ev) => {
    if (ev.target.closest('#fw-planner-wipe') ||
        ev.target.closest('#fw-synth-wipe')) {
      _hidePipelineToolbarTotal();
    }
  });
}
