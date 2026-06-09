/* Source · Playlist mode (June 2026 redesign) — paste ONE playlist,
 * browse + pick a subset of its videos, dispatch through the videos
 * pipeline. Mirror of channel.js — see that file + picker.js for the
 * shared design rationale. */
import { dispatchPipelineToIngest, parseLangs, setStatus } from "./shared.js";
import { wirePickerTab } from "./picker.js";

const form         = document.getElementById("ycs-playlist-form");
const status       = document.getElementById("ycs-playlist-status");
const submit       = document.getElementById("ycs-playlist-submit");
const fetchBtn     = document.getElementById("ycs-playlist-fetch");
const input        = document.getElementById("ycs-playlist-input");
const picker       = document.getElementById("ycs-playlist-picker");
const ingestAllBtn = document.getElementById("ycs-playlist-ingest-all");

if (form && input && picker) {
    wirePickerTab({
        source:           "playlist",
        inputEl:          input,
        fetchBtn,
        pickerRootEl:     picker,
        submitBtn:        submit,
        ingestAllBtn,
        statusEl:         status,
        formEl:           form,
        apiEnumerateBase: "/api/v1/ycs/content/playlist/videos",
        async dispatchPipeline(ids) {
            const incl = document.getElementById("ycs-playlist-incl-trans")?.checked ?? true;
            const langs = parseLangs(document.getElementById("ycs-playlist-langs")?.value);
            const body = { video_ids: ids, include_transcription: incl };
            if (langs) body.transcription_languages = langs;
            await dispatchPipelineToIngest(
                "/content/videos/pipeline", body, status,
            );
        },
        /* Ingest-all: server walks the entire playlist via `--print
         * "%(id)s"` (one yt-dlp call) and dispatches the 3-phase
         * pipeline against every video_id. Mirror of channel.js. */
        async dispatchIngestAll() {
            const playlistId = (input.value ?? "").trim();
            if (!playlistId) {
                setStatus(status, "error", "Paste a playlist URL/ID first.");
                return;
            }
            const incl = document.getElementById("ycs-playlist-incl-trans")?.checked ?? true;
            const langs = parseLangs(document.getElementById("ycs-playlist-langs")?.value);
            const body = { playlist_id: playlistId, include_transcription: incl };
            if (langs) body.transcription_languages = langs;
            await dispatchPipelineToIngest(
                "/content/playlist/pipeline", body, status,
            );
        },
    });
}

document.addEventListener("ycs:route", (ev) => {
    if (ev.detail?.mode !== "playlist" || !input) return;
    const items = ev.detail.items || [];
    if (!items.length) return;
    input.value = String(items[0]);
    input.focus();
    if (items.length > 1) {
        setStatus(
            status, "warn",
            `One playlist at a time — kept ${items[0]}, dropped ${items.length - 1} other(s).`,
        );
    }
});
