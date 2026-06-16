/* Postgres-backed query history. A floating panel anchored to the
 * editor header — toggled by the "History" button. Each row carries
 * the body + AI prompt (if any) + a Restore action that drops the
 * snapshot back into the editor.
 *
 * Save is fired automatically on every successful Run (orchestrator
 * calls `history.recordRun(...)` from `query.js`) and on the AI
 * `done` frame when `ok=true`. We don't deduplicate identical bodies
 * — that's the user's affordance via the Favorite toggle (Phase 5.x).
 */
const API = "/api/v1/ycs/query/history";


/* Friendlier message for the network-down case — `fetch()` throws
 * TypeError "Failed to fetch" on every network-level failure (CORS,
 * DNS, server down). Tell the user WHAT to check rather than echoing
 * the raw browser string. */
function humanizeError(e) {
    if (!e) return "Unknown error";
    const msg = e.message || String(e);
    if (e instanceof TypeError && /failed to fetch|networkerror|load failed/i.test(msg)) {
        return "Cannot reach the API server — is the backend running?";
    }
    return msg;
}


function htmlEscape(s) {
    return String(s ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}


function formatWhen(iso) {
    if (!iso) return "";
    const t = Date.parse(iso);
    if (isNaN(t)) return iso;
    const dt = new Date(t);
    return dt.toLocaleString();
}


export async function listHistory(backend) {
    const u = new URL(API, location.origin);
    if (backend) u.searchParams.set("backend", backend);
    u.searchParams.set("limit", "50");
    const r = await fetch(u.toString());
    if (!r.ok) throw new Error(`history fetch failed (${r.status})`);
    const j = await r.json();
    return j.items || [];
}


export async function recordRun({ backend, body, prompt }) {
    if (!body || !body.trim()) return;
    try {
        await fetch(API, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                backend, app: "ycs", body, prompt: prompt || "",
            }),
        });
    } catch (_) { /* best-effort: silent on outage */ }
}


export async function deleteEntry(entryId) {
    const r = await fetch(`${API}/${entryId}`, { method: "DELETE" });
    return r.ok;
}


/* Build the panel DOM lazily, anchored to a host element (the editor
 * header). Visible/hidden toggle is controlled by the caller. Returns
 * an object with `show / hide / refresh / setOnRestore` so the
 * orchestrator can drive it. */
export function makePanel(host, { onRestore }) {
    const wrap = document.createElement("div");
    wrap.className = "ycs-query-history-panel";
    wrap.style.display = "none";
    wrap.innerHTML = `
        <header class="ycs-query-history-head">
            <strong>History</strong>
            <span class="ycs-query-history-sub" id="ycs-query-history-sub">—</span>
            <button type="button" class="ycs-query-history-close"
                    id="ycs-query-history-close" aria-label="Close history">
                ×
            </button>
        </header>
        <div class="ycs-query-history-list" id="ycs-query-history-list">
            Loading…
        </div>`;
    host.appendChild(wrap);

    let currentBackend = "elasticsearch";
    let _onRestore = onRestore || (() => {});

    const listEl  = wrap.querySelector("#ycs-query-history-list");
    const subEl   = wrap.querySelector("#ycs-query-history-sub");
    const closeEl = wrap.querySelector("#ycs-query-history-close");

    closeEl.addEventListener("click", () => hide());

    function renderRows(items) {
        if (!items.length) {
            listEl.innerHTML = `
                <div class="ycs-query-history-empty">
                    No saved queries yet — run something on the left and it'll show up here.
                </div>`;
            return;
        }
        listEl.innerHTML = items.map((it) => `
            <article class="ycs-query-history-row" data-id="${it.id}">
                <header class="ycs-query-history-row-head">
                    <span class="ycs-query-history-row-backend">${htmlEscape(it.backend)}</span>
                    <span class="ycs-query-history-row-when">${htmlEscape(formatWhen(it.created_at))}</span>
                </header>
                ${it.prompt ? `<div class="ycs-query-history-row-prompt">${htmlEscape(it.prompt)}</div>` : ""}
                <pre class="ycs-query-history-row-body">${htmlEscape(it.body)}</pre>
                <footer class="ycs-query-history-row-actions">
                    <button type="button" class="ycs-query-history-restore"
                            data-restore-id="${it.id}">Restore</button>
                    <button type="button" class="ycs-query-history-delete"
                            data-delete-id="${it.id}">Delete</button>
                </footer>
            </article>
        `).join("");
    }

    listEl.addEventListener("click", async (ev) => {
        // Retry button in the network-error empty state.
        if (ev.target.closest("[data-history-retry]")) {
            refresh();
            return;
        }
        const r = ev.target.closest("[data-restore-id]");
        if (r) {
            const id = r.dataset.restoreId;
            const row = listEl.querySelector(`[data-id="${id}"] pre`);
            if (row) _onRestore({
                body: row.textContent || "",
                backend: row.closest(".ycs-query-history-row")
                    .querySelector(".ycs-query-history-row-backend").textContent,
            });
            hide();
            return;
        }
        const d = ev.target.closest("[data-delete-id]");
        if (d) {
            const id = d.dataset.deleteId;
            try {
                await deleteEntry(id);
                await refresh();
            } catch (e) {
                // Visual confirmation that the delete didn't land.
                // Don't blow away the list — the user might still want
                // to see the rows they have.
                d.disabled = false;
                d.textContent = humanizeError(e);
            }
        }
    });

    async function refresh() {
        subEl.textContent = currentBackend;
        listEl.innerHTML = "Loading…";
        try {
            const items = await listHistory(currentBackend);
            renderRows(items);
        } catch (e) {
            const msg = humanizeError(e);
            listEl.innerHTML = `
                <div class="ycs-query-history-error">
                    <div class="ycs-query-history-error-msg">
                        ${htmlEscape(msg)}
                    </div>
                    <button type="button"
                            class="ycs-query-history-retry"
                            data-history-retry="1">
                        Retry
                    </button>
                </div>`;
        }
    }

    function show(backend) {
        currentBackend = backend || currentBackend;
        wrap.style.display = "";
        refresh();
    }
    function hide() {
        wrap.style.display = "none";
    }

    return { show, hide, refresh, setOnRestore: (fn) => { _onRestore = fn; } };
}
