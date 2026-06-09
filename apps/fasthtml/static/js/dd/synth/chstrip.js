// synth/chstrip.js — Chapter-progress strip rendering + cell interaction.
// Extracted from synth.js Step 4 (2026-06-05 follow-up) using the DI
// registration pattern (see chstrip_deps.js for the contract).
//
// Why DI: this block has 6 cross-references to functions defined later
// in synth.js (pollSynthState, refreshSynthStartState, resetSynthCards,
// _resetSynthEventBuffer, renderSynthCards, _resizeSynthCanvas) plus
// the _nodeDrawerRef module-private value. A direct import would cycle.
// chstrip.js reads from the `deps` object instead; synth.js mutates
// `deps` via registerChstripDeps(...) at the bottom of its module body.
import * as Sa from '@dd/shared/state/api.js';
import * as Si from '@dd/shared/state/ingestion.js';
import * as Sy from '@dd/shared/state/synth.js';
import { escapeHtml } from '../shared/utils.js';
import { fmtMs, showElapsed } from '../shared/timing.js';
import { showToast } from '../shared/ui.js';
import { _setSynthStagePill } from './graph.js';
import { deps } from './chstrip_deps.js';



export function _showChStrip(visible) {
  if (!Sy.chstripEl) return;
  Sy.chstripEl.classList.toggle('visible', !!visible);
  // Showing/hiding the 30% chapter panel reflows the graph column
  // (100% ↔ ~70%). Cytoscape latches its container size, so re-fit on
  // the next frame (after layout settles) or the DAG renders at the
  // stale width. No-op until the canvas is mounted.
  if (Sy.synthGraph) {
    requestAnimationFrame(() => { try { deps._resizeSynthCanvas?.(); } catch (_) {} });
  }
}
// Derive a readable label from a chapter id when no real title is
// available yet (live SSE path only carries ids). Strips the "ch-NN-"
// prefix and turns separators into spaces:
//   ch-01-introduction-to-pydantic-basics → "Introduction to pydantic basics"
// Used only as a fallback — _applyChStripTitles upgrades to the exact
// backend title (e.g. "Introduction to Pydantic Basics") right after.
function _humanizeChapterId(id) {
  const s = String(id || '')
    .replace(/^ch[-_]?\d+[-_]?/i, '')
    .replace(/[-_]+/g, ' ')
    .trim();
  if (!s) return String(id || '');
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// Render the Chapters checklist. `items` may be an array of id STRINGS
// (live SSE / POST paths, ids only) OR {id, title} OBJECTS (durable
// hydrate path, exact titles). Vertical task-list layout: status glyph
// + ordinal + chapter title, one row per chapter (DD-CHAPTERS-SOTA
// 2026-05-28 — the agent/pipeline task-list pattern).
export function _renderChStrip(items) {
  if (!Sy.chstripCellsEl) return;
  const norm = (items || []).map(it =>
    (typeof it === 'string')
      ? { id: it, title: null }
      : { id: it.id, title: it.title || null }
  );
  const ids = norm.map(c => c.id);
  Sy.setStudyChapterIds(ids.slice());
  Sy.setStudyChapterStatus(new Map(ids.map(id => [id, 'pending'])));
  Sy.setStudyCurrentChapterId(null);
  Sy.chstripCellsEl.innerHTML = norm.map((c, i) => {
    const title = c.title || _humanizeChapterId(c.id);
    // title="" → full chapter name on hover when the row ellipsis-
    // truncates it (single-line rows; SOTA truncate+tooltip pattern).
    return (
      '<div class="fw-chstrip-cell" data-status="pending" ' +
      'data-chapter-id="' + c.id.replace(/"/g, '&quot;') + '" ' +
      'title="' + escapeHtml(title) + '">' +
      '  <span class="icon"></span>' +
      '  <span class="num">' + (i + 1) + '</span>' +
      '  <span class="label">' + escapeHtml(title) + '</span>' +
      '  <span class="time"></span>' +
      // "Open in Study" button — disabled until the chapter renders.
      // Enabled by `_markChStripCell(id, "done")`. Click navigates to
      // the Study page with the chapter pre-selected.
      '  <button type="button" class="fw-chstrip-open" ' +
      '          data-chapter-id="' + c.id.replace(/"/g, '&quot;') + '" ' +
      '          disabled ' +
      '          title="Synthesize this chapter first">📖</button>' +
      '</div>'
    );
  }).join('');
  _updateChStripCounter();
}

// Upgrade the checklist labels from id-derived fallbacks to the exact
// backend titles. Called right after a live _renderChStrip(ids) so the
// rows show real chapter names within one fetch. Silent on failure —
// the humanized fallback stays.
export async function _applyChStripTitles(slug) {
  if (!slug || !Sy.chstripCellsEl) return;
  try {
    const r = await fetch(Sa.API + '/synth/' + slug + '/study/chapters');
    if (!r.ok) return;
    const data = await r.json();
    (data.chapters || []).forEach(c => {
      if (!c || !c.id || !c.title) return;
      const cell = Sy.chstripCellsEl.querySelector(
        '.fw-chstrip-cell[data-chapter-id="' + c.id.replace(/"/g, '\\"') + '"]'
      );
      if (!cell) return;
      const lbl = cell.querySelector('.label');
      if (lbl) lbl.textContent = c.title;
      cell.title = c.title;   // keep the hover tooltip in sync
    });
  } catch (_) { /* keep humanized fallback */ }
}
export function _markChStripCell(chapterId, status) {
  if (!Sy.chstripCellsEl) return;
  Sy.studyChapterStatus.set(chapterId, status);
  const cell = Sy.chstripCellsEl.querySelector(
    '.fw-chstrip-cell[data-chapter-id="' + chapterId.replace(/"/g, '\\"') + '"]'
  );
  if (cell) {
    cell.dataset.status = status;
    // Sync the "Open in Study" button: enable on done, disable on
    // any non-done transition (failed/cancelled/back-to-pending so a
    // re-Synth doesn't leave a dead-link enabled).
    const openBtn = cell.querySelector('.fw-chstrip-open');
    if (openBtn) {
      if (status === 'done') {
        openBtn.removeAttribute('disabled');
        openBtn.title = 'Open this chapter in the Study page';
      } else {
        openBtn.setAttribute('disabled', 'disabled');
        openBtn.title = 'Synthesize this chapter first';
      }
    }
  }
  _updateChStripCounter();
}
// Per-chapter synth wall-clock, shown on the chstrip cell. `ms` from the
// `chapter_done` SSE event (live) or render-status API (persisted hydrate).
export function _markChStripCellTime(chapterId, ms) {
  if (!Sy.chstripCellsEl || !(Number(ms) > 0)) return;
  const cell = Sy.chstripCellsEl.querySelector(
    '.fw-chstrip-cell[data-chapter-id="' + chapterId.replace(/"/g, '\\"') + '"]'
  );
  if (!cell) return;
  const t = cell.querySelector('.time');
  if (t) t.textContent = fmtMs(ms);
}
export function _updateChStripCounter() {
  if (!Sy.chstripCounterEl) return;
  let done = 0, failed = 0, total = Sy.studyChapterIds.length;
  for (const s of Sy.studyChapterStatus.values()) {
    if (s === 'done') done++;
    else if (s === 'failed' || s === 'cancelled') failed++;
  }
  const txt = failed
    ? (done + ' done, ' + failed + ' failed / ' + total)
    : (done + ' / ' + total);
  Sy.chstripCounterEl.textContent = txt;
}
export function _resetStudyState() {
  Sy.setStudyThreadId(null);
  Sy.setStudyChapterIds([]);
  Sy.setStudyChapterStatus(new Map());
  Sy.setStudyCurrentChapterId(null);
  Sy.setStudyCurrentChapterThreadId(null);
  Sy.setStudyChapterThreads(new Map());
  Sy.setStudyPinnedChapterId(null);
  if (Sy.chstripCellsEl) Sy.chstripCellsEl.innerHTML = '';
  if (Sy.chstripCounterEl) Sy.chstripCounterEl.textContent = '';
  // 2026-06-08: don't hide the strip on wipe — leaving it visible but
  // empty matches the "sidebar stays in the layout" UX on the Pipeline
  // page. The Pipeline body server-renders it with `.visible` so it
  // never vanishes from the column; the standalone synth page's
  // `_hydrateChStripFromChapters` still hides it for single-chapter
  // (non-study) runs via its own `_showChStrip(false)` guard.
}

// Plan-existence gate for the Start Synth button. Synth REQUIRES a
// planner plan; GET /synth/{slug}/study/chapters returns 404 when none
// exists (it calls _load_plan server-side), so `r.ok` ⇔ a plan is
// written. This mirrors the server's _load_plan guard so the disabled
// button and the API agree (no bypass via a stray click). Fail-safe:
// any error → treated as "no plan" → button stays blocked.
export async function _refreshSynthPlanGate(slug) {
  let hasPlan = false;
  try {
    if (slug) {
      const r = await fetch(Sa.API + '/synth/' + slug + '/study/chapters');
      if (r.ok) {
        const data = await r.json();
        hasPlan = (((data && data.chapters) || []).length > 0);
      }
    }
  } catch (_) { /* network hiccup → no plan */ }
  Sy.setSynthHasPlan(hasPlan);
  deps.refreshSynthStartState?.();
}

// Durable strip reconstruction — rebuilds the chapter progress strip from
// MinIO-backed render status (GET /synth/{slug}/study/chapters) instead of
// the ephemeral SSE snapshot. THIS is what makes the strip survive a page
// refresh after a study run finishes.
export async function _hydrateChStripFromChapters(slug) {
  if (!slug || !Sy.chstripCellsEl) return false;
  try {
    const r = await fetch(Sa.API + '/synth/' + slug + '/study/chapters');
    if (!r.ok) return false;
    const data = await r.json();
    const chapters = (data.chapters || []).slice()
      .sort((a, b) => (a.order || 0) - (b.order || 0));
    if (chapters.length < 2) { _showChStrip(false); deps.refreshSynthStartState?.(); return false; }
    // Durable path — chapters carry exact titles; pass them through.
    _renderChStrip(chapters.map(c => ({ id: c.id, title: c.title })));
    // Persisted Synth total → navbar (survives refresh / cached studies).
    showElapsed('synth', Number(data.study_total_wall_ms || 0));
    chapters.forEach(c => {
      if (!c) return;
      if (c.rendered) _markChStripCell(c.id, 'done');
      _markChStripCellTime(c.id, c.wall_ms);   // persisted per-chapter wall
      // Persist the durable thread_id (from render-latest.json) so a
      // post-refresh click can re-open the chapter's graph canvas.
      if (c.thread_id) {
        Sy.studyChapterThreads.set(c.id, c.thread_id);
        const cell = Sy.chstripCellsEl.querySelector(
          '.fw-chstrip-cell[data-chapter-id="' + c.id.replace(/"/g, '\\"') + '"]'
        );
        if (cell) cell.dataset.chapterThreadId = c.thread_id;
      }
    });
    _showChStrip(true);
    // Pill reflects the persisted study state on refresh (no active
    // run → no pollStudyState → no live update would land otherwise).
    // All chapters rendered → green 'Done'. Some rendered → blue
    // 'Working · X/N' to indicate a partial / resumable state. None
    // rendered → leave the default 'Idle'.
    const _nDone = chapters.filter(c => c && c.rendered).length;
    const _nTot  = chapters.length;
    if (_nDone > 0 && _nDone === _nTot) {
      _setSynthStagePill('done');
    } else if (_nDone > 0) {
      _setSynthStagePill('working', 'Working · ' + _nDone + '/' + _nTot);
    }
    // Strip now reflects server render status → update Start/Resume label
    // (partial render → "Resume Synth", all/none → "Start Synth").
    deps.refreshSynthStartState?.();
    return true;
  } catch (e) {
    return false;
  }
}

// Visual: highlight the strip cell whose chapter the canvas is currently
// showing. Mutually exclusive — clears any prior selection.
export function _highlightStripCell(chapterId) {
  if (!Sy.chstripCellsEl) return;
  Sy.chstripCellsEl.querySelectorAll('.fw-chstrip-cell.selected')
    .forEach(c => c.classList.remove('selected'));
  if (!chapterId) return;
  const cell = Sy.chstripCellsEl.querySelector(
    '.fw-chstrip-cell[data-chapter-id="' + chapterId.replace(/"/g, '\\"') + '"]'
  );
  if (cell) cell.classList.add('selected');
}

// Strip-cell click handler — wires the "switch canvas to this chapter"
// behavior.
export function _onStripCellClick(cellEl) {
  if (!cellEl) return;
  const cid = cellEl.dataset.chapterId;
  if (!cid) return;
  const status = cellEl.dataset.status || 'pending';
  const chTid = cellEl.dataset.chapterThreadId
              || Sy.studyChapterThreads.get(cid)
              || null;

  // Unpin if user clicks the currently-running cell while pinned to it.
  if (cid === Sy.studyCurrentChapterId && Sy.studyPinnedChapterId === cid) {
    Sy.setStudyPinnedChapterId(null);
    _highlightStripCell(cid);   // stays highlighted as the running one
    return;
  }
  // Already showing this chapter's canvas — just pin/highlight, don't
  // reopen SSE (which would duplicate live event streams).
  if (chTid && Sy.synthThreadId === chTid) {
    Sy.setStudyPinnedChapterId(cid);
    _highlightStripCell(cid);
    return;
  }
  Sy.setStudyPinnedChapterId(cid);
  _highlightStripCell(cid);

  // No thread for this chapter — rendered before thread_id persistence
  // landed, OR never started. Clear the canvas and tell the user.
  if (!chTid) {
    Sy.setSynthThreadId(null);
    deps.resetSynthCards?.();
    deps._resetSynthEventBuffer?.();
    const _ndr = deps._getNodeDrawerRef?.();
    if (_ndr && _ndr.reset) _ndr.reset();
    try { deps.renderSynthCards?.({}); } catch (_) {}
    if (status === 'done') {
      showToast('This chapter was rendered before graph-history tracking ' +
                'was added. Re-run Synth to inspect its node graph.');
    }
    return;
  }

  // Switch the canvas to the clicked chapter's thread.
  Sy.setSynthThreadId(chTid);
  deps.resetSynthCards?.();
  deps._resetSynthEventBuffer?.();
  const _ndr = deps._getNodeDrawerRef?.();
  if (_ndr && _ndr.reset) _ndr.reset();
  // Explicit pill — done chapters get the green Done badge straight
  // away; running/pending get the blue Working badge. Default labels
  // ("Done" / "Working") render via the label map in _setSynthStagePill.
  const isDone = (status === 'done');
  _setSynthStagePill(isDone ? 'done' : 'working', null);

  // Initial paint from checkpoint state.
  (async () => {
    try {
      const r = await fetch(Sa.API + '/synth/debug/graph/' + chTid + '/state');
      if (r.ok) {
        const data = await r.json();
        deps.renderSynthCards?.(
          data.values || {},
          Array.isArray(data.next) ? data.next : null,
        );
      }
    } catch (_) { /* fall back to live events */ }
    Sy.set_synthLiveEventReceived(false);
    deps.pollSynthState?.(chTid);
  })();
}

if (Sy.chstripCellsEl) {
  Sy.chstripCellsEl.addEventListener('click', ev => {
    // "Open in Study" button — intercept FIRST so the cell's
    // click-to-focus-subgraph handler doesn't also fire. Navigates
    // to the Study page with `?chapter=cid` so it deep-links to that
    // chapter (study/chapters.js loadStudyChapters reads the param
    // and opens that one instead of the auto-first-rendered default).
    const openBtn = ev.target.closest('.fw-chstrip-open');
    if (openBtn) {
      ev.stopPropagation();
      if (openBtn.hasAttribute('disabled')) return;
      const cid = openBtn.dataset.chapterId;
      const picker = document.querySelector('.fw-picker');
      const slug = (picker && picker.dataset.ddSlug)
                   || new URLSearchParams(window.location.search).get('slug');
      if (!slug || !cid) return;
      window.location.href = '/docs-distiller/study?slug=' +
                             encodeURIComponent(slug) +
                             '&chapter=' + encodeURIComponent(cid);
      return;
    }
    const cell = ev.target.closest('.fw-chstrip-cell');
    if (cell) _onStripCellClick(cell);
  });
}

// SSE consumer for the STUDY-LEVEL channel — receives orchestrator
// events (study_start, chapter_running, chapter_done, study_done).
