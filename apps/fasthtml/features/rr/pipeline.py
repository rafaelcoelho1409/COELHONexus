"""Pipeline page — DeepAgents + FastMCP topology, rendered with the
shared `StageGraph` (Cytoscape + Dagre) helper Planner and Synth use.

Why reuse StageGraph instead of hand-rolling:
  * One visual language across DD Planner / DD Synth / RR Pipeline.
  * Dagre auto-layout — survives adding/removing subagents without
    manual grid coordinates.
  * Marching-ants edge animation conveys flow direction during a scan.
  * Click-into-drawer pattern is wired (we hook the same `onNodeClick`
    signature so a future RR drawer slots in without rewiring).

This module's job is data-only — it serializes the topology (`_NODES` +
`_EDGES`) to inline JSON, and renders an empty canvas + a slim status
pill. All Cytoscape wiring lives in `static/js/rr/pipeline.js`.

Status merger: the standalone status box is gone. The phase label /
message live in the pill on the graph header (one line, with the dot).
Per-phase KPIs (e.g. `6/8 extractions written`) land inside the node
that's actively running them via StageGraph's `kpi` field — exactly the
pattern Planner+Synth use today.
"""
import json

from fasthtml.common import (
    H3, H4, Button, Div, NotStr, P, Script, Span,
)

from .node_details import build_node_details


# Per-node topology. `phase` is the SSE phase string that activates the
# node; `kind` drives the visual chip (agent · subagent · tool · store);
# `sub` is a one-liner shown under the label inside the Cytoscape node.
_NODES: list[dict] = [
    dict(key="orchestrator",                       phase="running",     kind="agent",
         label="Orchestrator",      sub="DeepAgent"),
    dict(key="discovery_arxiv",                    phase="discovery",   kind="subagent",
         label="discovery_arxiv",   sub="arXiv"),
    dict(key="discovery_semantic_scholar",         phase="discovery",   kind="subagent",
         label="discovery_s2",      sub="Semantic Scholar"),
    dict(key="discovery_huggingface_daily_papers", phase="discovery",   kind="subagent",
         label="discovery_hf",      sub="HuggingFace Daily Papers"),
    dict(key="discovery_hn",                       phase="discovery",   kind="subagent",
         label="discovery_hn",      sub="Hacker News"),
    dict(key="triage",                             phase="triage",      kind="tool",
         label="triage",            sub="signal_score + dedup"),
    dict(key="deep_read",                          phase="deep_read",   kind="subagent",
         label="deep_read",         sub="per-paper extract"),
    dict(key="graph_build",                        phase="graph_build", kind="tool",
         label="graph_build",       sub="Neo4j + Qdrant"),
    dict(key="synthesis",                          phase="synthesis",   kind="subagent",
         label="synthesis",         sub="theme clustering"),
    dict(key="report",                             phase="report",      kind="subagent",
         label="report",            sub="digest assembly"),
    dict(key="persist",                            phase="persisting",  kind="store",
         label="persist",           sub="MinIO · Postgres"),
]


# (source, target). Dagre derives the layout — order doesn't matter beyond
# determinism for snapshot tests.
_EDGES: list[tuple[str, str]] = [
    ("orchestrator", "discovery_arxiv"),
    ("orchestrator", "discovery_semantic_scholar"),
    ("orchestrator", "discovery_huggingface_daily_papers"),
    ("orchestrator", "discovery_hn"),
    ("discovery_arxiv",                       "triage"),
    ("discovery_semantic_scholar",            "triage"),
    ("discovery_huggingface_daily_papers",    "triage"),
    ("discovery_hn",                          "triage"),
    ("triage",       "deep_read"),
    ("deep_read",    "graph_build"),
    ("graph_build",  "synthesis"),
    ("synthesis",    "report"),
    ("report",       "persist"),
]


def _StatusPill():
    """Stage pill — same shape as DD Planner/Synth's `fw-stage-pill`.

    Visual contract (idle / working / done / failed / cancelled):
      idle        → outlined gray, just the label
      working     → blue outline + spinning ✻ glyph + live elapsed timer
      done        → green outline + ✔ glyph + final elapsed
      failed      → red outline + ✕ glyph + error message + final elapsed
      cancelled   → muted outline + final elapsed
      cancelling  → same as working with the cancelling label

    main.js's `setStatus(phase, message)` maps the SSE phase to one of
    these statuses via PHASE_TO_STATUS and updates the data-status
    attribute that the CSS reads. Detail message ($error or stale-resume
    text) goes in `#rr-status-detail` and stays empty when not needed."""
    return Div(
        Span("Idle", cls = "rr-stage-pill-text", id = "rr-status-text"),
        Span("",     cls = "rr-stage-elapsed",   id = "rr-status-elapsed",
             title  = "Total scan time"),
        Span("",     cls = "rr-stage-detail",    id = "rr-status-detail"),
        id   = "rr-status",
        cls  = "rr-stage-pill",
        **{"data-status": "idle"},
    )


# Shape legend — mirrors the Cytoscape shapes pipeline.js assigns per kind
# so the visual vocabulary is learnable at a glance. SVG paths sized to a
# 22×14 box; stroke-only renders so they don't compete with the status pill.
#
# Shape choices match `_applyKindShapes` in pipeline.js exactly:
#   agent     hexagon       6 angular sides
#   subagent  ellipse       smooth oval
#   tool      rectangle     sharp 4 corners
#   store     barrel        curved sides, flat top/bottom
_KIND_LEGEND: tuple[tuple[str, str, str], ...] = (
    # (kind, svg_inner_markup, label)
    ("agent",
     '<polygon points="5,7 8,2 14,2 17,7 14,12 8,12" '
     'fill="none" stroke="currentColor" stroke-width="1.5" '
     'stroke-linejoin="round"/>',
     "agent"),
    ("subagent",
     '<ellipse cx="11" cy="7" rx="9" ry="5" '
     'fill="none" stroke="currentColor" stroke-width="1.5"/>',
     "subagent"),
    ("tool",
     '<rect x="2" y="2" width="18" height="10" '
     'fill="none" stroke="currentColor" stroke-width="1.5"/>',
     "tool"),
    ("store",
     # Barrel: straight vertical sides bulging outward — two side arcs
     # via cubic curves, flat top/bottom.
     '<path d="M3 3 L19 3 C 19.5 7, 19.5 7, 19 11 L3 11 '
     'C 2.5 7, 2.5 7, 3 3 Z" '
     'fill="none" stroke="currentColor" stroke-width="1.5" '
     'stroke-linejoin="round"/>',
     "store"),
)


def _KindLegend():
    """Small icon row showing which shape maps to which kind. Sits inside
    the graph header so the operator learns the vocabulary without having
    to hover nodes."""
    chips = []
    for (kind, svg_inner, label) in _KIND_LEGEND:
        chips.append(
            Span(
                NotStr(
                    '<svg class="rr-legend-shape" viewBox="0 0 22 14" '
                    f'aria-hidden="true">{svg_inner}</svg>'
                ),
                Span(label, cls = "rr-legend-label"),
                cls = f"rr-legend-chip rr-legend-chip-{kind}",
            )
        )
    return Div(*chips, cls = "rr-legend", **{"aria-label": "Node-shape legend"})


def _WipeSeenButton():
    """Small destructive-but-recoverable action — empties the profile's
    `radar_seen` membership table so the NEXT scan flips every paper to
    `is_new=true` again. JS handler in main.js confirms via the modal
    below (no browser `confirm()`) and posts to
    `POST /api/v1/rr/profile/default/reset-seen`."""
    return Button(
        "Wipe seen-set",
        type = "button",
        id   = "rr-wipe-seen-btn",
        cls  = "rr-wipe-seen-btn",
        title = ("Clear the seen-set so the next scan marks every paper "
                 "as new. Does not delete past scans, findings, or the "
                 "Neo4j Paper graph."),
        **{"aria-haspopup": "dialog", "aria-controls": "rr-wipe-seen-dialog"},
    )


def _WipeSeenDialog():
    """Native `<dialog>` confirmation — replaces the browser `confirm()`
    so the modal matches the rest of the RR chrome (Browse-all uses the
    same pattern). Triggered by `#rr-wipe-seen-btn`; confirms via the
    primary button or backdrop-click/Esc."""
    from fasthtml.common import Dialog
    return Dialog(
        Div(
            Div(
                H3("Clear the seen-set?", cls = "rr-wipe-dialog-title",
                   id  = "rr-wipe-dialog-title"),
                Button(
                    "×",
                    type = "button",
                    cls  = "rr-wipe-dialog-close",
                    **{"aria-label": "Close", "data-rr-wipe-close": "true"},
                ),
                cls = "rr-wipe-dialog-header",
            ),
            Div(
                P(
                    "The next scan will mark every paper as new again.",
                    cls = "rr-wipe-dialog-lead",
                ),
                P(
                    "Past scans, findings, MinIO digests, and the Neo4j Paper "
                    "graph are NOT touched.",
                    cls = "rr-wipe-dialog-note",
                ),
                cls = "rr-wipe-dialog-body",
            ),
            Div(
                Button(
                    "Cancel",
                    type = "button",
                    cls  = "rr-wipe-dialog-cancel",
                    id   = "rr-wipe-dialog-cancel-btn",
                ),
                Button(
                    "Wipe seen-set",
                    type = "button",
                    cls  = "rr-wipe-dialog-confirm",
                    id   = "rr-wipe-dialog-confirm-btn",
                ),
                cls = "rr-wipe-dialog-actions",
            ),
            cls = "rr-wipe-dialog-content",
        ),
        id   = "rr-wipe-seen-dialog",
        cls  = "rr-wipe-dialog",
        **{"aria-labelledby": "rr-wipe-dialog-title"},
    )


def _LlmTotalsStrip():
    """Scan-wide LLM totals — persistent across node clicks, live-updates
    as phase events fire (2026-06-16 Path-A v2).

    Hydrated by `pipeline.js::_renderScanTotals()`. Empty placeholder
    until a scan_id lands in the URL (?scan=<id>); then the JS fetches
    `/api/v1/rr/scan/{id}/llm-counters` and fills the cards + chips."""
    return Div(
        Div(
            Div(
                Div("Total LLM calls",  cls = "rr-totals-kpi-label"),
                Div("—",                cls = "rr-totals-kpi-value",
                    id  = "rr-totals-calls"),
                cls = "rr-totals-kpi",
            ),
            Div(
                Div("Tokens in",        cls = "rr-totals-kpi-label"),
                Div("—",                cls = "rr-totals-kpi-value",
                    id  = "rr-totals-in"),
                cls = "rr-totals-kpi",
            ),
            Div(
                Div("Tokens out",       cls = "rr-totals-kpi-label"),
                Div("—",                cls = "rr-totals-kpi-value",
                    id  = "rr-totals-out"),
                cls = "rr-totals-kpi",
            ),
            cls = "rr-totals-kpis",
        ),
        # Per-phase mini-breakdown — one chip per phase that has any
        # activity. JS fills this row dynamically.
        Div(id = "rr-totals-phases", cls = "rr-totals-phases"),
        # Scan-wide per-(provider, model) table — same shape as the
        # per-node drawer's per-model table but rolled up across phases.
        # Hidden when no rows; JS fills the inner HTML.
        Div(id = "rr-totals-models", cls = "rr-totals-models"),
        id  = "rr-totals",
        cls = "rr-totals",
        **{"aria-label": "Scan-wide LLM activity totals"},
    )


def _TopologyJson() -> str:
    """Single inline JSON payload pipeline.js consumes at canvas-init time.
    Carries both nodes (with phase + kind + label + sub) and edges, so the
    JS side has everything it needs without an HTTP round-trip."""
    return json.dumps({"nodes": _NODES, "edges": _EDGES})


def _NodeDrawer():
    """Side drawer that slides in from the right when a graph node is
    clicked. Content per node is rendered server-side from the
    `build_node_details()` mapping; pipeline.js handles the
    open/close + which node's content to show."""
    return Div(
        Div(
            Div(
                H3("", id = "rr-drawer-title", cls = "rr-drawer-title"),
                P("", id = "rr-drawer-subtitle", cls = "rr-drawer-subtitle"),
                Button(
                    "×",
                    type = "button",
                    cls  = "rr-drawer-close",
                    id   = "rr-drawer-close-btn",
                    **{"aria-label": "Close drawer"},
                ),
                cls = "rr-drawer-header",
            ),
            P(
                "",
                id  = "rr-drawer-source",
                cls = "rr-drawer-source",
            ),
            Div(id = "rr-drawer-body", cls = "rr-drawer-body"),
            cls = "rr-drawer-panel",
        ),
        Script(
            NotStr(json.dumps(build_node_details(), ensure_ascii=False)),
            id   = "rr-node-details",
            type = "application/json",
        ),
        id   = "rr-drawer",
        cls  = "rr-drawer",
        hidden = True,
    )


def PipelineBody():
    """Page body for `/research-radar`.

    Layout: one card with the slim status pill at the top, a Cytoscape
    canvas underneath. The canvas mount-point id (`rr-pipeline-canvas`)
    is what `static/js/rr/pipeline.js::initPipelineGraph()` resolves; the
    page module + main.js script tags load in that order so pipeline.js
    is the entry point that calls back into main.js's setStatus hook."""
    return Div(
        Div(
            Div(
                Div(
                    H3("Pipeline", cls = "rr-pipeline-title"),
                    Button(
                        "i",
                        type = "button",
                        # `rr-info-down` flips the tooltip BELOW the button
                        # so it doesn't get clipped by the row-3 toolbar
                        # above. (Default direction is upward.)
                        cls  = "rr-info rr-info-down",
                        **{
                            "aria-label":   "About the Pipeline view",
                            "data-tooltip": (
                                "Live agent topology — DeepAgents orchestrator + "
                                "4 discovery subagents (FastMCP-backed) + per-paper "
                                "deep_read + synthesis + report. Nodes light up as "
                                "their phase runs."
                            ),
                        },
                    ),
                    _StatusPill(),
                    cls = "rr-pipeline-title-row",
                ),
                Div(
                    _KindLegend(),
                    Div(
                        Button(
                            "i",
                            type = "button",
                            # `rr-info-down` flips the tooltip below the
                            # button so it doesn't clip behind row-2 chrome.
                            cls  = "rr-info rr-info-down",
                            **{
                                "aria-label":   "About Wipe seen-set",
                                "data-tooltip": (
                                    "Clears the `is_new` tracking only — next "
                                    "scan marks every paper as new again. Past "
                                    "scans, digests, and the Neo4j graph stay "
                                    "intact."
                                ),
                            },
                        ),
                        _WipeSeenButton(),
                        cls = "rr-wipe-seen-cluster",
                    ),
                    cls = "rr-pipeline-header-tail",
                ),
                _WipeSeenDialog(),
                cls = "rr-pipeline-header",
            ),
            Div(
                id  = "rr-pipeline-canvas",
                cls = "rr-pipeline-canvas",
                # Inline JSON read by pipeline.js to seed the StageGraph.
                **{"data-topology": _TopologyJson()},
            ),
            _LlmTotalsStrip(),
            _NodeDrawer(),
            cls = "rr-card rr-card-pipeline",
        ),
        # pipeline.js (Cytoscape init) loads BEFORE main.js so the latter
        # can call `window._rrSetPipelineState(phase, message)` from inside
        # setStatus(). Both are type=module so order is hoisted-safe.
        Script(src = "/static/js/rr/pipeline.js", type = "module"),
        Script(src = "/static/js/rr/main.js",     type = "module"),
        cls = "rr-page",
    )
