/* YCS · Pipeline panel — shared across all stage pages.
 *
 * Boots on every YCS page (Source / Ingest / Ask). Hidden by default;
 * surfaces the 3-progress-bar panel + current-video metadata card the
 * moment a pipeline is being tracked.
 *
 * Source-of-truth precedence for what to track:
 *   1. URL params `?extract=&qdrant=&neo4j=` (set by the Videos tab
 *      redirect right after dispatch). Wins because it's the freshest
 *      signal — user just clicked Start ingest.
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
const POLL_INTERVAL_MS = 1500;
const POLL_BACKOFF_AFTER = 30;
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

function _videoCard({ phase, item }) {
    const card = document.getElementById("ycs-vid-card");
    if (!card) return;
    if (!item || !item.id) return;
    card.style.display = "flex";
    document.getElementById("ycs-vid-card-phase").textContent = phase || "";
    document.getElementById("ycs-vid-card-title").textContent =
        item.title || "(no title)";
    document.getElementById("ycs-vid-card-channel").textContent =
        item.channel || "(unknown channel)";
    document.getElementById("ycs-vid-card-views").textContent =
        item.view_count != null ? `${fmtCount(item.view_count)} views` : "";
    document.getElementById("ycs-vid-card-duration").textContent =
        item.duration_string ||
        (item.duration ? `${Math.round(item.duration / 60)}m` : "");
    document.getElementById("ycs-vid-card-likes").textContent =
        item.like_count != null ? `${fmtCount(item.like_count)} likes` : "";
    document.getElementById("ycs-vid-card-date").textContent =
        item.upload_date && item.upload_date.length === 8
            ? `${item.upload_date.slice(0, 4)}-${item.upload_date.slice(4, 6)}-${item.upload_date.slice(6, 8)}`
            : "";
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

function _phaseDisplayName(prefix) {
    return prefix === "transcripts" ? "Transcripts"
         : prefix === "qdrant"      ? "Qdrant"
         : prefix === "neo4j"       ? "Neo4j"
         : prefix;
}

function _successHint(prefix, result) {
    if (prefix === "transcripts") {
        const t = result.transcriptions || {};
        const m = result.metadata || {};
        const newIdx = t.indexed ?? 0;
        const cached = t.cached ?? 0;
        const fetchFailed = t.fetch_failed ?? 0;
        const indexFailed = t.failed ?? 0;
        const available = newIdx + cached;
        const parts = [
            `${m.indexed ?? 0} metadata`,
            `${available} transcripts in ES (${newIdx} new · ${cached} cached)`,
        ];
        if (fetchFailed) parts.push(`${fetchFailed} fetch failed`);
        if (indexFailed) parts.push(`${indexFailed} index failed`);
        return parts.join(" · ");
    }
    if (prefix === "qdrant") {
        return `${result.total_transcripts ?? 0} transcripts · ${result.total_chunks ?? 0} chunks · ${result.points_upserted ?? 0} points`;
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
            window.location.href = `/youtube-content-search/ingest?${q.toString()}`;
        } catch (e) {
            btn.disabled = false;
            btn.textContent = origLabel;
            alert(`Rerun failed: ${e.message ?? e}`);
        }
    });
}

// ---- main tracking loop ----------------------------------------------------
async function trackPipeline({ extract, qdrant, neo4j, startedAt }) {
    _stopRequested = false;
    const panel = document.getElementById("ycs-pipe-panel");
    if (!panel) return;  // page didn't render the chrome (shouldn't happen)
    panel.style.display = "flex";
    const elapsedEl = document.getElementById("ycs-pipe-panel-elapsed");
    const rerunBtn = document.getElementById("ycs-pipe-rerun");
    const stopBtn = document.getElementById("ycs-pipe-stop");
    bindRerun(rerunBtn, extract);
    bindStop(stopBtn, extract);
    if (stopBtn) stopBtn.disabled = false;
    const ids = { transcripts: extract, qdrant, neo4j };
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
        let mostRecent = null;
        let mostRecentPhase = null;
        let allTerminal = true;
        for (let i = 0; i < BARS.length; i++) {
            const prefix = BARS[i];
            const r = results[i];
            const state = r.state || "PENDING";
            const meta = r.meta || (r.result ?? {});
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
            if (meta && meta.current_item) {
                mostRecent = meta.current_item;
                mostRecentPhase = prefix;
            }
            if (!["SUCCESS", "FAILURE", "REVOKED", "ERROR"].includes(state)) {
                allTerminal = false;
            }
        }
        if (mostRecent) {
            _videoCard({ phase: _phaseDisplayName(mostRecentPhase), item: mostRecent });
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
            if (stopBtn) stopBtn.disabled = true;
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
function boot() {
    if (!document.getElementById("ycs-pipe-panel")) return;
    const params = new URLSearchParams(window.location.search);
    const urlExtract = params.get("extract");
    const urlQdrant  = params.get("qdrant");
    const urlNeo4j   = params.get("neo4j");
    if (urlExtract && urlQdrant && urlNeo4j) {
        const ids = {
            extract: urlExtract,
            qdrant:  urlQdrant,
            neo4j:   urlNeo4j,
            startedAt: Date.now(),
        };
        writePersisted(ids);
        trackPipeline(ids);
        return;
    }
    const persisted = readPersisted();
    if (persisted) {
        trackPipeline(persisted);
        return;
    }
    // Nothing to track — panel remains display:none from the server.
}

boot();
