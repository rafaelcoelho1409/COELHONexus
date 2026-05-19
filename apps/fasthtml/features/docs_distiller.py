"""Docs Distiller feature — 3-step wizard.

  Step 1  Catalog    — framework picker (search + chips + tile grid)
  Step 2  Ingestion  — live progress + cancel button + downloaded files
  Step 3  Study      — page grid backed by the persistent MinIO manifest

A library sidebar (Steps 2 + 3) lists every framework already finalized
in MinIO; each row has refresh + delete buttons.

Behavior contracts with the backend (forwarded via the FastHTML proxy):
  POST /runs                      → {status: cached|queued|locked, run_id?, manifest?}
  POST /runs/{id}/cancel          → cooperative cancel
  GET  /runs/{id}                 → live progress + manifest snapshot (Redis)
  GET  /ingestion                 → sidebar data source (every finalized framework)
  GET  /ingestion/{slug}/manifest → canonical manifest from MinIO
  GET  /ingestion/{slug}/pages/{i}→ page body from MinIO

All HTML scaffolding lives here; CSS is in /static/css/app.css and the
client-side wizard logic is in /static/js/docs_distiller.js.
"""
import httpx
from fasthtml.common import (
    Button, Div, Img, Input, Option, P, Script, Select, Span,
)

from proxy import FASTAPI_URL
from shell import _Shell


def _fetch_catalog() -> list[dict]:
    try:
        r = httpx.get(f"{FASTAPI_URL}/api/v1/docs-distiller/resolver", timeout=5.0)
        r.raise_for_status()
        return r.json()
    except Exception:
        return []


def _Step(n: int, label: str, active: bool = False):
    cls = "fw-step active" if active else "fw-step"
    return Div(
        Span(str(n), cls="fw-step-circle"),
        Span(label, cls="fw-step-label"),
        cls=cls,
        id=f"fw-step-{n}",
        data_step=str(n),
    )


def _tile(f: dict):
    children = []
    # Multi-logo stack entries (e.g. LangChain - LangGraph - DeepAgents)
    # render every component logo in a horizontal strip. Single-logo
    # entries fall back to the legacy single-image render.
    logos = f.get("logos") or []
    if logos:
        children.append(Div(
            *[Img(src=u, alt="", cls="fw-tile-logo-multi") for u in logos],
            cls="fw-tile-logos",
        ))
    elif f.get("logo"):
        children.append(Img(src=f["logo"], alt="", cls="fw-tile-logo"))
    children.append(Div(f["name"], cls="fw-tile-name"))
    children.append(Div(f.get("category") or "—", cls="fw-tile-cat"))
    return Div(
        *children,
        cls="fw-tile",
        data_name=f["name"],
        data_slug=f["slug"],
        data_category=(f.get("category") or "Other"),
    )


def _Picker():
    catalog = _fetch_catalog()
    if not catalog:
        return Div(
            P(
                "Could not load the framework catalog. "
                "Make sure FastAPI is reachable at /api/v1/docs-distiller/resolver.",
                cls="fw-empty",
            ),
            cls="fw-picker",
        )

    cats = sorted({(f.get("category") or "Other") for f in catalog})
    chips = [Span("All", cls="fw-chip active", data_chip="All")] + [
        Span(c, cls="fw-chip", data_chip=c) for c in cats
    ]
    tiles = [_tile(f) for f in catalog]

    # Step 1 — catalog picker (always visible, never locked)
    step1_edit = Div(
        Div(
            Input(
                type="search", id="fw-search",
                placeholder=f"Search {len(catalog)} frameworks…",
                autocomplete="off", autofocus=True,
                cls="fw-search",
            ),
            Span("", id="fw-count", cls="fw-count"),
            cls="fw-search-row",
        ),
        Div(*chips, cls="fw-chips"),
        Div(*tiles, cls="fw-grid", id="fw-grid"),
        id="fw-step-1-edit",
    )

    # Step 2 — live progress (visible only during a run) + cached file list
    step2_body = Div(
        Div(
            Span("", id="fw-cache-notice-text", cls="fw-notice-text"),
            id="fw-cache-notice", cls="fw-notice", style="display:none;",
        ),
        Div(
            Span("", id="fw-denied-toast-text", cls="fw-toast-text"),
            Button("✕", id="fw-denied-toast-close", cls="fw-toast-close"),
            id="fw-denied-toast", cls="fw-toast", style="display:none;",
        ),
        # Live progress display — JS hides it when activeRunId is null
        Div(
            Div(
                Span("—", id="fw-progress-tier", cls="fw-progress-tier"),
                Span("idle", id="fw-progress-status", cls="fw-progress-status"),
                cls="fw-progress-head",
            ),
            Div(
                Div(cls="fw-progress-fill", id="fw-progress-fill"),
                cls="fw-progress-bar indeterminate", id="fw-progress-bar",
            ),
            Div(
                Span("", id="fw-progress-counter"),
                Span(""),
                cls="fw-progress-meta",
            ),
            Div("", id="fw-progress-url", cls="fw-progress-url"),
            Div(
                Div(
                    # Logo strip — JS populates with one or more <img>
                    # elements. Supports the unified stack tiles which
                    # carry a `logos: [...]` array (LangChain stack,
                    # Grafana stack) as well as the single-logo case.
                    Div(id="fw-progress-logos", cls="fw-progress-logos"),
                    Span("", id="fw-progress-framework",
                         cls="fw-progress-framework"),
                    cls="fw-progress-framework-box",
                ),
                Button("Cancel ingestion", id="fw-cancel", cls="btn-outline"),
                cls="fw-progress-actions",
            ),
            id="fw-progress-box", cls="fw-progress",
        ),
        # File list — populated from the canonical MinIO manifest whenever
        # the user navigates to Step 2 with an active framework selection.
        Div("", id="fw-step2-summary", cls="fw-pages-summary"),
        Div(
            Div(
                "Pick a framework in the catalog or the sidebar to see "
                "its downloaded files.",
                cls="fw-empty",
            ),
            id="fw-step2-grid", cls="fw-page-grid",
        ),
    )

    # Step 3 — Planner (Cytoscape DAG canvas only; cards removed 2026-05-19)
    step3_body = Div(
        # Header w/ Start button + progress meta
        Div(
            Div(
                # Title row — stage name + status pill share a flex line
                # so the pill reads as "this is what Planner is doing
                # right now" instead of floating above the canvas. Same
                # pattern as Linear issue header + LangSmith run header.
                Div(
                    Div("Planner", cls="fw-planner-title"),
                    Div(
                        Span("Idle", cls="fw-stage-pill-text",
                             id="fw-planner-pill-text"),
                        cls="fw-stage-pill", id="fw-planner-pill",
                        data_status="idle",
                    ),
                    cls="fw-planner-title-row",
                ),
                # Framework identity strip — logo(s) + catalog name. JS
                # populates from the `frameworkInfo` map (built off the
                # rendered catalog tiles) whenever activeSlug changes.
                # Falls back to a hint when no framework is selected.
                Div(
                    Div(id="fw-planner-fw-logos",
                        cls="fw-planner-fw-logos"),
                    Span("Pick a framework to start.",
                         id="fw-planner-fw-name",
                         cls="fw-planner-fw-name"),
                    id="fw-planner-fw", cls="fw-planner-fw",
                ),
                cls="fw-planner-head-text",
            ),
            Div(
                # Mode dropdown removed 2026-05-18 — the v1 LLM-vs-classical
                # split was superseded by the unified LITA-pattern
                # architecture (see docs/PLANNER-ARCHITECTURE-2026-05-17.md).
                # Server still accepts ?mode= with default "llm"; client
                # always sends "llm" implicitly.
                # Progress label ("Step N of 8") removed 2026-05-18 — its
                # info is now folded into the status pill (e.g. "WORKING
                # · 4/8") which lives next to the title.
                Button("Wipe planner", id="fw-planner-wipe",
                       cls="btn-outline", disabled=True,
                       title=("Delete this framework's planner cache "
                              "(MinIO embeddings + Postgres checkpoints "
                              "+ browser state)")),
                Button("Start Planner", id="fw-planner-start",
                       cls="btn-primary", disabled=True),
                cls="fw-planner-head-actions",
            ),
            cls="fw-planner-head",
        ),
        # Substep timeline — Cytoscape DAG canvas is the only render
        # path. The legacy vertical-cards layout was removed 2026-05-19
        # because the DAG canvas is strictly better for this domain
        # (one-click node inspection via NodeDrawer, visual stage
        # ordering, KPI badges per node, live SSE-driven coloring).
        # Empty-state placeholder shows when no slug is active.
        Div(
            "Pick a framework from the library to view the planner pipeline.",
            id="fw-planner-empty", cls="fw-stage-empty",
        ),
        Div(
            # Cytoscape mount point. Sized via CSS; Cytoscape draws to
            # a <canvas> child element it creates on init.
            Div(id="fw-planner-canvas", cls="fw-stage-canvas"),
            id="fw-planner-graph", cls="fw-planner-graph",
        ),
    )

    # Step 4 — Synth (Cytoscape DAG canvas only; cards removed 2026-05-19).
    # Per `docs/SYNTH-ARCHITECTURE-SOTA-2026-05-18.md`. The IMPLEMENTED set
    # comes from GET /synth/info — nodes light up as they ship, mirroring
    # the planner's incremental-rollout pattern. Node labels + descriptions
    # for the canvas now live in apps/fasthtml/static/js/docs_distiller.js
    # (SYNTH_NODE_LABELS) since the DAG is rendered client-side.
    step4_body = Div(
        # Header — mirrors Planner's pattern (title row with pill +
        # framework chip below + actions on the right). See Day 5 of
        # `docs/UI-ARCHITECTURE-SOTA-2026-05-18.md`.
        Div(
            Div(
                Div(
                    Div("Synth", cls="fw-planner-title"),
                    Div(
                        Span("Idle", cls="fw-stage-pill-text",
                             id="fw-synth-pill-text"),
                        cls="fw-stage-pill", id="fw-synth-pill",
                        data_status="idle",
                    ),
                    cls="fw-planner-title-row",
                ),
                Div(
                    Div(id="fw-synth-fw-logos", cls="fw-planner-fw-logos"),
                    Span("Pick a framework to start.",
                         id="fw-synth-fw-name",
                         cls="fw-planner-fw-name fw-planner-fw-name-empty"),
                    id="fw-synth-fw", cls="fw-planner-fw",
                ),
                cls="fw-planner-head-text",
            ),
            Div(
                # Refine budget — per the SOTA doc step 8 (CoRefine halting):
                # max replan iterations per chapter before forcing best-seen
                # commit. DISABLED until v2: the synth graph is single-pass
                # today (no StateGraph cycle back to sawc_write), so this
                # value is inert. start_synth doesn't consume it yet either.
                # Re-enable when the v2 refine loop + budget wiring lands.
                Div(
                    Span("Refine budget", cls="fw-planner-mode-label"),
                    Select(
                        Option("v2 — coming soon", value="5", selected=True),
                        id="fw-synth-budget", cls="fw-planner-mode-select",
                        disabled=True,
                        title=("Self-refine loop is deferred to v2 — every "
                               "chapter runs a single pass today."),
                    ),
                    cls="fw-planner-mode-box",
                ),
                Button("Wipe synth", id="fw-synth-wipe",
                       cls="btn-outline", disabled=True),
                Button("Start Synth", id="fw-synth-start",
                       cls="btn-primary", disabled=True),
                cls="fw-planner-head-actions",
            ),
            cls="fw-planner-head",
        ),
        # Empty-state placeholder — mirrors planner's. Visible when
        # activeSlug is null; JS hides it the moment a framework is
        # picked from the library.
        Div(
            "Pick a framework from the library to view the synth pipeline.",
            id="fw-synth-empty", cls="fw-stage-empty",
        ),
        # Chapter progress strip — visible only during STUDY mode runs
        # (when `Start Synth` is clicked with no specific chapter, the
        # orchestrator runs all chapters sequentially). JS populates one
        # `.fw-chstrip-cell` per chapter and flips its data-status as
        # `chapter_running`/`chapter_done` events arrive on the study
        # SSE channel. Hidden by default; .visible class added by JS.
        Div(
            Div(
                Span("Chapters", cls="fw-chstrip-title"),
                Span(id="fw-chstrip-counter", cls="fw-chstrip-counter"),
                cls="fw-chstrip-head",
            ),
            Div(id="fw-chstrip-cells", cls="fw-chstrip-cells"),
            id="fw-chstrip", cls="fw-chstrip",
        ),
        # Substep timeline — Cytoscape DAG canvas only (cards removed
        # 2026-05-19; see planner-side comment for rationale).
        Div(
            Div(id="fw-synth-canvas", cls="fw-stage-canvas"),
            id="fw-synth-graph", cls="fw-planner-graph",
        ),
    )

    # Step 5 — Study (chapter viewer for synthesized output)
    # Three-column layout per Mintlify/Docusaurus/Fumadocs 2026 convention:
    #   left:   chapter sidebar (rendered/not-rendered status per chapter)
    #   center: tabs (README / Challenges / Cards) + content
    #   right:  (optional) section TOC for the README tab
    # JS populates everything from the artifact endpoints when activeSlug
    # changes or the user lands on Step 5. Empty-state when no synth output
    # exists yet for the framework.
    step5_body = Div(
        # Header: framework name + global "regenerate synth" hint
        Div(
            Div(
                Div("Study", cls="fw-planner-title"),
                Div(
                    Span("Idle", cls="fw-stage-pill-text",
                         id="fw-study-pill-text"),
                    cls="fw-stage-pill", id="fw-study-pill",
                    data_status="idle",
                ),
                cls="fw-planner-title-row",
            ),
            Div(
                Div(id="fw-study-fw-logos", cls="fw-planner-fw-logos"),
                Span("Pick a framework with synthesized chapters.",
                     id="fw-study-fw-name",
                     cls="fw-planner-fw-name fw-planner-fw-name-empty"),
                id="fw-study-fw", cls="fw-planner-fw",
            ),
            cls="fw-planner-head-text",
        ),
        # Empty-state placeholder — visible when no slug active OR no
        # chapters rendered yet. JS hides this when there's content.
        Div(
            "Pick a framework from the library, then run Synth on its "
            "chapters to populate this study viewer.",
            id="fw-study-empty", cls="fw-stage-empty",
        ),
        # Reader — chapter list is now a slide-out SIDE WINDOW (toggled by
        # the `≡ Chapters` button in the tab strip); the materials reader
        # takes the full main width. Backdrop dims the page while the
        # window is open and closes it on click.
        Div(
            # Backdrop — only interactive while the side window is open.
            Div(id="fw-study-side-backdrop", cls="fw-study-side-backdrop"),
            # Slide-out chapter window (off-canvas left). JS toggles `.open`
            # on both this and the backdrop. Same #fw-study-chapter-list id
            # so the existing render/click logic is untouched.
            Div(
                Div(
                    Span("Chapters", cls="fw-study-side-title"),
                    Button("×", id="fw-study-side-close",
                           cls="fw-study-side-close", type="button",
                           title="Close"),
                    cls="fw-study-side-header",
                ),
                Div(id="fw-study-chapter-list",
                    cls="fw-study-chapter-list"),
                cls="fw-study-side",
                id="fw-study-side",
            ),
            # MAIN: tabs + content (full width)
            Div(
                # Tab strip — `≡ Chapters` toggle on the left, then the
                # 3 artifact tabs.
                Div(
                    Button("☰ Chapters", id="fw-study-toc-toggle",
                           cls="fw-study-toc-toggle", type="button",
                           title="Show chapters"),
                    Button("README", cls="fw-study-tab active",
                           data_tab="readme",
                           type="button"),
                    Button("Challenges", cls="fw-study-tab",
                           data_tab="challenges",
                           type="button"),
                    Button("Flashcards", cls="fw-study-tab",
                           data_tab="flashcards",
                           type="button"),
                    cls="fw-study-tabs",
                ),
                # Per-chapter heading strip (rendered when chapter
                # selected — shows chapter title + audit status)
                Div(id="fw-study-chapter-head",
                    cls="fw-study-chapter-head"),
                # Tab panes — only the active one is shown via CSS
                Div(
                    # README pane — marked.js renders into here
                    Div(
                        Div(
                            "Open the ☰ Chapters window and pick a chapter.",
                            cls="fw-empty",
                        ),
                        id="fw-study-readme",
                        cls="fw-study-pane fw-study-prose active",
                        data_tab="readme",
                    ),
                    # Challenges pane — collapsible Q/A
                    Div(
                        Div(
                            "Pick a chapter to view its active-recall "
                            "questions.",
                            cls="fw-empty",
                        ),
                        id="fw-study-challenges",
                        cls="fw-study-pane fw-study-prose",
                        data_tab="challenges",
                    ),
                    # Flashcards pane — flip-card study mode
                    Div(
                        Div(
                            "Pick a chapter to study its flashcards.",
                            cls="fw-empty",
                        ),
                        id="fw-study-flashcards",
                        cls="fw-study-pane fw-study-cards",
                        data_tab="flashcards",
                    ),
                    cls="fw-study-content",
                ),
                cls="fw-study-main",
                id="fw-study-main",
            ),
            cls="fw-study-grid",
            id="fw-study-grid",
        ),
    )

    return Div(
        # Stepper row — Catalog → Ingestion → Planner → Synth → Study
        Div(
            Div(
                _Step(1, "Catalog", active=True),
                Span(cls="fw-step-connector"),
                _Step(2, "Ingestion"),
                Span(cls="fw-step-connector"),
                _Step(3, "Planner"),
                Span(cls="fw-step-connector"),
                _Step(4, "Synth"),
                Span(cls="fw-step-connector"),
                _Step(5, "Study"),
                cls="fw-stepper",
            ),
            cls="fw-stepper-row",
        ),

        # Layout: sidebar + main step content
        Div(
            # Sidebar (library) — always rendered; JS toggles visual state.
            Div(
                P("Library", cls="fw-sidebar-title"),
                Div(
                    Div("Loading…", cls="fw-sidebar-empty"),
                    id="fw-sidebar-list",
                ),
                id="fw-sidebar", cls="fw-sidebar",
            ),
            # Main panel — holds the 5 step panels
            Div(
                Div(
                    step1_edit,
                    id="fw-step-1-panel", cls="fw-step-panel active",
                ),
                Div(step2_body, id="fw-step-2-panel", cls="fw-step-panel"),
                Div(step3_body, id="fw-step-3-panel", cls="fw-step-panel"),
                Div(step4_body, id="fw-step-4-panel", cls="fw-step-panel"),
                Div(step5_body, id="fw-step-5-panel", cls="fw-step-panel"),
                cls="fw-main",
            ),
            cls="fw-layout",
        ),

        # Sticky bar (Step 1 → Generate)
        Div(
            Span(
                "Selected: ",
                Span("", id="fw-selected-name", cls="fw-selected-name"),
                id="fw-selected-label", cls="fw-selected-label",
            ),
            Button("Start Ingestion", id="fw-generate", cls="btn-primary"),
            id="fw-sticky-bar", cls="fw-sticky-bar",
        ),
        # Generic confirm modal (reused by delete + future destructive actions)
        Div(
            Div(
                Div("", id="fw-modal-title", cls="fw-modal-title"),
                P("", id="fw-modal-message", cls="fw-modal-message"),
                Div(
                    Button("Cancel", id="fw-modal-cancel", cls="btn-outline"),
                    Button("Confirm", id="fw-modal-confirm", cls="btn-primary"),
                    cls="fw-modal-actions",
                ),
                cls="fw-modal",
            ),
            id="fw-modal", cls="fw-modal-backdrop",
        ),
        # File-content drawer (right-anchored slide-out). One instance; the
        # JS pages it through the current manifest's entries via prev/next.
        Div(
            Div(
                Div(
                    Div("", id="fw-drawer-name", cls="fw-drawer-name"),
                    Div("", id="fw-drawer-meta", cls="fw-drawer-meta"),
                    cls="fw-drawer-title",
                ),
                Div(
                    Button("◀", id="fw-drawer-prev",
                           cls="fw-drawer-btn", title="Previous (←)"),
                    Button("▶", id="fw-drawer-next",
                           cls="fw-drawer-btn", title="Next (→)"),
                    Button("✕", id="fw-drawer-close",
                           cls="fw-drawer-btn", title="Close (Esc)"),
                    cls="fw-drawer-controls",
                ),
                cls="fw-drawer-header",
            ),
            Div("", id="fw-drawer-body", cls="fw-drawer-body"),
            id="fw-drawer", cls="fw-drawer",
        ),
        # Node-detail drawer — opens when the user clicks a node on the
        # planner/synth canvas. Reuses `.fw-drawer` for slide-in chrome;
        # body holds a 3-zone layout: (A) sticky header with status +
        # KPIs, (B) live SSE event log (rAF-batched, sticky-bottom,
        # 200-line cap), (C) collapsible <details> for Inputs/Outputs
        # /Prompt. See `docs/UI-ARCHITECTURE-SOTA-2026-05-18.md` Day 3.
        Div(
            # Zone A — sticky header. Repurposes drawer-header chrome
            # but swaps the file-paging controls for a single ✕ close.
            Div(
                Div(
                    Span("○", id="fw-node-drawer-icon",
                         cls="fw-node-drawer-icon"),
                    Div(
                        Div("", id="fw-node-drawer-title",
                            cls="fw-drawer-name"),
                        Div("", id="fw-node-drawer-meta",
                            cls="fw-drawer-meta"),
                        cls="fw-drawer-title",
                    ),
                    cls="fw-node-drawer-head-left",
                ),
                Div(
                    Button("✕", id="fw-node-drawer-close",
                           cls="fw-drawer-btn", title="Close (Esc)"),
                    cls="fw-drawer-controls",
                ),
                cls="fw-drawer-header",
            ),
            # Zone A continued — KPI strip (inline, comma-separated).
            Div("", id="fw-node-drawer-kpis", cls="fw-node-drawer-kpis"),
            # Body wraps Zone B + Zone C so they share the scroll area.
            Div(
                # Zone B — live SSE event log.
                Div(
                    Div("Activity", cls="fw-node-drawer-section-title"),
                    Div(
                        Div("Open a node to stream its events here.",
                            cls="fw-empty",
                            id="fw-node-drawer-log-empty"),
                        Div("", id="fw-node-drawer-log",
                            cls="fw-node-drawer-log"),
                        cls="fw-node-drawer-log-wrap",
                    ),
                    cls="fw-node-drawer-section",
                ),
                # Zone C — collapsible <details> for Inputs/Outputs/
                # Prompt. Native HTML — zero JS for the toggle animation.
                Div("", id="fw-node-drawer-details",
                    cls="fw-node-drawer-details"),
                id="fw-node-drawer-body", cls="fw-drawer-body",
            ),
            id="fw-node-drawer", cls="fw-drawer",
        ),
        Script(src="/static/js/docs_distiller.js"),
        cls="fw-picker",
    )


def register(rt) -> None:
    """Attach /docs-distiller to `rt`. The / route lives in features/home.py."""
    @rt("/docs-distiller")
    def docs_distiller():
        return _Shell("docs-distiller", "Docs Distiller", body=_Picker())
