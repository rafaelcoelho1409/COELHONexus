/* YCS · Neo4j LLM-usage drawer — reuses DD Planner/Synth's exact
 * rendering (`static/js/dd/shared/llm_totals.js`'s exported
 * `kpiGrid`/`modelTable`) so the same KPI grid + per-model call/token
 * table shows here, backed by YCS's own counter store
 * (`domains.ycs.runtime.llm_counter`, keyed by extract_id + video_id
 * rather than DD's thread_id + LangGraph node — see that module's
 * docstring for why the same shape needed a different capture path:
 * DD's raw-AsyncOpenAI hot path gets a usable response object back
 * directly; YCS's `LLMGraphTransformer` never surfaces one to its
 * caller, so capture happens via a LangChain callback instead).
 *
 * 2026-09-14: moved from an always-expanded inline section in the
 * Neo4j bar to a side-view drawer on request — follows RR's own
 * `_LlmUsageDrawer()` pattern exactly (`features/rr/pipeline.py` /
 * `static/js/rr/pipeline.js`'s `_openLlmDrawer`/`_closeLlmDrawer`),
 * not DD's heavier 3-section version: YCS has one call type
 * (full-transcript extraction), so a single KPI strip + model table
 * is the whole story. */
import { kpiGrid, modelTable } from "@dd/shared/llm_totals.js";

const API = "/api/v1/ycs";

function _num(v) {
    const n = Number(v || 0);
    return Number.isFinite(n) ? n : 0;
}

async function _fetchCounters(extractId) {
    if (!extractId) return null;
    try {
        const r = await fetch(
            `${API}/admin/pipeline/${encodeURIComponent(extractId)}/llm-counters`,
        );
        if (!r.ok) return null;
        return await r.json();
    } catch {
        return null;
    }
}

// Refreshed on the pipeline panel's own poll cycle (every ~5s while a
// run is tracked, see pipeline_panel.js) regardless of whether the
// drawer is currently open — so the moment the user opens it, it's
// already showing current data instead of waiting on a fresh fetch.
export async function refreshYcsNeo4jLlmUsage(extractId) {
    const host = document.getElementById("ycs-llm-drawer-totals");
    if (!host || !extractId) return;
    const payload = await _fetchCounters(extractId);
    const calls = _num(((payload || {}).total || {}).calls);
    if (!calls) {
        host.innerHTML = '<div class="dd-llm-rail-empty">No LLM usage recorded yet.</div>';
        return;
    }
    host.innerHTML = kpiGrid(payload) + modelTable(payload);
}

function _openYcsLlmDrawer() {
    const drawer = document.getElementById("ycs-llm-drawer");
    if (drawer) drawer.classList.add("visible");
}

function _closeYcsLlmDrawer() {
    const drawer = document.getElementById("ycs-llm-drawer");
    if (drawer) drawer.classList.remove("visible");
}

let _bound = false;

// Called once at pipeline-panel init (see pipeline_panel.js) — binds
// the "LLM usage" button inside the Neo4j bar (id depends on which
// bar prefix passed `show_llm_usage=True`; currently only "neo4j")
// plus the drawer's own close button + Escape key.
export function bindYcsLlmUsageDrawer() {
    if (_bound) return;
    _bound = true;
    document.getElementById("ycs-bar-neo4j-llm-open")
        ?.addEventListener("click", _openYcsLlmDrawer);
    document.getElementById("ycs-llm-drawer-close-btn")
        ?.addEventListener("click", _closeYcsLlmDrawer);
    document.addEventListener("keydown", (ev) => {
        if (ev.key === "Escape") _closeYcsLlmDrawer();
    });
}
