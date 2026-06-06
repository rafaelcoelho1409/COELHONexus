/* Source · Videos mode — paste-to-chips with inline validation.
 *
 * SOTA pattern (Linear paste-many-issues, Vercel env-var bulk import,
 * Notion multi-select paste): textarea is the input affordance; each
 * line/CSV token is parsed into a chip with a status glyph. Live count
 * header above chips. Drag-drop .txt/.csv onto the textarea. Submit
 * gated on `valid ≥ 1`. */
import { dispatchToIngest, parseLangs, setStatus } from "./shared.js";
import { parseVideo } from "./parsers.js";

const form    = document.getElementById("ycs-videos-form");
const status  = document.getElementById("ycs-videos-status");
const submit  = document.getElementById("ycs-videos-submit");
const input   = document.getElementById("ycs-videos-input");
const chipsEl = document.getElementById("ycs-videos-chips");
const countEl = document.getElementById("ycs-videos-count");
const preview = document.getElementById("ycs-videos-preview");

/* Parse one token. Returns:
 *   { state: "valid",      id, original }  — bare 11-char ID
 *   { state: "recovered",  id, original }  — extracted from URL
 *   { state: "invalid",    original }      — didn't look like YT
 */
function parseToken(raw) {
    const t = (raw ?? "").trim();
    if (!t) return null;
    const parsed = parseVideo(t);
    if (!parsed) return { state: "invalid", original: t };
    // Bare 11-char ID == "valid"; anything else (URL extraction) == "recovered"
    const isBare = /^[A-Za-z0-9_-]{11}$/.test(t);
    return {
        state: isBare ? "valid" : "recovered",
        id: parsed.id,
        original: t,
    };
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
    const raw = input.value;
    const all = parseAll(raw);
    const { kept, dupes } = dedupe(all);
    const valid = kept.filter((t) => t.state !== "invalid");
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
        const glyph = t.state === "valid" ? "✓"
                    : t.state === "recovered" ? "↻"
                    : "×";
        const id = t.id ?? t.original;
        const title = t.state === "recovered"
            ? `Extracted ${t.id} from ${t.original}`
            : t.state === "invalid"
            ? `Not a YouTube ID or URL: ${t.original}`
            : `Video ID ${t.id}`;
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

/* Drag-drop .txt/.csv onto the textarea — read as plain text, append
 * to existing content (don't overwrite, so the user can mix paste +
 * drop). */
function onDragOver(ev) {
    ev.preventDefault();
    input.classList.add("dragover");
}
function onDragLeave() {
    input.classList.remove("dragover");
}
async function onDrop(ev) {
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
    render();
}
input?.addEventListener("dragover", onDragOver);
input?.addEventListener("dragleave", onDragLeave);
input?.addEventListener("drop", onDrop);

/* localStorage persistence — survives reloads. Save on every input
 * change (debounce-via-input event is fine; reading is cheap). Restore
 * on module load (below). */
const _BUFFER_KEY = "ycs:videos:buffer";
function _save() {
    try { localStorage.setItem(_BUFFER_KEY, input.value); } catch (_) {}
}
function _restore() {
    try {
        const v = localStorage.getItem(_BUFFER_KEY);
        if (v) { input.value = v; render(); }
    } catch (_) {}
}
input?.addEventListener("input", _save);
input?.addEventListener("blur", _save);

/* Listen for routing from the Search tab. `ycs:route` event delivers
 * `{ mode: "videos", items: [<id>, ...] }` — append IDs to the
 * textarea (newline-separated), preserving anything the user already
 * typed. Don't auto-submit; let them review. */
document.addEventListener("ycs:route", (ev) => {
    if (ev.detail?.mode !== "videos") return;
    const items = ev.detail.items || [];
    if (!items.length) return;
    const sep = input.value && !input.value.endsWith("\n") ? "\n" : "";
    input.value = (input.value || "") + sep + items.join("\n");
    _save();
    render();
    input.focus();
});

_restore();

form?.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const { kept } = dedupe(parseAll(input.value));
    const ids = kept.filter((t) => t.state !== "invalid").map((t) => t.id);
    if (!ids.length) {
        setStatus(status, "error", "Paste at least one valid video ID or URL.");
        return;
    }
    const incl = document.getElementById("ycs-videos-incl-trans").checked;
    const langs = parseLangs(document.getElementById("ycs-videos-langs").value);
    const body = { video_ids: ids, include_transcription: incl };
    if (langs) body.transcription_languages = langs;
    // On successful dispatch, clear the persisted buffer so the user
    // doesn't see stale chips after redirecting back from Ingest.
    try { localStorage.removeItem(_BUFFER_KEY); } catch (_) {}
    dispatchToIngest("/content/videos", body, status);
});
