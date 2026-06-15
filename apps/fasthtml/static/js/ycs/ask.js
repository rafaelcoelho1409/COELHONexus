/* YCS · Step 3 · Ask — Adaptive RAG chat (SSE streaming).
 *
 * Three subsystems wired here:
 *   (1) LLM config form — PUT /api/v1/ycs/agents/config
 *   (2) Channel multi-select populated from /admin/ingested-channels
 *   (3) Mode pill + composer → POST /api/v1/ycs/agents/search/stream (SSE)
 *
 * SSE consumption: events arrive as `data: {json}\n\n`. Each event is one
 * LangGraph node update (classify / retrieve / grade / generate / ...).
 * The renderer maps the node name to a stage pill class transition and,
 * when an event carries a `generation`, streams it into the answer
 * bubble.
 */
import { showConfirm, showToast } from "@dd/shared/ui/overlays.js";

const API = "/api/v1/ycs";

// ---- helpers ---------------------------------------------------------------
/* Markdown → sanitized HTML. `marked` and `DOMPurify` are loaded
 * globally in `layout/head.py`; they're available on every page. The
 * generation text comes from the LLM (not directly user-controllable
 * but still untrusted), so sanitize before innerHTML write. GitHub-
 * Flavored Markdown + line breaks on `\n` so streaming chunks render
 * cleanly as they grow. */
function renderMarkdown(text) {
    if (!text) return "";
    if (typeof marked === "undefined" || typeof DOMPurify === "undefined") {
        return htmlEscape(text);
    }
    try {
        // `breaks: false` — standard CommonMark: single newlines collapse
        // to a space; only blank lines start a new paragraph. `breaks:
        // true` was turning every `\n` into a `<br>` which doubled the
        // vertical air between rendered paragraphs.
        const html = marked.parse(String(text), { breaks: false, gfm: true });
        return DOMPurify.sanitize(html, {
            // Drop links' javascript: schemes; keep target+rel.
            ADD_ATTR: ["target", "rel"],
        });
    } catch (_) {
        return htmlEscape(text);
    }
}

async function api(path, opts = {}) {
    const r = await fetch(API + path, opts);
    let data = null;
    try { data = await r.json(); } catch (_) { /* */ }
    if (!r.ok) {
        const msg = (data && (data.detail ?? data.message)) || r.statusText;
        const err = new Error(typeof msg === "string" ? msg : "request failed");
        err.status = r.status;
        throw err;
    }
    return data;
}

function setStatus(node, kind, text) {
    if (!node) return;
    node.className = `ycs-search-status${kind ? ` ${kind}` : ""}`;
    node.textContent = text;
}

function htmlEscape(s) {
    return String(s ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

// ---- (1) Row-3 toolbar `.dd-catfilter` dropdowns ---------------------------
/* Both the LLM-config trigger and the thread picker use the
 * `.dd-catfilter` trigger+popover idiom (also used by the Ingestion
 * library filters). Same open/close behavior: toggle `.open` on the
 * wrapper, close all on outside-click and Escape, mutually exclusive
 * (opening one closes the others). */
function _closeAllCatfilters() {
    document.querySelectorAll(".dd-catfilter.open").forEach((w) => {
        w.classList.remove("open");
        w.querySelector(".dd-catfilter-trigger")
            ?.setAttribute("aria-expanded", "false");
    });
}

function bindCatfilter(triggerId, wrapperId, onOpen) {
    const wrapper = document.getElementById(wrapperId);
    const trigger = document.getElementById(triggerId);
    if (!wrapper || !trigger) return null;
    trigger.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const wasOpen = wrapper.classList.contains("open");
        _closeAllCatfilters();
        if (!wasOpen) {
            wrapper.classList.add("open");
            trigger.setAttribute("aria-expanded", "true");
            if (typeof onOpen === "function") onOpen();
        }
    });
    return { wrapper, trigger };
}

document.addEventListener("click", (ev) => {
    if (ev.target.closest?.(".dd-catfilter")) return;
    _closeAllCatfilters();
});
document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") _closeAllCatfilters();
});

bindCatfilter("ycs-ask-llm-trigger", "ycs-ask-llm");

const llmTestBtn = document.getElementById("ycs-llm-test");

/* "Test rotator" button — fires `POST /agents/rotator/ping` which
 * does a single `app.state.llm.ainvoke("ping")` round-trip through
 * the full FGTS-VA chain. Verifies the rotator is responsive at all;
 * if this fails, every Ask request will fail too. */
llmTestBtn?.addEventListener("click", async () => {
    const status = document.getElementById("ycs-llm-status");
    setStatus(status, "running", "Pinging rotator…");
    try {
        const r = await api("/agents/rotator/ping", {
            method:  "POST",
            headers: { "content-type": "application/json" },
            body:    "{}",
        });
        if (r.status === "ok") {
            setStatus(status, "", `OK · ${r.ms}ms`);
        } else {
            setStatus(status, "error", `Failed: ${r.error}`);
        }
    } catch (e) {
        setStatus(status, "error", `Failed: ${e.message}`);
    }
});

// ---- (2) Channel scope (row-3 `.dd-catfilter` popover) ---------------------
/* Replaces the old body-resident multi-select. State lives in this
 * `Set<channel_id>` rather than the DOM — the form-submit handler
 * reads from `selectedChannels` directly. The popover's checkbox rows
 * are populated lazily on first open (the picker is rarely opened, no
 * reason to pay the fetch cost at page load). */
const scopeLabel    = document.getElementById("ycs-ask-scope-label");
const scopeListEl   = document.getElementById("ycs-ask-scope-list");
const selectedChannels = new Set();
let allChannels = [];  // [{channel_id, channel, video_count}, ...]

function updateScopeLabel() {
    if (!scopeLabel) return;
    const n = selectedChannels.size;
    if (n === 0) {
        scopeLabel.textContent = "All channels";
        return;
    }
    if (n === 1) {
        const id = [...selectedChannels][0];
        const ch = allChannels.find((c) => c.channel_id === id);
        scopeLabel.textContent = ch?.channel || id;
        return;
    }
    scopeLabel.textContent = `${n} channels`;
}

function renderScopeList() {
    if (!scopeListEl) return;
    if (!allChannels.length) {
        scopeListEl.innerHTML =
            '<div class="ycs-ask-scope-empty">No channels indexed yet.</div>';
        return;
    }
    const frag = document.createDocumentFragment();
    /* "All channels" meta-row at the top — clearing the selection IS
     * "match anywhere" (no `channel_ids` sent to the backend). Toggle
     * stays in sync with the individual rows via the change handler. */
    const totalVideos = allChannels.reduce(
        (s, c) => s + (c.video_count || 0), 0,
    );
    const allRow = document.createElement("label");
    allRow.className = "ycs-ask-scope-row ycs-ask-scope-row-all";
    /* "All channels" is mutually exclusive with the per-channel picks:
     *  - 0 specific picks → checked + enabled (active "match anywhere")
     *  - 1+ specific picks → unchecked + DISABLED (uncheck the picks
     *    first to come back to All). Disabling beats the prior
     *    auto-recheck-on-uncheck trick because the locked-out state
     *    is what the user actually sees. */
    const allIsActive = selectedChannels.size === 0;
    const allAttrs = (allIsActive ? "checked" : "disabled");
    allRow.innerHTML = `
        <input type="checkbox" class="ycs-ask-scope-check-all" ${allAttrs}>
        <span class="ycs-ask-scope-row-name"><strong>All channels</strong></span>
        <span class="ycs-ask-scope-row-count">${totalVideos}</span>
    `;
    if (!allIsActive) allRow.classList.add("ycs-ask-scope-row-disabled");
    frag.appendChild(allRow);
    const divider = document.createElement("div");
    divider.className = "ycs-ask-scope-divider";
    frag.appendChild(divider);
    for (const ch of allChannels) {
        const id = ch.channel_id ?? "";
        if (!id) continue;
        const row = document.createElement("label");
        row.className = "ycs-ask-scope-row";
        const checked = selectedChannels.has(id) ? "checked" : "";
        row.innerHTML = `
            <input type="checkbox" class="ycs-ask-scope-check"
                   data-channel-id="${htmlEscape(id)}" ${checked}>
            <span class="ycs-ask-scope-row-name">${htmlEscape(ch.channel ?? id)}</span>
            <span class="ycs-ask-scope-row-count">${ch.video_count ?? 0}</span>
        `;
        frag.appendChild(row);
    }
    scopeListEl.replaceChildren(frag);
}

async function loadScopeList() {
    if (!scopeListEl) return;
    if (allChannels.length) {
        renderScopeList();
        return;
    }
    scopeListEl.innerHTML =
        '<div class="ycs-ask-scope-empty">Loading…</div>';
    try {
        const r = await api("/admin/ingested-channels");
        allChannels = r.items ?? [];
    } catch (_) {
        scopeListEl.innerHTML =
            '<div class="ycs-ask-scope-empty">Failed to load channels</div>';
        return;
    }
    renderScopeList();
}

/* Sync the "All channels" row to the current selection size:
 *   selectedChannels.size === 0  → checked + enabled
 *   selectedChannels.size  >= 1  → unchecked + disabled (greyed) */
function _refreshAllChannelsRow() {
    const cb = scopeListEl?.querySelector(".ycs-ask-scope-check-all");
    if (!cb) return;
    const active = selectedChannels.size === 0;
    cb.checked  =  active;
    cb.disabled = !active;
    cb.closest(".ycs-ask-scope-row-all")
        ?.classList.toggle("ycs-ask-scope-row-disabled", !active);
}

scopeListEl?.addEventListener("change", (ev) => {
    const cb = ev.target;
    /* "All channels" is only clickable when no specific picks exist
     * (disabled otherwise). Checking it clears all individual picks. */
    if (cb.matches?.(".ycs-ask-scope-check-all")) {
        if (cb.checked) {
            selectedChannels.clear();
            scopeListEl.querySelectorAll(".ycs-ask-scope-check")
                .forEach((box) => { box.checked = false; });
        }
        _refreshAllChannelsRow();
        updateScopeLabel();
        return;
    }
    if (cb.matches?.(".ycs-ask-scope-check")) {
        const id = cb.dataset.channelId;
        if (!id) return;
        if (cb.checked) selectedChannels.add(id);
        else            selectedChannels.delete(id);
        _refreshAllChannelsRow();
        updateScopeLabel();
    }
});

bindCatfilter("ycs-ask-scope-trigger", "ycs-ask-scope", loadScopeList);

// ---- (3) Mode pill ---------------------------------------------------------
let activeMode = "";  // "" = auto (no force_mode)
const modePills = document.querySelectorAll(".ycs-mode-pill");
modePills.forEach((p) => {
    p.addEventListener("click", () => {
        activeMode = p.dataset.mode || "";
        modePills.forEach((q) => q.classList.toggle("active", q === p));
    });
});

// ---- (3) Composer + SSE ----------------------------------------------------
/* 2026-06-14 — single continuous conversation feed (Claude / ChatGPT
 * shape). Every turn (user prompt + streaming assistant reply +
 * stages + DEEP panel + citations) is its OWN `.ycs-ask-turn` element
 * appended to `#ycs-ask-conversation`. No more "current answer area
 * sitting separate from past turns" — the conversation IS the history.
 * All SSE-driven writes target the in-flight turn via `currentTurnEl`
 * + class-scoped querySelector, so multiple turns coexist cleanly. */
const askForm        = document.getElementById("ycs-ask-form");
const askInput       = document.getElementById("ycs-ask-input");

/* Auto-grow textarea: ChatGPT/Claude/Perplexity pattern (mid-2026
 * SOTA). 1 row when empty → grows with content up to MAX_LINES → then
 * scrolls. `field-sizing: content` in CSS handles browsers that
 * support it (Chrome 123+, Firefox 130+, Safari 18.2+, ~78% global),
 * this JS path is the fallback that still ships in every major chat
 * UI because `field-sizing` isn't Baseline yet. */
const TEXTAREA_MAX_LINES = 10;
function adjustTextareaHeight() {
    if (!askInput) return;
    askInput.style.height = "auto";
    const cs = getComputedStyle(askInput);
    const lh = parseFloat(cs.lineHeight) || 22;
    const padY =
        parseFloat(cs.paddingTop)    || 0
      + parseFloat(cs.paddingBottom) || 0;
    const maxH = (lh * TEXTAREA_MAX_LINES) + padY + 2;
    askInput.style.height = Math.min(askInput.scrollHeight, maxH) + "px";
}
function resetTextareaHeight() {
    if (askInput) askInput.style.height = "";
}
askInput?.addEventListener("input", adjustTextareaHeight);
const askStatus      = document.getElementById("ycs-ask-status");
const conversationEl = document.getElementById("ycs-ask-conversation");
const threadIdEl     = document.getElementById("ycs-ask-thread-id");
const newThreadBtn   = document.getElementById("ycs-ask-new-thread");
const stopBtn        = document.getElementById("ycs-ask-stop");
const sendBtn        = document.getElementById("ycs-ask-send");
const emptyEl        = document.getElementById("ycs-ask-empty");

/* Send ↔ Stop swap (ChatGPT shape): exactly one of the two is visible
 * at any time. While a stream is in flight, the Stop button takes the
 * Send position; once the `end` SSE event lands (or the stream errors
 * out / is aborted), the Send button is restored. */
function setStreamingUI(streaming) {
    if (sendBtn) sendBtn.style.display = streaming ? "none"       : "inline-flex";
    if (stopBtn) stopBtn.style.display = streaming ? "inline-flex" : "none";
}

/* Active AbortController for the in-flight SSE fetch. Click on Stop
 * → controller.abort() → fetch rejects with AbortError → caught and
 * rendered as a "Stopped" pill. Null when no request is in flight. */
let currentAbortController = null;

/* The turn currently being streamed into (the LAST `.ycs-ask-turn`
 * in the conversation column). All applyUpdate / markStage / renderXxx
 * writes scope to its descendants. Null when no request is in flight. */
let currentTurnEl = null;

/* Captured during the stream so we can render them as clickable
 * followup chips on `end`. Cleared in `startNewTurn`. */
let currentSubQuestions = [];

function hideEmptyState() {
    if (emptyEl) emptyEl.style.display = "none";
}

function showEmptyState() {
    if (emptyEl) emptyEl.style.display = "";
}

/* Index of sub-question text → card element so `run_subagent` events
 * (which carry `latest_sub_question`) can find the matching card to
 * advance. Refilled per query in `startNewTurn`. */
const deepCardIndex = new Map();

// ---- per-turn DOM helpers --------------------------------------------------
/* `_q(class)` returns the first descendant of the in-flight turn with
 * the given class. Returns null when no turn is in flight. */
function _q(sel) {
    return currentTurnEl ? currentTurnEl.querySelector(sel) : null;
}

/* Build the streaming-turn DOM skeleton. The same shape `renderHistoryTurn`
 * uses for past turns minus the stages strip + DEEP panel (those are
 * streaming-only — they get filled in by SSE events as the turn unfolds). */
function _streamingTurnSkeleton(question) {
    const turn = document.createElement("div");
    turn.className = "ycs-ask-turn ycs-ask-turn-streaming";
    turn.dataset.streaming = "true";
    turn.dataset.question  = question;
    /* Citations no longer have an inline slot — they're consolidated
     * into the sticky right-rail (`#ycs-ask-rail`) showing the latest
     * turn's sources. P3 (2026-06-14). */
    /* Stages strip + DEEP panel are wrapped in a `.ycs-ask-turn-process`
     * accordion. Open by default while streaming (user wants to see
     * what the agent is doing); collapsed automatically on the `end`
     * event by `freezeCurrentTurn()` (the conversation reads cleaner
     * with completed turns folded). Click the header to re-expand. */
    turn.innerHTML = `
        <div class="ycs-ask-turn-user">
            <span class="ycs-ask-turn-role">You</span>
            <div class="ycs-ask-turn-user-body">${htmlEscape(question)}</div>
        </div>
        <div class="ycs-ask-turn-assistant">
            <div class="ycs-ask-turn-assistant-head">
                <span class="ycs-ask-turn-role">Assistant</span>
                <span class="ycs-ask-turn-mode-badge" data-mode=""></span>
            </div>
            <div class="ycs-ask-turn-process">
                <button type="button"
                        class="ycs-ask-turn-process-head"
                        aria-expanded="true">
                    <span class="ycs-ask-turn-process-chevron">▾</span>
                    <span class="ycs-ask-turn-process-label">Thinking</span>
                </button>
                <div class="ycs-ask-turn-process-body">
                    <div class="ycs-ask-stages">
                        <div class="ycs-step-circle" data-stage="retrieve">
                            <span class="ycs-step-circle-label">Retrieve</span>
                            <span class="ycs-step-circle-action"></span>
                        </div>
                        <div class="ycs-step-circle" data-stage="grade">
                            <span class="ycs-step-circle-label">Grade</span>
                            <span class="ycs-step-circle-action"></span>
                        </div>
                        <div class="ycs-step-circle" data-stage="generate">
                            <span class="ycs-step-circle-label">Generate</span>
                            <span class="ycs-step-circle-action"></span>
                        </div>
                        <div class="ycs-step-circle" data-stage="verify">
                            <span class="ycs-step-circle-label">Verify</span>
                            <span class="ycs-step-circle-action"></span>
                        </div>
                    </div>
                    <div class="ycs-ask-deep" style="display:none;">
                        <div class="ycs-ask-deep-banner"></div>
                        <div class="ycs-ask-deep-cards"></div>
                    </div>
                </div>
            </div>
            <div class="ycs-ask-turn-body ycs-ask-answer"></div>
            <div class="ycs-ask-followups"></div>
            ${_actionChipsHTML()}
        </div>
    `;
    return turn;
}

/* Per-turn action chips (Copy / Regenerate / Branch). Rendered into
 * every turn but kept hidden on streaming turns via CSS — they only
 * appear once the turn freezes. Click delegation lives on
 * `conversationEl`; handlers route by `data-action`. */
function _actionChipsHTML() {
    return `
        <div class="ycs-ask-turn-actions">
            <button type="button" class="ycs-ask-turn-action"
                    data-action="copy"
                    title="Copy the answer to clipboard">Copy</button>
            <button type="button" class="ycs-ask-turn-action"
                    data-action="regenerate"
                    title="Re-fire this question as a new turn">Regenerate</button>
            <button type="button" class="ycs-ask-turn-action"
                    data-action="branch"
                    title="Fork the conversation up to this turn into a new thread">Branch</button>
        </div>
    `;
}

/* Per-turn state — generation text + citations — held off the DOM in
 * a WeakMap so each turn can re-render its body whenever EITHER piece
 * updates (citations might arrive after the first generation chunk; we
 * still want the inline `[N]` pills to materialize once they do). The
 * WeakMap auto-clears when the turn element is GC'd. */
const turnDataMap = new WeakMap();
function getTurnData(turn) {
    if (!turn) return null;
    let d = turnDataMap.get(turn);
    if (!d) { d = { generation: "", citations: [] }; turnDataMap.set(turn, d); }
    return d;
}

/* `[Video: title]` → `__CITE_N__` token so the post-markdown step can
 * replace it with a `<sup>` pill (marked would HTML-escape an inline
 * tag we emitted in the markdown step). Matching is title-aware with
 * a substring fallback because LLMs sometimes paraphrase. */
function _citeTokens(text, citations) {
    if (!citations?.length) return text;
    const idx = new Map();
    citations.forEach((c, i) => {
        const t = (c.title || "").trim().toLowerCase();
        if (t) idx.set(t, i + 1);
    });
    return text.replace(/\[Video:\s*([^\]]+?)\]/gi, (match, raw) => {
        const key = raw.trim().toLowerCase();
        let n = idx.get(key);
        if (!n) {
            for (const [k, i] of idx) {
                if (k.includes(key) || key.includes(k)) { n = i; break; }
            }
        }
        return n ? `__CITE_${n}__` : match;
    });
}

function _citePills(html) {
    return html.replace(/__CITE_(\d+)__/g, (_, n) =>
        `<sup class="ycs-ask-cite-pill" data-cite="${n}"
               title="Citation ${n}">[${n}]</sup>`
    );
}

function renderMarkdownWithCitations(text, citations) {
    const tokenised = _citeTokens(text, citations);
    const html      = renderMarkdown(tokenised);
    return _citePills(html);
}

// ---- sources rail (sticky right column) -----------------------------------
const railListEl  = document.getElementById("ycs-ask-rail-list");
const railCountEl = document.getElementById("ycs-ask-rail-count");

function updateSourcesRail(citations) {
    if (!railListEl) return;
    const items = citations ?? [];
    if (railCountEl) railCountEl.textContent = String(items.length);
    if (!items.length) {
        railListEl.replaceChildren();
        return;
    }
    const frag = document.createDocumentFragment();
    items.forEach((c, i) => {
        const card = renderCitation(c);
        card.classList.add("ycs-ask-rail-card");
        card.dataset.citeId = String(i + 1);
        // Numbered chip overlaid on the card head — matches the inline
        // `[N]` pill in the answer text.
        const num = document.createElement("span");
        num.className = "ycs-ask-rail-card-n";
        num.textContent = `[${i + 1}]`;
        card.prepend(num);
        frag.appendChild(card);
    });
    railListEl.replaceChildren(frag);
}

function clearSourcesRail() {
    if (railListEl)  railListEl.replaceChildren();
    if (railCountEl) railCountEl.textContent = "0";
}

/* Hover an inline `[N]` pill → highlight its matching card in the
 * rail. Delegated so it works across all turns + the rail itself. */
conversationEl?.addEventListener("mouseover", (ev) => {
    const pill = ev.target.closest?.(".ycs-ask-cite-pill");
    if (!pill) return;
    const id = pill.dataset.cite;
    railListEl?.querySelectorAll(".ycs-ask-rail-card")
        .forEach((c) => {
            c.classList.toggle(
                "highlighted", c.dataset.citeId === id,
            );
        });
});
conversationEl?.addEventListener("mouseout", (ev) => {
    if (!ev.target.closest?.(".ycs-ask-cite-pill")) return;
    railListEl?.querySelectorAll(".ycs-ask-rail-card.highlighted")
        .forEach((c) => c.classList.remove("highlighted"));
});

/* Append a fresh streaming turn for the user's new question and set
 * `currentTurnEl` to it. The empty-state hint hides on the first turn.
 * `deepCardIndex` + `currentSubQuestions` reset for clean per-turn state. */
/* The actual scrolling container is the shell's `.page` (see
 * `static/css/base/shell.css` — `overflow-y: auto`); the document
 * itself never scrolls. Cache the ref + provide the only two helpers
 * the rest of the code needs. */
const pageScrollEl = document.querySelector(".page");

function _scrollPageToBottom(smooth = true) {
    if (!pageScrollEl) return;
    pageScrollEl.scrollTo({
        top:      pageScrollEl.scrollHeight,
        behavior: smooth ? "smooth" : "auto",
    });
}

/* True if the user is "watching the latest" — within 120px of the
 * scrollable bottom. Used to auto-follow streaming generation only
 * when the user hasn't scrolled up to re-read older content. */
function _isPageNearBottom() {
    if (!pageScrollEl) return true;
    const gap = pageScrollEl.scrollHeight
              - pageScrollEl.scrollTop
              - pageScrollEl.clientHeight;
    return gap < 120;
}

function startNewTurn(question) {
    if (!conversationEl) return;
    hideEmptyState();
    deepCardIndex.clear();
    currentSubQuestions = [];
    clearSourcesRail();
    currentTurnEl = _streamingTurnSkeleton(question);
    conversationEl.appendChild(currentTurnEl);
    // On Send: snap the conversation to the very bottom so the user
    // sees their just-typed message + the in-flight assistant area.
    // `requestAnimationFrame` waits for the new turn to land in the
    // DOM so `scrollHeight` includes it. The composer is `position:
    // fixed` and the conversation column reserves `padding-bottom:
    // 120px`, so scrolling to scrollHeight lands the user prompt
    // just above the composer card.
    requestAnimationFrame(() => _scrollPageToBottom(true));
}

/* Freeze the current turn when the stream ends (`end` SSE event):
 * drop the `ycs-ask-turn-streaming` class so any streaming-only
 * styling (pulsing pill, etc.) stops; release `currentTurnEl`. */
function freezeCurrentTurn() {
    if (!currentTurnEl) return;
    currentTurnEl.classList.remove("ycs-ask-turn-streaming");
    currentTurnEl.dataset.streaming = "false";
    // Auto-collapse the Stages accordion now that the turn is done —
    // the streamed answer is the headline; how the agent got there
    // becomes optional detail. User can re-expand by clicking it.
    const process = currentTurnEl.querySelector(".ycs-ask-turn-process");
    const head    = currentTurnEl.querySelector(".ycs-ask-turn-process-head");
    if (process) process.classList.add("collapsed");
    if (head)    head.setAttribute("aria-expanded", "false");
    currentTurnEl = null;
}

/* Delegated toggle for the Stages accordion across all turns. */
conversationEl?.addEventListener("click", (ev) => {
    const head = ev.target.closest?.(".ycs-ask-turn-process-head");
    if (!head) return;
    const process = head.closest(".ycs-ask-turn-process");
    if (!process) return;
    const open = process.classList.toggle("collapsed");
    head.setAttribute("aria-expanded", open ? "false" : "true");
});

/* Build a static historical-turn DOM (no stages / DEEP panel — those
 * are streaming artifacts not persisted in Postgres). Used by
 * `hydrateThreadHistory` to repaint past turns on page load. */
function renderHistoryTurn({ question, answer, mode = "", created_at = "" }) {
    if (!conversationEl) return;
    const turn = document.createElement("div");
    turn.className = "ycs-ask-turn ycs-ask-turn-history";
    turn.dataset.question = question || "";
    if (created_at) turn.dataset.createdAt = created_at;
    const modeBadge = mode
        ? `<span class="ycs-ask-turn-mode-badge" data-mode="${htmlEscape(mode)}">${htmlEscape(mode)}</span>`
        : "";
    turn.innerHTML = `
        <div class="ycs-ask-turn-user">
            <span class="ycs-ask-turn-role">You</span>
            <div class="ycs-ask-turn-user-body">${htmlEscape(question)}</div>
        </div>
        <div class="ycs-ask-turn-assistant">
            <div class="ycs-ask-turn-assistant-head">
                <span class="ycs-ask-turn-role">Assistant</span>
                ${modeBadge}
            </div>
            <div class="ycs-ask-turn-body ycs-ask-answer">${renderMarkdown(answer)}</div>
            ${_actionChipsHTML()}
        </div>
    `;
    conversationEl.appendChild(turn);
}

/* Wipe the whole conversation column + show the empty state. Used by
 * the New thread button and the switchThread / deleteThread cleanup. */
function clearConversation() {
    if (conversationEl) conversationEl.replaceChildren();
    clearSourcesRail();
    showEmptyState();
    currentTurnEl = null;
    deepCardIndex.clear();
    currentSubQuestions = [];
    setStatus(askStatus, "", "");
}

// ---- thread management -----------------------------------------------------
/* Thread id persists in localStorage so a page refresh keeps the
 * conversation. New thread → regenerate the id (the Postgres rows for
 * the previous id stay around but the new id won't reach them).
 * Switching threads via the picker re-uses an existing id and pulls
 * its history back via `hydrateThreadHistory()`. */
const LS_THREAD_KEY = "ycs-ask-thread-id";
const THREAD_LABEL_MAX = 16;  /* trigger-label trim before ellipsis */

function shortId() {
    const u = (crypto.randomUUID?.() ?? `t-${Date.now()}-${Math.floor(Math.random() * 1e6)}`);
    return u.replace(/-/g, "").slice(0, 12);
}

/* "New" placeholder when the live threadId has NO saved turns in
 * Postgres yet (fresh page load, just clicked New thread, or just
 * deleted the active one). The id stays in `threadId` + localStorage —
 * it gets surfaced as soon as the first turn lands and we re-call
 * `setThreadLabel(threadId)`. Showing the candidate id before any save
 * misleads users into thinking it's a saved conversation. */
function setThreadLabel(id) {
    if (!threadIdEl) return;
    if (!id) {
        threadIdEl.textContent = "New";
        threadIdEl.setAttribute(
            "title", "Fresh conversation — saved after the first message",
        );
        return;
    }
    threadIdEl.textContent =
        id.length > THREAD_LABEL_MAX
            ? `${id.slice(0, THREAD_LABEL_MAX)}…`
            : id;
    threadIdEl.setAttribute("title", id);
}

let threadId = "";
try { threadId = localStorage.getItem(LS_THREAD_KEY) || ""; } catch (_) { /* */ }
if (!threadId) {
    threadId = shortId();
    try { localStorage.setItem(LS_THREAD_KEY, threadId); } catch (_) { /* */ }
}
/* Tentative — `hydrateThreadHistory()` will downgrade to "New" if this
 * id has no Postgres rows. */
setThreadLabel(threadId);

newThreadBtn?.addEventListener("click", () => {
    threadId = shortId();
    try { localStorage.setItem(LS_THREAD_KEY, threadId); } catch (_) { /* */ }
    setThreadLabel("");
    clearConversation();
    _closeAllCatfilters();
});

/* Switch to an existing thread: persist the new id, clear the DOM,
 * pull and render its history. Called from a delegated click on the
 * picker rows. */
async function switchThread(id) {
    if (!id || id === threadId) {
        _closeAllCatfilters();
        return;
    }
    threadId = id;
    try { localStorage.setItem(LS_THREAD_KEY, threadId); } catch (_) { /* */ }
    setThreadLabel(threadId);
    clearConversation();
    _closeAllCatfilters();
    await hydrateThreadHistory();
}

/* Format a "2h ago" relative timestamp from an ISO string. Falls back
 * to the raw string if parsing fails. */
function relTime(iso) {
    if (!iso) return "";
    const t = Date.parse(iso);
    if (!Number.isFinite(t)) return iso;
    const s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 60)      return `${Math.floor(s)}s ago`;
    if (s < 3600)    return `${Math.floor(s / 60)}m ago`;
    if (s < 86400)   return `${Math.floor(s / 3600)}h ago`;
    if (s < 2592000) return `${Math.floor(s / 86400)}d ago`;
    return new Date(t).toISOString().slice(0, 10);
}

const threadListEl = document.getElementById("ycs-ask-thread-list");

async function loadThreadList() {
    if (!threadListEl) return;
    threadListEl.innerHTML =
        '<div class="ycs-ask-thread-empty">Loading…</div>';
    try {
        const r = await api("/agents/threads");
        const items = r.items ?? [];
        if (!items.length) {
            threadListEl.innerHTML =
                '<div class="ycs-ask-thread-empty">No saved threads yet.</div>';
            return;
        }
        const frag = document.createDocumentFragment();
        for (const it of items) {
            const row = document.createElement("div");
            row.className = "ycs-ask-thread-row";
            if (it.thread_id === threadId) row.classList.add("active");
            row.dataset.threadId = it.thread_id;
            const preview = (it.first_question || "(no title)").slice(0, 64);
            const turnTxt = `${it.turn_count} turn${it.turn_count === 1 ? "" : "s"}`;
            /* Two nested buttons (pick + delete) inside a non-button row
             * wrapper — nesting buttons inside buttons is invalid HTML.
             * Event delegation on threadListEl routes by `data-action`. */
            row.innerHTML = `
                <button type="button" class="ycs-ask-thread-row-pick"
                        data-action="pick"
                        title="Switch to this thread">
                    <span class="ycs-ask-thread-row-id">${htmlEscape(it.thread_id)}</span>
                    <span class="ycs-ask-thread-row-title">${htmlEscape(preview)}</span>
                    <span class="ycs-ask-thread-row-meta">${htmlEscape(turnTxt)} · ${htmlEscape(relTime(it.last_seen))}</span>
                </button>
                <button type="button" class="ycs-ask-thread-row-delete"
                        data-action="delete"
                        aria-label="Delete thread"
                        title="Delete this thread and all of its turns">🗑</button>
            `;
            frag.appendChild(row);
        }
        threadListEl.replaceChildren(frag);
    } catch (e) {
        threadListEl.innerHTML =
            `<div class="ycs-ask-thread-empty">Failed: ${htmlEscape(e.message)}</div>`;
    }
}

/* Delete the given thread server-side + refresh the picker. If the
 * deleted thread is the one we're currently on, regenerate a fresh
 * id and wipe the conversation DOM — leaving the user on a dangling
 * empty-data thread would be confusing. */
async function deleteThread(id) {
    if (!id) return;
    /* Same in-page confirm modal the DD Catalog page uses to delete an
     * ingested framework. `#fw-modal` DOM lives on the YCS shell via
     * `features/ycs/page.py::ConfirmModal()`. */
    const yes = await showConfirm(
        "Delete thread",
        `Permanently delete thread "${id}" and all of its turns? This `
        + `cannot be undone.`,
        "Delete",
    );
    if (!yes) return;
    try {
        await api(`/agents/threads/${encodeURIComponent(id)}`, {
            method: "DELETE",
        });
    } catch (e) {
        showToast(`Delete failed: ${e.message}`);
        return;
    }
    if (id === threadId) {
        threadId = shortId();
        try { localStorage.setItem(LS_THREAD_KEY, threadId); } catch (_) { /* */ }
        setThreadLabel("");
        clearConversation();
    }
    await loadThreadList();
}

threadListEl?.addEventListener("click", (ev) => {
    const btn = ev.target.closest?.("[data-action]");
    if (!btn) return;
    ev.stopPropagation();
    const row = btn.closest(".ycs-ask-thread-row");
    const id  = row?.dataset.threadId;
    if (!id) return;
    if (btn.dataset.action === "pick")   switchThread(id);
    if (btn.dataset.action === "delete") deleteThread(id);
});

bindCatfilter("ycs-ask-thread-trigger", "ycs-ask-thread", loadThreadList);

/* Boot rehydration. Pull persisted turns from Postgres and render them
 * in chronological order so the conversation panel survives refreshes.
 * Non-fatal — silent on any failure (offline, no thread, etc.). */
async function hydrateThreadHistory() {
    if (!threadId || !conversationEl) return;
    try {
        const r = await api(`/agents/history/${encodeURIComponent(threadId)}`);
        const items = r.items ?? [];
        if (items.length) {
            hideEmptyState();
            setThreadLabel(threadId);
        } else {
            // The id stays in localStorage as the candidate for the next
            // send — but the trigger reads "New" so the user doesn't
            // think a saved conversation exists.
            setThreadLabel("");
        }
        for (const item of items) {
            renderHistoryTurn({
                question:   item.question   ?? "",
                answer:     item.answer     ?? "",
                mode:       item.mode       ?? "",
                created_at: item.created_at ?? "",
            });
        }
    } catch (_) { /* non-fatal */ }
}
hydrateThreadHistory();

// Example-question chips fill the composer + scroll into view.
document.querySelectorAll(".ycs-ask-example-chip").forEach((chip) => {
    chip.addEventListener("click", () => {
        const q = chip.dataset.question || chip.textContent || "";
        askInput.value = q;
        askInput.focus();
        askInput.scrollIntoView({ block: "center", behavior: "smooth" });
    });
});

/* Per-node "what's happening right now" subtitle, rendered inline under
 * the matching stage pill's label. SOTA pattern (Claude, ChatGPT GPT-5.4
 * Thinking, Cursor 3.5): inline italic verb that says what's in flight.
 * Cleared once the next stage activates. */
const NODE_ACTION_TEXT = {
    contextualize:       "Resolving prior context",
    classify_query:      "Classifying intent",
    retrieve:            "Searching transcripts",
    rewrite_query:       "Refining query",
    grade_documents:     "Grading documents",
    plan_research:       "Planning sub-questions",
    run_subagent:        "Researching sub-question",
    direct_answer:       "Composing answer",
    run_standard:        "Running standard pipeline",
    generate:            "Writing answer",
    synthesize:          "Synthesizing findings",
    check_hallucination: "Verifying grounding",
    format_citations:    "Formatting citations",
    critic:              "Assessing confidence",
};

/* Keys match the EXACT node names emitted by the LangGraph backend
 * (see `apps/fastapi/domains/ycs/rag/{standard,adaptive}/graph.py`).
 * Old short aliases (`grade`, `hallucination`, `rewrite`) silently
 * dropped because no node emits them; the long names are what arrive
 * over SSE. `STAGE_ORDER` lets `markStage` advance all prior stages
 * to `done` when a later one goes `active` — without this the timeline
 * pills would skip the green-done transition entirely. */
const STAGE_ORDER = ["retrieve", "grade", "generate", "verify"];

const STAGE_MAP = {
    contextualize:       "retrieve",
    classify_query:      "retrieve",
    direct_answer:       "generate",
    run_standard:        "generate",
    plan_research:       "retrieve",
    run_subagent:        "retrieve",
    synthesize:          "generate",
    critic:              "verify",
    retrieve:            "retrieve",
    grade_documents:     "grade",
    generate:            "generate",
    check_hallucination: "verify",
    rewrite_query:       "retrieve",
    format_citations:    "verify",
};

/* All renderXxx functions below scope to the in-flight turn via `_q(...)`.
 * Each is a no-op when `currentTurnEl` is null (defensive — should never
 * happen mid-stream, but cheap to guard). */

function renderFollowups(subQuestions) {
    const host = _q(".ycs-ask-followups");
    if (!host || !subQuestions?.length) return;
    host.replaceChildren();
    const label = document.createElement("span");
    label.className = "ycs-ask-followups-label";
    label.textContent = "Followups";
    host.appendChild(label);
    for (const q of subQuestions) {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "ycs-ask-followup-chip";
        chip.textContent = q;
        chip.addEventListener("click", () => {
            askInput.value = q;
            askInput.focus();
            askInput.scrollIntoView({ block: "center", behavior: "smooth" });
        });
        host.appendChild(chip);
    }
}

function renderDeepCards(subQuestions, researchPlan) {
    const deep   = _q(".ycs-ask-deep");
    const cards  = _q(".ycs-ask-deep-cards");
    const banner = _q(".ycs-ask-deep-banner");
    if (!deep || !cards) return;
    deep.style.display = "block";
    cards.replaceChildren();
    deepCardIndex.clear();
    /* P6 preview-mode rendering: each card gets a checkbox (default
     * checked) and a "Run research" button is appended at the bottom.
     * On click, the chosen subset is fed back to the backend via the
     * second pass (sub_questions=[...], no preview_plan). */
    const isPreview = !!currentTurnEl
        && currentTurnEl.classList.contains("ycs-ask-turn-preview");
    if (banner) {
        const planTxt = researchPlan
            ? ` · ${htmlEscape(String(researchPlan).slice(0, 200))}`
            : "";
        const prefix = isPreview ? "Plan preview" : "Research plan";
        banner.innerHTML =
            `<strong>${prefix}</strong> ${subQuestions.length} sub-questions${planTxt}`;
    }
    for (const q of subQuestions) {
        const card = document.createElement("div");
        card.className = "ycs-ask-deep-card";
        card.dataset.state = isPreview ? "preview" : "queued";
        const stateLabel = isPreview ? "pending" : "queued";
        const checkbox = isPreview
            ? `<input type="checkbox" class="ycs-ask-deep-check"
                      value="${htmlEscape(q)}" checked>`
            : "";
        card.innerHTML = `
            <div class="ycs-ask-deep-card-head">
                ${checkbox}
                <span class="ycs-ask-deep-card-state">${stateLabel}</span>
                <span class="ycs-ask-deep-card-q">${htmlEscape(q)}</span>
            </div>
            <div class="ycs-ask-deep-card-body"></div>
        `;
        cards.appendChild(card);
        deepCardIndex.set(q, card);
    }
    if (isPreview) {
        const actions = document.createElement("div");
        actions.className = "ycs-ask-deep-actions";
        actions.innerHTML = `
            <button type="button" class="ycs-ask-deep-run">Run research</button>
            <span class="ycs-ask-deep-actions-hint">
                Uncheck any sub-question you don't want to research.
            </span>
        `;
        cards.appendChild(actions);
    }
}

/* "Run research" — user confirmed the preview plan; fire the second
 * pass into the SAME turn (reuseTurn) with the checked subset as
 * `sub_questions` so the backend skips `plan_research`. */
conversationEl?.addEventListener("click", async (ev) => {
    const btn = ev.target.closest?.(".ycs-ask-deep-run");
    if (!btn) return;
    ev.stopPropagation();
    const turn = btn.closest(".ycs-ask-turn");
    if (!turn) return;
    const checked = [...turn.querySelectorAll(".ycs-ask-deep-check:checked")]
        .map((cb) => cb.value);
    if (!checked.length) {
        showToast("Pick at least one sub-question.");
        return;
    }
    const question = turn.dataset.question || "";
    if (!question) return;
    currentTurnEl = turn;
    await sendQuestion(question, {
        reuseTurn:     true,
        sub_questions: checked,
    });
});

function advanceDeepCard(question, answerPreview) {
    if (!question || !deepCardIndex.size) return;
    const card = deepCardIndex.get(question);
    if (!card) return;
    card.dataset.state = "done";
    const stateEl = card.querySelector(".ycs-ask-deep-card-state");
    if (stateEl) stateEl.textContent = "done";
    const bodyEl = card.querySelector(".ycs-ask-deep-card-body");
    if (bodyEl && answerPreview) {
        bodyEl.textContent = answerPreview;
    }
}

function markStage(stage, state) {
    if (!stage) return;
    const stages = _q(".ycs-ask-stages");
    if (!stages) return;
    const node = stages.querySelector(`[data-stage="${stage}"]`);
    if (!node) return;
    if (state === "active") {
        const idx = STAGE_ORDER.indexOf(stage);
        for (const c of stages.querySelectorAll(".ycs-step-circle")) {
            const cidx = STAGE_ORDER.indexOf(c.dataset.stage);
            if (cidx < idx) {
                c.classList.add("done");
                c.classList.remove("active");
            } else if (cidx === idx) {
                c.classList.add("active");
                c.classList.remove("done");
            } else {
                c.classList.remove("active", "done");
            }
        }
    } else if (state === "done") {
        node.classList.remove("active");
        node.classList.add("done");
    }
}

function renderError(message) {
    const body = _q(".ycs-ask-turn-body.ycs-ask-answer");
    if (!body) return;
    body.innerHTML = "";
    const pill = document.createElement("div");
    pill.className = "ycs-ask-error-pill";
    pill.innerHTML = `<strong>Error</strong><span>${htmlEscape(message)}</span>`;
    body.appendChild(pill);
    const stages = _q(".ycs-ask-stages");
    if (stages) {
        for (const c of stages.querySelectorAll(".ycs-step-circle.active")) {
            c.classList.remove("active");
        }
    }
}

function renderCitation(c) {
    const card = document.createElement("a");
    card.className = "ycs-ask-citation";
    card.target = "_blank";
    card.rel = "noopener";
    card.href = c.url ?? "#";
    const vid = c.video_id ?? "";
    // YouTube serves mqdefault.jpg for every public video. maxresdefault
    // is hit-or-miss; mq is the most reliable thumbnail.
    const thumb = vid
        ? `<img class="ycs-ask-citation-thumb" loading="lazy"
                onerror="this.style.display='none'"
                src="https://i.ytimg.com/vi/${encodeURIComponent(vid)}/mqdefault.jpg"
                alt="">`
        : "";
    card.innerHTML = `
        ${thumb}
        <div class="ycs-ask-citation-body">
            <div class="ycs-ask-citation-head">
                <span class="ycs-ask-citation-channel">${htmlEscape(c.channel ?? "Citation")}</span>
                <span class="ycs-ask-citation-source">${htmlEscape(c.source ?? "")}</span>
            </div>
            <div class="ycs-ask-citation-title" title="${htmlEscape(c.title ?? "")}">${htmlEscape(c.title ?? "(untitled)")}</div>
            <div class="ycs-ask-citation-meta">
                <span>${htmlEscape(vid)}</span>
                <span>${htmlEscape(c.timestamp ?? "")}</span>
            </div>
        </div>
    `;
    return card;
}

function setStageAction(stage, text) {
    if (!stage) return;
    const stages = _q(".ycs-ask-stages");
    if (!stages) return;
    const pill = stages.querySelector(`[data-stage="${stage}"]`);
    if (!pill) return;
    const slot = pill.querySelector(".ycs-step-circle-action");
    if (slot) slot.textContent = text || "";
}

function clearAllStageActions() {
    const stages = _q(".ycs-ask-stages");
    if (!stages) return;
    stages.querySelectorAll(".ycs-step-circle-action")
        .forEach((s) => { s.textContent = ""; });
}

function applyUpdate(node, update) {
    let mappedStage = STAGE_MAP[node];
    if (mappedStage) {
        markStage(mappedStage, "active");
        // Clear stale action labels from prior stages and set the
        // current one's live verb (e.g. "Searching transcripts").
        clearAllStageActions();
        const action = NODE_ACTION_TEXT[node];
        if (action) setStageAction(mappedStage, action);
    }

    if (update.mode) {
        askStatus.textContent = `Mode: ${update.mode}`;
        const badge = _q(".ycs-ask-turn-mode-badge");
        if (badge) {
            badge.textContent = update.mode;
            badge.dataset.mode = update.mode;
        }
    }
    if (update.documents != null && update.document_count != null) {
        askStatus.textContent =
            `Retrieved ${update.document_count} document(s).`;
        markStage("retrieve", "done");
    }
    if (update.generation) {
        markStage("generate", "active");
        const body = _q(".ycs-ask-turn-body.ycs-ask-answer");
        const data = getTurnData(currentTurnEl);
        if (body && data) {
            // Re-render the full streaming buffer on every chunk —
            // marked is fast enough at <100KB and incremental parsers
            // are fragile on partial markdown. Full re-render is safe.
            // Citations may not have arrived yet — `[Video: title]`
            // markers stay as plain text until they do (and the next
            // re-render after `update.citations` swaps them for pills).
            const wasFollowing = _isPageNearBottom();
            data.generation = update.generation;
            body.innerHTML = renderMarkdownWithCitations(
                data.generation, data.citations,
            );
            // Only auto-follow the stream if the user was already at
            // the bottom. If they scrolled up to re-read earlier
            // turns, leave their viewport alone — same as ChatGPT.
            if (wasFollowing) _scrollPageToBottom(false);
        }
    }
    if (update.confidence_score != null) {
        markStage("verify", "active");
        const pct = (update.confidence_score * 100).toFixed(0);
        askStatus.textContent = `Confidence: ${pct}%`;
        const banner = _q(".ycs-ask-deep-banner");
        const deep   = _q(".ycs-ask-deep");
        if (banner && deep && deep.style.display !== "none") {
            banner.innerHTML =
                `<strong>Critic</strong> confidence ${pct}%`;
        }
    }
    if (Array.isArray(update.citations) && update.citations.length) {
        const data = getTurnData(currentTurnEl);
        if (data) {
            data.citations = update.citations;
            updateSourcesRail(update.citations);
            // Re-render the body now that citations are known so the
            // inline `[Video: title]` markers swap for `[N]` pills.
            const body = _q(".ycs-ask-turn-body.ycs-ask-answer");
            if (body && data.generation) {
                body.innerHTML = renderMarkdownWithCitations(
                    data.generation, data.citations,
                );
            }
        }
    }
    // DEEP-mode events ----------------------------------------------------
    if (Array.isArray(update.sub_questions) && update.sub_questions.length) {
        currentSubQuestions = update.sub_questions.slice();
        renderDeepCards(update.sub_questions, update.research_plan || "");
    }
    if (update.latest_sub_question) {
        advanceDeepCard(
            update.latest_sub_question,
            update.latest_sub_answer_preview || "",
        );
    }
    if (node === "synthesize") {
        const banner = _q(".ycs-ask-deep-banner");
        const deep   = _q(".ycs-ask-deep");
        if (banner && deep && deep.style.display !== "none") {
            const n = deepCardIndex.size;
            banner.innerHTML =
                `<strong>Synthesizing</strong> merging ${n} sub-answers…`;
        }
    }
}

async function consumeSSE(payload, signal) {
    const r = await fetch(API + "/agents/search/stream", {
        method: "POST",
        headers: {
            "content-type": "application/json",
            accept: "text/event-stream",
        },
        body:   JSON.stringify(payload),
        signal,
    });
    if (!r.ok || !r.body) {
        const t = await r.text();
        throw new Error(t || `HTTP ${r.status}`);
    }
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        // SSE delimiter = blank line. Process complete frames.
        let idx;
        while ((idx = buf.indexOf("\n\n")) !== -1) {
            const frame = buf.slice(0, idx).trim();
            buf = buf.slice(idx + 2);
            if (!frame.startsWith("data:")) continue;
            const json = frame.slice(5).trim();
            let evt;
            try { evt = JSON.parse(json); } catch (_) { continue; }
            const node = evt.node ?? "unknown";
            if (node === "end") {
                // Preview-mode end ("status":"preview") — the user must
                // confirm the plan before fan-out runs. Stay on the
                // current turn, leave the deep cards visible with
                // checkboxes + Run-research button (handled in
                // applyUpdate below). DON'T freeze or promote the
                // thread label — no row was saved server-side.
                if (evt.status === "preview") {
                    askStatus.textContent = "Plan ready — pick which sub-questions to research.";
                    askStatus.className = "ycs-search-status";
                    return;
                }
                markStage("verify", "done");
                askStatus.textContent = "Done.";
                askStatus.className = "ycs-search-status";
                renderFollowups(currentSubQuestions);
                freezeCurrentTurn();
                // The candidate threadId now has a real Postgres row —
                // promote the trigger from "New" to the live id.
                setThreadLabel(threadId);
                return;
            }
            if (node === "error") {
                renderError(evt.error || "stream error");
                setStatus(askStatus, "error", "Stream error");
                return;
            }
            applyUpdate(node, evt);
        }
    }
}

/* The core "send a question" path — extracted so the per-turn
 * Regenerate action and the DEEP plan-preview second pass can re-fire
 * without duplicating the SSE wiring. Options:
 *   - `preview_plan` (bool) — DEEP plan preview (P6): backend halts
 *     after `plan_research` so the user can prune sub-questions.
 *   - `sub_questions` (array) — bypass the planner LLM, use these.
 *   - `reuseTurn` (bool) — stream into the existing `currentTurnEl`
 *     instead of creating a new one (used by the preview → execute
 *     hand-off so the conversation reads as one turn). */
async function sendQuestion(question, opts = {}) {
    if (!question) return;
    if (!opts.reuseTurn) startNewTurn(question);
    if (opts.preview_plan && currentTurnEl) {
        currentTurnEl.classList.add("ycs-ask-turn-preview");
    } else if (currentTurnEl) {
        currentTurnEl.classList.remove("ycs-ask-turn-preview");
    }
    setStatus(askStatus, "running", "Thinking…");
    const channel_ids = [...selectedChannels];
    const payload = { question, thread_id: threadId };
    if (channel_ids.length)         payload.channel_ids   = channel_ids;
    if (activeMode)                 payload.force_mode    = activeMode;
    if (opts.preview_plan)          payload.preview_plan  = true;
    if (Array.isArray(opts.sub_questions) && opts.sub_questions.length) {
        payload.sub_questions = opts.sub_questions;
    }
    currentAbortController = new AbortController();
    setStreamingUI(true);
    try {
        await consumeSSE(payload, currentAbortController.signal);
    } catch (e) {
        if (e.name === "AbortError") {
            renderError("Stopped by user.");
            setStatus(askStatus, "error", "Stopped.");
        } else {
            renderError(e.message || "request failed");
            setStatus(askStatus, "error", `Failed: ${e.message}`);
        }
        freezeCurrentTurn();
    } finally {
        setStreamingUI(false);
        currentAbortController = null;
    }
}

askForm?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const question = (askInput.value ?? "").trim();
    if (!question) return;
    askInput.value = "";
    resetTextareaHeight();
    /* DEEP mode → first pass preview, second pass execute. Other modes
     * just send straight through. The hand-off happens on the
     * `.ycs-ask-deep-run` click handler defined below. */
    if (activeMode === "deep") {
        await sendQuestion(question, { preview_plan: true });
    } else {
        await sendQuestion(question);
    }
});

// ---- per-turn action chips (Copy / Regenerate / Branch) -------------------
async function copyTurnAnswer(turn) {
    const body = turn.querySelector(".ycs-ask-turn-body.ycs-ask-answer");
    const text = (body?.innerText || "").trim();
    if (!text) return;
    try {
        await navigator.clipboard.writeText(text);
        showToast("Answer copied to clipboard.");
    } catch (e) {
        showToast(`Copy failed: ${e.message}`);
    }
}

async function regenerateTurn(turn) {
    const q = turn.dataset.question || "";
    if (!q) return;
    await sendQuestion(q);
}

async function branchTurn(turn) {
    const yes = await showConfirm(
        "Branch conversation",
        `Fork this thread into a new conversation up to and including `
        + `this turn? You'll switch to the new thread; the original stays `
        + `untouched.`,
        "Branch",
    );
    if (!yes) return;
    const upTo = turn.dataset.createdAt || "";
    let r;
    try {
        r = await api(
            `/agents/threads/${encodeURIComponent(threadId)}/branch`,
            {
                method:  "POST",
                headers: { "content-type": "application/json" },
                body:    JSON.stringify({ up_to_created_at: upTo || null }),
            },
        );
    } catch (e) {
        showToast(`Branch failed: ${e.message}`);
        return;
    }
    const newId = r?.new_thread_id;
    if (!newId) return;
    threadId = newId;
    try { localStorage.setItem(LS_THREAD_KEY, threadId); } catch (_) { /* */ }
    setThreadLabel(threadId);
    clearConversation();
    await hydrateThreadHistory();
    showToast(`Branched into new thread (${r.copied} turns copied).`);
}

conversationEl?.addEventListener("click", (ev) => {
    const btn = ev.target.closest?.(".ycs-ask-turn-action");
    if (!btn) return;
    ev.stopPropagation();
    const turn = btn.closest(".ycs-ask-turn");
    if (!turn) return;
    switch (btn.dataset.action) {
        case "copy":       copyTurnAnswer(turn); break;
        case "regenerate": regenerateTurn(turn); break;
        case "branch":     branchTurn(turn);     break;
    }
});

stopBtn?.addEventListener("click", () => {
    currentAbortController?.abort();
});
