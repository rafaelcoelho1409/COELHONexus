/* YCS · Step 2 · Ingest — task polling + library aggregations.
 *
 * Responsibilities:
 *   1. If `?task=<id>` in URL, poll /api/v1/ycs/admin/task/{id} and
 *      render progress / final result. Show the Qdrant follow-up
 *      action on SUCCESS.
 *   2. Render the channels + playlists library grids by querying
 *      /api/v1/ycs/admin/ingested-channels  and  /ingested-playlists.
 *   3. Wire the pipeline action buttons to /api/v1/ycs/agents/{ingest/qdrant,
 *      ingest/neo4j} — same response shape as the Source step's dispatches
 *      (task_id + status).
 */

const POLL_INTERVAL_MS = 1500;
const POLL_BACKOFF_AFTER = 30;  // After this many ticks, slow down to 3s.

// ---- helpers ---------------------------------------------------------------
async function api(path, opts = {}) {
    const r = await fetch(`/api/v1/ycs${path}`, opts);
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

function setStatus(node, kind, text) {
    if (!node) return;
    node.className = `ycs-search-status${kind ? ` ${kind}` : ""}`;
    node.textContent = text;
}

function fmtCount(n) {
    if (n == null) return "0";
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

function makeCard({ kind, label, metaText, dataset }) {
    const card = document.createElement("div");
    card.className = "ycs-lib-card";
    if (dataset) Object.assign(card.dataset, dataset);
    card.innerHTML = `
        <div class="ycs-lib-card-head">
            <span class="ycs-lib-card-kind">${kind}</span>
            <span class="ycs-lib-card-label" title="${label}">${label}</span>
        </div>
        <div class="ycs-lib-card-meta">
            <span>${metaText}</span>
        </div>
    `;
    return card;
}

// ---- (1) task polling ------------------------------------------------------
async function pollTaskOnce(taskId) {
    try {
        return await api(`/admin/task/${encodeURIComponent(taskId)}`);
    } catch (e) {
        return { state: "ERROR", error: e.message ?? String(e) };
    }
}

function renderTaskState(payload, tick) {
    const box = document.getElementById("ycs-job-box");
    const status = document.getElementById("ycs-job-status");
    const phase = document.getElementById("ycs-job-phase");
    const idEl = document.getElementById("ycs-job-id");
    const fill = document.getElementById("ycs-job-fill");
    const counter = document.getElementById("ycs-job-counter");
    const elapsed = document.getElementById("ycs-job-elapsed");
    const summary = document.getElementById("ycs-job-summary");
    const followup = document.getElementById("ycs-job-followup");

    box.style.display = "flex";
    idEl.textContent = `· ${payload.task_id?.slice(0, 8) ?? ""}…`;
    elapsed.textContent = fmtElapsed(tick * POLL_INTERVAL_MS / 1000);

    const state = (payload.state || "PENDING").toLowerCase();
    status.textContent = payload.state || "PENDING";
    status.dataset.status =
        state === "success" ? "done" :
        state === "failure" || state === "error" ? "failed" :
        state === "started" || state === "progress" ? "running" :
        "running";

    const meta = payload.meta || {};
    const totalDocs = meta.total ?? 0;
    if (state === "progress" || state === "started" || state === "pending") {
        phase.textContent = meta.status
            ? `${meta.status}${totalDocs ? ` (${totalDocs})` : ""}`
            : "queued";
        counter.textContent = totalDocs ? `${totalDocs} items` : "";
        fill.style.width = totalDocs ? "60%" : "20%";
        document.getElementById("ycs-job-box").classList.remove("done");
    } else if (state === "success") {
        const r = payload.result ?? {};
        phase.textContent = "Done";
        counter.textContent = r.total_videos != null
            ? `${r.total_videos} videos`
            : "complete";
        fill.style.width = "100%";
        const m = r.metadata ?? {};
        const t = r.transcriptions ?? {};
        summary.innerHTML = `
            <div class="ycs-job-summary-row">
                <strong>Metadata:</strong> ${m.indexed ?? 0} indexed
                ${m.failed ? `· <span class="ycs-fail">${m.failed} failed</span>` : ""}
            </div>
            <div class="ycs-job-summary-row">
                <strong>Transcripts:</strong> ${t.indexed ?? 0} indexed
                ${t.failed ? `· <span class="ycs-fail">${t.failed} failed</span>` : ""}
            </div>
        `;
        if (followup) followup.disabled = false;
    } else if (state === "failure" || state === "error") {
        phase.textContent = payload.error || "Failed";
        fill.style.width = "100%";
        fill.style.background = "var(--error-text, #c41230)";
        counter.textContent = "";
    }
}

async function trackTask(taskId) {
    const followup = document.getElementById("ycs-job-followup");
    followup?.addEventListener("click", async () => {
        followup.disabled = true;
        const ps = document.getElementById("ycs-pipe-status");
        try {
            const r = await api("/agents/ingest/qdrant", {
                method: "POST",
                headers: { "content-type": "application/json" },
                body: JSON.stringify({}),
            });
            setStatus(ps, "running", `Qdrant ingest queued: ${r.task_id?.slice(0, 8)}…`);
        } catch (e) {
            setStatus(ps, "error", `Qdrant queue failed: ${e.message}`);
            followup.disabled = false;
        }
    });

    let tick = 0;
    let interval = POLL_INTERVAL_MS;
    while (true) {
        tick += 1;
        const payload = await pollTaskOnce(taskId);
        renderTaskState(payload, tick);
        const terminal = ["SUCCESS", "FAILURE", "REVOKED", "ERROR"]
            .includes(payload.state);
        if (terminal) break;
        if (tick > POLL_BACKOFF_AFTER) interval = 3000;
        await new Promise((r) => setTimeout(r, interval));
    }
}

// ---- (2) library aggregations ---------------------------------------------
async function renderChannels() {
    const grid = document.getElementById("ycs-channels-grid");
    if (!grid) return;
    try {
        const r = await api("/admin/ingested-channels");
        if (!r.items?.length) return;  // keep the empty card
        const select = document.getElementById("ycs-channel-filter");
        if (select) {
            for (const ch of r.items) {
                const opt = document.createElement("option");
                opt.value = ch.channel_id ?? "";
                opt.textContent = `${ch.channel ?? ch.channel_id} · ${ch.video_count}`;
                select.appendChild(opt);
            }
        }
        const frag = document.createDocumentFragment();
        for (const ch of r.items) {
            frag.appendChild(makeCard({
                kind: "Channel",
                label: ch.channel || ch.channel_id || "(unnamed)",
                metaText: `${fmtCount(ch.video_count)} videos`,
                dataset: { channelId: ch.channel_id ?? "" },
            }));
        }
        grid.replaceChildren(frag);
    } catch (e) {
        grid.innerHTML = `<div class="ycs-search-empty">Could not load channels: ${e.message}</div>`;
    }
}

async function renderPlaylists() {
    const grid = document.getElementById("ycs-playlists-grid");
    if (!grid) return;
    try {
        const r = await api("/admin/ingested-playlists");
        if (!r.items?.length) return;
        const frag = document.createDocumentFragment();
        for (const pl of r.items) {
            frag.appendChild(makeCard({
                kind: "Playlist",
                label: pl.playlist_title || pl.playlist_id || "(unnamed)",
                metaText: `${fmtCount(pl.video_count)} videos`,
                dataset: { playlistId: pl.playlist_id ?? "" },
            }));
        }
        grid.replaceChildren(frag);
    } catch (e) {
        grid.innerHTML = `<div class="ycs-search-empty">Could not load playlists: ${e.message}</div>`;
    }
}

// ---- (3) pipeline buttons --------------------------------------------------
function bindPipelineButtons() {
    const status = document.getElementById("ycs-pipe-status");
    const qBtn = document.getElementById("ycs-pipe-qdrant");
    const nBtn = document.getElementById("ycs-pipe-neo4j");

    qBtn?.addEventListener("click", async () => {
        qBtn.disabled = true;
        setStatus(status, "running", "Queuing Qdrant ingest…");
        try {
            const r = await api("/agents/ingest/qdrant", {
                method: "POST",
                headers: { "content-type": "application/json" },
                body: JSON.stringify({}),
            });
            setStatus(status, "running", `Qdrant queued: ${r.task_id?.slice(0, 8)}…`);
        } catch (e) {
            setStatus(status, "error", `Qdrant queue failed: ${e.message}`);
        } finally {
            qBtn.disabled = false;
        }
    });

    nBtn?.addEventListener("click", async () => {
        nBtn.disabled = true;
        setStatus(status, "running", "Queuing Neo4j extraction…");
        try {
            const r = await api("/agents/ingest/neo4j", {
                method: "POST",
                headers: { "content-type": "application/json" },
                body: JSON.stringify({ batch_size: 3 }),
            });
            setStatus(status, "running", `Neo4j queued: ${r.task_id?.slice(0, 8)}…`);
        } catch (e) {
            setStatus(status, "error", `Neo4j queue failed: ${e.message}`);
        } finally {
            nBtn.disabled = false;
        }
    });
}

// ---- entry ----------------------------------------------------------------
(async function init() {
    bindPipelineButtons();
    const params = new URLSearchParams(window.location.search);
    const taskId = params.get("task");
    if (taskId) trackTask(taskId);
    await Promise.allSettled([renderChannels(), renderPlaylists()]);
})();
