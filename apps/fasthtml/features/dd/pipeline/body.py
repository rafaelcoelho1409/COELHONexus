"""Pipeline body — unified Planner + Synth page (2026-06-08).

Shows the two Cytoscape canvases side by side (Planner on the left,
Synth on the right) with the chapter strip on the right-hand rail.
Preserves the exact DOM IDs the existing planner.js
and synth.js modules expect (`#fw-planner-canvas`, `#fw-planner-graph`,
`#fw-planner-empty`, `#fw-synth-canvas`, `#fw-synth-graph`,
`#fw-synth-empty`, `#fw-chstrip`, `#fw-chstrip-cells`,
`#fw-chstrip-counter`) so no JS rewires are needed — main.js's
`initPipeline` just runs `initPlanner()` then `initSynth()` and each
stage's existing wiring takes over.

SOTA pattern (June 2026): single unified page with two zoned subgraphs
+ shared downstream-asset view (the chapter strip). Mirrors Dagster's
asset-graph UX where upstream + downstream stages co-exist on one
canvas — without merging the controls (per-stage Start/Stop/Wipe
keeps failure isolation). See PIPELINE-UNIFIED-LAYOUT-2026-06-08.md
for the research backing."""
from fasthtml.common import Button, Div, NotStr, Script, Span

from .chrome import PipelineTotalSummary


# Both inline fallback scripts (planner + synth start buttons) — pulled
# verbatim from the per-stage bodies so a cold-load module-init failure
# on either stage doesn't kill the start path.
_PLANNER_FALLBACK_CLICK = """\
(function () {
  function uuid() {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      var r = Math.random() * 16 | 0;
      return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
    });
  }
  function getSlug() {
    var picker = document.querySelector('.fw-picker');
    var fromAttr = picker && picker.getAttribute('data-dd-slug');
    if (fromAttr) return fromAttr;
    try { return new URLSearchParams(window.location.search).get('slug'); }
    catch (_) { return null; }
  }
  function showFlash(msg, kind) {
    var box = document.createElement('div');
    box.textContent = msg;
    box.style.cssText =
      'position:fixed;left:50%;bottom:24px;transform:translateX(-50%);' +
      'background:' + (kind === 'err' ? '#a8071a' : '#1a3a52') + ';' +
      'color:#fff;padding:12px 20px;border-radius:6px;font-size:14px;' +
      'z-index:9999;box-shadow:0 4px 12px rgba(0,0,0,0.3);max-width:90vw';
    document.body.appendChild(box);
    setTimeout(function () { box.remove(); }, 4000);
  }
  async function handleClick(btn) {
    var slug = getSlug();
    if (!slug) {
      showFlash('Pick a framework from the Library picker first.', 'err');
      return;
    }
    btn.setAttribute('disabled', 'disabled');
    var origText = btn.textContent;
    btn.textContent = 'Starting…';
    try {
      var tid = null;
      var isResume = false;
      try {
        var rr = await fetch('/api/v1/docs-distiller/planner/recent');
        if (rr.ok) {
          var rd = await rr.json();
          var found = ((rd && rd.recent) || []).find(function (it) { return it.slug === slug; });
          if (found && found.thread_id) { tid = found.thread_id; isResume = true; }
        }
      } catch (_) {}
      if (!tid) tid = 'docs-distiller/' + slug + '/' + uuid();
      var url = isResume
        ? '/api/v1/docs-distiller/planner/' + encodeURIComponent(tid) + '/resume'
        : '/api/v1/docs-distiller/planner/' + slug +
          '?mode=llm&thread_id=' + encodeURIComponent(tid);
      var r = await fetch(url, { method: 'POST' });
      if (!r.ok) {
        var txt = await r.text();
        showFlash('Planner start failed: HTTP ' + r.status + ' — ' + txt.slice(0, 160), 'err');
        btn.removeAttribute('disabled');
        btn.textContent = origText;
        return;
      }
      var data = await r.json();
      if (data && data.status === 'locked') {
        showFlash(data.message || 'Planner blocked — another stage is running.', 'err');
        btn.removeAttribute('disabled');
        btn.textContent = origText;
        return;
      }
      try { localStorage.setItem('dd:planner:active:' + slug, tid); } catch (_) {}
      showFlash('Planner started. Reloading to track progress…');
      setTimeout(function () { window.location.reload(); }, 800);
    } catch (e) {
      showFlash('Planner start failed: ' + String(e), 'err');
      btn.removeAttribute('disabled');
      btn.textContent = origText;
    }
  }
  var btn = document.getElementById('fw-planner-start');
  if (btn) {
    btn.addEventListener('click', function () { handleClick(btn); });
  }
  document.addEventListener('click', function (ev) {
    var t = ev.target;
    if (!t || typeof t.closest !== 'function') return;
    var b = t.closest('#fw-planner-start');
    if (!b) return;
    if (b === btn) return;
    handleClick(b);
  });
})();
"""


# Start-button gating — Pipeline-page UX rule (2026-06-08):
# Start is enabled only when the respective stage has NO complete
# output. Once Planner has produced plan-latest.json, the Planner Start
# button is disabled — user must click Wipe Planner first (which
# cascades to wipe Synth too, via the existing wipe handler). Same for
# Synth: once every chapter has rendered, Synth Start is disabled until
# the user explicitly wipes. This prevents accidental re-runs of
# expensive cached work and makes the wipe-then-start flow the only
# path, with no inline cascade-confirm dialog needed.
#
# Re-probes the server state on: page load, focus restore, planner
# terminal SSE, any wipe button click. Watches the buttons themselves
# via MutationObserver so a mid-run state change (Stop → Start)
# triggers a re-evaluation.
_START_BUTTON_GATING = """\
(function () {
  function getSlug() {
    var picker = document.querySelector('.fw-picker');
    var fromAttr = picker && picker.getAttribute('data-dd-slug');
    if (fromAttr) return fromAttr;
    try { return new URLSearchParams(window.location.search).get('slug'); }
    catch (_) { return null; }
  }

  var pending = null;
  function debounced() {
    if (pending) clearTimeout(pending);
    pending = setTimeout(apply, 600);
  }

  async function probePlannerDone(slug) {
    try {
      var r = await fetch('/api/v1/docs-distiller/pipeline/' + slug + '/state');
      if (r.ok) return !!(await r.json()).planner;
    } catch (_) {}
    return false;
  }
  async function probeSynthAllDone(slug) {
    try {
      var r = await fetch('/api/v1/docs-distiller/synth/' + slug +
                          '/study/chapters');
      if (r.ok) {
        var chs = ((await r.json()).chapters) || [];
        return chs.length > 0 && chs.every(function (c) { return c.rendered; });
      }
    } catch (_) {}
    return false;
  }

  function syncGate(btnId, shouldDisable, reason) {
    var btn = document.getElementById(btnId);
    if (!btn) return;
    var html = btn.innerHTML || '';
    // Hands off during mid-run states — the module handler owns the
    // disabled state then (Cancel/Stop/Cancelling all reflect a live
    // operation that the user explicitly initiated).
    if (html.indexOf('Cancel') !== -1 ||
        html.indexOf('Cancelling') !== -1 ||
        html.indexOf('Stop') !== -1 ||
        html.indexOf('Starting') !== -1) return;
    if (shouldDisable) {
      btn.setAttribute('disabled', 'disabled');
      btn.dataset.pipelineGated = '1';
      btn.title = reason;
    } else if (btn.dataset.pipelineGated === '1') {
      btn.removeAttribute('disabled');
      delete btn.dataset.pipelineGated;
      btn.removeAttribute('title');
    }
  }

  async function apply() {
    pending = null;
    var slug = getSlug();
    if (!slug) return;
    var results = await Promise.all([
      probePlannerDone(slug),
      probeSynthAllDone(slug),
    ]);
    syncGate('fw-planner-start', results[0],
      'Planner output already exists for this framework. Click Wipe ' +
      'Planner first to re-run (cascades to wipe Synth too).');
    syncGate('fw-synth-start', results[1],
      'All chapters are rendered. Click Wipe Synth first to re-synth.');
  }

  // Initial probe — runs as soon as this script parses.
  apply();

  // Re-probe triggers
  window.addEventListener('focus', debounced);
  document.addEventListener('dd:planner:terminal', debounced);

  // Re-probe ~1.5s after any wipe click — gives the backend time to
  // clear its MinIO/Postgres state before we re-read the gate.
  document.addEventListener('click', function (ev) {
    if (ev.target.closest('#fw-planner-wipe') ||
        ev.target.closest('#fw-synth-wipe')) {
      setTimeout(apply, 1500);
    }
  });

  // Watch the buttons themselves for label/state changes from the
  // module path (e.g. cancel→start transitions). Filter out our own
  // disabled-attribute writes so we don't recurse.
  ['fw-planner-start', 'fw-synth-start'].forEach(function (id) {
    var el = document.getElementById(id);
    if (!el) return;
    var obs = new MutationObserver(function (muts) {
      var onlyOurDisable = muts.every(function (m) {
        return m.type === 'attributes' && m.attributeName === 'disabled';
      });
      if (onlyOurDisable) return;
      debounced();
    });
    obs.observe(el, {
      attributes: true, childList: true,
      characterData: true, subtree: true,
    });
  });
})();
"""


# Planner-done → synth-state refresh (2026-06-08).
# When Planner finishes successfully, the new plan-latest.json exists in
# MinIO and `/synth/{slug}/study/chapters` returns 200 with the chapter
# list. But the synth module cached `Sy.synthHasPlan=false` from when
# the page first loaded (no plan yet) — without a refresh nudge, the
# Synth Start button stays disabled and the chapter sidebar stays empty
# even though both should populate immediately. This inline script
# dynamic-imports the synth module on `dd:planner:terminal` and calls
# the three refresh entry points (`_hydrateChStripFromChapters`,
# `_refreshSynthPlanGate`, `refreshSynthStartState`) so the UI catches
# up at the same instant the user sees "Planner: Done".
_PLANNER_DONE_SYNTH_SYNC = """\
(function () {
  function getSlug() {
    var picker = document.querySelector('.fw-picker');
    var fromAttr = picker && picker.getAttribute('data-dd-slug');
    if (fromAttr) return fromAttr;
    try { return new URLSearchParams(window.location.search).get('slug'); }
    catch (_) { return null; }
  }
  function wait(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  document.addEventListener('dd:planner:terminal', async function (ev) {
    var status = (ev.detail && ev.detail.status) || '';
    if (status !== 'done') return;
    var slug = getSlug();
    if (!slug) return;
    try {
      var [synth, ui] = await Promise.all([
        import('@dd/synth/synth.js'),
        import('@dd/shared/ui.js'),
      ]);

      // 1. Populate the chapter strip from the freshly-written plan.
      if (synth._hydrateChStripFromChapters) {
        await synth._hydrateChStripFromChapters(slug);
      }
      // 2. Mark synth.synthHasPlan = true so the Start button's
      //    internal gate sees a plan.
      if (synth._refreshSynthPlanGate) {
        await synth._refreshSynthPlanGate(slug);
      }

      // 3. Cross-stage lock release race: the backend's CAD-finally
      //    runs ~50-150 ms AFTER the planner terminal SSE — when this
      //    listener fires, Redis may still show a stale planner lock.
      //    Poll-with-retry up to ~3 s: refreshCrossStageBlocker reads
      //    /pipeline/active, then crossStageBlockerFor('synth') reports
      //    whether planner is still locking us. Stop the moment the
      //    block clears.
      var cleared = false;
      var delays = [0, 250, 500, 1000, 1500];
      for (var i = 0; i < delays.length; i++) {
        if (delays[i]) await wait(delays[i]);
        try {
          if (ui.refreshCrossStageBlocker) {
            await ui.refreshCrossStageBlocker();
          }
          var blocker = ui.crossStageBlockerFor
                        ? ui.crossStageBlockerFor('synth')
                        : null;
          if (!blocker) { cleared = true; break; }
        } catch (_) { /* keep retrying */ }
      }
      if (!cleared) {
        console.warn('[pipeline] planner cross-stage lock did not ' +
                     'clear within 3s — synth Start may stay blocked');
      }

      // 4. Final re-evaluation — runs with all 3 gates clean
      //    (synthHasPlan=true, blocker=null, no in-flight run).
      if (synth.refreshSynthStartState) {
        synth.refreshSynthStartState();
      }
    } catch (e) {
      console.warn('[pipeline] synth refresh on planner done failed:', e);
    }
  });
})();
"""


# Auto-chain wiring — opt-in checkbox in the toolbar persists to
# localStorage; when ticked AND Planner finishes successfully, we
# programmatically click #fw-synth-start so Synth starts without the
# user lifting a finger. Listens for the CustomEvent the planner's
# polling module dispatches on terminal SSE.
_AUTO_CHAIN_WIRING = """\
(function () {
  var KEY = 'dd:pipeline:autochain';
  var cb = document.getElementById('fw-pipeline-autochain');
  if (cb) {
    try { cb.checked = localStorage.getItem(KEY) === '1'; } catch (_) {}
    cb.addEventListener('change', function () {
      try { localStorage.setItem(KEY, cb.checked ? '1' : '0'); } catch (_) {}
    });
  }
  document.addEventListener('dd:planner:terminal', function (ev) {
    var enabled = false;
    try { enabled = localStorage.getItem(KEY) === '1'; } catch (_) {}
    if (!enabled) return;
    var status = (ev.detail && ev.detail.status) || '';
    // Only chain on clean Planner completion — failed/cancelled stays
    // manual so the user can investigate before kicking off the
    // expensive Synth run.
    if (status !== 'done') return;
    var synthBtn = document.getElementById('fw-synth-start');
    if (!synthBtn) return;
    // Brief delay so the Planner UI finishes its terminal cleanup
    // (refreshes Start state, releases the cross-stage lock via the
    // backend CAD-finally) before we click — otherwise the click hits
    // a still-locked endpoint and shows a `locked` toast.
    setTimeout(function () {
      try {
        var box = document.createElement('div');
        box.textContent = '▷ Auto-chain: Planner done → starting Synth…';
        box.style.cssText =
          'position:fixed;left:50%;bottom:24px;transform:translateX(-50%);' +
          'background:#1a3a52;color:#fff;padding:12px 20px;border-radius:6px;' +
          'font-size:14px;z-index:9999;box-shadow:0 4px 12px rgba(0,0,0,0.3)';
        document.body.appendChild(box);
        setTimeout(function () { box.remove(); }, 3500);
      } catch (_) {}
      synthBtn.click();
    }, 1500);
  });
})();
"""


_SYNTH_FALLBACK_CLICK = """\
(function () {
  function getSlug() {
    var picker = document.querySelector('.fw-picker');
    var fromAttr = picker && picker.getAttribute('data-dd-slug');
    if (fromAttr) return fromAttr;
    try { return new URLSearchParams(window.location.search).get('slug'); }
    catch (_) { return null; }
  }
  function showFlash(msg, kind) {
    var box = document.createElement('div');
    box.textContent = msg;
    box.style.cssText =
      'position:fixed;left:50%;bottom:24px;transform:translateX(-50%);' +
      'background:' + (kind === 'err' ? '#a8071a' : '#1a3a52') + ';' +
      'color:#fff;padding:12px 20px;border-radius:6px;font-size:14px;' +
      'z-index:9999;box-shadow:0 4px 12px rgba(0,0,0,0.3);max-width:90vw';
    document.body.appendChild(box);
    setTimeout(function () { box.remove(); }, 4000);
  }
  async function handleClick(btn) {
    if (window.__synthWired) return;
    var slug = getSlug();
    if (!slug) {
      showFlash('Pick a framework from the Library picker first.', 'err');
      return;
    }
    btn.setAttribute('disabled', 'disabled');
    var origText = btn.textContent;
    btn.textContent = 'Starting…';
    try {
      var budgetEl = document.getElementById('fw-synth-budget');
      var budget = (budgetEl && budgetEl.value) || '5';
      var url = '/api/v1/docs-distiller/synth/' + slug +
                '?mode=quality&budget=' + encodeURIComponent(budget);
      var r = await fetch(url, { method: 'POST' });
      if (!r.ok) {
        var txt = await r.text();
        showFlash('Synth start failed: HTTP ' + r.status + ' — ' +
                  txt.slice(0, 160), 'err');
        btn.removeAttribute('disabled');
        btn.textContent = origText;
        return;
      }
      var data = await r.json();
      if (data && data.status === 'locked') {
        showFlash(data.message ||
                  'Synth blocked — another stage is running.', 'err');
        btn.removeAttribute('disabled');
        btn.textContent = origText;
        return;
      }
      if (data && data.study_thread_id) {
        try {
          localStorage.setItem('dd:study:active:' + slug,
                               data.study_thread_id);
        } catch (_) {}
      }
      showFlash('Synth started — ' + (data.n_chapters || 0) +
                ' chapter(s). Reloading to track progress…');
      setTimeout(function () { window.location.reload(); }, 800);
    } catch (e) {
      showFlash('Synth start failed: ' + String(e), 'err');
      btn.removeAttribute('disabled');
      btn.textContent = origText;
    }
  }
  var btn = document.getElementById('fw-synth-start');
  if (btn) {
    btn.addEventListener('click', function () { handleClick(btn); });
  }
  document.addEventListener('click', function (ev) {
    var t = ev.target;
    if (!t || typeof t.closest !== 'function') return;
    var b = t.closest('#fw-synth-start');
    if (!b) return;
    if (b === btn) return;
    handleClick(b);
  });
})();
"""


def PipelineBody(slug: str | None):
    empty_msg = (
        "Pick a framework from the library to view the planner+synth pipeline."
        if not slug else
        "Loading pipeline state…"
    )
    empty_style = "display:none;" if slug else "display:block;"
    grid_style = "display:grid;" if slug else "display:none;"
    return Div(
        Div(empty_msg, id = "fw-pipeline-empty", cls = "fw-stage-empty",
            style = empty_style),
        Div(
            PipelineTotalSummary(),
            # LEFT COLUMN — Planner + Synth canvases
            Div(
                # Planner zone (top)
                Div(
                    Div(
                        Div("Planner", cls = "fw-pipeline-zone-label"),
                        Button(
                            "LLM usage",
                            id = "fw-planner-llm-open",
                            cls = "fw-pipeline-zone-btn",
                            type = "button",
                            title = "Open planner LLM usage",
                        ),
                        cls = "fw-pipeline-zone-head",
                    ),
                    Div("Loading planner state…",
                        id = "fw-planner-empty", cls = "fw-stage-empty",
                        style = "display:none"),
                    Div(
                        Div(id = "fw-planner-canvas", cls = "fw-stage-canvas"),
                        id = "fw-planner-graph", cls = "fw-planner-graph",
                        style = "display:flex;",
                    ),
                    cls = "fw-pipeline-zone fw-pipeline-zone-planner",
                ),
                # Synth zone (bottom)
                Div(
                    Div(
                        Div("Synth", cls = "fw-pipeline-zone-label"),
                        Button(
                            "LLM usage",
                            id = "fw-synth-llm-open",
                            cls = "fw-pipeline-zone-btn",
                            type = "button",
                            title = "Open synth LLM usage",
                        ),
                        cls = "fw-pipeline-zone-head",
                    ),
                    Div("Loading synth state…",
                        id = "fw-synth-empty", cls = "fw-stage-empty"),
                    Div(
                        Div(id = "fw-synth-canvas", cls = "fw-stage-canvas"),
                        id = "fw-synth-graph", cls = "fw-planner-graph",
                    ),
                    cls = "fw-pipeline-zone fw-pipeline-zone-synth",
                ),
                cls = "fw-pipeline-canvases",
            ),
            # RIGHT COLUMN — chapter strip (always visible on the
            # Pipeline page, even when empty; populates as Planner emits
            # its chapter list and Synth ticks through them). The
            # `.visible` class is server-rendered here so a fresh page
            # load or a post-wipe state still keeps the column in the
            # layout — only the cells inside go empty.
            Div(
                Div(
                    Span("Chapters", cls = "fw-chstrip-title"),
                    Span(id = "fw-chstrip-counter", cls = "fw-chstrip-counter"),
                    cls = "fw-chstrip-head",
                ),
                Div(id = "fw-chstrip-cells", cls = "fw-chstrip-cells"),
                # Post-study book_harmonize indicator (2026-06-08).
                # See synth/body.py for the full rationale; same DOM
                # contract so polling.js's _updateBookHarmonize() works
                # on either page.
                Div(
                    Span(cls = "fw-bh-icon"),
                    Span("Harmonize", cls = "fw-bh-label"),
                    Span("—", cls = "fw-bh-status",
                         id = "fw-bh-status-text"),
                    id = "fw-book-harmonize",
                    cls = "fw-book-harmonize",
                    data_status = "idle",
                    title = ("Cross-chapter terminology + claim "
                             "consistency pass that runs after all "
                             "chapters complete"),
                ),
                id = "fw-chstrip", cls = "fw-chstrip visible",
            ),
            id = "fw-pipeline-grid", cls = "fw-pipeline-grid",
            style = grid_style,
        ),
        Script(NotStr(_PLANNER_FALLBACK_CLICK)),
        Script(NotStr(_SYNTH_FALLBACK_CLICK)),
        Script(NotStr(_START_BUTTON_GATING)),
        # Planner-done → synth-refresh runs BEFORE the auto-chain wiring
        # so when auto-chain is enabled and Planner finishes, the synth
        # plan-gate cache is already warm by the time the auto-click
        # fires on #fw-synth-start (no spurious "no plan" toast).
        Script(NotStr(_PLANNER_DONE_SYNTH_SYNC)),
        Script(NotStr(_AUTO_CHAIN_WIRING)),
        cls = "fw-step-panel active",
        id = "fw-step-pipeline-panel",
    )
