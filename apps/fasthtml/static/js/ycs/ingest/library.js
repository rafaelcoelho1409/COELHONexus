/* YCS · Ingest · Library — sidebar facets + row-card list of ingested
 * videos + per-row trash + bulk-action floating bar.
 *
 * Source-of-truth shape from the backend (see
 * `apps/fastapi/api/v1/ycs/admin/router.py`):
 *
 *   GET    /admin/videos?q=&channel=&status=&lang=&limit=&offset=
 *      → { items: [...], total, returned, offset, limit }
 *
 *   GET    /admin/videos/facets
 *      → { channels: [{key,label,count}], languages: […], statuses: […] }
 *
 *   DELETE /admin/videos/{video_id}
 *      → { status: "wiped", summary: {…} }
 *
 *   POST   /admin/videos/bulk-delete  body { video_ids: [...] }
 *      → { status: "wiped", summary: {…} }
 *
 * Per-row click:   selects row + reveals bulk bar.
 * Per-row trash:   confirms via shared `showConfirm` modal, then DELETE.
 * Bulk Delete:     confirms, then POST to bulk-delete.
 * Bulk Re-ingest:  POSTs to `/content/videos/pipeline` with the selected
 *                  video ids, redirects to /ingest with the new task ids
 *                  (Pipeline panel takes over). */

import { showConfirm } from "@dd/shared/ui/overlays.js";

const API   = "/api/v1/ycs";
const PAGE  = 50;
const STATE = {
    q:         "",
    channel:   null,         // single-select (radio-ish UX via checkbox)
    status:    null,
    lang:      null,
    offset:    0,
    selected:  new Set(),    // video_ids
    rows:      [],
    total:     0,
};

// ---- DOM refs (resolved once at boot) --------------------------------------
let DOM = {};

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

function htmlEscape(s) {
    return String(s ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

function fmtCount(n) {
    if (n == null) return "";
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
    return String(n);
}

function fmtDate(yyyymmdd) {
    if (!yyyymmdd || yyyymmdd.length !== 8) return "";
    return `${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6, 8)}`;
}

function fmtDuration(s) {
    if (!s) return "";
    if (typeof s === "string" && s.includes(":")) return s;  // already duration_string
    const n = Number(s);
    if (!Number.isFinite(n) || n <= 0) return "";
    const m = Math.floor(n / 60);
    const sec = Math.floor(n % 60).toString().padStart(2, "0");
    return `${m}:${sec}`;
}

// ---- rendering -------------------------------------------------------------
function renderFacets(facets) {
    /* Sidebar facets — each group is exclusive (radio-like via checkboxes).
     * Click toggles; second click on the same chip clears that facet. */
    for (const group of ["status", "channels", "languages"]) {
        const items = facets[group] || [];
        const container = document.getElementById(`ycs-lib-facet-${group}`);
        if (!container) continue;
        const frag = document.createDocumentFragment();
        for (const f of items) {
            const key = f.key ?? f.channel_id ?? "";
            const label = f.label ?? f.channel ?? key;
            if (!key) continue;
            const row = document.createElement("label");
            row.className = "ycs-lib-facet-row";
            row.innerHTML = `
                <input type="checkbox"
                       class="ycs-lib-facet-check"
                       data-group="${group}"
                       data-key="${htmlEscape(key)}">
                <span class="ycs-lib-facet-label" title="${htmlEscape(label)}">
                    ${htmlEscape(label)}
                </span>
                <span class="ycs-lib-facet-count">${fmtCount(f.count ?? f.video_count ?? 0)}</span>
            `;
            frag.appendChild(row);
        }
        container.replaceChildren(frag);
    }
    // After populating, sync checked state with STATE.
    syncFacetChecks();
}

function syncFacetChecks() {
    const fields = {
        status:    STATE.status,
        channels:  STATE.channel,
        languages: STATE.lang,
    };
    for (const [group, value] of Object.entries(fields)) {
        const container = document.getElementById(`ycs-lib-facet-${group}`);
        if (!container) continue;
        container.querySelectorAll(".ycs-lib-facet-check").forEach((cb) => {
            cb.checked = (cb.dataset.key === value);
        });
    }
}

function renderRows(items) {
    const list = document.getElementById("ycs-lib-list");
    if (!list) return;
    if (!items.length) {
        list.innerHTML = `<div class="ycs-lib-empty">No videos match the current filters.</div>`;
        return;
    }
    const frag = document.createDocumentFragment();
    for (const v of items) {
        const card = document.createElement("div");
        card.className = "ycs-lib-card";
        card.dataset.videoId = v.video_id;
        const checked = STATE.selected.has(v.video_id);
        if (checked) card.classList.add("selected");
        const statusPill = `<span class="ycs-lib-status ycs-lib-status-${v.status}">${htmlEscape(v.status)}</span>`;
        const thumb = v.thumbnail
            ? `<img class="ycs-lib-card-thumb" src="${htmlEscape(v.thumbnail)}" alt="" loading="lazy">`
            : `<div class="ycs-lib-card-thumb-empty"></div>`;
        const url = v.webpage_url || `https://www.youtube.com/watch?v=${v.video_id}`;
        card.innerHTML = `
            <input type="checkbox"
                   class="ycs-lib-card-check"
                   ${checked ? "checked" : ""}
                   data-video-id="${htmlEscape(v.video_id)}"
                   aria-label="Select ${htmlEscape(v.title || v.video_id)}">
            ${thumb}
            <div class="ycs-lib-card-body">
                <div class="ycs-lib-card-title-row">
                    ${statusPill}
                    <a class="ycs-lib-card-title"
                       href="${htmlEscape(url)}"
                       target="_blank"
                       rel="noopener"
                       title="${htmlEscape(v.title || "(no title)")}">
                        ${htmlEscape(v.title || "(no title)")}
                    </a>
                </div>
                <div class="ycs-lib-card-meta">
                    <span class="ycs-lib-card-channel">${htmlEscape(v.channel || "")}</span>
                    ${v.view_count != null ? `<span class="ycs-lib-card-sep">·</span><span>${fmtCount(v.view_count)} views</span>` : ""}
                    ${v.duration ? `<span class="ycs-lib-card-sep">·</span><span>${htmlEscape(fmtDuration(v.duration_string || v.duration))}</span>` : ""}
                    ${v.like_count != null ? `<span class="ycs-lib-card-sep">·</span><span>${fmtCount(v.like_count)} likes</span>` : ""}
                    ${v.upload_date ? `<span class="ycs-lib-card-sep">·</span><span>${htmlEscape(fmtDate(v.upload_date))}</span>` : ""}
                </div>
                <div class="ycs-lib-card-stats">
                    ${v.transcript_length ? `<span>${fmtCount(v.transcript_length)} chars</span>` : ""}
                    ${(v.transcript_langs || []).length ? `<span class="ycs-lib-card-sep">·</span><span>${(v.transcript_langs || []).join(", ")}</span>` : ""}
                    ${v.entity_count ? `<span class="ycs-lib-card-sep">·</span><span>${fmtCount(v.entity_count)} entities</span>` : ""}
                </div>
            </div>
            <button type="button"
                    class="ycs-lib-card-trash"
                    data-video-id="${htmlEscape(v.video_id)}"
                    title="Delete this video from ES + Qdrant + Neo4j"
                    aria-label="Delete this video">🗑</button>
        `;
        frag.appendChild(card);
    }
    list.replaceChildren(frag);
}

function renderBulkBar() {
    const bar = document.getElementById("ycs-lib-bulk-bar");
    const countEl = document.getElementById("ycs-lib-bulk-count");
    if (!bar || !countEl) return;
    const n = STATE.selected.size;
    countEl.textContent = String(n);
    bar.classList.toggle("visible", n > 0);
}

function renderCount() {
    const el = document.getElementById("ycs-lib-count");
    if (el) el.textContent = String(STATE.total);
}

// ---- loaders ---------------------------------------------------------------
async function loadFacets() {
    try {
        const f = await api("/admin/videos/facets");
        renderFacets(f);
    } catch (e) {
        console.warn("[ycs:lib] facets load failed", e);
    }
}

async function loadRows() {
    const q = new URLSearchParams();
    if (STATE.q)       q.set("q",       STATE.q);
    if (STATE.channel) q.set("channel", STATE.channel);
    if (STATE.status)  q.set("status",  STATE.status);
    if (STATE.lang)    q.set("lang",    STATE.lang);
    q.set("limit",  String(PAGE));
    q.set("offset", String(STATE.offset));
    try {
        const r = await api(`/admin/videos?${q.toString()}`);
        STATE.rows  = r.items || [];
        STATE.total = r.total || 0;
        renderRows(STATE.rows);
        renderCount();
    } catch (e) {
        const list = document.getElementById("ycs-lib-list");
        if (list) {
            list.innerHTML = `<div class="ycs-lib-empty">Could not load library: ${htmlEscape(e.message)}</div>`;
        }
    }
}

async function refresh() {
    await Promise.allSettled([loadFacets(), loadRows()]);
}

// ---- event wiring ----------------------------------------------------------
function bindFacetClicks() {
    document.getElementById("ycs-lib-sidebar")?.addEventListener("change", (ev) => {
        const cb = ev.target;
        if (!cb.matches?.(".ycs-lib-facet-check")) return;
        const group = cb.dataset.group;
        const key   = cb.dataset.key;
        // Exclusive within group: clear all others.
        cb.closest(".ycs-lib-facet-list")
          .querySelectorAll(".ycs-lib-facet-check")
          .forEach((other) => { if (other !== cb) other.checked = false; });
        const value = cb.checked ? key : null;
        if (group === "status")    STATE.status  = value;
        if (group === "channels")  STATE.channel = value;
        if (group === "languages") STATE.lang    = value;
        STATE.offset = 0;
        loadRows();
    });

    document.getElementById("ycs-lib-clear-filters")?.addEventListener("click", () => {
        STATE.status  = null;
        STATE.channel = null;
        STATE.lang    = null;
        STATE.q       = "";
        const s = document.getElementById("ycs-lib-search");
        if (s) s.value = "";
        syncFacetChecks();
        loadRows();
    });
}

function bindSearch() {
    let timer = null;
    document.getElementById("ycs-lib-search")?.addEventListener("input", (ev) => {
        clearTimeout(timer);
        timer = setTimeout(() => {
            STATE.q = ev.target.value.trim();
            STATE.offset = 0;
            loadRows();
        }, 250);
    });
}

function bindRowClicks() {
    const list = document.getElementById("ycs-lib-list");
    if (!list) return;
    list.addEventListener("change", (ev) => {
        const cb = ev.target;
        if (!cb.matches?.(".ycs-lib-card-check")) return;
        const vid = cb.dataset.videoId;
        if (cb.checked) STATE.selected.add(vid);
        else            STATE.selected.delete(vid);
        cb.closest(".ycs-lib-card")?.classList.toggle("selected", cb.checked);
        renderBulkBar();
    });
    list.addEventListener("click", async (ev) => {
        const trash = ev.target.closest?.(".ycs-lib-card-trash");
        if (!trash) return;
        ev.stopPropagation();
        const vid = trash.dataset.videoId;
        const ok = await showConfirm(
            "Delete this video?",
            "Wipes ES metadata + transcripts, Qdrant points, and " +
            "Neo4j Document + Video nodes for this video. Entity " +
            "nodes are left intact (may be referenced by other " +
            "videos). This cannot be undone.",
            "Delete",
        );
        if (!ok) return;
        try {
            await api(`/admin/videos/${encodeURIComponent(vid)}`, {
                method: "DELETE",
            });
            STATE.selected.delete(vid);
            renderBulkBar();
            await refresh();
        } catch (e) {
            alert(`Delete failed: ${e.message ?? e}`);
        }
    });
}

function bindBulkBar() {
    const bar = document.getElementById("ycs-lib-bulk-bar");
    if (!bar) return;
    document.getElementById("ycs-lib-bulk-cancel")?.addEventListener("click", () => {
        STATE.selected.clear();
        renderRows(STATE.rows);
        renderBulkBar();
    });
    document.getElementById("ycs-lib-bulk-delete")?.addEventListener("click", async () => {
        const n = STATE.selected.size;
        if (!n) return;
        const ok = await showConfirm(
            `Delete ${n} video${n === 1 ? "" : "s"}?`,
            "Wipes ES + Qdrant + Neo4j for the selected videos. " +
            "Entity nodes are left intact. This cannot be undone.",
            "Delete",
        );
        if (!ok) return;
        const ids = [...STATE.selected];
        try {
            await api("/admin/videos/bulk-delete", {
                method:  "POST",
                headers: { "content-type": "application/json" },
                body:    JSON.stringify({ video_ids: ids }),
            });
            STATE.selected.clear();
            renderBulkBar();
            await refresh();
        } catch (e) {
            alert(`Bulk delete failed: ${e.message ?? e}`);
        }
    });
    document.getElementById("ycs-lib-bulk-reingest")?.addEventListener("click", async () => {
        const n = STATE.selected.size;
        if (!n) return;
        const ids = [...STATE.selected];
        try {
            const r = await api("/content/videos/pipeline", {
                method:  "POST",
                headers: { "content-type": "application/json" },
                body:    JSON.stringify({
                    video_ids:               ids,
                    include_transcription:   true,
                    transcription_languages: null,
                }),
            });
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
            alert(`Re-ingest failed: ${e.message ?? e}`);
        }
    });
}

// ---- pipeline → library auto-refresh ---------------------------------------
/* Debounced refresh fires on:
 *   - `ycs:pipeline:phase-done` (each phase first reaches SUCCESS so
 *     ES/Qdrant/Neo4j become independently observable in the library
 *     status column without waiting for the whole chain).
 *   - `ycs:pipeline:done`       (all-terminal — final pass to catch
 *     any state the per-phase refreshes missed).
 *
 * Debounce keeps the multiple events from triggering 3 back-to-back
 * fetches when ALL phases complete close together (e.g., everything
 * cached). 400ms is short enough to feel live, long enough to coalesce.
 */
let _refreshTimer = null;
function scheduleRefresh() {
    clearTimeout(_refreshTimer);
    _refreshTimer = setTimeout(() => { refresh(); }, 400);
}

function bindPipelineEvents() {
    document.addEventListener("ycs:pipeline:phase-done", () => scheduleRefresh());
    document.addEventListener("ycs:pipeline:done",       () => scheduleRefresh());
}

// ---- boot ------------------------------------------------------------------
(function boot() {
    // Only boot on the Ingest page (where the library DOM exists).
    if (!document.getElementById("ycs-lib-panel")) return;
    bindFacetClicks();
    bindSearch();
    bindRowClicks();
    bindBulkBar();
    bindPipelineEvents();
    refresh();
})();
