/* Source · shared helpers — used by every per-mode module. Mirrors
 * `features/ycs/source/widgets.py` on the Python side: one file for
 * the vocabulary the per-mode files reuse. */

export const API = "/api/v1/ycs";

export function setStatus(node, kind, text) {
    if (!node) return;
    node.className = `ycs-search-status${kind ? ` ${kind}` : ""}`;
    node.textContent = text;
}

export function fmtCount(n) {
    if (n == null) return "";
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
    return String(n);
}

export function fmtDate(yyyymmdd) {
    if (!yyyymmdd || yyyymmdd.length !== 8) return "";
    return `${yyyymmdd.slice(0, 4)}-${yyyymmdd.slice(4, 6)}-${yyyymmdd.slice(6, 8)}`;
}

export function parseLangs(raw) {
    const v = (raw ?? "").trim();
    if (!v) return null;
    return v.split(/[,\s]+/).map((s) => s.trim()).filter(Boolean);
}

/* Queue work on the backend → redirect to /ingest?task=<id> so the
 * Ingest page can poll Celery status. Shared by videos / channel /
 * playlist. */
export async function dispatchToIngest(endpoint, body, statusEl) {
    setStatus(statusEl, "running", "Queuing…");
    try {
        const r = await fetch(API + endpoint, {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify(body),
        });
        let data = null;
        try { data = await r.json(); } catch (_) { /* */ }
        if (!r.ok) {
            const msg = (data && (data.detail ?? data.message)) || r.statusText;
            setStatus(statusEl, "error", `Dispatch failed (${r.status}): ${msg}`);
            return;
        }
        setStatus(statusEl, "running", `Queued: ${data.task_id?.slice(0, 8)}… — redirecting.`);
        window.location.href = `/youtube-content-search/ingest?task=${encodeURIComponent(data.task_id)}`;
    } catch (e) {
        setStatus(statusEl, "error", `Network error: ${e.message ?? e}`);
    }
}

/* Variant for the Videos tab — POSTs to the 3-phase pipeline endpoint
 * (`/content/videos/pipeline`), receives `{phases: {extract, qdrant,
 * neo4j}}`, and redirects to /ingest with all 3 task_ids in the URL
 * so the Ingest page can render 3 live progress bars + a sticky
 * video metadata card. */
export async function dispatchPipelineToIngest(endpoint, body, statusEl) {
    setStatus(statusEl, "running", "Queuing 3-phase pipeline…");
    try {
        const r = await fetch(API + endpoint, {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify(body),
        });
        let data = null;
        try { data = await r.json(); } catch (_) { /* */ }
        if (!r.ok) {
            const msg = (data && (data.detail ?? data.message)) || r.statusText;
            setStatus(statusEl, "error", `Dispatch failed (${r.status}): ${msg}`);
            return;
        }
        const p = data.phases ?? {};
        if (!p.extract || !p.qdrant || !p.neo4j) {
            setStatus(statusEl, "error", "Backend returned no phase IDs.");
            return;
        }
        // Pre-seed the pipeline-panel localStorage entry with the
        // ordered video_ids so the Ingest-page right-column list
        // renders rows immediately — no flash of "Waiting for
        // metadata…" while Phase 1's yt-dlp + ES round-trip completes.
        try {
            const ids = {
                extract:   p.extract,
                qdrant:    p.qdrant,
                neo4j:     p.neo4j,
                video_ids: Array.isArray(data.video_ids) ? data.video_ids : [],
                startedAt: Date.now(),
            };
            localStorage.setItem("ycs:pipeline:active", JSON.stringify(ids));
        } catch (_) { /* */ }
        const q = new URLSearchParams({
            extract: p.extract,
            qdrant:  p.qdrant,
            neo4j:   p.neo4j,
        });
        setStatus(
            statusEl, "running",
            `Queued: ${p.extract.slice(0, 8)}…/${p.qdrant.slice(0, 8)}…/${p.neo4j.slice(0, 8)}… — redirecting.`,
        );
        window.location.href = `/youtube-content-search/ingest?${q.toString()}`;
    } catch (e) {
        setStatus(statusEl, "error", `Network error: ${e.message ?? e}`);
    }
}
