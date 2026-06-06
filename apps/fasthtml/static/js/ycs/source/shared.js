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
