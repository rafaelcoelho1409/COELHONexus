/* Source · Videos mode (June 2026 redesign — same shape as Channel/
 * Playlist). Paste video IDs/URLs → click Fetch videos → picker fills
 * with thumbnails+titles+metadata → user selects subset → Start
 * Ingestion. Replaces the prior chips-preview-only flow which only
 * showed validation glyphs (✓/×) and never the actual video metadata.
 *
 * Backend endpoint: GET /api/v1/ycs/content/videos/preview?ids=…
 * (yt-dlp metadata fetch, server-side paginated by offset+limit).
 * Picker.js's `wirePickerTab` is reused with two per-tab overrides:
 *   - `buildQuery` swaps `?id=…` for `?ids=v1,v2,v3,…`
 *   - `validateInput` requires ≥1 parseable video ID
 */
import { dispatchPipelineToIngest, parseLangs, setStatus } from "./shared.js";
import { parseVideo } from "./parsers.js";
import { wirePickerTab } from "./picker.js";

const form         = document.getElementById("ycs-videos-form");
const status       = document.getElementById("ycs-videos-status");
const submit       = document.getElementById("ycs-videos-submit");
const fetchBtn     = document.getElementById("ycs-videos-fetch");
const input        = document.getElementById("ycs-videos-input");
const picker       = document.getElementById("ycs-videos-picker");
const ingestAllBtn = document.getElementById("ycs-videos-ingest-all");

/* Parse the textarea into a deduped list of YouTube video IDs.
 * Accepts: bare 11-char IDs, watch URLs, youtu.be URLs, mixed CSV+
 * newline. Anything we can't parse is silently skipped. */
function _parseTextareaToIds(text) {
    if (!text) return [];
    const tokens = String(text).split(/[\n,]+/);
    const seen = new Set();
    const out = [];
    for (const raw of tokens) {
        const t = (raw ?? "").trim();
        if (!t) continue;
        const parsed = parseVideo(t);
        const id = parsed?.id;
        if (id && !seen.has(id)) {
            seen.add(id);
            out.push(id);
        }
    }
    return out;
}

/* localStorage persistence — survives reloads (same posture the prior
 * implementation had so users don't lose their paste buffer). */
const _BUFFER_KEY = "ycs:videos:buffer";
function _save() { try { localStorage.setItem(_BUFFER_KEY, input.value); } catch (_) {} }
function _restore() {
    try {
        const v = localStorage.getItem(_BUFFER_KEY);
        if (v && input) { input.value = v; }
    } catch (_) {}
}

if (input) {
    input.addEventListener("input", _save);
    input.addEventListener("blur",  _save);
    _restore();
}

/* Drag-drop .txt/.csv onto the textarea — read as plain text, append. */
function _onDragOver(ev) {
    ev.preventDefault();
    input.classList.add("dragover");
}
function _onDragLeave() {
    input.classList.remove("dragover");
}
async function _onDrop(ev) {
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
}
input?.addEventListener("dragover",  _onDragOver);
input?.addEventListener("dragleave", _onDragLeave);
input?.addEventListener("drop",      _onDrop);

/* Listen for routing from the Search tab — drop the items into the
 * textarea (newline-joined) so the user can review before clicking
 * Fetch. Per the kind-aware bulk routing, only `kind === "video"`
 * items land here. */
document.addEventListener("ycs:route", (ev) => {
    if (ev.detail?.mode !== "videos" || !input) return;
    const items = ev.detail.items || [];
    if (!items.length) return;
    const sep = input.value && !input.value.endsWith("\n") ? "\n" : "";
    input.value = (input.value || "") + sep + items.join("\n");
    _save();
    input.focus();
});

if (form && input && picker) {
    wirePickerTab({
        source:           "videos",
        inputEl:          input,
        fetchBtn,
        pickerRootEl:     picker,
        submitBtn:        submit,
        statusEl:         status,
        formEl:           form,
        apiEnumerateBase: "/api/v1/ycs/content/videos/preview",
        // Override: parse the textarea each fetch + build ?ids=… query.
        // Channel/Playlist use the default `?id=…` builder.
        buildQuery(value, offset, limit) {
            const ids = _parseTextareaToIds(value);
            return `ids=${encodeURIComponent(ids.join(","))}&offset=${offset}&limit=${limit}`;
        },
        // Override: surface a clear error when the textarea has no
        // parseable IDs (parseVideo couldn't recover anything).
        validateInput(value) {
            const ids = _parseTextareaToIds(value);
            return ids.length ? null
                : "Paste at least one valid YouTube video ID or URL.";
        },
        async dispatchPipeline(ids) {
            const incl = document.getElementById("ycs-videos-incl-trans")?.checked ?? true;
            const langs = parseLangs(document.getElementById("ycs-videos-langs")?.value);
            const body = { video_ids: ids, include_transcription: incl };
            if (langs) body.transcription_languages = langs;
            // Clear the buffered paste so a refresh after redirect
            // shows an empty textarea, not the just-submitted IDs.
            try { localStorage.removeItem(_BUFFER_KEY); } catch (_) { /* */ }
            await dispatchPipelineToIngest(
                "/content/videos/pipeline", body, status,
            );
        },
        ingestAllBtn,
        /* Ingest-all: unlike Channel/Playlist (server enumerates the
         * whole container), the pasted textarea IS the full set — so
         * just dispatch every parsed id, skipping the picker's
         * 100-per-page preview/selection entirely. */
        async dispatchIngestAll() {
            const ids = _parseTextareaToIds(input.value);
            if (!ids.length) {
                setStatus(status, "error", "Paste at least one valid YouTube video ID or URL.");
                return;
            }
            const incl = document.getElementById("ycs-videos-incl-trans")?.checked ?? true;
            const langs = parseLangs(document.getElementById("ycs-videos-langs")?.value);
            const body = { video_ids: ids, include_transcription: incl };
            if (langs) body.transcription_languages = langs;
            try { localStorage.removeItem(_BUFFER_KEY); } catch (_) { /* */ }
            await dispatchPipelineToIngest(
                "/content/videos/pipeline", body, status,
            );
        },
    });
}
