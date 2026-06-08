/* Source · Search mode — sync yt-dlp metadata browse.
 *
 * Three SOTA-aligned subsystems live here:
 *   1. Filter chip bar (Linear / Vercel / Height idiom)
 *   2. Compact stacked results list (NN/g "list entry" pattern)
 *   3. Density toggle (Compact / Comfortable)
 *
 * Hidden inputs (#ycs-fh-*) mirror chip state so the existing
 * FormData → SearchRequest serialization in `readSearchRequest()`
 * stays unchanged.
 */
import { API, fmtCount, fmtDate } from "./shared.js";

// ============================================================
// Filter chip bar
// ============================================================

/* Backend accepts YYYYMMDD (8 digits, no separators); native
 * <input type="date"> emits YYYY-MM-DD. Stash one of each direction
 * so the editor can round-trip between hidden store and the picker. */
const _DATE_MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
function _yyyymmddToIso(v) {
    const m = (v || "").match(/^(\d{4})(\d{2})(\d{2})$/);
    return m ? `${m[1]}-${m[2]}-${m[3]}` : "";
}
function _isoToYyyymmdd(v) {
    return (v || "").replace(/-/g, "").slice(0, 8);
}
function _fmtDateChip(yyyymmdd) {
    const m = (yyyymmdd || "").match(/^(\d{4})(\d{2})(\d{2})$/);
    if (!m) return yyyymmdd || "";
    return `${_DATE_MONTHS[parseInt(m[2], 10) - 1]} ${parseInt(m[3], 10)}, ${m[1]}`;
}

const FILTERS = {
    duration: {
        label: "Duration",
        type:  "select",
        options: [
            { value: "Under 4 minutes", label: "Under 4 min" },
            { value: "4 - 20 minutes",  label: "4–20 min" },
            { value: "Over 20 minutes", label: "Over 20 min" },
        ],
        format: (v) => v.replace(" minutes", "m").replace("Under 4m", "<4m").replace("Over 20m", ">20m"),
    },
    date_after:     { label: "After",      type: "date",
                      format: _fmtDateChip },
    date_before:    { label: "Before",     type: "date",
                      format: _fmtDateChip },
    min_views:      { label: "≥ Views",    type: "number",
                      placeholder: "10000",
                      format: (v) => fmtCount(parseInt(v, 10)) },
    max_views:      { label: "≤ Views",    type: "number",
                      placeholder: "1000000",
                      format: (v) => fmtCount(parseInt(v, 10)) },
    min_likes:      { label: "≥ Likes",    type: "number",
                      placeholder: "100",
                      format: (v) => fmtCount(parseInt(v, 10)) },
    title_contains: { label: "Title",      type: "text",
                      placeholder: "text or *=op",
                      format: (v) => v.length > 16 ? v.slice(0, 16) + "…" : v },
    channel_name:   { label: "Channel",    type: "text",
                      placeholder: "name",
                      format: (v) => v.length > 16 ? v.slice(0, 16) + "…" : v },
    sort_by_date:   { label: "Sort",       type: "toggle",
                      onValue: "newest",
                      format: () => "newest" },
    /* Shorts exclusion — per the June 2026 yt-dlp research, no native
     * `is_short` field exists; we approximate with `duration>?60` and
     * `!url~='/shorts/'` on the backend. Toggle filter (no editor). */
    exclude_shorts: { label: "No shorts",  type: "toggle",
                      onValue: "yes",
                      format: () => "≥ 1 min" },
    /* Show-only-one-kind filter. Applied server-side after
     * normalization (see domain.detect_entry_kind). */
    kind_filter:    { label: "Kind",       type: "select",
                      options: [
                          { value: "video",    label: "Videos only" },
                          { value: "channel",  label: "Channels only" },
                          { value: "playlist", label: "Playlists only" },
                      ],
                      format: (v) => v === "video" ? "Videos"
                                  : v === "channel" ? "Channels"
                                  : "Playlists" },
};

const chipsEl  = document.getElementById("ycs-filter-chips");
const addBtn   = document.getElementById("ycs-filter-add-btn");

function _hiddenFor(name) {
    return document.getElementById(`ycs-fh-${name}`);
}

function _hasValue(name) {
    const v = _hiddenFor(name)?.value ?? "";
    return v.trim() !== "" && v !== "false";
}

function _setValue(name, value) {
    const h = _hiddenFor(name);
    if (h) h.value = value ?? "";
}

function _renderChip(name) {
    const spec = FILTERS[name];
    const v = _hiddenFor(name)?.value ?? "";
    if (!v) return null;
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "ycs-filter-chip";
    chip.dataset.filter = name;
    const display = spec.format ? spec.format(v) : v;
    chip.innerHTML = `
        <span class="ycs-filter-chip-key">${spec.label}</span>
        <span class="ycs-filter-chip-val">${display}</span>
        <span class="ycs-filter-chip-x" aria-label="Remove">×</span>
    `;
    chip.addEventListener("click", (ev) => {
        if (ev.target.classList.contains("ycs-filter-chip-x")) {
            _setValue(name, "");
            _renderBar();
            return;
        }
        _openEditor(name, chip);
    });
    return chip;
}

function _renderBar() {
    // Wipe + rebuild every chip, leave the + Filter button at the end.
    Array.from(chipsEl.querySelectorAll(".ycs-filter-chip")).forEach((el) => el.remove());
    for (const name of Object.keys(FILTERS)) {
        const chip = _renderChip(name);
        if (chip) chipsEl.insertBefore(chip, addBtn);
    }
}

// ---- Menu (+ Filter trigger) -----------------------------------------------

let menuEl = null;

function _closeMenu() {
    menuEl?.remove();
    menuEl = null;
    addBtn?.setAttribute("aria-expanded", "false");
    document.removeEventListener("click", _onDocClickMenu, true);
}

function _onDocClickMenu(ev) {
    if (menuEl?.contains(ev.target) || addBtn?.contains(ev.target)) return;
    _closeMenu();
}

function _openMenu() {
    _closeMenu();
    const remaining = Object.keys(FILTERS).filter((n) => !_hasValue(n));
    if (!remaining.length) return;
    menuEl = document.createElement("div");
    menuEl.className = "ycs-filter-menu";
    menuEl.innerHTML = remaining.map((name) => {
        const spec = FILTERS[name];
        return `<button type="button" class="ycs-filter-menu-item" data-filter="${name}">${spec.label}</button>`;
    }).join("");
    menuEl.addEventListener("click", (ev) => {
        const item = ev.target.closest(".ycs-filter-menu-item");
        if (!item) return;
        const name = item.dataset.filter;
        _closeMenu();
        const spec = FILTERS[name];
        if (spec.type === "toggle") {
            _setValue(name, spec.onValue);
            _renderBar();
            return;
        }
        // Render the chip with a placeholder value so the editor anchors
        // to a real chip element.
        _setValue(name, " ");
        _renderBar();
        const chip = chipsEl.querySelector(`.ycs-filter-chip[data-filter="${name}"]`);
        if (chip) _openEditor(name, chip, true);
    });
    addBtn.insertAdjacentElement("afterend", menuEl);
    addBtn.setAttribute("aria-expanded", "true");
    // Defer the document listener so the OPENING click doesn't immediately close.
    setTimeout(() => document.addEventListener("click", _onDocClickMenu, true), 0);
}

addBtn?.addEventListener("click", () => {
    if (menuEl) _closeMenu(); else _openMenu();
});

// ---- Inline editor (chip click) -------------------------------------------

let editorEl = null;

function _closeEditor(commit, name, value) {
    editorEl?.remove();
    editorEl = null;
    document.removeEventListener("click", _onDocClickEditor, true);
    if (commit) {
        let v = (value ?? "").trim();
        // Date pickers emit YYYY-MM-DD; backend wants YYYYMMDD.
        if (FILTERS[name]?.type === "date") v = _isoToYyyymmdd(v);
        _setValue(name, v);
    } else if (name && _hiddenFor(name)?.value === " ") {
        // Cancel of a freshly-added chip → drop it.
        _setValue(name, "");
    }
    _renderBar();
}

function _onDocClickEditor(ev) {
    if (editorEl?.contains(ev.target)) return;
    // Click outside → commit current input value.
    const input = editorEl?.querySelector("input, select");
    _closeEditor(true, editorEl?.dataset.filter, input?.value);
}

function _openEditor(name, anchorChip, justAdded = false) {
    if (editorEl) _closeEditor(false);
    const spec = FILTERS[name];
    if (spec.type === "toggle") return;   // toggle has no editor
    editorEl = document.createElement("div");
    editorEl.className = "ycs-filter-editor";
    editorEl.dataset.filter = name;
    const cur = _hiddenFor(name)?.value ?? "";
    if (spec.type === "select") {
        const opts = spec.options
            .map((o) => `<option value="${o.value}" ${cur === o.value ? "selected" : ""}>${o.label}</option>`)
            .join("");
        editorEl.innerHTML = `
            <label class="ycs-filter-editor-label">${spec.label}</label>
            <select class="ycs-filter-editor-input">${opts}</select>
        `;
    } else {
        const inputType = spec.type === "number" ? "number"
                        : spec.type === "date"   ? "date"
                        : "text";
        const curTrim = cur.trim();
        const editorVal = justAdded
            ? ""
            : (spec.type === "date" ? _yyyymmddToIso(curTrim) : curTrim);
        editorEl.innerHTML = `
            <label class="ycs-filter-editor-label">${spec.label}</label>
            <input class="ycs-filter-editor-input"
                   type="${inputType}"
                   value="${editorVal}"
                   placeholder="${spec.placeholder ?? ""}">
        `;
    }
    anchorChip.insertAdjacentElement("afterend", editorEl);
    const inp = editorEl.querySelector("input, select");
    inp?.focus();
    inp?.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") {
            ev.preventDefault();
            _closeEditor(true, name, inp.value);
        } else if (ev.key === "Escape") {
            ev.preventDefault();
            _closeEditor(false, name);
        }
    });
    if (spec.type === "select" || spec.type === "date") {
        // Selects + native date pickers auto-commit on change.
        inp.addEventListener("change", () => _closeEditor(true, name, inp.value));
    }
    setTimeout(() => document.addEventListener("click", _onDocClickEditor, true), 0);
}

_renderBar();

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

function renderResults(results) {
    if (!results?.length) {
        resultsEl.innerHTML = `<div class="ycs-search-empty">No matches. Loosen the filters and try again.</div>`;
        return;
    }
    const frag = document.createDocumentFragment();
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

function _select(id, snippet, on) {
    if (on) _selected.set(id, snippet);
    else    _selected.delete(id);
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
        _bulkBarEl.innerHTML = `
            <span class="ycs-bulk-count"></span>
            <button type="button" class="ycs-bulk-btn" data-route="videos">→ <span data-count="videos">0</span> Videos</button>
            <button type="button" class="ycs-bulk-btn" data-route="channel">→ <span data-count="channel">0</span> Channels</button>
            <button type="button" class="ycs-bulk-btn" data-route="playlist">→ <span data-count="playlist">0</span> Playlists</button>
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
