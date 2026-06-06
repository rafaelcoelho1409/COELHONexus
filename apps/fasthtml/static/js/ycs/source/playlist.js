/* Source · Playlist mode — paste-many playlists → N parallel Celery
 * dispatches. Mirror of channel.js with `parsePlaylist`. */
import { API, parseLangs, setStatus } from "./shared.js";
import { parsePlaylist } from "./parsers.js";

const form    = document.getElementById("ycs-playlist-form");
const status  = document.getElementById("ycs-playlist-status");
const submit  = document.getElementById("ycs-playlist-submit");
const input   = document.getElementById("ycs-playlist-input");
const chipsEl = document.getElementById("ycs-playlist-chips");
const countEl = document.getElementById("ycs-playlist-count");
const preview = document.getElementById("ycs-playlist-preview");

function parseToken(raw) {
    const t = (raw ?? "").trim();
    if (!t) return null;
    const parsed = parsePlaylist(t);
    if (!parsed) return { state: "invalid", original: t };
    return { state: "valid", id: parsed.display, original: t };
}

function parseAll(text) {
    if (!text) return [];
    return text.split(/[\n,]+/).map(parseToken).filter(Boolean);
}

function dedupe(tokens) {
    const seen = new Set();
    let dupes = 0;
    const kept = [];
    for (const t of tokens) {
        const key = t.id ?? t.original;
        if (seen.has(key)) { dupes++; continue; }
        seen.add(key);
        kept.push(t);
    }
    return { kept, dupes };
}

function render() {
    const all = parseAll(input.value);
    const { kept, dupes } = dedupe(all);
    const valid = kept.filter((t) => t.state === "valid");
    const invalid = kept.filter((t) => t.state === "invalid");
    if (!kept.length) {
        preview.dataset.state = "empty";
        chipsEl.innerHTML = "";
        countEl.textContent = "";
        if (submit) submit.disabled = true;
        return;
    }
    preview.dataset.state = valid.length ? "ready" : "blocked";
    const parts = [
        `${valid.length} valid`,
        invalid.length ? `${invalid.length} invalid` : null,
        dupes ? `${dupes} duplicate` : null,
    ].filter(Boolean);
    countEl.textContent = parts.join(" · ");
    chipsEl.innerHTML = kept.map((t) => {
        const glyph = t.state === "valid" ? "✓" : "×";
        const id = t.id ?? t.original;
        const title = t.state === "invalid"
            ? `Not a YouTube playlist URL/ID: ${t.original}`
            : `Playlist ${t.id}`;
        return `
            <span class="ycs-chip" data-state="${t.state}" title="${title}">
                <span class="ycs-chip-glyph" aria-hidden="true">${glyph}</span>
                <span class="ycs-chip-id">${id.slice(0, 24)}${id.length > 24 ? "…" : ""}</span>
            </span>
        `;
    }).join("");
    if (submit) submit.disabled = valid.length === 0;
}

input?.addEventListener("input", render);
input?.addEventListener("blur", render);
input?.addEventListener("paste", () => setTimeout(render, 0));

input?.addEventListener("dragover", (ev) => { ev.preventDefault(); input.classList.add("dragover"); });
input?.addEventListener("dragleave", () => input.classList.remove("dragover"));
input?.addEventListener("drop", async (ev) => {
    ev.preventDefault();
    input.classList.remove("dragover");
    const file = ev.dataTransfer?.files?.[0];
    if (!file) return;
    if (!/\.(txt|csv)$/i.test(file.name)) {
        setStatus(status, "error", "Drop a .txt or .csv file.");
        return;
    }
    const text = await file.text();
    input.value = input.value ? `${input.value}\n${text}` : text;
    _save();
    render();
});

const _BUFFER_KEY = "ycs:playlist:buffer";
function _save() { try { localStorage.setItem(_BUFFER_KEY, input.value); } catch (_) {} }
function _restore() {
    try {
        const v = localStorage.getItem(_BUFFER_KEY);
        if (v) { input.value = v; render(); }
    } catch (_) {}
}
input?.addEventListener("input", _save);
input?.addEventListener("blur", _save);

document.addEventListener("ycs:route", (ev) => {
    if (ev.detail?.mode !== "playlist") return;
    const items = ev.detail.items || [];
    if (!items.length) return;
    const sep = input.value && !input.value.endsWith("\n") ? "\n" : "";
    input.value = (input.value || "") + sep + items.join("\n");
    _save();
    render();
    input.focus();
});

_restore();

form?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const { kept } = dedupe(parseAll(input.value));
    const valid = kept.filter((t) => t.state === "valid").map((t) => t.id);
    if (!valid.length) {
        setStatus(status, "error", "Paste at least one valid playlist URL or ID.");
        return;
    }
    const max = parseInt(document.getElementById("ycs-playlist-max")?.value || "0", 10) || 0;
    const incl = document.getElementById("ycs-playlist-incl-trans").checked;
    const langs = parseLangs(document.getElementById("ycs-playlist-langs").value);
    setStatus(status, "running", `Queuing ${valid.length} playlist${valid.length === 1 ? "" : "s"}…`);
    if (submit) submit.disabled = true;
    try {
        const results = await Promise.all(valid.map((pid) => {
            const body = { playlist_id: pid, max_results: max, include_transcription: incl };
            if (langs) body.transcription_languages = langs;
            return fetch(API + "/content/playlist", {
                method: "POST",
                headers: { "content-type": "application/json" },
                body: JSON.stringify(body),
            }).then(async (r) => {
                let data = null;
                try { data = await r.json(); } catch (_) {}
                return { ok: r.ok, status: r.status, data };
            }).catch((e) => ({ ok: false, status: 0, data: { detail: String(e) } }));
        }));
        const okIds = results.filter((r) => r.ok && r.data?.task_id).map((r) => r.data.task_id);
        const failed = results.length - okIds.length;
        if (!okIds.length) {
            const first = results.find((r) => !r.ok)?.data?.detail || "All dispatches failed.";
            setStatus(status, "error", `All ${results.length} dispatches failed: ${String(first).slice(0, 80)}`);
            if (submit) submit.disabled = false;
            return;
        }
        try { localStorage.removeItem(_BUFFER_KEY); } catch (_) {}
        const note = failed ? ` (${failed} failed)` : "";
        setStatus(status, "running", `Queued ${okIds.length}/${results.length}${note} — redirecting to first.`);
        window.location.href = `/youtube-content-search/ingest?task=${encodeURIComponent(okIds[0])}`;
    } catch (e) {
        setStatus(status, "error", `Network error: ${e.message ?? e}`);
        if (submit) submit.disabled = false;
    }
});
