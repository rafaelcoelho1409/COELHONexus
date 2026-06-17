/* YCS · Step 4 · Query — orchestrator for the SOTA workbench.
 *
 * Phase 1 + 2 wired here:
 *   · Row-3 pill strip → backend switch (editor language + scaffold + renderer)
 *   · CodeMirror 6 editor (./query/editor.js)
 *   · Run button → POST /api/v1/ycs/query/raw/{backend}
 *   · Per-backend renderer (./query/renderers.js) into the right pane
 *   · View-mode toggle (graph / table / json) for Neo4j results
 *
 * Phase 3 + 4 + 5 hook in here later — they're stubs today (`ai.js`,
 * `history.js`) so each phase is its own append-only module without
 * touching this entry file.
 */
import { makeEditor }                       from "@ycs/query/editor.js";
import { render, renderNeo4jGraphIfMissing, refreshView } from "@ycs/query/renderers.js";
import * as ai                              from "@ycs/query/ai.js";
import { makePanel as makeHistoryPanel,
         recordRun as historyRecord }        from "@ycs/query/history.js";


const API = "/api/v1/ycs/query";

// App pinned to YCS for now (cross-app pivot was removed 2026-06-15).
const APP = "ycs";

const state = {
    backend:   "elasticsearch",
    viewMode:  "table",
    editor:    null,             // editor handle from makeEditor()
    inflight:  null,             // AbortController for the active Run
    aiCtrl:    null,             // AbortController for the active AI gen
    cachedGraph: null,           // for Neo4j view-toggle without re-fetch
    namespaces: null,
};

const els = {};


// ---- helpers ---------------------------------------------------------------
function htmlEscape(s) {
    return String(s ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}


function backendLabel(key) {
    switch (key) {
        case "elasticsearch": return "Elasticsearch";
        case "qdrant":        return "Qdrant";
        case "neo4j":         return "Neo4j";
        default:              return key;
    }
}


async function postJSON(path, body, signal) {
    const r = await fetch(API + path, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify(body),
        signal,
    });
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


// ---- pill state ------------------------------------------------------------
function setPill(strip, dataAttr, value) {
    if (!strip) return;
    strip.querySelectorAll("[data-" + dataAttr + "]").forEach((btn) => {
        const active = btn.dataset[toCamel(dataAttr)] === value;
        btn.classList.toggle("active", active);
        btn.setAttribute("aria-pressed", active ? "true" : "false");
    });
}


function toCamel(s) {
    return s.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
}


// ---- header / status -------------------------------------------------------
function setStatus(text, kind) {
    if (!els.ehStatus) return;
    els.ehStatus.textContent = text;
    els.ehStatus.className = "ycs-query-eh-status"
        + (kind ? ` ycs-query-eh-status-${kind}` : "");
}


function updateHeader(resp) {
    if (els.ehBackend) els.ehBackend.textContent = backendLabel(state.backend);
    if (els.ehNamespace) {
        const m = state.namespaces?.matrix?.[APP]?.[state.backend];
        els.ehNamespace.textContent = m?.label ? `· ${m.label}` : "";
    }
    if (els.rhStats) {
        if (!resp) {
            els.rhStats.textContent = "";
            return;
        }
        const n   = (resp.hits || []).length;
        const tot = (resp.total != null) ? resp.total : n;
        const ms  = (resp.took_ms != null) ? ` in ${resp.took_ms} ms` : "";
        els.rhStats.textContent = `${n} / ${tot} rows${ms}`;
    }
}


// ---- run -------------------------------------------------------------------
function clearPanels() {
    ["graph", "table", "json"].forEach((k) => {
        if (els.panels[k]) els.panels[k].innerHTML = "";
    });
    if (els.notice) els.notice.innerHTML = "";
}


function showError(msg, notes) {
    const tail = (notes || []).length
        ? `<ul class="ycs-query-error-notes">${notes.map((n) => `<li>${htmlEscape(n)}</li>`).join("")}</ul>`
        : "";
    els.notice.innerHTML = `
        <div class="ycs-query-error">
            <strong>Query failed.</strong>
            <pre>${htmlEscape(msg)}</pre>
            ${tail}
        </div>`;
}


function showNotes(notes) {
    if (!notes || !notes.length) { els.notice.innerHTML = ""; return; }
    els.notice.innerHTML = `
        <div class="ycs-query-notes">
            <strong>Notes:</strong>
            <ul>${notes.map((n) => `<li>${htmlEscape(n)}</li>`).join("")}</ul>
        </div>`;
}


async function runEditor() {
    if (!state.editor) return;
    const body = state.editor.getText();
    if (!body.trim()) {
        showError("Editor is empty.", []);
        return;
    }

    if (state.inflight) state.inflight.abort();
    const ctrl = new AbortController();
    state.inflight = ctrl;

    setStatus("Running…", "running");
    if (els.empty) els.empty.style.display = "none";
    clearPanels();
    state.cachedGraph = null;

    let resp;
    try {
        resp = await postJSON(
            `/raw/${state.backend}`,
            { app: APP, body },
            ctrl.signal,
        );
    } catch (e) {
        if (e.name === "AbortError") { setStatus("Cancelled", "muted"); return; }
        setStatus("Error", "error");
        showError(e.message || String(e), []);
        updateHeader(null);
        return;
    } finally {
        if (state.inflight === ctrl) state.inflight = null;
    }

    updateHeader(resp);
    if (!resp.ok) {
        setStatus("Error", "error");
        showError(resp.error || "Unknown error", resp.notes || []);
        return;
    }

    setStatus(`Done · ${resp.took_ms} ms`, "ok");
    showNotes(resp.notes);
    const meta = render(state.backend, resp, els.panels, state.viewMode);
    state.cachedGraph = meta.graph;
    // Auto-record successful runs into history (Phase 5). Best-effort
    // — a Postgres outage won't surface to the user here.
    historyRecord({
        backend: state.backend,
        body,
        prompt:  els.aiPrompt?.value || "",
    });
}


// ---- view-mode toggle (Neo4j) ---------------------------------------------
function setViewMode(next) {
    if (next === state.viewMode) return;
    state.viewMode = next;
    if (els.results) els.results.dataset.viewMode = next;
    if (els.viewToggle) {
        els.viewToggle.querySelectorAll("[data-view-mode]").forEach((b) => {
            const active = b.dataset.viewMode === next;
            b.classList.toggle("active", active);
            b.setAttribute("aria-pressed", active ? "true" : "false");
        });
    }
    // Lazy graph render the first time the user flips to Graph after a
    // Neo4j Run (the table view was rendered eagerly).
    if (next === "graph" && state.backend === "neo4j" && state.cachedGraph) {
        renderNeo4jGraphIfMissing(els.panels, state.cachedGraph);
    }
    // Tabulator + Cytoscape both render at 0px height when their
    // container is `display:none` at mount time — force a redraw now
    // that the panel just became visible.
    refreshView(els.panels, next);
}


// ---- backend switch (called when the row-3 pill changes) -------------------
function setBackend(next) {
    if (next === state.backend) return;
    state.backend = next;
    state.editor?.setBackend(next);
    updateHeader(null);
    clearPanels();
    setStatus("Ready", null);
    // Hide the Graph toggle pill for non-Neo4j backends — the right
    // pane stays focused on the relevant view.
    if (els.viewToggle) {
        els.viewToggle.classList.toggle(
            "ycs-query-view-toggle-narrow",
            next !== "neo4j",
        );
    }
}


// ---- AI generation --------------------------------------------------------
function setAIStatus(text, kind) {
    if (!els.aiStatus) return;
    els.aiStatus.textContent = text || "";
    els.aiStatus.className = "ycs-query-ai-status"
        + (kind ? ` ycs-query-ai-status-${kind}` : "");
    if (els.aiStop) {
        els.aiStop.style.display = (kind === "running" || kind === "repair")
            ? "inline-flex" : "none";
    }
    if (els.aiGo) {
        els.aiGo.disabled = (kind === "running" || kind === "repair");
    }
}


/* Show or hide the FGTS-VA-selected model chip. `name` empty = hide
 * (the `hidden` attribute keeps the row collapse identical to the
 * pre-Generate state). */
function setAIModel(name) {
    if (!els.aiModel || !els.aiModelName) return;
    if (name && String(name).trim()) {
        els.aiModelName.textContent = String(name);
        els.aiModel.removeAttribute("hidden");
    } else {
        els.aiModelName.textContent = "";
        els.aiModel.setAttribute("hidden", "");
    }
}


function runAI() {
    if (state.aiCtrl) state.aiCtrl.abort();
    const prompt = (els.aiPrompt?.value || "").trim();
    if (!prompt) {
        setAIStatus("Type a question first.", "error");
        return;
    }
    const previous = state.editor?.getText() || "";
    state.aiCtrl = ai.start({
        backend:  state.backend,
        prompt,
        previous,
        editor:   state.editor,
        onStatus: setAIStatus,
        onModel:  setAIModel,
        onDone:   () => { state.aiCtrl = null; },
    });
}


function stopAI() {
    if (state.aiCtrl) {
        state.aiCtrl.abort();
        state.aiCtrl = null;
    }
}


// ---- init ------------------------------------------------------------------
function cacheEls() {
    els.editorMount = document.getElementById("ycs-query-editor");
    els.run         = document.getElementById("ycs-query-run");
    els.ehBackend   = document.getElementById("ycs-query-eh-backend");
    els.ehNamespace = document.getElementById("ycs-query-eh-namespace");
    els.ehStatus    = document.getElementById("ycs-query-eh-status");
    els.empty       = document.getElementById("ycs-query-empty");
    els.notice      = document.getElementById("ycs-query-notice");
    els.results     = document.getElementById("ycs-query-results");
    els.rhStats     = document.getElementById("ycs-query-rh-stats");
    els.viewToggle  = document.getElementById("ycs-query-view-toggle");
    els.panels = {
        graph: document.getElementById("ycs-query-results-graph"),
        table: document.getElementById("ycs-query-results-table"),
        json:  document.getElementById("ycs-query-results-json"),
    };
    els.backendStrip = document.querySelector(".ycs-query-backend-tabs");
    els.aiPrompt = document.getElementById("ycs-query-ai-prompt");
    els.aiGo     = document.getElementById("ycs-query-ai-go");
    els.aiStop   = document.getElementById("ycs-query-ai-stop");
    els.aiStatus = document.getElementById("ycs-query-ai-status");
    els.aiModel     = document.getElementById("ycs-query-ai-model");
    els.aiModelName = document.getElementById("ycs-query-ai-model-name");
    els.historyToggle = document.getElementById("ycs-query-history-toggle");
    els.editorHeader  = document.getElementById("ycs-query-editor-header");
}


function bindEvents() {
    if (els.run) {
        els.run.addEventListener("click", runEditor);
    }
    if (els.backendStrip) {
        els.backendStrip.addEventListener("click", (ev) => {
            const btn = ev.target.closest("[data-query-backend]");
            if (!btn || btn.disabled) return;
            const next = btn.dataset.queryBackend;
            if (!next || next === state.backend) return;
            setPill(els.backendStrip, "query-backend", next);
            setBackend(next);
        });
    }
    if (els.viewToggle) {
        els.viewToggle.addEventListener("click", (ev) => {
            const btn = ev.target.closest("[data-view-mode]");
            if (!btn) return;
            setViewMode(btn.dataset.viewMode);
        });
    }
    if (els.aiGo) {
        els.aiGo.addEventListener("click", runAI);
    }
    if (els.aiStop) {
        els.aiStop.addEventListener("click", stopAI);
    }
    if (els.aiPrompt) {
        // Ctrl/Cmd+Enter in the prompt fires the AI; Ctrl/Cmd+Shift+
        // Enter dispatches Run after AI. Same idiom as Hex's notebook
        // shortcuts.
        els.aiPrompt.addEventListener("keydown", (ev) => {
            if ((ev.ctrlKey || ev.metaKey) && ev.key === "Enter") {
                ev.preventDefault();
                if (ev.shiftKey) runEditor();
                else runAI();
            }
        });
    }
    // History panel — lazy mount inside the left column the first
    // time the user opens it. The panel is positioned via CSS to
    // overlay the editor zone.
    if (els.historyToggle) {
        let panel = null;
        const onRestore = (entry) => {
            if (!entry || !entry.body) return;
            // Switch backend first so the editor's language matches.
            if (entry.backend && entry.backend !== state.backend) {
                setPill(els.backendStrip, "query-backend", entry.backend);
                setBackend(entry.backend);
            }
            state.editor?.setText(entry.body);
        };
        els.historyToggle.addEventListener("click", () => {
            if (!panel) {
                const host = document.querySelector(".ycs-query-left");
                panel = makeHistoryPanel(host, { onRestore });
                state._historyPanel = panel;
            }
            if (state._historyVisible) {
                panel.hide();
                state._historyVisible = false;
            } else {
                panel.show(state.backend);
                state._historyVisible = true;
            }
        });
    }
}


async function fetchNamespaces() {
    try {
        const r = await fetch(API + "/namespaces");
        if (r.ok) state.namespaces = await r.json();
    } catch (_) { /* */ }
    updateHeader(null);
}


function showEditorLoadError(err) {
    if (!els.editorMount) return;
    const msg = (err && (err.message || String(err))) || "unknown";
    els.editorMount.removeAttribute("data-cm-loading");
    els.editorMount.innerHTML = `
        <div class="ycs-query-editor-loaderr">
            <strong>Editor failed to load.</strong>
            <pre>${String(msg).replace(/[&<>"]/g, (c) =>
                ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c])}</pre>
            <p>The backend tabs still work; you can paste a query into the
            fallback textarea below and Run.</p>
            <textarea id="ycs-query-fallback-input"
                      class="ycs-query-fallback-input"
                      rows="10"
                      placeholder="Paste DSL/Cypher/JSON here…"></textarea>
        </div>`;
    // Provide a minimal editor-handle shim so the rest of the
    // orchestrator (Run, AI, history) keeps working over the textarea.
    const ta = els.editorMount.querySelector("#ycs-query-fallback-input");
    state.editor = {
        backend: state.backend,
        getText:    () => ta.value,
        setText:    (s) => { ta.value = String(s ?? ""); },
        appendText: (s) => { ta.value += String(s ?? ""); },
        setBackend: (next) => { state.editor.backend = next; },
    };
    setStatus("Editor degraded — fallback textarea active", "error");
}


async function init() {
    cacheEls();
    if (!els.editorMount) return;       // not on Query page

    // Bind pill + AI + history events FIRST so backend switching
    // works even if the CodeMirror load fails (the editor mount has
    // a try/catch fallback below).
    bindEvents();

    try {
        state.editor = makeEditor(els.editorMount, { onRun: runEditor });
    } catch (e) {
        console.error("[ycs-query] CodeMirror load failed:", e);
        showEditorLoadError(e);
    }

    setBackend("elasticsearch");
    setViewMode("table");
    setStatus("Ready", null);
    fetchNamespaces();
}


/* `init()` is async at function level but the makeEditor import is
 * statically resolved at module load — the try/catch above only fires
 * on RUN-time errors inside makeEditor. ESM failures (the import map
 * resolution itself) surface as an unhandled module load error: catch
 * it on `window` so the user sees something. */
window.addEventListener("error", (ev) => {
    if (!ev.message) return;
    const m = String(ev.message);
    if (m.includes("codemirror") || m.includes("@codemirror")) {
        console.error("[ycs-query] CM module load failed:", ev);
        showEditorLoadError(ev.error || ev.message);
    }
});

init().catch((e) => {
    console.error("[ycs-query] init failed:", e);
    showEditorLoadError(e);
});
