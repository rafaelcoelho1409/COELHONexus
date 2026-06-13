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

const API = "/api/v1/ycs";

// ---- helpers ---------------------------------------------------------------
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

const llmForm = document.getElementById("ycs-llm-form");
const llmTestBtn = document.getElementById("ycs-llm-test");

/* Collect FormData → { field: value } with the same normalization the
 * Save + Test handlers want. Empty strings are stripped so the
 * resulting object only carries fields the user actually filled in. */
function readLLMForm() {
    if (!llmForm) return {};
    const fd = new FormData(llmForm);
    const body = {};
    for (const [k, v] of fd.entries()) {
        if (v === "" || v === null) continue;
        body[k] = k === "temperature" ? parseFloat(v) : v;
    }
    if (!body.provider) body.provider = "NVIDIA";
    return body;
}

/* Populate the form from the persisted Redis config (sans api_key, which
 * the server redacts). The api_key field is left empty + gets a
 * placeholder hinting at the saved state. Non-fatal on miss. */
async function hydrateLLMForm() {
    if (!llmForm) return;
    try {
        const r = await api("/agents/config");
        const cfg = r?.config ?? {};
        for (const id of ["provider", "model", "temperature", "base_url"]) {
            const el = document.getElementById(`ycs-llm-${id === "base_url" ? "base" : id === "temperature" ? "temp" : id}`);
            if (el && cfg[id] != null) el.value = cfg[id];
        }
        const key = document.getElementById("ycs-llm-key");
        if (key && r?.has_api_key) {
            key.placeholder = "(saved — enter to replace)";
        }
    } catch (_) { /* non-fatal */ }
}
hydrateLLMForm();

llmForm?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const status = document.getElementById("ycs-llm-status");
    setStatus(status, "running", "Saving…");
    try {
        const r = await api("/agents/config", {
            method: "PUT",
            headers: { "content-type": "application/json" },
            body: JSON.stringify(readLLMForm()),
        });
        setStatus(status, "", `Saved (${r.config?.provider ?? "?"}).`);
        // Wipe the password field so a refresh doesn't re-submit it.
        const key = document.getElementById("ycs-llm-key");
        if (key) {
            key.value = "";
            key.placeholder = "(saved — enter to replace)";
        }
    } catch (e) {
        setStatus(status, "error", `Save failed: ${e.message}`);
    }
});

llmTestBtn?.addEventListener("click", async () => {
    const status = document.getElementById("ycs-llm-status");
    setStatus(status, "running", "Testing…");
    try {
        const r = await api("/agents/config/test", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify(readLLMForm()),
        });
        if (r.status === "ok") {
            setStatus(status, "", `OK · ${r.model} · ${r.ms}ms`);
        } else {
            setStatus(status, "error", `Test failed: ${r.error}`);
        }
    } catch (e) {
        setStatus(status, "error", `Test failed: ${e.message}`);
    }
});

// ---- (2) Channel multi-select ---------------------------------------------
async function populateChannels() {
    const select = document.getElementById("ycs-ask-channels");
    if (!select) return;
    try {
        const r = await api("/admin/ingested-channels");
        for (const ch of r.items ?? []) {
            const opt = document.createElement("option");
            opt.value = ch.channel_id ?? "";
            opt.textContent = `${ch.channel ?? ch.channel_id} · ${ch.video_count}`;
            select.appendChild(opt);
        }
    } catch (_) {
        // Non-fatal — user can still ask without channel scope.
    }
}
populateChannels();

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
const askForm = document.getElementById("ycs-ask-form");
const askInput = document.getElementById("ycs-ask-input");
const askStatus = document.getElementById("ycs-ask-status");
const answerEl = document.getElementById("ycs-ask-answer");
const citationsEl = document.getElementById("ycs-ask-citations");
const stagesEl = document.getElementById("ycs-ask-stages");
const historyEl = document.getElementById("ycs-ask-history");
const threadIdEl = document.getElementById("ycs-ask-thread-id");
const newThreadBtn = document.getElementById("ycs-ask-new-thread");
const deepEl       = document.getElementById("ycs-ask-deep");
const deepCardsEl  = document.getElementById("ycs-ask-deep-cards");
const deepBannerEl = document.getElementById("ycs-ask-deep-banner");
const stopBtn      = document.getElementById("ycs-ask-stop");
const emptyEl      = document.getElementById("ycs-ask-empty");
const followupsEl  = document.getElementById("ycs-ask-followups");

/* Active AbortController for the in-flight SSE fetch. Click on Stop
 * → controller.abort() → fetch rejects with AbortError → caught and
 * rendered as a "Stopped" pill. Null when no request is in flight. */
let currentAbortController = null;

/* Captured during the stream so we can render them as clickable
 * followup chips on `end`. Cleared in resetConversation. */
let currentSubQuestions = [];

function hideEmptyState() {
    if (emptyEl) emptyEl.style.display = "none";
}

/* Index of sub-question text → card element so `run_subagent` events
 * (which carry `latest_sub_question`) can find the matching card to
 * advance. Refilled per query in `resetConversation()`. */
const deepCardIndex = new Map();

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

function setThreadLabel(id) {
    if (!threadIdEl) return;
    threadIdEl.textContent =
        id.length > THREAD_LABEL_MAX
            ? `${id.slice(0, THREAD_LABEL_MAX)}…`
            : id;
    threadIdEl.setAttribute("title", id);
}

function clearConversationDom() {
    historyEl?.replaceChildren();
    answerEl.textContent = "";
    citationsEl.replaceChildren();
    if (followupsEl) followupsEl.replaceChildren();
    if (stagesEl)    stagesEl.style.display = "none";
    if (deepEl)      deepEl.style.display    = "none";
    if (emptyEl)     emptyEl.style.display   = "";
    setStatus(askStatus, "", "");
}

let threadId = "";
try { threadId = localStorage.getItem(LS_THREAD_KEY) || ""; } catch (_) { /* */ }
if (!threadId) {
    threadId = shortId();
    try { localStorage.setItem(LS_THREAD_KEY, threadId); } catch (_) { /* */ }
}
setThreadLabel(threadId);

newThreadBtn?.addEventListener("click", () => {
    threadId = shortId();
    try { localStorage.setItem(LS_THREAD_KEY, threadId); } catch (_) { /* */ }
    setThreadLabel(threadId);
    clearConversationDom();
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
    clearConversationDom();
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
            const row = document.createElement("button");
            row.type = "button";
            row.className = "ycs-ask-thread-row";
            if (it.thread_id === threadId) row.classList.add("active");
            row.dataset.threadId = it.thread_id;
            const preview = (it.first_question || "(no title)").slice(0, 64);
            row.innerHTML = `
                <span class="ycs-ask-thread-row-id">${htmlEscape(it.thread_id)}</span>
                <span class="ycs-ask-thread-row-title">${htmlEscape(preview)}</span>
                <span class="ycs-ask-thread-row-meta">${it.turn_count} turn${it.turn_count === 1 ? "" : "s"} · ${htmlEscape(relTime(it.last_seen))}</span>
            `;
            frag.appendChild(row);
        }
        threadListEl.replaceChildren(frag);
    } catch (e) {
        threadListEl.innerHTML =
            `<div class="ycs-ask-thread-empty">Failed: ${htmlEscape(e.message)}</div>`;
    }
}

threadListEl?.addEventListener("click", (ev) => {
    const row = ev.target.closest?.(".ycs-ask-thread-row");
    if (!row) return;
    ev.stopPropagation();
    switchThread(row.dataset.threadId);
});

bindCatfilter("ycs-ask-thread-trigger", "ycs-ask-thread", loadThreadList);

/* DOM-render one Q+A turn into the history strip. Shared between
 * `archiveCurrentTurn` (fresh stream just completed) and the page-boot
 * history rehydrator. */
function renderTurn({ question, answer, citationsHTML = "", mode = "" }) {
    const turn = document.createElement("div");
    turn.className = "ycs-ask-turn";
    const modeBadge = mode
        ? `<span class="ycs-ask-turn-mode">${htmlEscape(mode)}</span>`
        : "";
    turn.innerHTML = `
        <div class="ycs-ask-turn-user">
            <span class="ycs-ask-turn-role">You</span>
            <div class="ycs-ask-turn-body">${htmlEscape(question)}</div>
        </div>
        <div class="ycs-ask-turn-assistant">
            <span class="ycs-ask-turn-role">Answer ${modeBadge}</span>
            <div class="ycs-ask-turn-body">${htmlEscape(answer)}</div>
            ${citationsHTML ? `<div class="ycs-ask-turn-citations">${citationsHTML}</div>` : ""}
        </div>
    `;
    historyEl.appendChild(turn);
    historyEl.scrollTop = historyEl.scrollHeight;
}

/* Snapshot the freshly-completed Q+A into the history strip so the
 * next ask doesn't clobber it. Called from `consumeSSE` on the `end`
 * event when the answer is non-empty. */
function archiveCurrentTurn(question) {
    const answer = (answerEl.textContent || "").trim();
    if (!answer) return;
    renderTurn({
        question,
        answer,
        citationsHTML: citationsEl.innerHTML,
    });
}

/* Boot rehydration. Pull persisted turns from Postgres and render them
 * in chronological order so the conversation panel survives refreshes.
 * Non-fatal — silent on any failure (offline, no thread, etc.). */
async function hydrateThreadHistory() {
    if (!threadId || !historyEl) return;
    try {
        const r = await api(`/agents/history/${encodeURIComponent(threadId)}`);
        const items = r.items ?? [];
        if (items.length) hideEmptyState();
        for (const item of items) {
            renderTurn({
                question: item.question ?? "",
                answer:   item.answer ?? "",
                mode:     item.mode ?? "",
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

function resetConversation() {
    hideEmptyState();
    answerEl.textContent = "";
    citationsEl.innerHTML = "";
    if (followupsEl) followupsEl.replaceChildren();
    stagesEl.style.display = "flex";
    for (const c of stagesEl.querySelectorAll(".ycs-step-circle")) {
        c.classList.remove("active", "done");
    }
    if (deepEl) deepEl.style.display = "none";
    if (deepCardsEl)  deepCardsEl.replaceChildren();
    if (deepBannerEl) deepBannerEl.textContent = "";
    deepCardIndex.clear();
    currentSubQuestions = [];
}

function renderFollowups(subQuestions) {
    if (!followupsEl || !subQuestions?.length) return;
    followupsEl.replaceChildren();
    const label = document.createElement("span");
    label.className = "ycs-ask-followups-label";
    label.textContent = "Followups";
    followupsEl.appendChild(label);
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
        followupsEl.appendChild(chip);
    }
}

function renderDeepCards(subQuestions, researchPlan) {
    if (!deepEl || !deepCardsEl) return;
    deepEl.style.display = "block";
    deepCardsEl.replaceChildren();
    deepCardIndex.clear();
    if (deepBannerEl) {
        const planTxt = researchPlan
            ? ` · ${htmlEscape(String(researchPlan).slice(0, 200))}`
            : "";
        deepBannerEl.innerHTML =
            `<strong>Research plan</strong> ${subQuestions.length} sub-questions${planTxt}`;
    }
    for (const q of subQuestions) {
        const card = document.createElement("div");
        card.className = "ycs-ask-deep-card";
        card.dataset.state = "queued";
        card.innerHTML = `
            <div class="ycs-ask-deep-card-head">
                <span class="ycs-ask-deep-card-state">queued</span>
                <span class="ycs-ask-deep-card-q">${htmlEscape(q)}</span>
            </div>
            <div class="ycs-ask-deep-card-body"></div>
        `;
        deepCardsEl.appendChild(card);
        deepCardIndex.set(q, card);
    }
}

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
    const node = stagesEl.querySelector(`[data-stage="${stage}"]`);
    if (!node) return;
    if (state === "active") {
        const idx = STAGE_ORDER.indexOf(stage);
        for (const c of stagesEl.querySelectorAll(".ycs-step-circle")) {
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
    answerEl.innerHTML = "";
    const pill = document.createElement("div");
    pill.className = "ycs-ask-error-pill";
    pill.innerHTML = `<strong>Error</strong><span>${htmlEscape(message)}</span>`;
    answerEl.appendChild(pill);
    for (const c of stagesEl.querySelectorAll(".ycs-step-circle.active")) {
        c.classList.remove("active");
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

function applyUpdate(node, update) {
    let mappedStage = STAGE_MAP[node];
    if (mappedStage) markStage(mappedStage, "active");

    if (update.mode) {
        askStatus.textContent = `Mode: ${update.mode}`;
    }
    if (update.documents != null && update.document_count != null) {
        askStatus.textContent =
            `Retrieved ${update.document_count} document(s).`;
        markStage("retrieve", "done");
    }
    if (update.generation) {
        markStage("generate", "active");
        answerEl.textContent = update.generation;
        // Pin the latest content into view as it streams in.
        answerEl.scrollIntoView({ block: "end", behavior: "smooth" });
    }
    if (update.confidence_score != null) {
        markStage("verify", "active");
        const pct = (update.confidence_score * 100).toFixed(0);
        askStatus.textContent = `Confidence: ${pct}%`;
        if (deepBannerEl && deepEl?.style.display !== "none") {
            deepBannerEl.innerHTML =
                `<strong>Critic</strong> confidence ${pct}%`;
        }
    }
    if (Array.isArray(update.citations) && update.citations.length) {
        const frag = document.createDocumentFragment();
        for (const c of update.citations) frag.appendChild(renderCitation(c));
        citationsEl.replaceChildren(frag);
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
    if (node === "synthesize" && deepBannerEl
        && deepEl?.style.display !== "none") {
        const n = deepCardIndex.size;
        deepBannerEl.innerHTML =
            `<strong>Synthesizing</strong> merging ${n} sub-answers…`;
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
                markStage("verify", "done");
                askStatus.textContent = "Done.";
                askStatus.className = "ycs-search-status";
                renderFollowups(currentSubQuestions);
                archiveCurrentTurn(payload.question);
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

askForm?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const question = (askInput.value ?? "").trim();
    if (!question) return;
    resetConversation();
    setStatus(askStatus, "running", "Thinking…");
    askInput.value = "";
    // Read selected channel scope (multi-select).
    const select = document.getElementById("ycs-ask-channels");
    const channel_ids = [...(select?.selectedOptions ?? [])]
        .map((o) => o.value).filter(Boolean);
    const payload = { question, thread_id: threadId };
    if (channel_ids.length) payload.channel_ids = channel_ids;
    if (activeMode) payload.force_mode = activeMode;
    currentAbortController = new AbortController();
    if (stopBtn) stopBtn.style.display = "inline-flex";
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
    } finally {
        if (stopBtn) stopBtn.style.display = "none";
        currentAbortController = null;
    }
});

stopBtn?.addEventListener("click", () => {
    currentAbortController?.abort();
});
