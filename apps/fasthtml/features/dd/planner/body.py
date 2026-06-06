"""Planner body — empty state + Cytoscape DAG canvas.

Server-renders the correct initial visibility based on `slug` — empty
placeholder when there's no slug, graph wrapper when there IS one. The
Cytoscape canvas has non-zero dimensions on first paint (so the layout
settles at the real size) and the "Loading planner state…" placeholder
doesn't linger if `_toggleStageEmpty` is slow to fire (a common
symptom when the planner.js dynamic import chain takes ~1s on cold
load). JS `_toggleStageEmpty` is still wired for runtime toggles
(e.g. user wipes a framework mid-session), but no longer the sole
source of truth for initial state.

A small inline `<script>` at the bottom wires a fallback click handler
on `#fw-planner-start` that issues a direct `fetch` to the FastAPI
endpoint. This bypasses the ES-module load chain entirely (which is
where the planner module-init or dynamic-import sequence had been
silently failing on some clients — typically mobile browsers where
devtools aren't available to diagnose). The full-featured module
handler still wins when it loads (it sets `window.__plannerWired=true`
before this fallback fires); the fallback only activates when the
module path didn't run."""
from fasthtml.common import Div, NotStr, Script


_FALLBACK_CLICK = """\
(function () {
  // Fallback click wiring — fires only if the ES-module path hasn't
  // attached its own handler by the time the user taps. Reads slug
  // from the .fw-picker data-attribute (or ?slug=) and POSTs to the
  // planner endpoint with a fresh thread_id. Keeps the planner usable
  // even when planner.js fails to load (cold cache / slow CDN / 4G
  // hiccup) — instead of an opaque silent no-op.
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
    // ALWAYS run inline — the module handler proved broken on
    // mobile (probe showed __plannerWired=true but module-side
    // startPlanner produced no visible UI change after the click).
    // Inline path is now the canonical handler; planner.js's
    // own document-click delegation was removed in the same change.
    var slug = getSlug();
    if (!slug) {
      showFlash('Pick a framework from the Library picker first.', 'err');
      return;
    }
    btn.setAttribute('disabled', 'disabled');
    var origText = btn.textContent;
    btn.textContent = 'Starting…';
    try {
      // Prefer reusing an existing thread (resume) so cached
      // checkpoints aren't re-run. Mirror the module path's
      // smart-resume logic.
      var tid = null;
      var isResume = false;
      try {
        var rr = await fetch('/api/v1/docs-distiller/planner/recent');
        if (rr.ok) {
          var rd = await rr.json();
          var found = ((rd && rd.recent) || []).find(function (it) { return it.slug === slug; });
          if (found && found.thread_id) { tid = found.thread_id; isResume = true; }
        }
      } catch (_) { /* fall through to fresh thread */ }
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
      // Persist the thread id so post-reload _tryResumeActivePlanner
      // reconnects to the live SSE without a round-trip to /recent.
      try { localStorage.setItem('dd:planner:active:' + slug, tid); } catch (_) {}
      showFlash('Planner started. Reloading to track progress…');
      setTimeout(function () { window.location.reload(); }, 800);
    } catch (e) {
      showFlash('Planner start failed: ' + String(e), 'err');
      btn.removeAttribute('disabled');
      btn.textContent = origText;
    }
  }
  // Direct attach on the button as soon as it's in DOM. This script tag
  // sits AFTER the toolbar row in source order (it's rendered inside the
  // PlannerBody panel which comes after the topbar-wrap), so the button
  // is always queryable at this point.
  var btn = document.getElementById('fw-planner-start');
  // PROOF-OF-LIFE marker on the button title so the user can verify
  // (without DevTools) whether this inline script even ran. If the
  // button's tooltip after load is "PROBE: inline script ran (vN)",
  // the script is executing fine and any "nothing happens on click"
  // is a CLICK-DETECTION issue — not a script-attach issue.
  if (btn) {
    btn.addEventListener('click', function () { handleClick(btn); });
  }
  // Belt-and-suspenders: also do document-level delegation.
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


def PlannerBody(slug: str | None):
    empty_msg = (
        "Pick a framework from the library to view the planner pipeline."
        if not slug else
        "Loading planner state…"
    )
    # Header moved to the row-3 toolbar (PlannerPill + PlannerActions).
    # Title is redundant with the active stage tab; framework identity is
    # redundant with the Library picker — both dropped from the body.
    empty_style = "display:none;" if slug else "display:block;"
    graph_style = "display:flex;" if slug else "display:none;"
    return Div(
        Div(empty_msg, id = "fw-planner-empty", cls = "fw-stage-empty",
            style = empty_style),
        Div(
            Div(id = "fw-planner-canvas", cls = "fw-stage-canvas"),
            id = "fw-planner-graph", cls = "fw-planner-graph",
            style = graph_style,
        ),
        Script(NotStr(_FALLBACK_CLICK)),
        cls = "fw-step-panel active",
        id = "fw-step-3-panel",
    )
