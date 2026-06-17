/* YCS Query — per-backend result renderers (SOTA pick, June 2026).
 *
 * Three view modes (Graph / Table / JSON) share one set of right-pane
 * panels. The orchestrator (`query.js`) flips a `data-view-mode`
 * attribute on `#ycs-query-results` so only one panel is visible at a
 * time, and calls `refreshView()` on toggle so libraries that measure
 * the DOM (Tabulator, Cytoscape) recalc dimensions when their panel
 * becomes visible.
 *
 * Library choices and why:
 *   · Tabulator 6        — Table view. MIT, vanilla-JS ESM, virtual DOM
 *                          scrolling, header filter + sort + clipboard
 *                          + CSV/JSON export in the free build. Nested
 *                          values render as a pill that opens a JSON
 *                          popover on click.
 *   · vanilla-jsoneditor — JSON view. ISC, ESM bundle. Tree mode with
 *                          search, copy-path, fold/unfold, type badges.
 *                          Handles 1 MB documents without sweat.
 *   · Cytoscape + fcose  — Graph view (Neo4j only). Force-directed
 *                          layout matching the Neo4j Browser feel; nodes
 *                          colored by label via a deterministic hash
 *                          into the Tableau-10 palette, sized by degree;
 *                          edge labels with white background to fight
 *                          occlusion; sticky drag (lock on dragfree).
 *                          Tooltips are a positioned div populated on
 *                          mouseover — keeps us off the popper/floating
 *                          -ui dep chain.
 *
 * `cytoscape` is loaded eagerly in HEAD as a UMD global (so the DD
 * planner keeps working); we only `cytoscape.use(fcose)` here on first
 * Neo4j-graph render.
 */
import { TabulatorFull as Tabulator } from "tabulator-tables";
import { createJSONEditor }           from "vanilla-jsoneditor";


// ---- helpers --------------------------------------------------------------
function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function escapeAttr(s) { return escapeHtml(s).replace(/\n/g, " "); }


/* Tableau-10 categorical palette — colorblind-safe, well-separated
 * hues. Used to color graph nodes by their Neo4j label. */
const PALETTE = [
    "#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F",
    "#EDC948", "#B07AA1", "#FF9DA7", "#9C755F", "#BAB0AC",
];


function colorForLabel(label) {
    let h = 0;
    for (const c of String(label || "Node")) h = (h * 31 + c.charCodeAt(0)) | 0;
    return PALETTE[Math.abs(h) % PALETTE.length];
}


// ---- mount-instance bookkeeping ------------------------------------------
/* WeakMap per-panel so render() can dispose the old instance cleanly on
 * every Run (Tabulator and JSONEditor both leak listeners + DOM nodes
 * if you just `innerHTML = ""` over them). */
const _tabInstances = new WeakMap();
const _jseInstances = new WeakMap();
const _cyInstances  = new WeakMap();


function _destroyTab(panelEl) {
    const t = _tabInstances.get(panelEl);
    if (t) { try { t.destroy(); } catch (_) {} _tabInstances.delete(panelEl); }
}
function _destroyJSE(panelEl) {
    const j = _jseInstances.get(panelEl);
    if (j) { try { j.destroy(); } catch (_) {} _jseInstances.delete(panelEl); }
}
function _destroyCy(panelEl) {
    const c = _cyInstances.get(panelEl);
    if (c) { try { c.destroy(); } catch (_) {} _cyInstances.delete(panelEl); }
}


// ---- Tabulator (Table view) ----------------------------------------------
/* Render any cell value sensibly:
 *   null / "" → muted placeholder
 *   array / object → pill ("Array[7]" / "{4}") that opens a JSON popover
 *   number → fixed-4 if float, otherwise raw
 *   string → escape and render */
function _cellFormatter(cell) {
    const v = cell.getValue();
    if (v == null) return `<span class="ycs-q-null">null</span>`;
    if (v === "")  return `<span class="ycs-q-null">""</span>`;
    if (Array.isArray(v)) {
        return `<span class="ycs-q-pill" title="${escapeAttr(JSON.stringify(v).slice(0, 600))}">Array[${v.length}]</span>`;
    }
    if (typeof v === "object") {
        return `<span class="ycs-q-pill" title="${escapeAttr(JSON.stringify(v).slice(0, 600))}">{${Object.keys(v).length}}</span>`;
    }
    if (typeof v === "number") {
        return Number.isInteger(v) ? String(v) : v.toFixed(4);
    }
    if (typeof v === "boolean") return v ? "true" : "false";
    return escapeHtml(String(v));
}


function _inferColumns(rows) {
    // Union of keys across all rows, preserving first-seen order.
    const cols = [];
    const seen = new Set();
    rows.forEach((row) => {
        Object.keys(row || {}).forEach((k) => {
            if (!seen.has(k)) { seen.add(k); cols.push(k); }
        });
    });
    return cols.map((field) => ({
        field,
        title: field,
        headerFilter:  "input",
        headerSort:    true,
        resizable:     true,
        minWidth:      80,
        formatter:     _cellFormatter,
        formatterParams: { allowHTML: true },
        cellClick: (e, cell) => {
            const v = cell.getValue();
            if (v && typeof v === "object") _showCellPopover(cell.getElement(), v);
        },
    }));
}


/* Simple positioned-div popover for nested cell values. Replaces the
 * row-expand pattern with something less DOM-heavy and that doesn't
 * fight Tabulator's virtualization. */
function _showCellPopover(anchor, value) {
    document.getElementById("ycs-query-cell-popover")?.remove();
    const div = document.createElement("div");
    div.id = "ycs-query-cell-popover";
    div.className = "ycs-query-cell-popover";
    div.innerHTML = `
        <header>
            <span class="ycs-query-cell-popover-title">Cell value</span>
            <button class="ycs-query-cell-popover-close" title="Close (Esc)">×</button>
        </header>
        <pre>${escapeHtml(JSON.stringify(value, null, 2))}</pre>`;
    document.body.appendChild(div);
    const rect = anchor.getBoundingClientRect();
    const w = 460, h = 360;
    const left = Math.min(rect.left, window.innerWidth  - w - 12);
    const top  = Math.min(rect.bottom + 6, window.innerHeight - h - 12);
    div.style.left = `${Math.max(8, left)}px`;
    div.style.top  = `${Math.max(8, top)}px`;
    const close = () => { div.remove(); document.removeEventListener("keydown", onKey); document.removeEventListener("mousedown", onOut); };
    const onKey = (e) => { if (e.key === "Escape") close(); };
    const onOut = (e) => { if (!div.contains(e.target)) close(); };
    div.querySelector(".ycs-query-cell-popover-close").onclick = close;
    document.addEventListener("keydown",  onKey);
    setTimeout(() => document.addEventListener("mousedown", onOut), 50);
}


function _mountTabulator(panelEl, rows) {
    _destroyTab(panelEl);
    panelEl.innerHTML = `
        <div class="ycs-query-table-toolbar">
            <span class="ycs-query-table-count">${rows.length.toLocaleString()} row${rows.length === 1 ? "" : "s"}</span>
            <span class="ycs-query-table-spacer"></span>
            <button class="ycs-query-table-btn" data-export="csv"  title="Download as CSV">CSV</button>
            <button class="ycs-query-table-btn" data-export="json" title="Download as JSON">JSON</button>
            <button class="ycs-query-table-btn" data-export="copy" title="Copy selected (or all) to clipboard">Copy</button>
        </div>
        <div class="ycs-query-tabulator"></div>`;
    const mount = panelEl.querySelector(".ycs-query-tabulator");
    if (!rows.length) {
        panelEl.querySelector(".ycs-query-tabulator").innerHTML =
            `<div class="ycs-query-empty-results">Query ran successfully — no rows.</div>`;
        return;
    }
    const cols = [
        {
            formatter:   "rownum",
            width:       50,
            frozen:      true,
            headerSort:  false,
            hozAlign:    "right",
            headerHozAlign: "right",
            cssClass:    "ycs-q-rownum",
        },
        ..._inferColumns(rows),
    ];
    const t = new Tabulator(mount, {
        data:                 rows,
        columns:              cols,
        layout:               "fitDataStretch",
        maxHeight:            "100%",
        renderVerticalBuffer: 300,
        clipboard:            true,
        clipboardCopyRowRange: "selected",
        selectableRows:       true,
        movableColumns:       true,
        placeholder:          "No results",
        // Persist column widths/order across reruns on the same page.
        persistence:          { sort: true, filter: true, columns: false },
    });
    _tabInstances.set(panelEl, t);
    // Wire toolbar export buttons after the table has finished building
    // so download() targets a real table state.
    t.on("tableBuilt", () => {
        panelEl.querySelector('[data-export="csv"]').onclick  =
            () => t.download("csv",  "results.csv");
        panelEl.querySelector('[data-export="json"]').onclick =
            () => t.download("json", "results.json");
        panelEl.querySelector('[data-export="copy"]').onclick = () => {
            const sel = t.getSelectedRows();
            if (sel.length) t.copyToClipboard("selected");
            else            t.copyToClipboard("active");
        };
    });
}


// ---- vanilla-jsoneditor (JSON view) --------------------------------------
function _mountJSONEditor(panelEl, content) {
    _destroyJSE(panelEl);
    panelEl.innerHTML = `<div class="ycs-query-jse"></div>`;
    const mount  = panelEl.querySelector(".ycs-query-jse");
    const editor = createJSONEditor({
        target: mount,
        props: {
            content:        { json: content },
            mode:           "tree",
            readOnly:       true,
            mainMenuBar:    true,
            navigationBar:  true,
            statusBar:      false,
            indentation:    2,
        },
    });
    _jseInstances.set(panelEl, editor);
}


// ---- Cytoscape graph (Neo4j only) ----------------------------------------
/* Walk an arbitrary RawQueryResponse.hits[*].raw tree and pull out the
 * unique {nodes, edges} so we can drive a Cytoscape graph regardless of
 * how the Cypher RETURN was shaped (RETURN n vs. RETURN n,r,m vs.
 * RETURN path). */
function _collectGraph(hits) {
    const nodes = new Map();
    const edges = new Map();

    function pushNode(n) {
        if (!n || n._kind !== "node") return;
        if (nodes.has(n.id)) return;
        const props = n.properties || {};
        nodes.set(n.id, {
            id:       n.id,
            labels:   n.labels || [],
            label:    (n.labels && n.labels[0]) || "Node",
            display:  props.title || props.name || props.id
                      || props.video_id || props.channel_id || n.id,
            properties: props,
        });
    }
    function pushRel(r) {
        if (!r || r._kind !== "relationship") return;
        if (edges.has(r.id)) return;
        edges.set(r.id, {
            id:         r.id,
            source:     r.start,
            target:     r.end,
            type:       r.type,
            properties: r.properties || {},
        });
    }
    function walk(v) {
        if (!v) return;
        if (Array.isArray(v)) { v.forEach(walk); return; }
        if (typeof v !== "object") return;
        if (v._kind === "node")           pushNode(v);
        else if (v._kind === "relationship") pushRel(v);
        else if (v._kind === "path") {
            (v.nodes || []).forEach(pushNode);
            (v.rels  || []).forEach(pushRel);
        } else Object.values(v).forEach(walk);
    }
    hits.forEach((h) => walk(h.raw));
    return { nodes: [...nodes.values()], edges: [...edges.values()] };
}


/* cytoscape-fcose is loaded as a UMD bundle (window.cytoscapeFcose).
 * Register once with cytoscape; subsequent calls are no-ops. */
function _registerFcose() {
    if (!window.cytoscape) return false;
    if (window.cytoscape._fcoseRegistered) return true;
    if (!window.cytoscapeFcose) return false;
    try {
        window.cytoscape.use(window.cytoscapeFcose);
        window.cytoscape._fcoseRegistered = true;
        return true;
    } catch (_) { return false; }
}


function _propsTableHTML(label, props) {
    const entries = Object.entries(props || {})
        .filter(([_, v]) => v != null && v !== "")
        .slice(0, 20);
    if (!entries.length) {
        return `
            <div class="ycs-q-tt-label">${escapeHtml(label)}</div>
            <div class="ycs-q-tt-empty">(no properties)</div>`;
    }
    const rows = entries.map(([k, v]) => {
        const raw = (typeof v === "object") ? JSON.stringify(v) : String(v);
        const val = raw.length > 80 ? raw.slice(0, 77) + "…" : raw;
        return `<tr><th>${escapeHtml(k)}</th><td>${escapeHtml(val)}</td></tr>`;
    }).join("");
    return `
        <div class="ycs-q-tt-label">${escapeHtml(label)}</div>
        <table class="ycs-q-tt-table"><tbody>${rows}</tbody></table>`;
}


function _renderNeo4jGraph(panels, graphData) {
    const { nodes, edges } = graphData;
    _destroyCy(panels.graph);

    if (!nodes.length) {
        panels.graph.innerHTML = `
            <div class="ycs-query-empty-results">
                The result rows don't contain any graph objects.
                Try returning whole nodes/relationships instead of
                scalar columns (e.g. <code>RETURN n, r, m</code>).
            </div>`;
        return;
    }

    const fcoseOk = _registerFcose();

    // Assign a stable color per node-label.
    const uniqLabels  = [...new Set(nodes.map((n) => n.label))];
    const labelColors = Object.fromEntries(
        uniqLabels.map((l) => [l, colorForLabel(l)]),
    );

    panels.graph.innerHTML = `
        <div class="ycs-query-graph-legend">
            ${uniqLabels.map((l) => `
                <span class="ycs-query-graph-chip">
                    <span class="ycs-query-graph-swatch" style="background:${labelColors[l]}"></span>
                    <span class="ycs-query-graph-chip-label">${escapeHtml(l)}</span>
                </span>`).join("")}
            <span class="ycs-query-graph-meta">
                ${nodes.length} node${nodes.length === 1 ? "" : "s"}
                · ${edges.length} relationship${edges.length === 1 ? "" : "s"}
                ${fcoseOk ? "" : ' · <span title="fcose layout plugin not loaded — falling back to cose">cose fallback</span>'}
            </span>
            <span class="ycs-query-graph-actions">
                <button class="ycs-query-graph-btn" data-graph-action="fit"     title="Fit to viewport">Fit</button>
                <button class="ycs-query-graph-btn" data-graph-action="unlock"  title="Unlock all locked nodes">Unlock</button>
                <button class="ycs-query-graph-btn" data-graph-action="relayout" title="Re-run layout">Relayout</button>
            </span>
        </div>
        <div class="ycs-query-cyto-wrap">
            <div id="ycs-query-cyto" class="ycs-query-cyto"></div>
            <div class="ycs-query-graph-tooltip" hidden></div>
        </div>
    `;
    const container = panels.graph.querySelector("#ycs-query-cyto");
    const tooltip   = panels.graph.querySelector(".ycs-query-graph-tooltip");

    /* Spread tuned for legibility on ~10–200 node graphs with wide
     * labels (140px max). Two critical flags:
     *   · nodeDimensionsIncludeLabels: true  — without this fcose only
     *     considers the node's CIRCLE (~64px) for overlap; the wider
     *     LABEL box gets ignored and labels collide even when circles
     *     don't. This is the single most impactful spacing flag.
     *   · quality: "proof"                   — unlocks the stronger
     *     constraint-based optimization pass; the "default" quality
     *     skips post-processing that resolves residual overlaps.
     * Plus we push the spring params well past defaults
     * (nodeRepulsion=4500, idealEdgeLength=50, nodeSeparation=75) since
     * our nodes are big + labelled. */
    const layoutCfg = fcoseOk
        ? {
            name:                         "fcose",
            quality:                      "proof",
            nodeDimensionsIncludeLabels:  true,
            animate:                      true,
            animationDuration:            800,
            randomize:                    true,
            uniformNodeDimensions:        false,
            packComponents:               true,
            // Spring forces — pushed roughly 10× the defaults so labelled
            // nodes never touch.
            nodeRepulsion:      60000,
            idealEdgeLength:    260,
            nodeSeparation:     220,
            edgeElasticity:     0.25,
            // Gravity — keep weak so the graph spreads to the canvas
            // instead of bunching at the center.
            gravity:            0.08,
            gravityRange:       4.5,
            gravityCompound:    1.0,
            gravityRangeCompound: 2.0,
            // More iterations + larger tiling padding for components.
            numIter:            4000,
            tile:               true,
            tilingPaddingVertical:   40,
            tilingPaddingHorizontal: 40,
            initialEnergyOnIncremental: 0.5,
            padding:            60,
            fit:                true,
        }
        : {
            name:                         "cose",
            nodeDimensionsIncludeLabels:  true,
            animate:                      false,
            nodeRepulsion:                30000,
            idealEdgeLength:              240,
            edgeElasticity:               80,
            nodeOverlap:                  40,
            componentSpacing:             180,
            padding:                      50,
            fit:                          true,
        };

    const cy = window.cytoscape({
        container,
        elements: [
            ...nodes.map((n) => ({
                data: {
                    id:         n.id,
                    label:      n.label,
                    display:    n.display,
                    color:      labelColors[n.label] || "#888",
                    properties: n.properties,
                    _kind:      "node",
                },
            })),
            ...edges.map((e) => ({
                data: {
                    id:         e.id,
                    source:     e.source,
                    target:     e.target,
                    label:      e.type,
                    properties: e.properties,
                    _kind:      "edge",
                },
            })),
        ],
        style: [
            {
                selector: "node",
                style: {
                    "background-color":         "data(color)",
                    "label":                    "data(display)",
                    "color":                    "#1c1c1c",
                    "font-size":                "10px",
                    "font-weight":              "500",
                    "text-valign":              "bottom",
                    "text-halign":              "center",
                    "text-margin-y":            6,
                    "text-wrap":                "ellipsis",
                    "text-max-width":           "140px",
                    "text-background-color":    "#ffffff",
                    "text-background-opacity":  0.92,
                    "text-background-padding":  "2px",
                    "text-background-shape":    "round-rectangle",
                    "width":  "mapData(degree, 0, 20, 28, 64)",
                    "height": "mapData(degree, 0, 20, 28, 64)",
                    "border-color":             "#ffffff",
                    "border-width":             2.5,
                    "transition-property":      "border-color, border-width, background-color",
                    "transition-duration":      "120ms",
                    "overlay-padding":          6,
                },
            },
            {
                selector: "node:selected",
                style: {
                    "border-color": "#c41230",
                    "border-width": 4,
                },
            },
            {
                selector: "node.faded",
                style: { "opacity": 0.18, "text-opacity": 0.18 },
            },
            {
                selector: "node.locked",
                style: {
                    "border-color": "#c41230",
                    "border-style": "dashed",
                    "border-width": 2.5,
                },
            },
            {
                selector: "edge",
                style: {
                    "curve-style":              "bezier",
                    "line-color":               "#bdbdbd",
                    "width":                    1.6,
                    "target-arrow-color":       "#bdbdbd",
                    "target-arrow-shape":       "triangle",
                    "arrow-scale":              0.9,
                    "label":                    "data(label)",
                    "font-size":                "9px",
                    "color":                    "#555",
                    "text-rotation":            "autorotate",
                    "text-background-color":    "#ffffff",
                    "text-background-opacity":  0.92,
                    "text-background-padding":  "2px",
                    "text-background-shape":    "round-rectangle",
                    "transition-property":      "line-color, target-arrow-color, width",
                    "transition-duration":      "120ms",
                },
            },
            {
                selector: "edge:selected",
                style: {
                    "line-color":         "#c41230",
                    "target-arrow-color": "#c41230",
                    "width":              2.6,
                },
            },
            {
                selector: "edge.faded",
                style: {
                    "opacity":      0.1,
                    "text-opacity": 0.1,
                },
            },
        ],
        layout:                layoutCfg,
        wheelSensitivity:      0.2,
        boxSelectionEnabled:   true,
    });

    // Lock node on drag-drop so it stays where the user put it
    // (matches Neo4j Browser's stickiness). The CSS class is applied
    // visually too.
    cy.on("dragfree", "node", (evt) => {
        evt.target.lock();
        evt.target.addClass("locked");
    });

    // Tooltips
    function showTip(html, ev) {
        tooltip.innerHTML = html;
        tooltip.hidden = false;
        const rect  = panels.graph.getBoundingClientRect();
        const tipW  = tooltip.offsetWidth  || 280;
        const tipH  = tooltip.offsetHeight || 160;
        let x = ev.originalEvent.clientX - rect.left + 14;
        let y = ev.originalEvent.clientY - rect.top  + 14;
        if (x + tipW > panels.graph.clientWidth)  x = panels.graph.clientWidth  - tipW - 8;
        if (y + tipH > panels.graph.clientHeight) y = panels.graph.clientHeight - tipH - 8;
        tooltip.style.left = `${Math.max(8, x)}px`;
        tooltip.style.top  = `${Math.max(8, y)}px`;
    }
    function hideTip() { tooltip.hidden = true; }

    cy.on("mouseover", "node", (ev) => showTip(_propsTableHTML(ev.target.data("label"), ev.target.data("properties")), ev));
    cy.on("mouseout",  "node", hideTip);
    cy.on("mouseover", "edge", (ev) => showTip(_propsTableHTML(ev.target.data("label"), ev.target.data("properties")), ev));
    cy.on("mouseout",  "edge", hideTip);

    // Click-to-focus: highlight neighborhood, fade the rest.
    cy.on("tap", "node", (ev) => {
        const n         = ev.target;
        const neighbors = n.neighborhood().union(n);
        cy.elements().addClass("faded");
        neighbors.removeClass("faded");
    });
    cy.on("tap", (ev) => {
        if (ev.target === cy) cy.elements().removeClass("faded");
    });

    // Toolbar buttons
    panels.graph.querySelector('[data-graph-action="fit"]').onclick =
        () => cy.fit(undefined, 40);
    panels.graph.querySelector('[data-graph-action="unlock"]').onclick = () => {
        cy.nodes().unlock();
        cy.nodes().removeClass("locked");
    };
    panels.graph.querySelector('[data-graph-action="relayout"]').onclick = () => {
        cy.nodes().unlock();
        cy.nodes().removeClass("locked");
        cy.layout(layoutCfg).run();
    };

    _cyInstances.set(panels.graph, cy);
    // Back-compat: a couple of older debug snippets reach for __cy.
    container.__cy = cy;
}


// ---- per-backend dispatch ------------------------------------------------
function _flattenES(hits) {
    return hits.map((h) => {
        const raw = h.raw || {};
        const src = raw._source || {};
        return { _index: raw._index, _id: raw._id, _score: raw._score, ...src };
    });
}
function _flattenQdrant(hits) {
    return hits.map((h) => {
        const raw = h.raw || {};
        return { id: raw.id, score: raw.score, ...(raw.payload || {}) };
    });
}
function _flattenNeo4j(hits) {
    return hits.map((h) => h.raw || {});
}


function _renderBackend(backend, resp, panels, viewMode) {
    const hits = resp.hits || [];
    let tableRows, jsonContent, graph = null;

    if (backend === "elasticsearch") {
        tableRows   = _flattenES(hits);
        jsonContent = hits.map((h) => h.raw);
    } else if (backend === "qdrant") {
        tableRows   = _flattenQdrant(hits);
        jsonContent = hits.map((h) => h.raw);
    } else {
        tableRows   = _flattenNeo4j(hits);
        jsonContent = hits.map((h) => h.raw);
        graph       = _collectGraph(hits);
    }

    _mountTabulator(panels.table, tableRows);
    _mountJSONEditor(panels.json, jsonContent);

    if (backend === "neo4j") {
        if (viewMode === "graph") _renderNeo4jGraph(panels, graph);
        else {
            // Lazy: only render the graph when the user toggles to it.
            _destroyCy(panels.graph);
            panels.graph.innerHTML = "";
        }
    } else {
        _destroyCy(panels.graph);
        panels.graph.innerHTML = `
            <div class="ycs-query-empty-results">
                Graph view is only available for Neo4j results.
            </div>`;
    }
    return graph;
}


// ---- public API ----------------------------------------------------------
/* `panels` = {graph, table, json} DOM nodes. Called once per Run.
 * Returns metadata the orchestrator may need (cached graph data so a
 * later view-toggle to "graph" can render without re-fetching). */
export function render(backend, resp, panels, viewMode = "table") {
    if (!resp || !resp.ok) {
        _destroyTab(panels.table);
        _destroyJSE(panels.json);
        _destroyCy(panels.graph);
        ["graph", "table", "json"].forEach((k) => { panels[k].innerHTML = ""; });
        return { graph: null };
    }
    const graph = _renderBackend(backend, resp, panels, viewMode);
    return { graph };
}


/* Lazy Cytoscape render — called when the user toggles to Graph after
 * a Neo4j Run completed in Table mode. */
export function renderNeo4jGraphIfMissing(panels, cachedGraph) {
    if (!cachedGraph) return;
    if (panels.graph.querySelector(".ycs-query-cyto")) return;
    _renderNeo4jGraph(panels, cachedGraph);
}


/* Called by the orchestrator AFTER it flips `data-view-mode`. Forces
 * Tabulator + Cytoscape to recalc dimensions now that their panel is
 * actually visible — both libraries silently render at 0px when their
 * container is `display:none` at mount time. */
export function refreshView(panels, viewMode) {
    if (viewMode === "table") {
        const t = _tabInstances.get(panels.table);
        if (t) requestAnimationFrame(() => { try { t.redraw(true); } catch (_) {} });
    } else if (viewMode === "graph") {
        const c = _cyInstances.get(panels.graph);
        if (c) requestAnimationFrame(() => {
            try { c.resize(); c.fit(undefined, 40); } catch (_) {}
        });
    }
    // vanilla-jsoneditor tolerates display:none — no refresh needed.
}
