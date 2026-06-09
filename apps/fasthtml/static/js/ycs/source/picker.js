/* YCS · Source · Shared video picker — single-source enumeration UI.
 *
 * Used by channel.js and playlist.js to render a master+row checkbox
 * table inside the tab's `.ycs-picker` container after the user pastes
 * ONE channel/playlist URL. The user then selects a subset (or all) of
 * the videos and submits via the tab's `Start Ingestion` button, which
 * dispatches the SELECTED video_ids through the existing
 * `/content/videos/pipeline` chain (same pipeline the Videos tab uses).
 *
 * SOTA shape (PatternFly / Helios / Carbon for bulk-select tables,
 * NN/g + UXdivers for pagination-over-infinite-scroll on TASK-style
 * selection, Wipelist for the single-source view + total + filter
 * idiom):
 *   - Header: master checkbox (3-state: unchecked / indeterminate /
 *     checked when ALL loaded items are selected) + title filter input
 *     + total + loaded count
 *   - Body: scrollable list of rows with per-row checkbox + thumbnail +
 *     title + meta (duration · views · channel) + duration. Filter is
 *     client-side on the loaded items.
 *   - Footer: "Load more" button + selection-count chip
 *
 * Public API (called by channel.js / playlist.js):
 *
 *   buildPicker({
 *     rootEl,         // .ycs-picker container element
 *     source,         // "channel" | "playlist"
 *     fetchPage,      // async (offset, limit) → { items, total, has_more,
 *                     //                            title, channel, ... }
 *     submitBtn,      // the form's `Start Ingestion` button (enabled/disabled
 *                     // based on selection size)
 *     onSubmit,       // async (video_ids: string[]) → void — owner POSTs
 *                     // to /content/videos/pipeline and redirects
 *     statusEl,       // optional element to surface load-state text
 *   })
 *
 * Returns { reset(), refetch(), getSelected() }.
 */

const PAGE_LIMIT = 100;  // yt-dlp ~3-6s/page on typical channels

function _htmlEscape(s) {
    return String(s ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

function _fmtCount(n) {
    if (n == null) return "";
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000)     return `${(n / 1_000).toFixed(1)}K`;
    return String(n);
}

function _fmtDuration(v) {
    if (v.duration_string) return v.duration_string;
    const s = v.duration;
    if (!s || s <= 0) return "";
    const total = Math.round(s);
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const sec = total % 60;
    if (h > 0) return `${h}:${String(m).padStart(2,"0")}:${String(sec).padStart(2,"0")}`;
    return `${m}:${String(sec).padStart(2,"0")}`;
}

function _setStatus(node, kind, text) {
    if (!node) return;
    node.className = `ycs-search-status${kind ? ` ${kind}` : ""}`;
    node.textContent = text;
}

/* Build the picker UI inside `rootEl`. Called once after a successful
 * first fetch (the call site renders status-only chrome before the
 * first page lands). Subsequent calls (Load more, filter changes) just
 * mutate the existing DOM in place.
 *
 * `ingestAllBtn` (optional): pre-rendered <button> DOM element that
 * lives in the tab's sticky bottom bar. Picker owns its enabled state
 * + label text:
 *   - Disabled until ≥ 1 item is loaded
 *   - Label: "Ingest all <N>" once total is known, "Ingest all" otherwise,
 *           "Queuing…" while a dispatch is in flight
 * Co-located with `Start Ingestion` so the two-mode choice (selection
 * vs. whole source) is one glance — sticky-bar pattern, not picker-head.
 *
 * `onIngestAll` (optional): `() => Promise<void>` callback fired when
 * the user clicks `ingestAllBtn`. The wirer is responsible for the
 * actual POST to the channel/playlist pipeline endpoint — picker.js
 * just manages the button state + dispatches the click. */
export function buildPicker({
    rootEl, source, fetchPage, submitBtn, onSubmit, statusEl,
    ingestAllBtn, onIngestAll,
}) {
    if (!rootEl) {
        throw new Error("buildPicker: rootEl is required");
    }
    const state = {
        items:    [],       // accumulated VideoSnippets across pages
        selected: new Set(),// video_id set
        title:    null,
        channel:  null,
        total:    null,
        offset:   0,
        hasMore:  false,
        loading:  false,
        filterQ:  "",
        ingestingAll: false,// Ingest-all in flight — disables the btn
    };

    function _filtered() {
        const q = state.filterQ.trim().toLowerCase();
        if (!q) return state.items;
        return state.items.filter((v) =>
            (v.title || "").toLowerCase().includes(q) ||
            (v.channel || "").toLowerCase().includes(q),
        );
    }

    function _renderHead() {
        const totalTxt = state.total != null
            ? `${_fmtCount(state.total)} videos`
            : `${state.items.length} loaded · total ?`;
        const loadedTxt = state.total != null
            ? `${state.items.length} of ${_fmtCount(state.total)} loaded`
            : `${state.items.length} loaded`;
        // The Ingest-all button lives in the sticky bottom bar — driven
        // by `_syncIngestAllBtn()` against the external `ingestAllBtn`
        // element. Keeps the head focused on source identity + counts.
        return `
            <div class="ycs-picker-head">
                <div class="ycs-picker-source">
                    <div class="ycs-picker-title">${_htmlEscape(state.title || "")}</div>
                    <div class="ycs-picker-sub">${_htmlEscape(state.channel || "")}</div>
                </div>
                <div class="ycs-picker-counts">
                    <span class="ycs-picker-total">${totalTxt}</span>
                    <span class="ycs-picker-loaded">${loadedTxt}</span>
                </div>
            </div>
            <div class="ycs-picker-toolbar">
                <label class="ycs-picker-master">
                    <input type="checkbox" class="ycs-picker-master-cb"
                           aria-label="Select all loaded videos">
                    <span>Select all loaded</span>
                </label>
                <input type="search"
                       class="ycs-picker-filter"
                       placeholder="Filter by title or channel…"
                       autocomplete="off">
            </div>
        `;
    }

    function _syncIngestAllBtn() {
        if (!ingestAllBtn) return;
        const hasItems = state.items.length > 0;
        ingestAllBtn.disabled = !hasItems || state.ingestingAll;
        if (state.ingestingAll) {
            ingestAllBtn.textContent = "Queuing all…";
        } else if (state.total != null) {
            ingestAllBtn.textContent = `Ingest all ${_fmtCount(state.total)}`;
        } else {
            ingestAllBtn.textContent = "Ingest all";
        }
    }

    function _renderRow(v) {
        const checked = state.selected.has(v.id) ? "checked" : "";
        const thumb = v.thumbnail
            ? `<img class="ycs-picker-thumb" src="${_htmlEscape(v.thumbnail)}" alt="" loading="lazy">`
            : `<div class="ycs-picker-thumb ycs-picker-thumb-empty"></div>`;
        const meta = [
            v.view_count != null ? `${_fmtCount(v.view_count)} views` : null,
            v.upload_date ? _fmtDate(v.upload_date) : null,
        ].filter(Boolean).join(" · ");
        return `
            <div class="ycs-picker-row" data-video-id="${_htmlEscape(v.id)}">
                <input type="checkbox" class="ycs-picker-cb"
                       ${checked}
                       data-video-id="${_htmlEscape(v.id)}"
                       aria-label="Select ${_htmlEscape(v.title || v.id)}">
                ${thumb}
                <div class="ycs-picker-row-body">
                    <div class="ycs-picker-row-title" title="${_htmlEscape(v.title || v.id)}">
                        ${_htmlEscape(v.title || v.id)}
                    </div>
                    ${meta ? `<div class="ycs-picker-row-meta">${_htmlEscape(meta)}</div>` : ""}
                </div>
                <span class="ycs-picker-row-dur">${_htmlEscape(_fmtDuration(v))}</span>
            </div>
        `;
    }

    function _fmtDate(yyyymmdd) {
        if (!yyyymmdd || !/^\d{8}$/.test(yyyymmdd)) return "";
        return `${yyyymmdd.slice(0,4)}-${yyyymmdd.slice(4,6)}-${yyyymmdd.slice(6,8)}`;
    }

    function _renderFooter() {
        const loadMore = state.hasMore
            ? `<button type="button" class="ycs-picker-loadmore"
                       ${state.loading ? "disabled" : ""}>
                ${state.loading ? "Loading…" : "Load more"}
              </button>`
            : "";
        const sel = state.selected.size;
        return `
            <div class="ycs-picker-footer">
                ${loadMore}
                <div class="ycs-picker-selection">
                    <span class="ycs-picker-sel-count">${sel}</span>
                    <span class="ycs-picker-sel-label">selected</span>
                </div>
            </div>
        `;
    }

    function _renderAll() {
        const filtered = _filtered();
        const rows = filtered.map(_renderRow).join("");
        rootEl.innerHTML = `
            ${_renderHead()}
            <div class="ycs-picker-list" role="list">
                ${rows || `<div class="ycs-picker-empty">No matches — clear the filter to see all.</div>`}
            </div>
            ${_renderFooter()}
        `;
        rootEl.dataset.state = "ready";
        _syncMasterCheckbox(filtered);
        _bindControls();
        _syncIngestAllBtn();
    }

    function _syncMasterCheckbox(filtered) {
        const master = rootEl.querySelector(".ycs-picker-master-cb");
        if (!master) return;
        const visible = filtered;
        if (!visible.length) {
            master.checked = false;
            master.indeterminate = false;
            return;
        }
        const selCount = visible.reduce(
            (n, v) => n + (state.selected.has(v.id) ? 1 : 0), 0,
        );
        if (selCount === 0) {
            master.checked = false;
            master.indeterminate = false;
        } else if (selCount === visible.length) {
            master.checked = true;
            master.indeterminate = false;
        } else {
            // PatternFly / Carbon canonical: indeterminate state when
            // partial selection — visual feedback that "click clears".
            master.checked = false;
            master.indeterminate = true;
        }
    }

    function _bindControls() {
        const master = rootEl.querySelector(".ycs-picker-master-cb");
        master?.addEventListener("change", () => {
            const filtered = _filtered();
            if (master.checked) {
                for (const v of filtered) state.selected.add(v.id);
            } else {
                for (const v of filtered) state.selected.delete(v.id);
            }
            _renderAll();
            _refreshSubmit();
        });
        const filter = rootEl.querySelector(".ycs-picker-filter");
        let filterTimer = null;
        filter?.addEventListener("input", (ev) => {
            clearTimeout(filterTimer);
            filterTimer = setTimeout(() => {
                state.filterQ = ev.target.value;
                _renderAll();
            }, 120);
        });
        // Preserve focus + caret on filter across re-renders by
        // re-focusing after _renderAll when the input was the source.
        if (filter && state.filterQ) {
            filter.value = state.filterQ;
            filter.focus();
            const len = filter.value.length;
            try { filter.setSelectionRange(len, len); } catch (_) { /* */ }
        }
        rootEl.querySelectorAll(".ycs-picker-cb").forEach((cb) => {
            cb.addEventListener("change", () => {
                const vid = cb.dataset.videoId;
                if (cb.checked) state.selected.add(vid);
                else            state.selected.delete(vid);
                _syncMasterCheckbox(_filtered());
                _renderFooterOnly();
                _refreshSubmit();
            });
        });
        const loadMoreBtn = rootEl.querySelector(".ycs-picker-loadmore");
        loadMoreBtn?.addEventListener("click", () => loadMore());
    }

    /* External sticky-bar Ingest-all button: click handler bound ONCE
     * (the button isn't re-rendered between fetches; only its
     * disabled+text state changes via _syncIngestAllBtn). */
    if (ingestAllBtn && onIngestAll) {
        ingestAllBtn.addEventListener("click", async () => {
            if (state.ingestingAll || ingestAllBtn.disabled) return;
            state.ingestingAll = true;
            _syncIngestAllBtn();
            try {
                await onIngestAll();
            } finally {
                // If onIngestAll redirected, this never runs; on
                // failure (status surfaced in statusEl) we re-enable
                // the button so the user can retry.
                state.ingestingAll = false;
                _syncIngestAllBtn();
            }
        });
    }

    function _renderFooterOnly() {
        const footer = rootEl.querySelector(".ycs-picker-footer");
        if (!footer) return;
        const tmp = document.createElement("div");
        tmp.innerHTML = _renderFooter();
        footer.replaceWith(tmp.firstElementChild);
        rootEl.querySelector(".ycs-picker-loadmore")
            ?.addEventListener("click", () => loadMore());
    }

    function _refreshSubmit() {
        if (!submitBtn) return;
        submitBtn.disabled = state.selected.size < 1;
        const sel = state.selected.size;
        // Surface the count in the button label so the user sees the
        // exact unit-of-work BEFORE they click.
        submitBtn.textContent = sel > 0
            ? `Start Ingestion (${_fmtCount(sel)} video${sel === 1 ? "" : "s"})`
            : "Start Ingestion";
    }

    async function _loadPage(offset) {
        state.loading = true;
        _setStatus(statusEl, "running", `Loading videos ${offset + 1}–${offset + PAGE_LIMIT}…`);
        try {
            const r = await fetchPage(offset, PAGE_LIMIT);
            state.items.push(...(r.items || []));
            state.offset  = offset + (r.items?.length ?? 0);
            state.total   = r.total ?? state.total;
            state.title   = r.title ?? state.title;
            state.channel = r.channel ?? state.channel;
            state.hasMore = !!r.has_more;
            _setStatus(statusEl, "ok",
                state.total != null
                    ? `Loaded ${state.items.length} of ${_fmtCount(state.total)} videos.`
                    : `Loaded ${state.items.length} videos.`,
            );
        } catch (e) {
            _setStatus(statusEl, "error", `Fetch failed: ${e.message ?? e}`);
            throw e;
        } finally {
            state.loading = false;
        }
    }

    async function loadMore() {
        if (state.loading || !state.hasMore) return;
        await _loadPage(state.offset);
        _renderAll();
        _refreshSubmit();
    }

    async function refetch() {
        state.items = [];
        state.selected.clear();
        state.offset = 0;
        state.total = null;
        state.title = null;
        state.channel = null;
        state.hasMore = false;
        state.filterQ = "";
        await _loadPage(0);
        _renderAll();
        _refreshSubmit();
    }

    function reset() {
        state.items = [];
        state.selected.clear();
        state.offset = 0;
        state.total = null;
        state.title = null;
        state.channel = null;
        state.hasMore = false;
        state.filterQ = "";
        rootEl.innerHTML = "";
        rootEl.dataset.state = "empty";
        _refreshSubmit();
        _syncIngestAllBtn();
    }

    function getSelected() {
        return [...state.selected];
    }

    return { refetch, reset, getSelected, loadMore };
}

/* Convenience wiring used by channel.js / playlist.js / videos.js —
 * all three call this with their own DOM ids + (Videos only) an
 * override that translates the textarea's paste-many shape into the
 * preview endpoint's `?ids=` query. Keeps the per-tab JS modules
 * tiny: paste URL/IDs → fetch first page → render → on submit, POST
 * the selection.
 *
 * `buildQuery` (optional): `(inputValue, offset, limit) → string`
 * returning the URL query portion AFTER `?`. Defaults to
 * `id=${inputValue}&offset=…&limit=…` (channel/playlist shape).
 * Videos overrides with `ids=…` (comma-joined parsed IDs).
 *
 * `validateInput` (optional): `(inputValue) → string|null` returning
 * an error message if the input is rejectable, or null/undef if it's
 * fine. Videos overrides to require ≥ 1 parseable ID. */
export function wirePickerTab({
    source,                // "channel" | "playlist" | "videos"
    inputEl,               // <input>/<textarea> with the URL or pasted IDs
    fetchBtn,              // <button type="submit"> "Fetch videos"
    pickerRootEl,          // .ycs-picker container
    submitBtn,             // the form's `Start Ingestion` button
    ingestAllBtn,          // optional: sticky-bar `Ingest all` button element
    statusEl,              // status div
    formEl,                // <form> wrapper
    apiEnumerateBase,      // e.g. "/api/v1/ycs/content/channel/videos"
    dispatchPipeline,      // (video_ids, include_transcription, languages) → void
    dispatchIngestAll,     // optional: () → Promise<void> for Ingest-all button
    buildQuery,            // optional per-tab query builder
    validateInput,         // optional per-tab input validator
}) {
    const _buildQuery = buildQuery || ((value, offset, limit) =>
        `id=${encodeURIComponent(value)}&offset=${offset}&limit=${limit}`
    );
    const picker = buildPicker({
        rootEl: pickerRootEl,
        source,
        statusEl,
        submitBtn,
        ingestAllBtn,
        onIngestAll: dispatchIngestAll,
        async fetchPage(offset, limit) {
            const value = (inputEl?.value ?? "").trim();
            const url = `${apiEnumerateBase}?${_buildQuery(value, offset, limit)}`;
            const r = await fetch(url);
            let data = null;
            try { data = await r.json(); } catch (_) { /* */ }
            if (!r.ok) {
                const msg = (data && (data.detail ?? data.message)) || r.statusText;
                throw new Error(typeof msg === "string" ? msg : "request failed");
            }
            return data;
        },
        async onSubmit(_ids) { /* handled by form submit below */ },
    });

    formEl?.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        const submitterId = ev.submitter?.id;
        if (submitterId === submitBtn?.id) {
            // The bottom Start-ingest button — fan out to the videos
            // pipeline with the selection. The picker enforces ≥1 via
            // submitBtn.disabled, so this should always have items.
            const ids = picker.getSelected();
            if (!ids.length) return;
            await dispatchPipeline(ids);
            return;
        }
        // Anything else = the top Fetch button (or Enter on input).
        const v = (inputEl?.value ?? "").trim();
        const validationErr = validateInput ? validateInput(v) : null;
        if (validationErr) {
            _setStatus(statusEl, "error", validationErr);
            return;
        }
        if (!v) {
            _setStatus(statusEl, "error", "Paste a channel/playlist URL or ID.");
            return;
        }
        try {
            await picker.refetch();
        } catch (_) { /* status set inside refetch */ }
    });

    return picker;
}
