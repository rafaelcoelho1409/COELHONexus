/* Source · client-side URL-parse preview ("unfurl") wiring shared by
 * channel.js + playlist.js.
 *
 * SOTA pattern (Slack / Notion / Linear, June 2026 research):
 * paste URL → REPLACE the input in-place with a compact "claim" card
 * carrying the parsed identity, gate submit on success. Replacement
 * (not adjacency) is what makes the resolved state feel committed
 * rather than tentative. × on the card resets back to the input.
 *
 * `data-state` on the preview wrapper drives CSS + visibility:
 *   hidden  — empty input, nothing rendered, input visible
 *   parsed  — URL matched, card shown, INPUT HIDDEN, submit enabled
 *   invalid — non-empty input didn't match, card shown next to input,
 *             submit disabled
 */

function _setPreview(previewEl, state, parsed, raw, onReset) {
    previewEl.dataset.state = state;
    if (state === "hidden") { previewEl.innerHTML = ""; return; }
    if (state === "parsed") {
        const labels = { channel: "Channel", playlist: "Playlist", video: "Video" };
        previewEl.innerHTML = `
            <span class="ycs-url-preview-icon" aria-hidden="true">✓</span>
            <div class="ycs-url-preview-body">
                <span class="ycs-url-preview-kind">${labels[parsed.kind] || parsed.kind}</span>
                <span class="ycs-url-preview-id">${parsed.display}</span>
            </div>
            <span class="ycs-url-preview-hint">Metadata fetches on submit</span>
            <button type="button" class="ycs-url-preview-reset" aria-label="Clear" title="Clear">×</button>
        `;
        previewEl.querySelector(".ycs-url-preview-reset")
            ?.addEventListener("click", onReset);
        return;
    }
    if (state === "invalid") {
        previewEl.innerHTML = `
            <span class="ycs-url-preview-icon" aria-hidden="true">!</span>
            <div class="ycs-url-preview-body">
                <span class="ycs-url-preview-kind">Unrecognized</span>
                <span class="ycs-url-preview-id">${(raw ?? "").slice(0, 80)}</span>
            </div>
            <span class="ycs-url-preview-hint">Doesn't look like a YouTube URL or ID</span>
        `;
    }
}

/* Bind paste + input + blur on `input` → call `parser(text)`. When the
 * parser returns a non-null object we mark previewEl as `parsed`,
 * HIDE the input (toggle `.ycs-url-input-hidden`), and enable
 * submitBtn. Empty input → `hidden`, input visible. Non-empty +
 * unparsed → `invalid`, input still visible, submit disabled. */
export function bindUrlParser({ input, previewEl, submitBtn, parser }) {
    if (!input || !previewEl) return;
    const reset = () => {
        input.value = "";
        input.classList.remove("ycs-url-input-hidden");
        previewEl.dataset.state = "hidden";
        previewEl.innerHTML = "";
        if (submitBtn) submitBtn.disabled = true;
        input.focus();
    };
    const refresh = () => {
        const raw = input.value;
        if (!raw || !raw.trim()) {
            input.classList.remove("ycs-url-input-hidden");
            _setPreview(previewEl, "hidden");
            if (submitBtn) submitBtn.disabled = true;
            return;
        }
        const parsed = parser(raw);
        if (parsed) {
            input.classList.add("ycs-url-input-hidden");
            _setPreview(previewEl, "parsed", parsed, null, reset);
            if (submitBtn) submitBtn.disabled = false;
        } else {
            input.classList.remove("ycs-url-input-hidden");
            _setPreview(previewEl, "invalid", null, raw, reset);
            if (submitBtn) submitBtn.disabled = true;
        }
    };
    input.addEventListener("input", refresh);
    input.addEventListener("blur", refresh);
    input.addEventListener("paste", () => setTimeout(refresh, 0));
    refresh();
    // Expose reset so other modules can request a fresh state (e.g. when
    // channel.js loads a new item from its queue strip).
    return { reset, refresh };
}
