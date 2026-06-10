/* YCS · Pipeline panel — shared across all stage pages.
 *
 * Boots on every YCS page (Source / Ingest / Ask). Hidden by default;
 * surfaces the 3-progress-bar panel + current-video metadata card the
 * moment a pipeline is being tracked.
 *
 * Source-of-truth precedence for what to track:
 *   1. URL params `?extract=&qdrant=&neo4j=` (set by the Videos tab
 *      redirect right after dispatch). Wins because it's the freshest
 *      signal — user just clicked Start Ingestion.
 *   2. localStorage `ycs:pipeline:active` (24h TTL mirrors the backend
 *      Redis snapshot). Restores state across tab navigation +
 *      page reloads.
 *   3. Nothing → panel stays `display:none`, contributes no layout.
 *
 * Whenever (1) is present, the IDs are persisted to localStorage so
 * subsequent navigations resurface the same run via (2). No dismiss
 * affordance — the panel is intentionally persistent until either the
 * localStorage TTL expires (24h, matching the backend Redis snapshot)
 * or a new dispatch overwrites the entry. Stop revokes the chain but
 * keeps the panel up so the user sees the cancelled state.
 */

/* Cross-feature reuse: DD's framework-level confirm modal. The DOM
 * (`#fw-modal`) is rendered by `ConfirmModal()` in `YCSPage` (see
 * `features/ycs/page.py`); the CSS lives in shell-wide
 * `components/overlays.css`. Using this in place of the native
 * `window.confirm()` keeps the Stop dialog visually consistent with
 * DD's Wipe Planner / Wipe Synth confirmations. */
import { showConfirm } from "@dd/shared/ui/overlays.js";

const API = "/api/v1/ycs";
/* Poll cadence — 700ms was 1500ms. Phase 2 (Qdrant) finishes in ~5s
 * for a small batch and Phase 1 with cached transcripts is even
 * faster; at 1500ms the JS only caught 0–1 intermediate polls between
 * PENDING and SUCCESS, so the bars jumped 2%→100% instead of
 * advancing. 700ms catches ~7 intermediate polls on Phase 2, which is
 * enough to animate. BACKOFF kicks in at tick 60 (~42s) for runs
 * dominated by slow Phase 3 — past that we ease off to 3000ms. */
const POLL_INTERVAL_MS = 700;
const POLL_BACKOFF_AFTER = 60;
const STORAGE_KEY = "ycs:pipeline:active";
const STORAGE_TTL_MS = 24 * 60 * 60 * 1000;  // match backend Redis TTL

const BARS = ["transcripts", "qdrant", "neo4j"];

// ---- helpers ---------------------------------------------------------------
async function api(path, opts = {}) {
    const r = await fetch(`${API}${path}`, opts);
    let data = null;
    try { data = await r.json(); } catch (_) { /* */ }
    if (!r.ok) {
        const msg = (data && (data.detail ?? data.message)) || r.statusText;
        const err = new Error(typeof msg === "string" ? msg : "request failed");
        err.status = r.status;
        throw err;
    }
    return data;
}

function fmtCount(n) {
    if (n == null) return "";
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
    return String(n);
}

function fmtElapsed(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return "";
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60).toString().padStart(2, "0");
    return `${m}:${s}`;
}

// ---- localStorage persistence ----------------------------------------------
function readPersisted() {
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (!raw) return null;
        const obj = JSON.parse(raw);
        if (!obj || !obj.extract || !obj.qdrant || !obj.neo4j) return null;
        // Expire stale records — mirrors the backend Redis TTL so we
        // don't leave abandoned panels lingering forever.
        if (obj.startedAt && (Date.now() - obj.startedAt) > STORAGE_TTL_MS) {
            localStorage.removeItem(STORAGE_KEY);
            return null;
        }
        return obj;
    } catch (_) {
        return null;
    }
}

function writePersisted(ids) {
    try {
        const payload = {
            extract:   ids.extract,
            qdrant:    ids.qdrant,
            neo4j:     ids.neo4j,
            video_ids: Array.isArray(ids.video_ids) ? ids.video_ids : [],
            startedAt: ids.startedAt ?? Date.now(),
        };
        localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    } catch (_) { /* */ }
}

// ---- task polling + bar rendering ------------------------------------------
async function pollTaskOnce(taskId) {
    try {
        return await api(`/admin/task/${encodeURIComponent(taskId)}`);
    } catch (e) {
        return { state: "ERROR", error: e.message ?? String(e) };
    }
}

function _setBar(prefix, { state, pct, label, hint }) {
    const fill = document.getElementById(`ycs-bar-${prefix}-fill`);
    const pctEl = document.getElementById(`ycs-bar-${prefix}-pct`);
    const stateEl = document.getElementById(`ycs-bar-${prefix}-state`);
    const hintEl = document.getElementById(`ycs-bar-${prefix}-hint`);
    const row = document.getElementById(`ycs-bar-${prefix}`);
    if (!row) return;
    if (state) row.dataset.state = state;
    if (typeof pct === "number") {
        fill.style.width = `${Math.max(0, Math.min(100, pct))}%`;
        pctEl.textContent = `${Math.round(pct)}%`;
    }
    if (label != null) stateEl.textContent = label;
    if (hint != null) hintEl.textContent = hint;
}

function _htmlEscape(s) {
    return String(s ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

/* Per-(video, store) status derivation. Each store cell carries its
 * OWN pill — replaces the old conflated single-status row that only
 * advanced when Neo4j finished. The 5 cell states are:
 *
 *   failed   — id is in THIS store's failed_ids
 *   running  — id == current_item.id of THIS store's active task
 *   done     — id is in THIS store's completed_ids
 *   skipped  — backend tagged this id as a cache hit for THIS store
 *              (e.g. Phase 1 cached transcripts, Phase 3 skip-on-
 *              video_id in Neo4j). NOT currently emitted; reserved.
 *   queued   — none of the above + this store hasn't reached SUCCESS yet
 *   done*    — this store finished successfully and the id is in the
 *              dispatched set (= must have been processed even if
 *              completed_ids is missing from the SUCCESS result dict)
 *
 * `phaseKey` ∈ {"transcripts","qdrant","neo4j"} — same as the bar
 * prefix. `phaseMeta` is the Celery `meta` dict from `/admin/task/{id}`'s
 * last poll; for SUCCESS state the meta is the task RESULT dict.
 * `phaseState` carries the Celery state so a finished store can mark
 * stragglers as "done" even when the result dict lacks completed_ids.
 */
function _videoStoreStatus(videoId, phaseKey, phaseMeta, phaseState) {
    const m = phaseMeta || {};
    if (Array.isArray(m.failed_ids) && m.failed_ids.includes(videoId)) {
        return "failed";
    }
    if (m.current_item?.id === videoId) return "running";
    if (Array.isArray(m.completed_ids) && m.completed_ids.includes(videoId)) {
        return "done";
    }
    // Phase reached terminal SUCCESS but the result dict didn't carry
    // completed_ids (Qdrant + Phase 3's success payloads don't enumerate
    // per-video). Anything still queued at that point must have been
    // processed — there's nothing for this phase left to do.
    if (phaseState === "SUCCESS") return "done";
    if (phaseState === "FAILURE" || phaseState === "REVOKED" || phaseState === "ERROR") {
        return "failed";
    }
    return "queued";
}

const _STORE_STATUS_LABEL = {
    queued:  "Queued",
    running: "Running",
    done:    "Done",
    failed:  "Failed",
    skipped: "Skipped",
};

/* Track per-video timings — start time = first poll where ANY store
 * cell flipped to "running" for that id; end time = first poll where
 * Neo4j (the last phase) flipped to terminal. Closure-scoped because
 * the data shape only needs to live for the lifetime of one
 * trackPipeline() invocation. */
const _videoTiming = {
    started: new Map(),   // vid → epoch ms
    finished: new Map(),  // vid → epoch ms
};

function _fmtCellDuration(ms) {
    if (!Number.isFinite(ms) || ms <= 0) return "—";
    const s = ms / 1000;
    if (s < 60)  return `${s.toFixed(1)}s`;
    const m = Math.floor(s / 60);
    const r = Math.floor(s % 60).toString().padStart(2, "0");
    return `${m}:${r}`;
}

/* Render the 5-column per-video × per-store table inside the drawer.
 * Columns: Video (title + channel) · ES · Qdrant · Neo4j · Time.
 * Each store cell is an independent pill so the user sees ES finish
 * before Qdrant starts before Neo4j starts — instead of one row-wide
 * pill that's misleadingly "queued" until Phase 3 ends.
 *
 * `videos`: metadata array from Phase 1's `all_items` payload (titles
 *           + channels). Empty until that arrives.
 * `metaByPhase`: per-phase last-poll meta dict.
 * `phaseStates`: per-phase Celery state (SUCCESS / PROGRESS / …).
 * `videoIds`: ordering authority — derived from localStorage / state
 *             endpoint so the table shape is stable across renders.
 */
function _renderVideoTable({ videos, metaByPhase, phaseStates, videoIds }) {
    const bodyEl  = document.getElementById("ycs-pipe-table-body");
    const headCountEl = document.getElementById("ycs-pipe-drawer-count");
    const btnCountEl  = document.getElementById("ycs-pipe-videos-btn-count");
    if (!bodyEl) return;
    const total = videoIds.length || videos.length;
    if (headCountEl) headCountEl.textContent = String(total);
    if (btnCountEl)  btnCountEl.textContent  = String(total);
    if (!videoIds.length && !videos.length) return;
    const metaById = new Map();
    for (const v of videos) {
        if (v?.id) metaById.set(v.id, v);
    }
    const ids = videoIds.length ? videoIds : videos.map((v) => v.id);
    const now = Date.now();
    const frag = document.createDocumentFragment();
    for (const vid of ids) {
        if (!vid) continue;
        const v      = metaById.get(vid) || { id: vid };
        const title  = _htmlEscape(v.title || vid);
        const channel = v.channel ? _htmlEscape(v.channel) : "";
        // Derive per-store status independently.
        const statuses = {};
        let anyRunning = false;
        for (const p of ["transcripts", "qdrant", "neo4j"]) {
            statuses[p] = _videoStoreStatus(
                vid, p, metaByPhase[p] || {}, phaseStates[p],
            );
            if (statuses[p] === "running") anyRunning = true;
        }
        const neo4jDone   = statuses.neo4j === "done";
        const neo4jFailed = statuses.neo4j === "failed";
        // Timing: started = first time we observed running; finished =
        // first time we observed all-3-done OR a failure on any store.
        if (anyRunning && !_videoTiming.started.has(vid)) {
            _videoTiming.started.set(vid, now);
        }
        const allDone = (
            statuses.transcripts === "done" &&
            statuses.qdrant      === "done" &&
            statuses.neo4j       === "done"
        );
        const anyFailed = (
            statuses.transcripts === "failed" ||
            statuses.qdrant      === "failed" ||
            statuses.neo4j       === "failed"
        );
        if ((allDone || anyFailed) && !_videoTiming.finished.has(vid)) {
            _videoTiming.finished.set(vid, now);
        }
        let durationLabel = "—";
        const t0 = _videoTiming.started.get(vid);
        const t1 = _videoTiming.finished.get(vid);
        if (t0 != null) {
            durationLabel = _fmtCellDuration((t1 ?? now) - t0);
        }
        const rowState = anyFailed ? "failed"
                       : allDone   ? "done"
                       : anyRunning ? "running"
                       : "queued";
        const row = document.createElement("div");
        row.className = `ycs-pipe-table-row ycs-pipe-table-row-${rowState}`;
        row.dataset.videoId = vid;
        const pillFor = (p) => {
            const s = statuses[p];
            const label = _STORE_STATUS_LABEL[s] || s;
            return `<span class="ycs-pipe-cell-pill ycs-pipe-cell-pill-${s}" title="${p}: ${label}">${label}</span>`;
        };
        row.innerHTML = `
            <div class="ycs-pipe-table-cell ycs-pipe-table-cell-video">
                <div class="ycs-pipe-cell-title" title="${title}">${title}</div>
                ${channel ? `<div class="ycs-pipe-cell-channel">${channel}</div>` : ""}
            </div>
            <div class="ycs-pipe-table-cell">${pillFor("transcripts")}</div>
            <div class="ycs-pipe-table-cell">${pillFor("qdrant")}</div>
            <div class="ycs-pipe-table-cell">${pillFor("neo4j")}</div>
            <div class="ycs-pipe-table-cell ycs-pipe-table-cell-time">${durationLabel}</div>
        `;
        frag.appendChild(row);
    }
    bodyEl.replaceChildren(frag);
}

/* Drawer open/close wiring. Idempotent — guards against double-bind
 * when trackPipeline restarts (resume / re-dispatch in the same SPA
 * session would otherwise wire the handler twice). */
function _bindDrawer() {
    const btn   = document.getElementById("ycs-pipe-videos-btn");
    const root  = document.getElementById("ycs-pipe-drawer-root");
    const close = document.getElementById("ycs-pipe-drawer-close");
    const scrim = document.getElementById("ycs-pipe-drawer-scrim");
    if (!root || root.dataset.bound === "1") return;
    root.dataset.bound = "1";
    const open = () => { root.classList.add("is-open"); };
    const dismiss = () => { root.classList.remove("is-open"); };
    btn?.addEventListener("click", open);
    close?.addEventListener("click", dismiss);
    scrim?.addEventListener("click", dismiss);
    document.addEventListener("keydown", (ev) => {
        if (ev.key === "Escape" && root.classList.contains("is-open")) {
            dismiss();
        }
    });
}

function _phaseLabel(state, meta) {
    if (state === "SUCCESS") return "Done";
    if (state === "FAILURE" || state === "ERROR") return "Failed";
    if (state === "PENDING") return "Queued";
    const m = meta || {};
    if (m.current != null && m.total) {
        return `${m.phase || "running"} · ${m.current}/${m.total}`;
    }
    if (m.phase) return m.phase;
    return state || "Running";
}

function _phasePct(state, meta) {
    if (state === "SUCCESS") return 100;
    if (state === "FAILURE" || state === "ERROR") return 100;
    const m = meta || {};
    if (m.total && m.current != null) {
        return Math.max(2, Math.min(100, (m.current / m.total) * 100));
    }
    if (m.phase && state === "PROGRESS") return 8;
    return 0;
}

function _successHint(prefix, result) {
    if (prefix === "transcripts") {
        const t = result.transcriptions || {};
        const m = result.metadata || {};
        const newIdx = t.indexed ?? 0;
        const cached = t.cached ?? 0;
        const fetchFailed = t.fetch_failed ?? 0;
        const indexFailed = t.failed ?? 0;
        const noTranscript = t.no_transcript ?? 0;
        const available = newIdx + cached;
        const parts = [
            `${m.indexed ?? 0} metadata`,
            `${available} transcripts in ES (${newIdx} new · ${cached} cached)`,
        ];
        // Permanent "video has no captions" — expected outcome, kept
        // separate from infra fetch failures.
        if (noTranscript) parts.push(`${noTranscript} no transcript`);
        if (fetchFailed) parts.push(`${fetchFailed} fetch failed`);
        if (indexFailed) parts.push(`${indexFailed} index failed`);
        return parts.join(" · ");
    }
    if (prefix === "qdrant") {
        const parts = [
            `${result.total_transcripts ?? 0} transcripts`,
            `${result.total_chunks ?? 0} chunks`,
            `${result.points_upserted ?? 0} points`,
        ];
        // Videos whose content_hash matched — skipped without re-embedding.
        const unchanged = result.videos_unchanged ?? 0;
        if (unchanged) parts.push(`${unchanged} unchanged`);
        return parts.join(" · ");
    }
    if (prefix === "neo4j") {
        return `${result.nodes_created ?? 0} nodes · ${result.relationships_created ?? 0} rels · ${result.entities_merged ?? 0} merged`;
    }
    return "";
}

// ---- Stop / Rerun button handlers ------------------------------------------
let _stopRequested = false;

function bindStop(btn, extractId) {
    if (!btn || !extractId) return;
    if (btn.dataset.bound === "1") return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", async () => {
        const ok = await showConfirm(
            "Stop the pipeline?",
            "The currently-running phase gets SIGTERM; queued phases " +
            "are cancelled. Completed phases keep their writes — re-run " +
            "with the Rerun button to resume.",
            "Stop",
        );
        if (!ok) return;
        btn.disabled = true;
        const orig = btn.textContent;
        btn.textContent = "Stopping…";
        try {
            await api(
                `/content/videos/pipeline/${encodeURIComponent(extractId)}/stop`,
                {
                    method: "POST",
                    headers: { "content-type": "application/json" },
                    body: "{}",
                },
            );
            // Intentionally NOT clearing localStorage here. The panel
            // is rendered only on the Ingest page (see
            // `features/ycs/page.py::YCSPage`), so a stopped run can't
            // leak into Source / Ask — there's no DOM there to host
            // it. Keeping the entry means an Ingest revisit shows the
            // Cancelled-state confirmation until the 24h TTL expires
            // or a new dispatch overwrites it. Retry stays enabled
            // for the user to refire the same video IDs.
            _stopRequested = true;
            btn.textContent = "Stopped";
        } catch (e) {
            btn.disabled = false;
            btn.textContent = orig;
            alert(`Stop failed: ${e.message ?? e}`);
        }
    });
}

/* Wipe handler — confirms via the shared `showConfirm` modal, then
 * POSTs the extract id to the backend wipe endpoint, which:
 *   1. Sweeps ES (metadata + transcripts) + Qdrant (hybrid points)
 *      + Neo4j (Document + Video nodes) for every video.
 *   2. Revokes any in-flight chain phases so a mid-LLM Phase 3
 *      doesn't finish and write orphans after the wipe.
 * After a successful wipe + revoke the panel updates the head's
 * elapsed slot with a short summary so the user has feedback
 * before clicking Retry.
 */
function bindWipe(btn, extractId) {
    if (!btn || !extractId) return;
    if (btn.dataset.bound === "1") return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", async () => {
        const ok = await showConfirm(
            "Wipe cache for these videos?",
            "Deletes every cached artifact for the videos in this " +
            "pipeline — ES metadata + transcripts, Qdrant hybrid " +
            "points, Neo4j Document and Video nodes. The next Retry " +
            "will re-run the full chain from scratch (no Phase 1 " +
            "cache hits, no Phase 3 skip-on-video_id). Entity nodes " +
            "are left intact. This cannot be undone.",
            "Wipe",
        );
        if (!ok) return;
        btn.disabled = true;
        const orig = btn.textContent;
        btn.textContent = "Wiping…";
        try {
            const r = await api(
                `/content/videos/pipeline/${encodeURIComponent(extractId)}/wipe`,
                {
                    method: "POST",
                    headers: { "content-type": "application/json" },
                    body: "{}",
                },
            );
            const s = r.summary || {};
            const es = s.es || {};
            const qd = s.qdrant || {};
            const nj = s.neo4j || {};
            const elapsedEl = document.getElementById("ycs-pipe-panel-elapsed");
            if (elapsedEl) {
                elapsedEl.textContent =
                    `Wiped: ES ${es.metadata_deleted ?? 0}m + ` +
                    `${es.transcripts_deleted ?? 0}t · ` +
                    `Qdrant ${qd.qdrant_deleted ?? 0} · ` +
                    `Neo4j ${nj.documents_deleted ?? 0}d + ` +
                    `${nj.videos_deleted ?? 0}v`;
            }
            btn.textContent = "Wiped";
        } catch (e) {
            btn.disabled = false;
            btn.textContent = orig;
            alert(`Wipe failed: ${e.message ?? e}`);
        }
    });
}

function bindRerun(btn, extractId) {
    if (!btn || !extractId) return;
    if (btn.dataset.bound === "1") return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", async () => {
        btn.disabled = true;
        const origLabel = btn.textContent;
        btn.textContent = "Queuing retry…";
        try {
            const r = await api(
                `/content/videos/pipeline/${encodeURIComponent(extractId)}/rerun`,
                {
                    method: "POST",
                    headers: { "content-type": "application/json" },
                    body: "{}",
                },
            );
            const p = r.phases || {};
            if (!p.extract || !p.qdrant || !p.neo4j) {
                throw new Error("backend returned no phase IDs");
            }
            const q = new URLSearchParams({
                extract: p.extract,
                qdrant:  p.qdrant,
                neo4j:   p.neo4j,
            });
            window.location.href = `/youtube-content-search/ingestion?${q.toString()}`;
        } catch (e) {
            btn.disabled = false;
            btn.textContent = origLabel;
            alert(`Rerun failed: ${e.message ?? e}`);
        }
    });
}

// ---- main tracking loop ----------------------------------------------------
async function trackPipeline({ extract, qdrant, neo4j, video_ids, startedAt }) {
    _stopRequested = false;
    const panel = document.getElementById("ycs-pipe-panel");
    if (!panel) return;  // page didn't render the chrome (shouldn't happen)
    panel.style.display = "flex";
    const elapsedEl = document.getElementById("ycs-pipe-panel-elapsed");
    const rerunBtn = document.getElementById("ycs-pipe-rerun");
    const stopBtn = document.getElementById("ycs-pipe-stop");
    const wipeBtn = document.getElementById("ycs-pipe-wipe");
    bindRerun(rerunBtn, extract);
    bindStop(stopBtn, extract);
    bindWipe(wipeBtn, extract);
    _bindDrawer();
    if (stopBtn) stopBtn.disabled = false;
    // Wipe is always enabled — the backend wipe endpoint now revokes
    // any in-flight chain phases before/during the wipe, so it's safe
    // to wipe mid-run. Lets the user use Wipe as a "kill + clean"
    // panic button when a corrupt cache is suspected, not just after
    // a terminal finish.
    if (wipeBtn) wipeBtn.disabled = false;
    const ids = { transcripts: extract, qdrant, neo4j };
    // Stable ordering for the video list — `video_ids` is the
    // authoritative dispatch list (saved to localStorage by the POST
    // response handler). Falls back to `[]` for legacy localStorage
    // entries written before the field existed.
    const orderedVideoIds = Array.isArray(video_ids) ? video_ids : [];
    // Phase 1's metadata_done payload populates this once yt-dlp
    // returns; until then the list renders id-only rows.
    let allItems = [];
    // Track which phases have already fired `ycs:pipeline:phase-done`
    // so the library auto-refresh only triggers once per phase per run.
    // Without this, every poll past SUCCESS would re-dispatch the event
    // and the library would refresh repeatedly.
    const phaseDoneFired = new Set();
    // Use the persisted start time so the elapsed counter is consistent
    // across page reloads — restoring from localStorage gives us the
    // original dispatch moment rather than the moment-of-restore.
    const startMs = Number(startedAt) || Date.now();
    let tick = 0;
    let interval = POLL_INTERVAL_MS;
    while (true) {
        tick += 1;
        elapsedEl.textContent = fmtElapsed((Date.now() - startMs) / 1000);
        const polls = BARS.map((b) => pollTaskOnce(ids[b]));
        const results = await Promise.all(polls);
        const metaByPhase = {};
        const phaseStates = {};
        let allTerminal = true;
        for (let i = 0; i < BARS.length; i++) {
            const prefix = BARS[i];
            const r = results[i];
            const state = r.state || "PENDING";
            const meta = r.meta || (r.result ?? {});
            metaByPhase[prefix] = meta;
            phaseStates[prefix] = state;
            _setBar(prefix, {
                state: state.toLowerCase(),
                pct: _phasePct(state, meta),
                label: state === "FAILURE" || state === "ERROR"
                    ? `Failed: ${(r.error || "").slice(0, 60)}`
                    : _phaseLabel(state, meta),
                hint: state === "SUCCESS" && r.result
                    ? _successHint(prefix, r.result)
                    : null,
            });
            // Phase 1 emits a one-shot `all_items` payload after yt-dlp
            // metadata extraction completes (before transcripts start),
            // so the drawer table renders with titles + channels early.
            if (Array.isArray(meta.all_items) && meta.all_items.length) {
                allItems = meta.all_items;
            }
            if (!["SUCCESS", "FAILURE", "REVOKED", "ERROR"].includes(state)) {
                allTerminal = false;
            }
            // Dispatch a phase-completion event the moment THIS phase
            // first reaches SUCCESS so the Ingest-page Library widget
            // auto-refreshes WITHOUT waiting for Phase 3. Phase 1 done
            // ⇒ ES metadata + transcripts are now visible (library
            // shows "partial" pill). Phase 3 done ⇒ Neo4j entity_count
            // populated (library flips "partial"→"done"). One event
            // per phase per run (guarded by phaseDoneFired set).
            if (state === "SUCCESS" && !phaseDoneFired.has(prefix)) {
                phaseDoneFired.add(prefix);
                try {
                    document.dispatchEvent(new CustomEvent(
                        "ycs:pipeline:phase-done",
                        { detail: { phase: prefix, extract_id: extract } },
                    ));
                } catch (_) { /* CustomEvent unsupported — ignore */ }
            }
        }
        // Render the per-video × per-store drawer table every poll.
        // Cheap for typical pipeline runs (1-20 videos). Per-store
        // status independently derived so ES/Qdrant/Neo4j cells
        // advance separately as each phase progresses — replaces the
        // misleading row-wide single pill the prior ship had.
        if (orderedVideoIds.length || allItems.length) {
            _renderVideoTable({
                videos:      allItems,
                metaByPhase,
                phaseStates,
                videoIds:    orderedVideoIds,
            });
        }
        if (allTerminal || _stopRequested) {
            if (_stopRequested) {
                for (const prefix of BARS) {
                    const row = document.getElementById(`ycs-bar-${prefix}`);
                    const curState = row?.dataset?.state;
                    if (curState && ["success", "failure", "error"].includes(curState)) continue;
                    _setBar(prefix, {
                        state: "cancelled",
                        pct: 100,
                        label: "Cancelled",
                        hint: "Stopped by user — partial writes preserved.",
                    });
                }
            }
            if (rerunBtn) rerunBtn.disabled = false;
            if (wipeBtn) wipeBtn.disabled = false;
            if (stopBtn) stopBtn.disabled = true;
            // Final terminal-state event so the Library widget does a
            // last refresh — even if a phase failed (the row might be
            // visible as "partial" rather than "done"). Dispatched
            // after the bars settle so the library refresh sees the
            // final cross-store presence.
            try {
                document.dispatchEvent(new CustomEvent(
                    "ycs:pipeline:done",
                    {
                        detail: {
                            extract_id: extract,
                            stopped:    _stopRequested,
                        },
                    },
                ));
            } catch (_) { /* */ }
            break;
        }
        if (tick > POLL_BACKOFF_AFTER) interval = 3000;
        await new Promise((r) => setTimeout(r, interval));
    }
}

// ---- boot ------------------------------------------------------------------
/* Run on every YCS page, but no-op unless the page actually rendered
 * the panel DOM. The panel is only rendered on the Ingest stage (see
 * `features/ycs/page.py::YCSPage`); Source / Ask pages don't include
 * it, so `getElementById("ycs-pipe-panel")` returns null and we exit.
 * This keeps the same shell-wide `main.js` import working on every
 * stage page without leaking the panel UI to Source / Ask.
 *
 * Source-of-truth precedence on Ingest:
 *   1. URL `?extract=&qdrant=&neo4j=` → write to localStorage + track.
 *   2. localStorage → restore + track without URL params.
 *   3. Nothing → panel stays hidden.
 */
async function boot() {
    if (!document.getElementById("ycs-pipe-panel")) return;
    const params = new URLSearchParams(window.location.search);
    const urlExtract = params.get("extract");
    const urlQdrant  = params.get("qdrant");
    const urlNeo4j   = params.get("neo4j");
    if (urlExtract && urlQdrant && urlNeo4j) {
        // The Source → Ingest redirect carries phase IDs in the URL,
        // but NOT video_ids (URL would bloat). Fetch them from the
        // backend's persisted state once so the right-column list has
        // a stable ordering authority.
        let videoIds = [];
        try {
            const state = await api(
                `/content/videos/pipeline/${encodeURIComponent(urlExtract)}/state`,
            );
            if (Array.isArray(state.video_ids)) videoIds = state.video_ids;
        } catch (_) { /* fall back to empty — list renders from all_items only */ }
        const ids = {
            extract:   urlExtract,
            qdrant:    urlQdrant,
            neo4j:     urlNeo4j,
            video_ids: videoIds,
            startedAt: Date.now(),
        };
        writePersisted(ids);
        trackPipeline(ids);
        return;
    }
    const persisted = readPersisted();
    if (persisted) {
        // If localStorage was written before the video_ids field
        // existed, rehydrate from the backend's saved state so the
        // list still renders on a returning visit.
        if (!Array.isArray(persisted.video_ids) || !persisted.video_ids.length) {
            try {
                const state = await api(
                    `/content/videos/pipeline/${encodeURIComponent(persisted.extract)}/state`,
                );
                if (Array.isArray(state.video_ids)) {
                    persisted.video_ids = state.video_ids;
                    writePersisted(persisted);
                }
            } catch (_) { /* */ }
        }
        trackPipeline(persisted);
        return;
    }
    // Nothing to track — panel remains display:none from the server.
}

boot();
