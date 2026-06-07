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

// ---- (1) LLM config form ---------------------------------------------------
const llmToggle = document.querySelector("#ycs-llm-panel .ycs-filters-toggle");
const llmBody = document.querySelector("#ycs-llm-form");
llmToggle?.addEventListener("click", () => {
    const open = llmBody.classList.toggle("ycs-llm-open");
    llmToggle.classList.toggle("open", open);
    llmToggle.setAttribute("aria-expanded", String(open));
});

const llmForm = document.getElementById("ycs-llm-form");
llmForm?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const status = document.getElementById("ycs-llm-status");
    setStatus(status, "running", "Saving…");
    const fd = new FormData(llmForm);
    const body = {};
    for (const [k, v] of fd.entries()) {
        if (v === "" || v === null) continue;
        body[k] = k === "temperature" ? parseFloat(v) : v;
    }
    if (!body.provider) body.provider = "NVIDIA";
    try {
        const r = await api("/agents/config", {
            method: "PUT",
            headers: { "content-type": "application/json" },
            body: JSON.stringify(body),
        });
        setStatus(status, "", `Saved (${r.config?.provider ?? "?"}).`);
        // Wipe the password field so a refresh doesn't re-submit it.
        const key = document.getElementById("ycs-llm-key");
        if (key) key.value = "";
    } catch (e) {
        setStatus(status, "error", `Save failed: ${e.message}`);
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

// ---- thread management -----------------------------------------------------
/* One thread_id per browser session per page-load. Same id → same
 * Postgres `conversation_history` row group, so back-and-forth turns
 * carry context. `New thread` regenerates the id and wipes the DOM
 * history (the Postgres rows stay, but the new id won't reach them).
 */
function shortId() {
    const u = (crypto.randomUUID?.() ?? `t-${Date.now()}-${Math.floor(Math.random() * 1e6)}`);
    return u.replace(/-/g, "").slice(0, 12);
}
let threadId = shortId();
if (threadIdEl) threadIdEl.textContent = threadId;

newThreadBtn?.addEventListener("click", () => {
    threadId = shortId();
    if (threadIdEl) threadIdEl.textContent = threadId;
    historyEl.replaceChildren();
    answerEl.textContent = "";
    citationsEl.replaceChildren();
    stagesEl.style.display = "none";
    setStatus(askStatus, "", "");
});

/* Snapshot the freshly-completed Q+A into the history strip so the
 * next ask doesn't clobber it. Called from `consumeSSE` on the `end`
 * event when the answer is non-empty. */
function archiveCurrentTurn(question) {
    const answer = (answerEl.textContent || "").trim();
    if (!answer) return;
    const turn = document.createElement("div");
    turn.className = "ycs-ask-turn";
    const citationsHTML = citationsEl.innerHTML;
    turn.innerHTML = `
        <div class="ycs-ask-turn-user">
            <span class="ycs-ask-turn-role">You</span>
            <div class="ycs-ask-turn-body">${htmlEscape(question)}</div>
        </div>
        <div class="ycs-ask-turn-assistant">
            <span class="ycs-ask-turn-role">Answer</span>
            <div class="ycs-ask-turn-body">${htmlEscape(answer)}</div>
            ${citationsHTML ? `<div class="ycs-ask-turn-citations">${citationsHTML}</div>` : ""}
        </div>
    `;
    historyEl.appendChild(turn);
    historyEl.scrollTop = historyEl.scrollHeight;
}

const STAGE_MAP = {
    contextualize:   "retrieve",
    classify_query:  "retrieve",
    direct_answer:   "generate",
    run_standard:    "generate",
    plan_research:   "retrieve",
    run_subagent:    "retrieve",
    synthesize:      "generate",
    critic:          "verify",
    retrieve:        "retrieve",
    grade:           "grade",
    generate:        "generate",
    hallucination:   "verify",
    answer_relevance:"verify",
    rewrite:         "retrieve",
};

function resetConversation() {
    answerEl.textContent = "";
    citationsEl.innerHTML = "";
    stagesEl.style.display = "flex";
    for (const c of stagesEl.querySelectorAll(".ycs-step-circle")) {
        c.classList.remove("active", "done");
    }
}

function markStage(stage, state) {
    if (!stage) return;
    const node = stagesEl.querySelector(`[data-stage="${stage}"]`);
    if (!node) return;
    if (state === "active") {
        for (const c of stagesEl.querySelectorAll(".ycs-step-circle")) {
            c.classList.remove("active");
        }
        node.classList.add("active");
    } else if (state === "done") {
        node.classList.remove("active");
        node.classList.add("done");
    }
}

function renderCitation(c) {
    const card = document.createElement("a");
    card.className = "ycs-lib-card";
    card.target = "_blank";
    card.rel = "noopener";
    card.href = c.url ?? "#";
    card.innerHTML = `
        <div class="ycs-lib-card-head">
            <span class="ycs-lib-card-kind">${htmlEscape(c.channel ?? "Citation")}</span>
            <span class="ycs-lib-card-label" title="${htmlEscape(c.title)}">${htmlEscape(c.title ?? "(untitled)")}</span>
        </div>
        <div class="ycs-lib-card-meta">
            <span>${htmlEscape(c.timestamp ?? "")}</span>
            <span>${htmlEscape(c.video_id ?? "")}</span>
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
    }
    if (update.confidence_score != null) {
        markStage("verify", "active");
        askStatus.textContent =
            `Confidence: ${(update.confidence_score * 100).toFixed(0)}%`;
    }
    if (Array.isArray(update.citations) && update.citations.length) {
        const frag = document.createDocumentFragment();
        for (const c of update.citations) frag.appendChild(renderCitation(c));
        citationsEl.replaceChildren(frag);
    }
}

async function consumeSSE(payload) {
    const r = await fetch(API + "/agents/search/stream", {
        method: "POST",
        headers: {
            "content-type": "application/json",
            accept: "text/event-stream",
        },
        body: JSON.stringify(payload),
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
                archiveCurrentTurn(payload.question);
                return;
            }
            if (node === "error") {
                throw new Error(evt.error || "stream error");
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
    try {
        await consumeSSE(payload);
    } catch (e) {
        setStatus(askStatus, "error", `Failed: ${e.message}`);
    }
});
