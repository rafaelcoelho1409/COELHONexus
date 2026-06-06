// planner/drawer.js — NodeDrawer IIFE.
//
// Self-contained right-side drawer that surfaces a single planner/synth
// graph node's live activity: status icon + KPI strip + Results panel
// + a sticky-bottom log fed by SSE events. Extracted from planner.js
// (Phase D, 2026-06-05) — was lines 358-630 of the monolith, IIFE
// boundary made the extraction clean. The reset() escape hatch lets the
// study orchestrator clear stale events when advancing to the next
// chapter without forcing the user to close the drawer.
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
  const elClose     = document.getElementById('fw-node-drawer-close');

  const MAX_LOG_LINES = 200;
  const STATUS_ICON = {
    future: '⏳', pending: '○', running: '◐',
    done: '●', failed: '✕', cancelled: '∅',
  };

  let _openStage = null;        // 'planner' | 'synth' | null
  let _openNodeId = null;
  let _pendingEvents = [];
  let _flushScheduled = false;
  let _userPinnedScroll = true; // true = auto-scroll to bottom; false = user scrolled up
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

  function _renderDetails(ctx) {
    if (!elDetails) return;
    const resultsBlock = ctx.resultsHtml
      ? '<div class="fw-node-drawer-results">' + ctx.resultsHtml + '</div>'
      : '<div class="fw-empty fw-node-drawer-waiting">' +
        (ctx.status === 'running'
          ? 'Running — results will appear once this node commits its checkpoint.'
          : ctx.status === 'failed'
          ? 'This node failed before producing output. See the activity log for details.'
          : ctx.status === 'future'
          ? 'Not yet implemented — substep will activate when its node code ships.'
          : 'Waiting for this node to run.') +
        '</div>';
    const debug = [];
    if (ctx.inputs) debug.push({
      id: 'inputs',  title: 'Inputs (upstream state, raw)',
      content: '<pre>' + escapeHtml(ctx.inputs) + '</pre>',
    });
    if (ctx.outputs) debug.push({
      id: 'outputs', title: 'Outputs (this node, raw)',
      content: '<pre>' + escapeHtml(ctx.outputs) + '</pre>',
    });
    const debugBlock = debug.length
      ? debug.map(s =>
          '<details class="fw-node-drawer-detail" data-section="' + s.id + '">' +
            '<summary>' + escapeHtml(s.title) + '</summary>' +
            '<div class="fw-node-drawer-detail-body">' + s.content + '</div>' +
          '</details>'
        ).join('')
      : '';
    elDetails.innerHTML = resultsBlock + debugBlock;
  }

  function _populate(stage, nodeId, ctx) {
    const key = stage + '/' + nodeId;
    _prevSeenForOpen = _lastSeenAt.get(key) || 0;
    _lastSeenAt.set(key, Date.now());
    _openStage  = stage;
    _openNodeId = nodeId;
    _pendingEvents = [];
    _userPinnedScroll = true;
    if (elTitle) elTitle.textContent = ctx.label || nodeId;
    if (elMeta)  elMeta.textContent  = stage + ' · ' + nodeId;
    _updateStatusIcon(ctx.status || 'pending');
    _renderKpis(ctx.kpis);
    _renderDetails(ctx);
    if (elLog) elLog.innerHTML = '';
    if (elLogEmpty) elLogEmpty.style.display = '';
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
  }

  function updateContext(ctx) {
    if (!_openNodeId) return;
    ctx = ctx || {};
    if (ctx.status !== undefined) _updateStatusIcon(ctx.status);
    if (ctx.kpis   !== undefined) _renderKpis(ctx.kpis);
    _renderDetails(ctx);
  }

  // User scroll-away detection — locks auto-scroll until they return to
  // bottom. Threshold of 24px so a small wheel nudge doesn't flip it.
  if (elLog) {
    elLog.addEventListener('scroll', () => {
      const atBottom = (elLog.scrollHeight - elLog.scrollTop - elLog.clientHeight) < 24;
      _userPinnedScroll = atBottom;
    });
  }
  if (elClose) elClose.addEventListener('click', close);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && elDrawer && elDrawer.classList.contains('visible')) {
      close();
    }
  });

  // reset() — clear in-flight events + DOM log without closing. Used
  // when the study orchestrator advances to the next chapter so an
  // already-open drawer doesn't keep stale events from the previous
  // chapter's run of the same node.
  function reset() {
    _pendingEvents = [];
    if (elLog) {
      while (elLog.firstChild) elLog.removeChild(elLog.firstChild);
    }
    if (elLogEmpty) elLogEmpty.style.display = '';
    _lastSeenAt.clear();
    _prevSeenForOpen = 0;
  }

  return { open, close, reset, isOpenFor, appendEvent, updateContext,
           get openNodeId() { return _openNodeId; },
           get openStage()  { return _openStage; } };
})();
