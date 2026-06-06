/* Source · tab switching + filter expand/collapse + smart-paste.
 *
 * SOTA pattern (NN/g + Primer + Mobbin, June 2026 review): segmented
 * pills in row-3 — DETECT mode-mismatched paste content, SUGGEST a
 * switch via inline chip, NEVER auto-route. Tube Archivist #299
 * documents why silent auto-detection erodes user trust.
 *
 * The chip:
 *   - appears inline below the input on paste
 *   - one click switches the active mode + injects the content into
 *     the destination input
 *   - one click on the × dismisses it
 *
 * Form values persist across tab switches because panels stay in the
 * DOM (only `display: none` toggled), so no per-mode state cache needed.
 */
import { detectMode } from "./parsers.js";

const tabs = document.querySelectorAll("[data-mode]");
const panels = document.querySelectorAll(".ycs-tab-body");

function activateMode(mode) {
    tabs.forEach((t) => t.classList.toggle("active", t.dataset.mode === mode));
    panels.forEach((p) => p.classList.toggle("active", p.id === `ycs-tab-${mode}`));
}

tabs.forEach((tab) => {
    tab.addEventListener("click", () => activateMode(tab.dataset.mode));
});

// The old Filters expand/collapse handler was removed when the
// `_SearchFiltersPanel` block became `_FilterChipBar` (Linear-style
// inline chips) — chip add/remove logic lives in search.js now.

// ---- Smart-paste detection ------------------------------------------------
function destInputFor(mode) {
    return document.getElementById({
        videos:   "ycs-videos-input",
        channel:  "ycs-channel-id",
        playlist: "ycs-playlist-id",
    }[mode]);
}

function showSuggestionChip(input, currentMode, suggestedMode, content) {
    // One chip per input — replace any previous suggestion.
    input.parentElement?.querySelector(".ycs-paste-chip")?.remove();
    const chip = document.createElement("div");
    chip.className = "ycs-paste-chip";
    chip.innerHTML = `
        <span class="ycs-paste-chip-text">
            Looks like a <strong>${suggestedMode}</strong>${suggestedMode === "videos" ? " URL" : ""}
            — switch?
        </span>
        <button type="button" class="ycs-paste-chip-accept">Switch</button>
        <button type="button" class="ycs-paste-chip-dismiss" aria-label="Dismiss">×</button>
    `;
    chip.querySelector(".ycs-paste-chip-accept").addEventListener("click", () => {
        activateMode(suggestedMode);
        const dest = destInputFor(suggestedMode);
        if (dest) {
            dest.value = content;
            dest.focus();
        }
        chip.remove();
    });
    chip.querySelector(".ycs-paste-chip-dismiss").addEventListener("click", () => {
        chip.remove();
    });
    input.insertAdjacentElement("afterend", chip);
}

// Attach paste detection to the 3 mode inputs. The Search query input is
// excluded — its content is a search string, not a URL.
const SMART_PASTE_INPUTS = [
    { id: "ycs-videos-input",  mode: "videos" },
    { id: "ycs-channel-id",    mode: "channel" },
    { id: "ycs-playlist-id",   mode: "playlist" },
];
for (const { id, mode } of SMART_PASTE_INPUTS) {
    const el = document.getElementById(id);
    if (!el) continue;
    el.addEventListener("paste", (e) => {
        const text = (e.clipboardData || window.clipboardData)?.getData("text");
        const detected = detectMode(text);
        if (!detected || detected === mode) return;
        // Defer chip render until after the paste lands so the user sees
        // their content first, then the suggestion.
        setTimeout(() => showSuggestionChip(el, mode, detected, text.trim()), 0);
    });
}
