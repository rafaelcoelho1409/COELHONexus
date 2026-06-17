/* Source · Search mode — sync yt-dlp metadata browse.
 *
 * Two subsystems live here:
 *   1. Compact stacked results list (NN/g "list entry" pattern)
 *   2. Density toggle (Compact / Comfortable)
 *
 * Filters now live as native HTML form controls in the always-visible
 * filter grid (see `features/ycs/source/search.py::_FilterGrid`).
 * Their `name=` attributes match the backend `SearchRequest` schema
 * verbatim, so the FormData → SearchRequest serialization in
 * `readSearchRequest()` below picks them up with no extra wiring.
 *
 * 2026-06-17 — replaced the prior toggle-dropdown chip-bar pattern
 * with the always-visible faceted grid. The dropdown's JS click
 * handlers were unreliable under strict-shield browsers (Brave
 * Shields, hardened Chromium derivatives) — making the filter button
 * a single point of failure. Native form controls bypass every click-
 * handler failure mode because the browser handles the interaction
 * directly.
 */
import { API, fmtCount, fmtDate } from "./shared.js";

// ============================================================
// Date format bridge
// ============================================================
// Backend accepts YYYYMMDD (8 digits); native <input type="date">
// emits YYYY-MM-DD. Normalised at submit time in readSearchRequest.
function _isoToYyyymmdd(v) {
    return (v || "").replace(/-/g, "").slice(0, 8);
}

// Date-typed FormData entries (matches the input `name=` attributes
// in `features/ycs/source/search.py::_FilterGrid`). Normalised by
// `readSearchRequest()` below.
const _DATE_FIELDS = new Set(["date_after", "date_before"]);

// ============================================================
// Density toggle
// ============================================================

const densityBtns = document.querySelectorAll(".ycs-density-btn");
const resultsEl = document.getElementById("ycs-search-results");
const _DENSITY_KEY = "ycs:results:density";

function _applyDensity(d) {
    if (resultsEl) resultsEl.dataset.density = d;
    densityBtns.forEach((b) => b.classList.toggle("active", b.dataset.density === d));
    try { localStorage.setItem(_DENSITY_KEY, d); } catch (_) {}
}

(() => {
    let d = "compact";
    try { d = localStorage.getItem(_DENSITY_KEY) || "compact"; } catch (_) {}
    _applyDensity(d);
})();

densityBtns.forEach((b) => {
    b.addEventListener("click", () => _applyDensity(b.dataset.density));
});

// ============================================================
// Page size (results per search) — replaces the old max_results
// number input. Mirrors its value to the hidden #ycs-fh-max_results
// so the existing FormData → SearchRequest serialization works
// unchanged. Persisted in localStorage.
// ============================================================

const pageSizeSel = document.getElementById("ycs-page-size");
const pageSizeHidden = document.getElementById("ycs-fh-max_results");
const _PAGE_SIZE_KEY = "ycs:results:page_size";

function _applyPageSize(v) {
    if (pageSizeHidden) pageSizeHidden.value = v;
    if (pageSizeSel && pageSizeSel.value !== v) pageSizeSel.value = v;
    try { localStorage.setItem(_PAGE_SIZE_KEY, v); } catch (_) {}
}

(() => {
    let v = "25";
    try { v = localStorage.getItem(_PAGE_SIZE_KEY) || "25"; } catch (_) {}
    _applyPageSize(v);
})();

/* Page-size change auto-refetches if a search is live. Skips when no
 * search has been submitted yet (avoid empty-query firing) or when a
 * fetch is already in flight (debounce-via-guard). Always resets to
 * page 1 — a smaller size on page 4 of 200 would silently move the
 * user. */
pageSizeSel?.addEventListener("change", async () => {
    const v = pageSizeSel.value;
    _applyPageSize(v);
    if (!_state?.baseReq || _state.inflight) return;
    _state.size = parseInt(v, 10) || 25;
    _state.page = 1;
    _state.cache = [];
    _state.exhausted = false;
    const ok = await _fetchAtLeast(_state.size);
    if (ok) _renderPage();
});

// ============================================================
// Search form submission + results render
// ============================================================

const searchForm = document.getElementById("ycs-search-form");
const paginationEl = document.getElementById("ycs-pagination");
const paginationRangeEl = document.getElementById("ycs-pagination-range");
const prevBtn = document.getElementById("ycs-pagination-prev");
const nextBtn = document.getElementById("ycs-pagination-next");

/* The inline pagination's middle slot doubles as the status line — one
 * element, multiple states. `data-state` on the wrapper drives CSS;
 * `textContent` carries the message. */
function _setStatus(state, msg) {
    paginationEl.dataset.state = state;
    paginationRangeEl.textContent = msg ?? "";
    if (state !== "visible") {
        if (prevBtn) prevBtn.disabled = true;
        if (nextBtn) nextBtn.disabled = true;
    }
}

const NUMERIC_FIELDS = new Set([
    "max_results",
    "duration_min", "duration_max",
    "min_views", "max_views", "min_likes",
    "age_limit",
]);

/* Whitelist of fields the backend's `SearchRequest` accepts. FastHTML's
 * `auto_name` quietly sets `name = id` on form-associated elements
 * (Select / Input / Textarea) when `name` isn't explicit — so the
 * page-size <select id="ycs-page-size"> ends up with name
 * "ycs-page-size" and FormData includes it. The SearchRequest model
 * has `extra = "forbid"` and 422s any unknown field. Whitelisting
 * here is bulletproof — anything not on this list is dropped. */
const KNOWN_FIELDS = new Set([
    "query", "max_results",
    "sort_by_date", "exclude_shorts",
    "kind_filter",
    "duration", "duration_min", "duration_max",
    "date_after", "date_before",
    "min_views", "max_views", "min_likes",
    "is_live", "live_status", "availability", "age_limit",
    "title_contains", "description_contains", "channel_name",
]);
const TOGGLE_FIELDS = new Set(["sort_by_date", "exclude_shorts"]);

function readSearchRequest() {
    const fd = new FormData(searchForm);
    const req = {};
    for (const [k, v] of fd.entries()) {
        if (!KNOWN_FIELDS.has(k)) continue;
        if (v === "" || v === null) continue;
        if (TOGGLE_FIELDS.has(k)) { req[k] = true; continue; }
        if (NUMERIC_FIELDS.has(k)) {
            const n = parseInt(v, 10);
            if (Number.isFinite(n)) req[k] = n;
            continue;
        }
        if (_DATE_FIELDS.has(k)) {
            // <input type="date"> emits YYYY-MM-DD; backend wants
            // YYYYMMDD. Skip the field entirely when the user left
            // it blank (the empty-string short-circuit above already
            // handles "", but a partial like "2024" would fall
            // through and 422 the backend — so we re-validate here).
            const yyyymmdd = _isoToYyyymmdd(v);
            if (yyyymmdd.length === 8) req[k] = yyyymmdd;
            continue;
        }
        req[k] = v;
    }
    return req;
}

// Per-video duration sits in the meta line of each result row
// (`v.duration_string` is rendered first, before views + channel +
// date). No cross-page total — that lived briefly in the status line
// but was removed because it was answering the wrong question (the
// user wanted per-row duration, not a sum).

/* Best-effort duration string. yt-dlp's `duration_string` is missing
 * for live / premiere entries; fall back to formatting raw seconds. */
function _durationStr(v) {
    if (v.duration_string) return v.duration_string;
    const s = v.duration;
    if (!s || s <= 0) return null;
    const total = Math.round(s);
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const sec = total % 60;
    if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
    return `${m}:${String(sec).padStart(2, "0")}`;
}

/* Best-effort release-date string. yt-dlp's `upload_date` is the
 * canonical source for flat-playlist + approximate_date; falls back
 * to release_timestamp / timestamp (unix epoch seconds). Handles
 * partial YYYYMMDD (e.g. `20240900` from approximate dates).
 *
 * Formats relative ("3 weeks ago") for the last 30 days, then
 * absolute ("Apr 2024" / "2024"). YouTube's native convention. */
const _MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
function _ago(diffMs) {
    const s = Math.max(0, Math.round(diffMs / 1000));
    if (s < 60)         return "just now";
    const m = Math.floor(s / 60);
    if (m < 60)         return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24)         return `${h}h ago`;
    const d = Math.floor(h / 24);
    if (d < 30)         return d === 1 ? "1 day ago" : `${d} days ago`;
    return null;   // ≥30 days → caller renders absolute
}
function _absDate(y, mo, da) {
    if (!y) return null;
    if (!mo || mo < 1) return String(y);
    const monthLabel = _MONTHS[mo - 1] || "";
    if (!da || da < 1) return `${monthLabel} ${y}`;
    return `${monthLabel} ${da}, ${y}`;
}
function _dateStr(v) {
    // 1. Try YYYYMMDD `upload_date` (with partial-date support for
    //    approximate dates: "20240900" → September 2024, "20240000" → 2024).
    if (v.upload_date && /^\d{8}$/.test(v.upload_date)) {
        const y  = parseInt(v.upload_date.slice(0, 4), 10);
        const mo = parseInt(v.upload_date.slice(4, 6), 10);
        const da = parseInt(v.upload_date.slice(6, 8), 10);
        const now = new Date();
        if (mo && da) {
            const dt = new Date(Date.UTC(y, mo - 1, da));
            const rel = _ago(now - dt);
            if (rel) return rel;
        }
        return _absDate(y, mo, da);
    }
    // 2. Try unix epoch seconds.
    const ts = v.release_timestamp ?? v.timestamp;
    if (typeof ts === "number" && ts > 0) {
        const dt = new Date(ts * 1000);
        const rel = _ago(Date.now() - dt);
        if (rel) return rel;
        return _absDate(dt.getUTCFullYear(), dt.getUTCMonth() + 1, dt.getUTCDate());
    }
    return null;
}

function renderResult(v) {
    const kind = v.kind || "video";
    const card = document.createElement("div");
    card.className = "ycs-result";
    card.dataset.kind = kind;
    // Per-kind meta line — videos show views/duration/channel/date.
    // For channel/playlist rows, the backend probes a cheap
    // `--playlist-items 1` per result and surfaces `video_count`
    // (None on probe failure → falls back to bare "Channel"/"Playlist").
    let meta;
    if (kind === "video") {
        meta = [
            v.view_count != null ? `${fmtCount(v.view_count)} views` : null,
            _durationStr(v),
            v.channel,
            _dateStr(v),
        ].filter(Boolean).join(" · ");
    } else {
        const countPart = v.video_count != null
            ? `${fmtCount(v.video_count)} video${v.video_count === 1 ? "" : "s"}`
            : null;
        const labelPart = kind === "channel" ? "Channel" : "Playlist";
        meta = [labelPart, countPart].filter(Boolean).join(" · ");
    }
    const thumb = v.thumbnail
        ? `<img src="${v.thumbnail}" alt="" loading="lazy">` : "";
    const checked = _isSelected(v.id) ? "checked" : "";
    // Per the user's spec: badge EVERY result (Video / Channel / Playlist)
    // so the kind is always glanceable, not just the non-default ones.
    const kindBadge = `<span class="ycs-result-kind" data-kind="${kind}">${kind}</span>`;
    card.innerHTML = `
        <input type="checkbox" class="ycs-result-check" data-id="${v.id}" ${checked}
               title="Select (Shift+click for range)">
        <a class="ycs-result-thumb" href="${v.url}" target="_blank" rel="noopener">
            ${thumb}
        </a>
        <div class="ycs-result-body">
            <div class="ycs-result-title-row">
                ${kindBadge}
                <a class="ycs-result-title" href="${v.url}" target="_blank" rel="noopener">${v.title ?? "(no title)"}</a>
            </div>
            <div class="ycs-result-meta">${meta}</div>
            ${v.description ? `<div class="ycs-result-desc">${v.description}</div>` : ""}
        </div>
        <div class="ycs-result-actions">
            <button type="button" class="ycs-result-action" data-action="copy-id" title="Copy ID">${v.id}</button>
        </div>
    `;
    card.querySelector(".ycs-result-check")
        ?.addEventListener("click", (ev) => _onCheckClick(ev, v));
    card.querySelector(".ycs-result-action[data-action='copy-id']")
        ?.addEventListener("click", () => {
            navigator.clipboard?.writeText(v.id);
        });
    return card;
}

/* Master-checkbox row — Gmail/Linear/GitHub Issues idiom. Sits at the
 * top of the result list (sticky) so its checkbox lines up with the
 * per-row checkboxes below it. State is tri-modal:
 *   unchecked      — no visible row is selected
 *   indeterminate  — some but not all visible rows are selected
 *   checked        — every visible row is selected
 * Click semantics match Gmail: when partial OR none, click selects
 * all visible; when fully selected, click clears all visible. */
function _buildMasterRow() {
    const row = document.createElement("div");
    row.className = "ycs-search-masterrow";
    row.innerHTML = `
        <input type="checkbox" class="ycs-search-master-cb"
               aria-label="Select all visible results">
        <span class="ycs-search-master-label">Select all</span>
    `;
    row.addEventListener("click", (ev) => {
        // Whole-row clicks toggle too — but ignore the click that
        // bubbled from the checkbox itself so we don't double-fire.
        if (ev.target.classList.contains("ycs-search-master-cb")) return;
        _toggleSelectAll();
    });
    row.querySelector(".ycs-search-master-cb")
        .addEventListener("change", () => _toggleSelectAll());
    return row;
}

function renderResults(results) {
    if (!results?.length) {
        resultsEl.innerHTML = `<div class="ycs-search-empty">No matches. Loosen the filters and try again.</div>`;
        return;
    }
    const frag = document.createDocumentFragment();
    frag.appendChild(_buildMasterRow());
    for (const v of results) frag.appendChild(renderResult(v));
    resultsEl.replaceChildren(frag);
}

// ============================================================
// Pagination — client-side cache + slice.
//
// yt-dlp doesn't support offset; the backend takes `max_results` and
// returns top-N. To page forward we re-fetch with a larger limit and
// cache. Prev reads from the cache — no network round trip. When the
// server returns fewer items than asked for, we've reached the end of
// the result set and disable Next.
// ============================================================

let _state = {
    baseReq: null,    // last submitted form payload (without max_results)
    cache:   [],      // all results fetched so far
    page:    1,
    size:    25,
    exhausted: false, // server gave back < requested → no more results
    inflight: false,
};

function _currentSlice() {
    const start = (_state.page - 1) * _state.size;
    return _state.cache.slice(start, start + _state.size);
}

function _renderPage() {
    const slice = _currentSlice();
    renderResults(slice);
    // Keep the Select-all toggle's enabled/label state in sync with
    // the page slice. Runs even on the empty path so the button
    // returns to "Select all" + disabled when results clear.
    _syncSelectAllBtn();
    const total = _state.cache.length;
    if (total === 0) {
        _setStatus("empty", "No results.");
        return;
    }
    const from = (_state.page - 1) * _state.size + 1;
    const to   = Math.min(_state.page * _state.size, total);
    const suffix = _state.exhausted ? `of ${total}` : `of ${total}+`;
    _setStatus("visible", `${from}–${to} ${suffix}`);
    // Enable Prev when not on page 1; Next when we have more cached
    // beyond the current slice OR the source can still grow.
    prevBtn.disabled = _state.page <= 1;
    nextBtn.disabled = _state.exhausted && to >= total;
}

async function _fetchAtLeast(needed) {
    /* Re-fetch with `max_results = needed` so the server delivers the
     * cumulative range covering pages 1..currentPage. yt-dlp re-runs
     * from scratch every call — wasteful but no backend change. */
    const req = { ..._state.baseReq, max_results: needed };
    _state.inflight = true;
    _setStatus("running", `Loading page ${_state.page}…`);
    try {
        const r = await fetch(API + "/content/search", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify(req),
        });
        if (!r.ok) {
            const txt = await r.text();
            _setStatus("error", `Failed (${r.status}): ${txt.slice(0, 60)}`);
            return false;
        }
        const data = await r.json();
        _state.cache = data.results || [];
        _state.exhausted = _state.cache.length < needed;
        return true;
    } catch (e) {
        _setStatus("error", `Network error: ${e}`);
        return false;
    } finally {
        _state.inflight = false;
    }
}

searchForm?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    /* 2026-06-17 — collapse the filter `<details>` panel on every
     * submit so the user returns to a clean baseline after each
     * search. Setting `open = false` is the native HTMLDetailsElement
     * API — no animation, no JS click handler, browser handles the
     * collapse directly. */
    const filterDetails = document.getElementById("ycs-filter-details");
    if (filterDetails) filterDetails.open = false;
    const req = readSearchRequest();
    const size = req.max_results || 25;
    // baseReq is the form payload minus the moving `max_results`; we
    // override that per-page in `_fetchAtLeast`.
    const { max_results, ...rest } = req;
    _state = {
        baseReq:   rest,
        cache:     [],
        page:      1,
        size,
        exhausted: false,
        inflight:  false,
    };
    resultsEl.innerHTML = "";
    _setStatus("running", `Searching: ${req.query}…`);
    const ok = await _fetchAtLeast(size);
    if (ok) _renderPage();
});

prevBtn?.addEventListener("click", () => {
    if (_state.inflight || _state.page <= 1) return;
    _state.page -= 1;
    _renderPage();
});

nextBtn?.addEventListener("click", async () => {
    if (_state.inflight) return;
    const nextPage = _state.page + 1;
    const needed = nextPage * _state.size;
    if (_state.cache.length < needed && !_state.exhausted) {
        const ok = await _fetchAtLeast(needed);
        if (!ok) return;
    }
    // If we already have enough cached OR the source is exhausted but
    // there are still cached items in the next slice, advance.
    if ((_state.page) * _state.size < _state.cache.length) {
        _state.page = nextPage;
        _renderPage();
    } else {
        _renderPage();   // refreshes status text + button states
    }
});

// ============================================================
// Multi-select + bulk routing — Gmail / Linear / GitHub Issues idiom.
//
// User checks rows in the result list, then a floating capsule
// appears at the bottom with "N selected · → Videos · → Channels ·
// Clear". Routing dispatches a `ycs:route` CustomEvent and switches
// to the target tab; the tab modules (videos.js, channel.js) listen
// for the event and absorb the items.
//
// Per-tab routing:
//   videos:  `items` = list of video IDs → appended to the textarea
//   channel: `items` = list of unique @handle/UC channels → queue strip
// (Playlist is omitted — search results don't surface playlist IDs.)
// ============================================================

const _selected = new Map();   // id → snippet
let _lastChecked = null;
let _bulkBarEl = null;

function _isSelected(id) { return _selected.has(id); }

/* Kinds that are MUTUALLY EXCLUSIVE within their own kind — selecting
 * a second channel deselects the first; same for playlists. The
 * Channel and Playlist tabs each take ONE source at a time (the picker
 * UI on those tabs is built around a single channel/playlist's video
 * list), so allowing the user to "select 3 channels" on Search would
 * just mislead them — only the first would route through. Videos are
 * unaffected (multi-video selection is genuine multi-batch dispatch). */
const _SINGLETON_KINDS = new Set(["channel", "playlist"]);

function _select(id, snippet, on) {
    if (on) {
        const k = snippet?.kind || "video";
        if (_SINGLETON_KINDS.has(k)) {
            // Deselect any other already-selected entry of the SAME
            // kind. Iterate over a snapshot so the in-place delete +
            // DOM uncheck don't fight the iterator.
            for (const [otherId, otherSnip] of Array.from(_selected.entries())) {
                if (otherId === id) continue;
                if ((otherSnip?.kind || "video") !== k) continue;
                _selected.delete(otherId);
                const cb = resultsEl.querySelector(
                    `.ycs-result-check[data-id="${otherId}"]`,
                );
                if (cb) cb.checked = false;
            }
        }
        _selected.set(id, snippet);
    } else {
        _selected.delete(id);
    }
    resultsEl.classList.toggle("has-selection", _selected.size > 0);
    _renderBulkBar();
}

function _clearSelection() {
    _selected.clear();
    _lastChecked = null;
    resultsEl.classList.remove("has-selection");
    resultsEl.querySelectorAll(".ycs-result-check").forEach((cb) => { cb.checked = false; });
    _renderBulkBar();
}

/* Build the route payload for a given mode by selecting only matching-
 * kind items from the selection. Per the kind-aware routing rule:
 *   videos   → only kind === "video"      (send video IDs)
 *   channel  → only kind === "channel"    (send channel id / @handle)
 *   playlist → only kind === "playlist"   (send playlist IDs)
 * Mixed selections are partitioned — the user only sends the matching
 * subset; the rest stay selected so they can be routed elsewhere. */
function _itemsForMode(mode) {
    const snippets = Array.from(_selected.values());
    if (mode === "videos") {
        return snippets.filter((v) => (v.kind || "video") === "video").map((v) => v.id);
    }
    if (mode === "channel") {
        return Array.from(new Set(
            snippets
                .filter((v) => v.kind === "channel")
                .map((v) => v.id || v.channel_id)
                .filter(Boolean)
        ));
    }
    if (mode === "playlist") {
        return Array.from(new Set(
            snippets
                .filter((v) => v.kind === "playlist")
                .map((v) => v.id)
                .filter(Boolean)
        ));
    }
    return [];
}

function _route(mode) {
    const items = _itemsForMode(mode);
    if (!items.length) return;
    document.dispatchEvent(new CustomEvent("ycs:route", {
        detail: { mode, items },
    }));
    // Only clear the items we actually routed — others stay selected.
    const routedSet = new Set(items);
    for (const [id, snippet] of Array.from(_selected.entries())) {
        const k = snippet.kind || "video";
        const matches = (mode === "videos" && k === "video" && routedSet.has(id))
                     || (mode === "channel" && k === "channel" && routedSet.has(id))
                     || (mode === "playlist" && k === "playlist" && routedSet.has(id));
        if (matches) _selected.delete(id);
    }
    resultsEl.classList.toggle("has-selection", _selected.size > 0);
    if (_selected.size === 0) _lastChecked = null;
    _renderBulkBar();
    // Uncheck DOM checkboxes for routed items.
    routedSet.forEach((id) => {
        const cb = resultsEl.querySelector(`.ycs-result-check[data-id="${id}"]`);
        if (cb) cb.checked = false;
    });
    document.querySelector(`[data-mode="${mode}"]`)?.click();
}

function _renderBulkBar() {
    const n = _selected.size;
    if (!_bulkBarEl) {
        _bulkBarEl = document.createElement("div");
        _bulkBarEl.className = "ycs-bulk-bar";
        // Channel + Playlist render in the SINGULAR ("→ 1 Channel")
        // because the Channel/Playlist tabs each accept exactly one
        // source — selecting a second of the same kind on this list
        // deselects the first (see `_SINGLETON_KINDS` + `_select`).
        // Videos stay pluralized — multi-video dispatch is genuine.
        _bulkBarEl.innerHTML = `
            <span class="ycs-bulk-count"></span>
            <button type="button" class="ycs-bulk-btn" data-route="videos">→ <span data-count="videos">0</span> Videos</button>
            <button type="button" class="ycs-bulk-btn" data-route="channel">→ <span data-count="channel">0</span> Channel</button>
            <button type="button" class="ycs-bulk-btn" data-route="playlist">→ <span data-count="playlist">0</span> Playlist</button>
            <button type="button" class="ycs-bulk-btn ycs-bulk-clear" data-route="clear" title="Clear selection">×</button>
        `;
        _bulkBarEl.querySelectorAll("[data-route]").forEach((btn) => {
            btn.addEventListener("click", () => {
                const r = btn.dataset.route;
                if (r === "clear") _clearSelection();
                else _route(r);
            });
        });
        // Mount inside the search tab body so display:none on tab switch
        // hides it too (position:fixed descendants disappear with their
        // hidden ancestor).
        document.getElementById("ycs-tab-search")?.appendChild(_bulkBarEl);
    }
    _bulkBarEl.querySelector(".ycs-bulk-count").textContent = `${n} selected`;
    // Per-kind counts drive button enabled state — a button with 0
    // matching items in the selection is grayed out.
    for (const mode of ["videos", "channel", "playlist"]) {
        const c = _itemsForMode(mode).length;
        const span = _bulkBarEl.querySelector(`[data-count="${mode}"]`);
        if (span) span.textContent = String(c);
        const btn = _bulkBarEl.querySelector(`[data-route="${mode}"]`);
        if (btn) btn.disabled = c === 0;
    }
    _bulkBarEl.classList.toggle("visible", n > 0);
    // Selection state changed → update Select-all label (e.g., a
    // per-row click that brings the slice to "all selected" should
    // flip the button to "Deselect all").
    _syncSelectAllBtn();
}

/* Select-all behavior — checks/unchecks every result currently
 * visible (the page slice, not the full server-side cache). Lives
 * inside the master-row at the top of the result list (built by
 * `_buildMasterRow`). Tri-state per Gmail/Linear:
 *   checked        — every visible row in the selection
 *   indeterminate  — some but not all visible rows selected
 *   unchecked      — no visible row selected
 * Click on the checkbox OR the whole row toggles. Singleton kinds
 * (Channel / Playlist) obey `_SINGLETON_KINDS` inside `_select` so
 * only the last channel/playlist survives after the iteration;
 * videos all stay selected. */
function _syncSelectAllBtn() {
    const row = resultsEl.querySelector(".ycs-search-masterrow");
    if (!row) return;
    const cb = row.querySelector(".ycs-search-master-cb");
    const slice = _currentSlice();
    const selectedCount = slice.reduce(
        (n, s) => n + (_isSelected(s.id) ? 1 : 0), 0,
    );
    if (selectedCount === 0) {
        cb.checked = false;
        cb.indeterminate = false;
    } else if (selectedCount === slice.length) {
        cb.checked = true;
        cb.indeterminate = false;
    } else {
        cb.checked = false;
        cb.indeterminate = true;
    }
    row.classList.toggle("has-selection", selectedCount > 0);
}

function _toggleSelectAll() {
    const slice = _currentSlice();
    if (!slice.length) return;
    const shouldSelect = !slice.every((s) => _isSelected(s.id));
    for (const s of slice) {
        _select(s.id, s, shouldSelect);
        const cb = resultsEl.querySelector(
            `.ycs-result-check[data-id="${s.id}"]`,
        );
        if (cb) cb.checked = shouldSelect && _isSelected(s.id);
    }
    _lastChecked = null;
    _syncSelectAllBtn();
}

function _onCheckClick(ev, snippet) {
    const id = snippet.id;
    const on = ev.target.checked;
    if (ev.shiftKey && _lastChecked) {
        const slice = _currentSlice();
        const a = slice.findIndex((s) => s.id === _lastChecked);
        const b = slice.findIndex((s) => s.id === id);
        if (a >= 0 && b >= 0) {
            const [lo, hi] = a < b ? [a, b] : [b, a];
            for (let i = lo; i <= hi; i++) {
                const s = slice[i];
                _select(s.id, s, on);
                const cb = resultsEl.querySelector(`.ycs-result-check[data-id="${s.id}"]`);
                if (cb) cb.checked = on;
            }
            _lastChecked = id;
            return;
        }
    }
    _select(id, snippet, on);
    _lastChecked = id;
}
