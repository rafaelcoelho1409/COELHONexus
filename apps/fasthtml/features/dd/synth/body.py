"""Synth body — empty state + 70/30 split: DAG canvas (left) + chapter checklist (right).

Chapter list lives in a narrow side panel (where a vertical list belongs)
instead of a full-width strip; clicking a chapter focuses its sub-graph
on the canvas (`_onStripCellClick`, already wired). Canvas + `#fw-chstrip`
both start display:none — JS reveals the graph when a framework is active
and the chapter panel only in study mode (≥2 chapters), so a non-study
run keeps the graph at full width (flex).

A small inline `<script>` at the bottom wires a fallback click handler on
`#fw-synth-start` that issues a direct `fetch` to FastAPI's
`/synth/{slug}?mode=quality&budget=5`. Mirrors what `body.py` does for
the Planner. The ES-module path has many fragile gates
(`synthHasPlan`, `synthImplemented.size`, `synthThreadId`/`studyThreadId`
clearing) that can silent-return on a fresh page load before the
catalog/registry hydration completes — leaving the pill stuck on its
server-rendered "Idle" text. The inline fallback runs at HTML-parse
time, defers to the module when `window.__synthWired === true`, and
otherwise POSTs directly + reloads to track progress."""
from fasthtml.common import Div, NotStr, Script, Span


_FALLBACK_CLICK = """\
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
    if (window.__synthWired) return;   // module handler will fire
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
    if (b === btn) return;   // direct handler above already handled it
    handleClick(b);
  });
})();
"""


def SynthBody(slug: str | None):
    empty_msg = (
        "Pick a framework from the library to view the synth pipeline."
        if not slug else
        "Loading synth state…"
    )
    return Div(
        Div(empty_msg, id = "fw-synth-empty", cls = "fw-stage-empty"),
        Div(
            Div(
                Div(id = "fw-synth-canvas", cls = "fw-stage-canvas"),
                id = "fw-synth-graph", cls = "fw-planner-graph",
            ),
            Div(
                Div(
                    Span("Chapters", cls = "fw-chstrip-title"),
                    Span(id = "fw-chstrip-counter", cls = "fw-chstrip-counter"),
                    cls = "fw-chstrip-head",
                ),
                Div(id = "fw-chstrip-cells", cls = "fw-chstrip-cells"),
                # Post-study book_harmonize indicator (2026-06-08).
                # Surfaces the cross-chapter coherence pass that runs
                # after every chapter completes. Hidden until the
                # book_harmonize_start SSE arrives; states map to
                # data-status: running / skipped / done.
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
                id = "fw-chstrip", cls = "fw-chstrip",
            ),
            cls = "fw-synth-split",
        ),
        Script(NotStr(_FALLBACK_CLICK)),
        cls = "fw-step-panel active",
        id = "fw-step-4-panel",
    )
