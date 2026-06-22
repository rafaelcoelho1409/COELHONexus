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

# Reuse DD's shared modal — same pattern as `features/rr/digest.py`. Mounts
# the `#fw-modal` element backed by `showConfirm()` in
# `static/js/dd/shared/ui/overlays.js`. The Recent-scans dropdown is now
# on Pipeline page too (2026-06-17), and its per-row trash button calls
# `showConfirm()` to confirm the delete — without the modal here, the
# button is a silent no-op (Promise.resolve(false) → "user cancelled").
from features.dd.shared.overlays import ConfirmModal

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


def _LlmUsageDrawer():
    """Scan-wide LLM usage drawer for the active pipeline run.

    Hydrated by `pipeline.js::_renderScanTotals()`. The same live poller
    used by the previous inline totals strip now fills this drawer so the
    data keeps updating while the scan runs."""
    return Div(
        Div(
            Div(
                Div("LLM usage", id = "rr-llm-drawer-name",
                    cls = "fw-drawer-name"),
                Div("Scan-wide totals for this pipeline run.",
                    id = "rr-llm-drawer-meta", cls = "fw-drawer-meta"),
                cls = "fw-drawer-title",
            ),
            Div(
                Button(
                    "✕",
                    type = "button",
                    cls  = "fw-drawer-btn",
                    id   = "rr-llm-drawer-close-btn",
                    **{"aria-label": "Close LLM usage drawer"},
                ),
                cls = "fw-drawer-controls",
            ),
            cls = "fw-drawer-header",
        ),
        Div(
            Div(
                Div("Pipeline LLM usage", cls = "dd-llm-rail-label"),
                Div(id = "rr-llm-drawer-totals", cls = "dd-llm-rail-host"),
                id = "rr-llm-drawer-pipeline-section",
                cls = "dd-llm-rail-section",
            ),
            id = "rr-llm-drawer-body", cls = "fw-drawer-body",
        ),
        id = "rr-llm-drawer",
        cls = "fw-drawer",
    )


def _PipelineGraphHead():
    """Graph header matching DD's graph-box pattern."""
    return Div(
        Div("Pipeline", cls = "rr-pipeline-zone-label"),
        Button(
            "LLM usage",
            id = "rr-totals-open",
            cls = "rr-pipeline-zone-btn",
            type = "button",
            title = "Open pipeline LLM usage",
        ),
        cls = "rr-pipeline-zone-head",
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
    # 2026-06-17 restructure:
    #   - rr-card-pipeline container REMOVED (page surface displays
    #     directly, no nested box)
    #   - H3 "Pipeline" title + ⓘ info button REMOVED (the stage
    #     subnav in row 2 already identifies this view)
    #   - Scan topic pill MOVED to the row-3 toolbar (left of Start
    #     Scan) via body.py's ScanForm; lives next to the controls
    #     that operate on it, mirroring the Digest restructure
    # Slim header now: status pill on the left, kind legend on the
    # right. Pipeline graph box + drawers hang directly off rr-page.
    return Div(
        # 2026-06-17 v3: topic now renders as a page-title heading
        # (red left-bar accent matching the navbar `.title`), not a
        # pill. Reads as "this is what the page is about" instead of
        # "this is a chip/tag". Element id `#rr-status-topic` is
        # preserved so main.js's `_setPillTopic` write keeps working
        # without code changes. `data-empty="true"` collapses the
        # whole strip via CSS when no scan is loaded.
        Div(
            Span(
                "",
                id    = "rr-status-topic",
                cls   = "rr-topic-title",
                title = "Scan topic",
                **{"data-empty": "true"},
            ),
            cls = "rr-topic-strip",
        ),
        Div(
            _StatusPill(),
            _KindLegend(),
            cls = "rr-pipeline-header",
        ),
        Div(
            _PipelineGraphHead(),
            Div(
                id  = "rr-pipeline-canvas",
                cls = "rr-pipeline-canvas",
                # Inline JSON read by pipeline.js to seed the StageGraph.
                **{"data-topology": _TopologyJson()},
            ),
            cls = "rr-pipeline-zone",
        ),
        _NodeDrawer(),
        _LlmUsageDrawer(),
        # 2026-06-17: ConfirmModal target for showConfirm() — required by
        # the Recent-scans dropdown's per-row trash button. Same component
        # the Digest page mounts.
        ConfirmModal(),
        # pipeline.js (Cytoscape init) loads BEFORE main.js so the latter
        # can call `window._rrSetPipelineState(phase, message)` from inside
        # setStatus(). Both are type=module so order is hoisted-safe.
        Script(src = "/static/js/rr/pipeline.js", type = "module"),
        Script(src = "/static/js/rr/main.js",     type = "module"),
        cls = "rr-page rr-page-pipeline",
    )
