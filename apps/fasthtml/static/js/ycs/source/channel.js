/* Source · Channel mode (June 2026 redesign) — paste ONE channel,
 * browse + pick a subset of its videos, dispatch SELECTED video_ids
 * through `/content/videos/pipeline` (same chain the Videos tab uses).
 *
 * Replaces the paste-many textarea. The smallest unit of work is a
 * video; the new shape forces the user to be explicit about which
 * videos to queue, instead of accidentally enqueuing thousands across
 * multiple channels. See features/ycs/source/channel.py for the DOM
 * shape and picker.js for the shared table/selection module. */
import { dispatchPipelineToIngest, parseLangs, setStatus } from "./shared.js";
import { wirePickerTab } from "./picker.js";

const form         = document.getElementById("ycs-channel-form");
const status       = document.getElementById("ycs-channel-status");
const submit       = document.getElementById("ycs-channel-submit");
const fetchBtn     = document.getElementById("ycs-channel-fetch");
const input        = document.getElementById("ycs-channel-input");
const picker       = document.getElementById("ycs-channel-picker");
const ingestAllBtn = document.getElementById("ycs-channel-ingest-all");

if (form && input && picker) {
    wirePickerTab({
        source:           "channel",
        inputEl:          input,
        fetchBtn,
        pickerRootEl:     picker,
        submitBtn:        submit,
        ingestAllBtn,
        statusEl:         status,
        formEl:           form,
        apiEnumerateBase: "/api/v1/ycs/content/channel/videos",
        async dispatchPipeline(ids) {
            const incl = document.getElementById("ycs-channel-incl-trans")?.checked ?? true;
            const langs = parseLangs(document.getElementById("ycs-channel-langs")?.value);
            const body = { video_ids: ids, include_transcription: incl };
            if (langs) body.transcription_languages = langs;
            await dispatchPipelineToIngest(
                "/content/videos/pipeline", body, status,
            );
        },
        /* Ingest-all: bypass the 100-per-page picker cap. Server walks
         * the entire channel via `--print "%(id)s"` (one yt-dlp call,
         * no client-side pagination loop) and dispatches the 3-phase
         * pipeline against every video_id. */
        async dispatchIngestAll() {
            const channelId = (input.value ?? "").trim();
            if (!channelId) {
                setStatus(status, "error", "Paste a channel URL/handle first.");
                return;
            }
            const incl = document.getElementById("ycs-channel-incl-trans")?.checked ?? true;
            const langs = parseLangs(document.getElementById("ycs-channel-langs")?.value);
            const body = { channel_id: channelId, include_transcription: incl };
            if (langs) body.transcription_languages = langs;
            await dispatchPipelineToIngest(
                "/content/channel/pipeline", body, status,
            );
        },
    });
}

/* Search-tab routing: previously appended channel IDs to a textarea.
 * The single-source rule means we accept ONLY the first routed channel
 * and surface a warn-status if more were sent. */
document.addEventListener("ycs:route", (ev) => {
    if (ev.detail?.mode !== "channel" || !input) return;
    const items = ev.detail.items || [];
    if (!items.length) return;
    input.value = String(items[0]);
    input.focus();
    if (items.length > 1) {
        setStatus(
            status, "warn",
            `One channel at a time — kept ${items[0]}, dropped ${items.length - 1} other(s).`,
        );
    }
});
