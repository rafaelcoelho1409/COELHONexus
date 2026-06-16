/* Per-backend result renderers for the right pane.
 *
 * Each renderer takes the `RawQueryResponse` JSON envelope + a `panels`
 * object (the three sibling div mount points: `graph`, `table`,
 * `json`) and fills them. The orchestrator (`query.js`) is responsible
 * for clearing prior content + flipping the `data-view-mode` attribute
 * on `#ycs-query-results` so CSS shows the right panel.
 *
 * The renderers DON'T mutate global state; they're pure DOM writers.
 */

// ---- helpers --------------------------------------------------------------
function htmlEscape(s) {
    return String(s ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}


/* Pretty-print JSON with conservative truncation so a single oversized
 * payload can't blow the renderer into MB-scale strings. */
function jsonBlock(value, { collapsed = false } = {}) {
    const text = JSON.stringify(value, null, 2);
    const t = htmlEscape(text);
    if (!collapsed) return `<pre class="ycs-query-json">${t}</pre>`;
    return `
        <details class="ycs-query-json-details">
            <summary>JSON (${text.length.toLocaleString()} chars)</summary>
            <pre class="ycs-query-json">${t}</pre>
        </details>`;
}


// ---- Elasticsearch — virtualized-ish table over _source ------------------
/* `RawQueryResponse.hits` for ES is `[{summary, raw: {_index, _id,
 * _score, _source}}]`. The table shows id, score, title, channel,
 * upload_date + a JSON details cell.
 *
 * Not strictly virtualized — capping at the server-side `size` cap
 * (200) keeps the DOM under a few hundred rows. If we later raise the
 * cap, swap in row virtualization. */
function renderES(resp, panels) {
    const hits = resp.hits || [];
    if (!hits.length) {
        panels.table.innerHTML = `
            <div class="ycs-query-empty-results">
                Query ran successfully — no hits.
            </div>`;
        return;
    }

    const head = `
        <tr>
            <th class="ycs-q-th-idx">#</th>
            <th>Index</th>
            <th>ID</th>
            <th>Score</th>
            <th>Title</th>
            <th>Detail</th>
        </tr>`;

    const rows = hits.map((h, i) => {
        const raw = h.raw || {};
        const src = raw._source || {};
        const title = src.title || src.video_id || raw._id || "(untitled)";
        const score = (raw._score != null) ? Number(raw._score).toFixed(3) : "—";
        const isTr  = (raw._index || "").endsWith("transcriptions");
        const snippet = isTr
            ? (src.content || "").slice(0, 220)
            : (src.description || "").slice(0, 220);
        return `
            <tr>
                <td class="ycs-q-th-idx">${i + 1}</td>
                <td><code>${htmlEscape(raw._index || "")}</code></td>
                <td><code>${htmlEscape(raw._id || "")}</code></td>
                <td>${htmlEscape(score)}</td>
                <td>
                    <div class="ycs-q-cell-title">${htmlEscape(title)}</div>
                    ${snippet ? `<div class="ycs-q-cell-sub">${htmlEscape(snippet)}…</div>` : ""}
                </td>
                <td>${jsonBlock(raw, { collapsed: true })}</td>
            </tr>`;
    }).join("");

    panels.table.innerHTML = `
        <table class="ycs-query-table">
            <thead>${head}</thead>
            <tbody>${rows}</tbody>
        </table>`;

    // JSON view = the whole array, expanded. Cheap to render.
    panels.json.innerHTML = jsonBlock(hits.map((h) => h.raw));
    panels.graph.innerHTML = `
        <div class="ycs-query-empty-results">
            Graph view is only available for Neo4j results.
        </div>`;
}


// ---- Qdrant — card grid ---------------------------------------------------
/* `RawQueryResponse.hits[].raw` for Qdrant is
 * `{id, score, payload}`. */
function renderQdrant(resp, panels) {
    const hits = resp.hits || [];
    if (!hits.length) {
        panels.table.innerHTML = `
            <div class="ycs-query-empty-results">
                Query ran successfully — no points.
            </div>`;
        return;
    }

    const cards = hits.map((h, i) => {
        const raw = h.raw || {};
        const payload = raw.payload || {};
        const title = payload.title
            || payload.video_id
            || payload.arxiv_id
            || raw.id
            || `Hit #${i + 1}`;
        const snippet = (payload.content || payload.abstract || "").slice(0, 300);
        const meta = [];
        if (raw.score != null)       meta.push(`score ${Number(raw.score).toFixed(3)}`);
        if (payload.channel)         meta.push(htmlEscape(payload.channel));
        if (payload.upload_date)     meta.push(htmlEscape(payload.upload_date));
        if (payload.chunk_index != null) meta.push(`chunk ${payload.chunk_index}`);

        const url = payload.webpage_url
            || (payload.video_id ? `https://www.youtube.com/watch?v=${payload.video_id}` : "")
            || (payload.arxiv_id ? `https://arxiv.org/abs/${payload.arxiv_id}` : "");

        const linkChip = url ? `
            <a class="ycs-query-card-link" href="${htmlEscape(url)}"
               target="_blank" rel="noopener noreferrer">Open</a>` : "";

        return `
            <article class="ycs-query-card">
                <header class="ycs-query-card-head">
                    <h4 class="ycs-query-card-title">${htmlEscape(title)}</h4>
                    ${linkChip}
                </header>
                <div class="ycs-query-card-meta">${meta.join(" · ")}</div>
                ${snippet ? `<p class="ycs-query-card-snippet">${htmlEscape(snippet)}…</p>` : ""}
                ${jsonBlock(raw, { collapsed: true })}
            </article>`;
    }).join("");

    panels.table.innerHTML = `<div class="ycs-query-card-grid">${cards}</div>`;
    panels.json.innerHTML  = jsonBlock(hits.map((h) => h.raw));
    panels.graph.innerHTML = `
        <div class="ycs-query-empty-results">
            Graph view is only available for Neo4j results.
        </div>`;
}


// ---- Neo4j — graph + table + JSON --------------------------------
/* `RawQueryResponse.hits[].raw` for Neo4j is the per-row dict, with
 * any Node / Relationship / Path values flattened to
 * `{_kind: "node"|"relationship"|"path", ...}` by the service. */
function _collectGraph(hits) {
    const nodes = new Map();   // id → { id, label, properties }
    const edges = new Map();   // id → { id, source, target, type, properties }

    function pushNode(n) {
        if (!n || n._kind !== "node") return;
        if (nodes.has(n.id)) return;
        nodes.set(n.id, {
            id: n.id,
            label: (n.labels && n.labels[0]) || "Node",
            display:
                (n.properties && (n.properties.title || n.properties.name
                    || n.properties.id || n.properties.video_id)) || n.id,
            properties: n.properties || {},
        });
    }
    function pushRel(r) {
        if (!r || r._kind !== "relationship") return;
        if (edges.has(r.id)) return;
        edges.set(r.id, {
            id: r.id,
            source: r.start,
            target: r.end,
            type: r.type,
            properties: r.properties || {},
        });
    }
    function walk(v) {
        if (!v) return;
        if (Array.isArray(v)) { v.forEach(walk); return; }
        if (typeof v !== "object") return;
        if (v._kind === "node") pushNode(v);
        else if (v._kind === "relationship") pushRel(v);
        else if (v._kind === "path") {
            (v.nodes || []).forEach(pushNode);
            (v.rels  || []).forEach(pushRel);
        } else {
            Object.values(v).forEach(walk);
        }
    }
    hits.forEach((h) => walk(h.raw));
    return { nodes: [...nodes.values()], edges: [...edges.values()] };
}


function _renderNeo4jGraph(panels, graphData) {
    const { nodes, edges } = graphData;
    if (!nodes.length) {
        panels.graph.innerHTML = `
            <div class="ycs-query-empty-results">
                The result rows don't contain any graph objects.
                Try returning whole nodes/relationships instead of
                scalar columns (e.g. <code>RETURN n, r, m</code>).
            </div>`;
        return;
    }
    panels.graph.innerHTML = `<div id="ycs-query-cyto" class="ycs-query-cyto"></div>`;
    const container = panels.graph.querySelector("#ycs-query-cyto");

    // Cytoscape is loaded eagerly in HEAD via the same script the DD
    // planner uses, so window.cytoscape is always defined here.
    const cy = window.cytoscape({
        container,
        elements: [
            ...nodes.map((n) => ({
                data: { id: n.id, label: n.label, display: n.display },
            })),
            ...edges.map((e) => ({
                data: { id: e.id, source: e.source, target: e.target, label: e.type },
            })),
        ],
        layout: { name: "cose", animate: false, padding: 30 },
        style: [
            {
                selector: "node",
                style: {
                    "background-color": "#c41230",
                    "label": "data(display)",
                    "color": "#000",
                    "font-size": "10px",
                    "text-valign": "center",
                    "text-halign": "center",
                    "text-margin-y": 14,
                    "text-wrap": "ellipsis",
                    "text-max-width": "100px",
                    "width": 24, "height": 24,
                    "border-color": "#fff", "border-width": 2,
                },
            },
            {
                selector: "edge",
                style: {
                    "curve-style": "bezier",
                    "line-color": "#999",
                    "width": 1.4,
                    "target-arrow-color": "#999",
                    "target-arrow-shape": "triangle",
                    "label": "data(label)",
                    "font-size": "9px",
                    "color": "#555",
                    "text-rotation": "autorotate",
                    "text-background-color": "#fff",
                    "text-background-opacity": 0.9,
                    "text-background-padding": "1px",
                },
            },
        ],
    });
    // Stash on the panel so a subsequent render can `cy.destroy()` it.
    container.__cy = cy;
}


function _renderNeo4jTable(panels, hits) {
    if (!hits.length) {
        panels.table.innerHTML = `
            <div class="ycs-query-empty-results">
                Query ran successfully — no rows.
            </div>`;
        return;
    }
    // Union of column names — Cypher rows often share a column set
    // but the renderer should tolerate a hybrid response.
    const cols = [];
    const seen = new Set();
    hits.forEach((h) => {
        Object.keys(h.raw || {}).forEach((k) => {
            if (!seen.has(k)) { seen.add(k); cols.push(k); }
        });
    });
    const head = `
        <tr>
            <th class="ycs-q-th-idx">#</th>
            ${cols.map((c) => `<th>${htmlEscape(c)}</th>`).join("")}
        </tr>`;
    const rows = hits.map((h, i) => `
        <tr>
            <td class="ycs-q-th-idx">${i + 1}</td>
            ${cols.map((c) => {
                const v = (h.raw || {})[c];
                if (v == null) return `<td class="ycs-q-cell-null">—</td>`;
                if (typeof v === "object") {
                    return `<td>${jsonBlock(v, { collapsed: true })}</td>`;
                }
                return `<td>${htmlEscape(String(v))}</td>`;
            }).join("")}
        </tr>
    `).join("");
    panels.table.innerHTML = `
        <table class="ycs-query-table">
            <thead>${head}</thead>
            <tbody>${rows}</tbody>
        </table>`;
}


function renderNeo4j(resp, panels, viewMode) {
    const hits = resp.hits || [];
    const graph = _collectGraph(hits);
    _renderNeo4jTable(panels, hits);
    panels.json.innerHTML = jsonBlock(hits.map((h) => h.raw));
    if (viewMode === "graph") _renderNeo4jGraph(panels, graph);
    else panels.graph.innerHTML = "";   // lazy: only render when toggled on
    return graph;
}


// ---- public API -----------------------------------------------------------
/* `panels` = {graph, table, json} DOM nodes. The orchestrator calls
 * this once per Run. Returns metadata the orchestrator may use (today:
 * the cached graph for Neo4j so a later view-toggle to "graph" can
 * render without re-fetching). */
export function render(backend, resp, panels, viewMode = "table") {
    if (!resp || !resp.ok) {
        ["graph", "table", "json"].forEach((k) => { panels[k].innerHTML = ""; });
        return { graph: null };
    }
    if (backend === "elasticsearch") {
        renderES(resp, panels);
        return { graph: null };
    }
    if (backend === "qdrant") {
        renderQdrant(resp, panels);
        return { graph: null };
    }
    if (backend === "neo4j") {
        const graph = renderNeo4j(resp, panels, viewMode);
        return { graph };
    }
    return { graph: null };
}


/* Lazy Cytoscape render — called when the user toggles the Graph view
 * after an initial table-mode render. Avoids the layout cost on every
 * Run. */
export function renderNeo4jGraphIfMissing(panels, cachedGraph) {
    if (!cachedGraph) return;
    if (panels.graph.querySelector(".ycs-query-cyto")) return;
    _renderNeo4jGraph(panels, cachedGraph);
}
