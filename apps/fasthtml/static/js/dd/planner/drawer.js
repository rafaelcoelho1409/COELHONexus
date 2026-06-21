// planner/drawer.js — NodeDrawer IIFE.
//
// SOTA tabbed step-detail pane (2026-06-08): three-tab structure
// (Overview / Activity / Raw I/O) matching LangSmith + Dagster +
// Vercel Workflows + Langfuse June 2026 step-detail panes. Overview
// houses the rich SUBSTEP_RENDERERS output (KPI cards + tables +
// outline + metadata footer). Activity carries the live SSE event
// stream with a count badge for new events since last viewed. Raw
// I/O exposes inputs/outputs JSON for power-user inspection.
//
// Public API (consumed by planner.js and synth.js):
//   NodeDrawer.open(stage, nodeId, ctx)
//      ctx = {label, kpis, status, resultsHtml?, inputs?, outputs?}
//   NodeDrawer.close()
//   NodeDrawer.reset()                          // clear log + lastSeen map
//   NodeDrawer.isOpenFor(stage, nodeId)
//   NodeDrawer.appendEvent(ev)                  // route an SSE event
//   NodeDrawer.updateContext(ctx)
//   NodeDrawer.openStage / NodeDrawer.openNodeId  // for caller polling.
import { escapeHtml } from '../shared/utils.js';


export const NodeDrawer = (function() {
  const elDrawer    = document.getElementById('fw-node-drawer');
  const elIcon      = document.getElementById('fw-node-drawer-icon');
  const elTitle     = document.getElementById('fw-node-drawer-title');
  const elMeta      = document.getElementById('fw-node-drawer-meta');
  const elKpis      = document.getElementById('fw-node-drawer-kpis');
  const elLog       = document.getElementById('fw-node-drawer-log');
  const elLogEmpty  = document.getElementById('fw-node-drawer-log-empty');
  const elDetails   = document.getElementById('fw-node-drawer-details');
  const elRaw       = document.getElementById('fw-node-drawer-raw');
  const elTabs      = document.getElementById('fw-node-drawer-tabs');
  const elActBadge  = document.getElementById(
    'fw-node-drawer-tab-activity-badge');
  const elTabOverviewPanel = document.getElementById(
    'fw-node-drawer-tab-overview-panel');
  const elTabActivityPanel = document.getElementById(
    'fw-node-drawer-tab-activity-panel');
  const elTabRawPanel      = document.getElementById(
    'fw-node-drawer-tab-raw-panel');
  const elClose     = document.getElementById('fw-node-drawer-close');

  const MAX_LOG_LINES = 200;
  const STATUS_ICON = {
    future: '⏳', pending: '○', running: '◐',
    done: '●', failed: '✕', cancelled: '∅',
  };
  const TABS = ['overview', 'activity', 'raw'];

  let _openStage = null;        // 'planner' | 'synth' | null
  let _openNodeId = null;
  let _activeTab = 'overview';
  let _newEventCount = 0;       // resets on tab-activate
  let _pendingEvents = [];
  let _flushScheduled = false;
  let _userPinnedScroll = true; // true = auto-scroll to bottom; false = user scrolled up
  let _currentCtx = {};
  // "Since last viewed" tracking: maps `${stage}/${nodeId}` → epoch ms
  // of last drawer-open for that node. Events whose timestamp is
  // newer than the previous lastSeen get an `.is-new` highlight.
  // Per-session (not persisted) — chat-style affordance.
  const _lastSeenAt = new Map();
  let _prevSeenForOpen = 0;     // captured at open(); 0 = first open ever

  function _fmtTs(ts) {
    const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date();
    const h = String(d.getHours()).padStart(2, '0');
    const m = String(d.getMinutes()).padStart(2, '0');
    const s = String(d.getSeconds()).padStart(2, '0');
    return `${h}:${m}:${s}`;
  }

  function _makeLogLine(ev) {
    const div = document.createElement('div');
    div.className = 'fw-node-drawer-log-line';
    const kind = (ev && ev.kind) || 'info';
    div.dataset.kind = kind;
    if (kind === 'error' || ev.error) div.classList.add('severity-error');
    else if (kind === 'warning')      div.classList.add('severity-warn');
    const evTsMs = (typeof ev.ts === 'number') ? ev.ts * 1000 : Date.now();
    if (_prevSeenForOpen > 0 && evTsMs > _prevSeenForOpen) {
      div.classList.add('is-new');
    }
    const tidy = {};
    Object.keys(ev || {}).forEach(k => {
      if (k === 'ts' || k === 'step' || k === 'kind') return;
      tidy[k] = ev[k];
    });
    const tidyStr = Object.keys(tidy).length
      ? ' ' + Object.entries(tidy)
          .map(([k, v]) => `${k}=${typeof v === 'object'
            ? JSON.stringify(v).slice(0, 60) : String(v).slice(0, 60)}`)
          .join(' ')
      : '';
    div.textContent = `▸ ${_fmtTs(ev.ts)} ${kind}${tidyStr}`;
    return div;
  }

  function _scheduleFlush() {
    if (_flushScheduled) return;
    _flushScheduled = true;
    requestAnimationFrame(() => {
      _flushScheduled = false;
      if (_pendingEvents.length === 0 || !elLog) return;
      if (elLogEmpty) elLogEmpty.style.display = 'none';
      const frag = document.createDocumentFragment();
      _pendingEvents.forEach(ev => frag.appendChild(_makeLogLine(ev)));
      elLog.appendChild(frag);
      _pendingEvents = [];
      while (elLog.childElementCount > MAX_LOG_LINES) {
        elLog.removeChild(elLog.firstChild);
      }
      if (_userPinnedScroll) {
        elLog.scrollTop = elLog.scrollHeight;
      }
    });
  }

  function _updateStatusIcon(status) {
    if (!elIcon) return;
    elIcon.textContent = STATUS_ICON[status] || '○';
    elIcon.dataset.status = status || 'pending';
  }

  function _renderKpis(kpis) {
    if (!elKpis) return;
    if (!kpis || typeof kpis !== 'object') {
      elKpis.innerHTML = '';
      elKpis.style.display = 'none';
      return;
    }
    const entries = Object.entries(kpis).filter(([, v]) =>
      v !== undefined && v !== null && v !== '');
    if (!entries.length) {
      elKpis.innerHTML = '';
      elKpis.style.display = 'none';
      return;
    }
    elKpis.innerHTML = entries.map(([k, v]) =>
      '<span class="fw-node-drawer-kpi">' +
        '<span class="fw-node-drawer-kpi-label">' + escapeHtml(k) + '</span>' +
        '<span class="fw-node-drawer-kpi-value">' +
          escapeHtml(typeof v === 'object' ? JSON.stringify(v) : String(v)) +
        '</span>' +
      '</span>'
    ).join('');
    elKpis.style.display = '';
  }

  function _renderPillList(items) {
    if (!Array.isArray(items) || !items.length) return '';
    return (
      '<div class="fw-node-drawer-pills">' +
        items.map(item =>
          '<span class="fw-node-drawer-pill">' + escapeHtml(String(item)) + '</span>'
        ).join('') +
      '</div>'
    );
  }

  function _renderNodeSpec(ctx) {
    const details = ctx.details || null;
    if (!details) return '';
    const actions = Array.isArray(details.actions) ? details.actions : [];
    const tokenMetrics = Array.isArray(ctx.tokenMetrics)
      ? ctx.tokenMetrics : [];
    const baseMetrics = Array.isArray(ctx.metrics) ? ctx.metrics : [];
    const tokenLabels = new Set(tokenMetrics.map(m => String(m.label || '').toLowerCase()));
    const metrics = tokenMetrics.concat(
      tokenMetrics.length
        ? baseMetrics.filter(m => !tokenLabels.has(String(m.label || '').toLowerCase()))
        : baseMetrics,
    );
    const actionHtml = actions.length
      ? '<ul class="fw-node-drawer-actions">' +
          actions.map(a => '<li>' + escapeHtml(String(a)) + '</li>').join('') +
        '</ul>'
      : '';
    const metricHtml = metrics.length
      ? '<div class="fw-node-drawer-metrics">' +
          metrics.map(m =>
            '<span class="fw-node-drawer-metric">' +
              '<span class="fw-node-drawer-metric-label">' +
                escapeHtml(String(m.label || 'metric')) +
              '</span>' +
              '<span class="fw-node-drawer-metric-value">' +
                escapeHtml(String(m.value ?? '')) +
              '</span>' +
              (m.note
                ? '<span class="fw-node-drawer-metric-note">' +
                    escapeHtml(String(m.note)) +
                  '</span>'
                : '') +
            '</span>'
          ).join('') +
        '</div>'
      : '';
    const modelRows = Array.isArray(ctx.modelRows) ? ctx.modelRows : [];
    const modelHtml = modelRows.length
      ? '<div class="fw-node-drawer-models-title">Per provider · model</div>' +
        '<table class="fw-node-drawer-models">' +
          '<thead><tr><th>provider</th><th>model</th><th>calls</th>' +
          '<th>input tokens</th><th>output tokens</th><th>reasoning</th></tr></thead>' +
          '<tbody>' +
            modelRows.map(r =>
              '<tr>' +
                '<td title="' + escapeHtml(r.raw || '') + '">' +
                  escapeHtml(r.provider || '') +
                '</td>' +
                '<td title="' + escapeHtml(r.raw || '') + '">' +
                  escapeHtml(r.model || '') +
                '</td>' +
                '<td>' + escapeHtml(Number(r.calls || 0).toLocaleString()) + '</td>' +
                '<td>' + escapeHtml(Number(r.tokens_in || 0).toLocaleString()) + '</td>' +
                '<td>' + escapeHtml(Number(r.tokens_out || 0).toLocaleString()) + '</td>' +
                '<td>' + escapeHtml(Number(r.reasoning_tokens || 0).toLocaleString()) + '</td>' +
              '</tr>'
            ).join('') +
          '</tbody>' +
        '</table>'
      : '';
    return (
      '<section class="fw-node-drawer-spec">' +
        '<div class="fw-node-drawer-spec-head">' +
          '<div>' +
            '<div class="fw-node-drawer-spec-title">' +
              escapeHtml(details.title || ctx.label || 'Node') +
            '</div>' +
            (details.subtitle
              ? '<div class="fw-node-drawer-spec-subtitle">' +
                  escapeHtml(details.subtitle) +
                '</div>'
              : '') +
          '</div>' +
          (details.kind
            ? '<span class="fw-node-drawer-kind">' +
                escapeHtml(details.kind) +
              '</span>'
            : '') +
        '</div>' +
        actionHtml +
        '<div class="fw-node-drawer-io">' +
          '<div><span>Inputs</span>' + _renderPillList(details.inputs) + '</div>' +
          '<div><span>Outputs</span>' + _renderPillList(details.outputs) + '</div>' +
        '</div>' +
        (details.llm
          ? '<div class="fw-node-drawer-llm-note">' +
              escapeHtml(details.llm) +
            '</div>'
          : '') +
        metricHtml +
        modelHtml +
      '</section>'
    );
  }

  // Render the Overview tab — the rich SUBSTEP_RENDERERS output OR a
  // status-aware empty/waiting state when the node hasn't produced
  // output yet.
  function _renderOverview(ctx) {
    if (!elDetails) return;
    const specHtml = _renderNodeSpec(ctx);
    if (ctx.resultsHtml) {
      elDetails.innerHTML =
        specHtml +
        '<div class="fw-node-drawer-results">' + ctx.resultsHtml + '</div>';
      return;
    }
    const status = ctx.status || 'pending';
    const msg =
      status === 'running'
        ? 'Running — results will appear once this node commits its checkpoint.'
      : status === 'failed'
        ? 'This node failed before producing output. See the Activity tab for the error trace.'
      : status === 'cancelled'
        ? 'Cancelled before producing output. Re-run to retry.'
      : status === 'future'
        ? 'Not yet implemented — substep will activate when its node code ships.'
      : status === 'done'
        ? 'Completed without a rich renderer for this node yet. Inspect Raw I/O for the raw checkpoint, or check Activity for the event trace.'
      : 'Waiting for this node to run.';
    elDetails.innerHTML = specHtml +
      '<div class="fw-empty fw-node-drawer-waiting">' + escapeHtml(msg) +
      '</div>';
  }

  // Render the Raw I/O tab — inputs + outputs accordions, or a single
  // "nothing to show yet" empty when both are absent.
  function _renderRaw(ctx) {
    if (!elRaw) return;
    const debug = [];
    if (ctx.inputs) debug.push({
      id: 'inputs',  title: 'Inputs (upstream state, raw)',
      content: '<pre>' + escapeHtml(ctx.inputs) + '</pre>',
    });
    if (ctx.outputs) debug.push({
      id: 'outputs', title: 'Outputs (this node, raw)',
      content: '<pre>' + escapeHtml(ctx.outputs) + '</pre>',
    });
    if (!debug.length) {
      elRaw.innerHTML =
        '<div class="fw-empty fw-node-drawer-waiting">' +
          'Raw inputs/outputs appear here once the upstream nodes have ' +
          'checkpointed and this node has committed.' +
        '</div>';
      return;
    }
    elRaw.innerHTML = debug.map(s =>
      '<details class="fw-node-drawer-detail" data-section="' + s.id +
        '" open>' +
        '<summary>' + escapeHtml(s.title) + '</summary>' +
        '<div class="fw-node-drawer-detail-body">' + s.content + '</div>' +
      '</details>'
    ).join('');
  }

  function _updateActivityBadge() {
    if (!elActBadge) return;
    if (_activeTab === 'activity' || _newEventCount === 0) {
      elActBadge.textContent = '';
      elActBadge.style.display = 'none';
      return;
    }
    elActBadge.textContent = String(_newEventCount);
    elActBadge.style.display = '';
  }

  function _switchTab(name) {
    if (!TABS.includes(name)) return;
    _activeTab = name;
    if (elTabs) {
      elTabs.querySelectorAll('.fw-node-drawer-tab').forEach(btn => {
        const isActive = btn.dataset.tab === name;
        btn.classList.toggle('active', isActive);
        btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
      });
    }
    const panels = {
      overview: elTabOverviewPanel,
      activity: elTabActivityPanel,
      raw:      elTabRawPanel,
    };
    Object.entries(panels).forEach(([k, panel]) => {
      if (!panel) return;
      panel.classList.toggle('active', k === name);
      panel.style.display = (k === name) ? '' : 'none';
    });
    if (name === 'activity') {
      _newEventCount = 0;
      _updateActivityBadge();
      // Scroll log to bottom when the user opens the tab — the moment
      // they want activity, they want the latest.
      if (elLog && _userPinnedScroll) {
        elLog.scrollTop = elLog.scrollHeight;
      }
    } else {
      _updateActivityBadge();
    }
  }

  function _populate(stage, nodeId, ctx) {
    const key = stage + '/' + nodeId;
    _prevSeenForOpen = _lastSeenAt.get(key) || 0;
    _lastSeenAt.set(key, Date.now());
    _openStage  = stage;
    _openNodeId = nodeId;
    _pendingEvents = [];
    _newEventCount = 0;
    _userPinnedScroll = true;
    _currentCtx = ctx || {};
    const details = ctx.details || {};
    if (elTitle) elTitle.textContent = details.title || ctx.label || nodeId;
    if (elMeta) {
      const metaParts = [stage, nodeId];
      if (details.kind) metaParts.push(details.kind);
      elMeta.textContent = metaParts.join(' · ');
    }
    _updateStatusIcon(ctx.status || 'pending');
    _renderKpis(ctx.kpis);
    _renderOverview(ctx);
    _renderRaw(ctx);
    if (elLog) elLog.innerHTML = '';
    if (elLogEmpty) elLogEmpty.style.display = '';
    // Always restore the Overview tab on (re)open — the primary
    // content. Users who specifically want logs/raw click the tab.
    _switchTab('overview');
  }

  function open(stage, nodeId, ctx) {
    if (!elDrawer) return;
    ctx = ctx || {};
    const wasVisible = elDrawer.classList.contains('visible');
    const isSameNode = (_openStage === stage && _openNodeId === nodeId);
    const elBody = document.getElementById('fw-node-drawer-body');
    if (wasVisible && !isSameNode && elBody) {
      elBody.classList.add('fw-node-drawer-fading');
      setTimeout(() => {
        _populate(stage, nodeId, ctx);
        elBody.classList.remove('fw-node-drawer-fading');
      }, 140);
    } else {
      _populate(stage, nodeId, ctx);
    }
    elDrawer.classList.add('visible');
    if (elClose) setTimeout(() => elClose.focus(), 100);
  }

  function close() {
    if (!elDrawer) return;
    elDrawer.classList.remove('visible');
    _openStage = null;
    _openNodeId = null;
    _currentCtx = {};
  }

  function isOpenFor(stage, nodeId) {
    return _openStage === stage && _openNodeId === nodeId;
  }

  function appendEvent(ev) {
    if (!ev || !_openNodeId) return;
    _pendingEvents.push(ev);
    _scheduleFlush();
    if (ev.kind === 'start')   _updateStatusIcon('running');
    else if (ev.kind === 'done') _updateStatusIcon('done');
    else if (ev.kind === 'error') _updateStatusIcon('failed');
    if (_activeTab !== 'activity') {
      _newEventCount += 1;
      _updateActivityBadge();
    }
  }

  function updateContext(ctx) {
    if (!_openNodeId) return;
    ctx = Object.assign({}, _currentCtx, ctx || {});
    if (!Object.prototype.hasOwnProperty.call(ctx, 'tokenMetrics')) {
      ctx.tokenMetrics = _currentCtx.tokenMetrics;
    }
    if (!Object.prototype.hasOwnProperty.call(ctx, 'modelRows')) {
      ctx.modelRows = _currentCtx.modelRows;
    }
    _currentCtx = ctx;
    if (ctx.status !== undefined) _updateStatusIcon(ctx.status);
    if (ctx.kpis   !== undefined) _renderKpis(ctx.kpis);
    _renderOverview(ctx);
    _renderRaw(ctx);
  }

  // User scroll-away detection — locks auto-scroll until they return to
  // bottom. Threshold of 24px so a small wheel nudge doesn't flip it.
  if (elLog) {
    elLog.addEventListener('scroll', () => {
      const atBottom = (elLog.scrollHeight - elLog.scrollTop - elLog.clientHeight) < 24;
      _userPinnedScroll = atBottom;
    });
  }
  // Tab click delegation — single listener on the strip.
  if (elTabs) {
    elTabs.addEventListener('click', (e) => {
      const btn = e.target.closest('.fw-node-drawer-tab');
      if (!btn) return;
      const tab = btn.dataset.tab;
      if (tab) _switchTab(tab);
    });
  }
  if (elClose) elClose.addEventListener('click', close);
  document.addEventListener('keydown', (e) => {
    if (!elDrawer || !elDrawer.classList.contains('visible')) return;
    if (e.key === 'Escape') { close(); return; }
    // Number-key tab shortcut (1=overview, 2=activity, 3=raw). Ignore
    // when the user is typing inside an input/textarea, and skip when
    // any modifier is held so this never clashes with browser hotkeys.
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const tag = (document.activeElement?.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea') return;
    if (e.key === '1') _switchTab('overview');
    else if (e.key === '2') _switchTab('activity');
    else if (e.key === '3') _switchTab('raw');
  });

  // reset() — clear in-flight events + DOM log without closing. Used
  // when the study orchestrator advances to the next chapter so an
  // already-open drawer doesn't keep stale events from the previous
  // chapter's run of the same node.
  function reset() {
    _pendingEvents = [];
    _newEventCount = 0;
    if (elLog) {
      while (elLog.firstChild) elLog.removeChild(elLog.firstChild);
    }
    if (elLogEmpty) elLogEmpty.style.display = '';
    _lastSeenAt.clear();
    _prevSeenForOpen = 0;
    _currentCtx = {};
    _updateActivityBadge();
  }

  return { open, close, reset, isOpenFor, appendEvent, updateContext,
           get openNodeId() { return _openNodeId; },
           get openStage()  { return _openStage; } };
})();
