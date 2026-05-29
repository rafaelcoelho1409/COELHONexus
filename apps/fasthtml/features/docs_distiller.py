"""Docs Distiller feature — server-side stage split.

Each stage of the pipeline has its OWN URL so users can bookmark,
share, and use browser back/forward across the (often hours-long)
flow. The framework slug is carried in the `?slug=` query string.

  /docs-distiller                        catalog (framework picker)
  /docs-distiller/ingestion?slug=<s>     ingestion progress + files
  /docs-distiller/planner?slug=<s>       planner DAG + outputs
  /docs-distiller/synth?slug=<s>         synth DAG + chapter strip
  /docs-distiller/study?slug=<s>         README / Challenges / Cards

The shared chrome (library sidebar, stage sub-nav, modal, file drawer,
node drawer, toast/notice) is rendered on every page so the client-side
state.js refs resolve uniformly. Only ONE stage panel is rendered per
page; the others' DOM is omitted entirely.

Behavior contracts with the backend (forwarded via the FastHTML proxy):
  POST /runs                      → {status: cached|queued|locked, run_id?, manifest?}
  POST /runs/{id}/cancel          → cooperative cancel
  GET  /runs/{id}                 → live progress + manifest snapshot (Redis)
  GET  /ingestion                 → sidebar data source
  GET  /ingestion/{slug}/manifest → canonical manifest from MinIO
  GET  /ingestion/{slug}/pages/{i}→ page body from MinIO
"""
import time

import httpx
from fasthtml.common import (
    A, Button, Div, Img, Input, Nav, P, Script, Span,
)
from starlette.requests import Request

from proxy import FASTAPI_URL
from shell import _Shell


# 2026-05-26 — Module-level TTL cache. Every /docs-distiller GET used
# to issue a fresh blocking httpx call to FastAPI. Under load (heavy
# bandit cascades during synth/planner runs), the call could hold a
# Starlette threadpool worker for up to 5s, surfacing as "page keeps
# loading" in the browser. Cache survives 60s; on backend failure we
# return the last good value rather than empty — so the picker keeps
# working through brief FastAPI hiccups.
_CATALOG_TTL_S = 60.0
_catalog_cache: dict = {"data": None, "ts": 0.0}


def _fetch_catalog() -> list[dict]:
    now = time.monotonic()
    if (
        _catalog_cache["data"] is not None
        and (now - _catalog_cache["ts"]) < _CATALOG_TTL_S
    ):
        return _catalog_cache["data"]
    try:
        r = httpx.get(
            f"{FASTAPI_URL}/api/v1/docs-distiller/resolver", timeout=2.5,
        )
        r.raise_for_status()
        data = r.json() or []
        _catalog_cache["data"] = data
        _catalog_cache["ts"] = now
        return data
    except Exception:
        # Backend hiccup — keep serving the last known catalog so the
        # picker stays usable. Empty list only on a cold-start failure.
        return _catalog_cache["data"] or []


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
    # Hidden ingested badge — revealed by CSS only when picker.js adds
    # `.fw-tile-ingested` (slug found in the /ingestion library). Shows
    # which catalog frameworks have already been downloaded.
    children.append(Span("✓ Ingested", cls="fw-tile-badge", aria_hidden="true"))
    return Div(
        *children,
        cls="fw-tile",
        data_name=f["name"],
        data_slug=f["slug"],
        data_category=(f.get("category") or "Other"),
    )


# ============================================================ #
# Stage sub-nav — primary navigation INSIDE Docs Distiller.    #
# ============================================================ #
# Real <a href> links (NO JS stepper) so each stage is its own
# bookmarkable URL. The active slug is propagated across links
# via ?slug= so the user keeps the framework when jumping stages.
# Catalog never carries a slug (it's the picker).
_STAGES = [
    ("catalog",   "Catalog",   "/docs-distiller"),
    ("ingestion", "Ingestion", "/docs-distiller/ingestion"),
    ("planner",   "Planner",   "/docs-distiller/planner"),
    ("synth",     "Synth",     "/docs-distiller/synth"),
    ("study",     "Study",     "/docs-distiller/study"),
]


def _StageSubNav(active_key: str, slug: str | None):
    """Stage sub-nav. Stages other than catalog get ?slug= appended
    so the user keeps the framework context across page reloads."""
    links = []
    for key, label, href in _STAGES:
        if key != "catalog" and slug:
            href_with_slug = f"{href}?slug={slug}"
        else:
            href_with_slug = href
        cls = "dd-substage active" if key == active_key else "dd-substage"
        links.append(A(label, href=href_with_slug, cls=cls,
                       data_substage=key))
    return Nav(*links, cls="dd-substage-nav", aria_label="Docs Distiller stages")


# ============================================================ #
# Shared chrome — rendered on every stage page.                #
# ============================================================ #
def _FrameworkPicker(slug: str | None, catalog: list[dict] | None = None):
    """Header-anchored framework picker (2026 SOTA pattern, replaces
    the 260px left rail).

    Visual: a chevroned button in the title row showing the current
    selection ("LangChain ▾") or a placeholder ("Library ▾"). Click
    opens a right-aligned popover containing a search input + the
    list of ingested frameworks. Each item carries the same refresh /
    delete actions the old sidebar had — same DOM ids so library.js's
    renderSidebar() targets the popover list without changes.

    Why header instead of sidebar:
      - The picker is used to SWITCH frameworks, not to browse them
        continuously. It belongs to the navigation chrome, not the
        content area.
      - Reclaims 260px of horizontal real estate on every stage page
        — biggest win on planner / synth DAG canvases and the study
        reader where horizontal room is scarce.
      - Matches GitHub repo switcher / Vercel project switcher /
        Linear workspace dropdown — the established 2026 pattern for
        a primary resource picker inside a multi-page workspace.
    """
    # Resolve the current selection's display info from the catalog
    # so the trigger button can render server-side with the right
    # name + logo — no JS flicker. Multi-logo stacks (LangChain bundle,
    # Grafana bundle) render the first logo as the trigger badge.
    info = None
    if slug:
        catalog = catalog or _fetch_catalog()
        for f in catalog or []:
            if f.get("slug") == slug:
                info = f
                break
    label = (info or {}).get("name") or slug or "Library"
    logos = (info or {}).get("logos") or []
    primary_logo = logos[0] if logos else (info or {}).get("logo")

    trigger_children = []
    if primary_logo:
        trigger_children.append(
            Img(src=primary_logo, alt="", cls="dd-fw-picker-logo"))
    trigger_children.append(Span(label, cls="dd-fw-picker-label"))
    trigger_children.append(Span("▾", cls="dd-fw-picker-chevron",
                                 aria_hidden="true"))

    return Div(
        Button(
            *trigger_children,
            type="button",
            id="dd-fw-picker-trigger",
            cls="dd-fw-picker-trigger",
            aria_haspopup="listbox",
            aria_expanded="false",
            aria_label="Switch ingested framework",
        ),
        Div(
            Input(
                type="search",
                id="dd-fw-picker-search",
                placeholder="Search ingested frameworks…",
                cls="dd-fw-picker-search",
                autocomplete="off",
            ),
            # `id="fw-sidebar-list"` is preserved so library.js's
            # existing renderSidebar() and refresh/delete handlers
            # work unchanged. Only the wrapper has moved — from a
            # 260px left rail into this popover.
            Div(
                Div("Loading…", cls="fw-sidebar-empty"),
                id="fw-sidebar-list",
                cls="dd-fw-picker-list",
            ),
            id="dd-fw-picker-popover",
            cls="dd-fw-picker-popover",
            role="listbox",
        ),
        cls="dd-fw-picker",
        id="dd-fw-picker",
        data_dd_slug=(slug or ""),
    )


def _ConfirmModal():
    """Reused by delete + future destructive actions."""
    return Div(
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
    )


def _FileDrawer():
    """Right-anchored slide-out for viewing individual ingested pages."""
    return Div(
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
    )


def _NodeDrawer():
    """Right-anchored slide-out for planner/synth node inspection. See
    docs/UI-ARCHITECTURE-SOTA-2026-05-18.md Day 3 — 3-zone layout."""
    return Div(
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
        Div("", id="fw-node-drawer-kpis", cls="fw-node-drawer-kpis"),
        Div(
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
            Div("", id="fw-node-drawer-details",
                cls="fw-node-drawer-details"),
            id="fw-node-drawer-body", cls="fw-drawer-body",
        ),
        id="fw-node-drawer", cls="fw-drawer",
    )


def _NoticeAndToast():
    """Cache notice + denied toast — both start hidden, JS toggles."""
    return (
        Div(
            Span("", id="fw-cache-notice-text", cls="fw-notice-text"),
            id="fw-cache-notice", cls="fw-notice", style="display:none;",
        ),
        Div(
            Span("", id="fw-denied-toast-text", cls="fw-toast-text"),
            Button("✕", id="fw-denied-toast-close", cls="fw-toast-close"),
            id="fw-denied-toast", cls="fw-toast", style="display:none;",
        ),
    )


def _StickyBar():
    """Catalog-only sticky Generate bar. Other stages omit this."""
    return Div(
        Span(
            "Selected: ",
            Span("", id="fw-selected-name", cls="fw-selected-name"),
            id="fw-selected-label", cls="fw-selected-label",
        ),
        Button("Start Ingestion", id="fw-generate", cls="btn-primary"),
        id="fw-sticky-bar", cls="fw-sticky-bar",
    )


# ============================================================ #
# Stage toolbar (2026-05-28) — row 3 of the sticky header.      #
# ============================================================ #
# Left = per-stage contextual tools (status pill, Start/Wipe
# actions, catalog search). Right = the framework Library picker
# (resource-level, present on every stage). This row carries
# `.topbar-collapsible` so it slides away on scroll-down while the
# brand/nav (row 1) and stage tabs (row 2) stay pinned.
#
# IMPORTANT: every interactive element keeps its original id/class
# so the existing JS (state.js global lookups + planner/synth/study
# handlers) resolves it unchanged — only its DOM location moved out
# of the stage body and into this toolbar.
def _PlannerPill():
    return Div(
        Span("Idle", cls="fw-stage-pill-text", id="fw-planner-pill-text"),
        # Total planner wall-clock — updated live by planner.js (ticks while
        # running) and from GET /planner/{slug}/timing on load/cached runs.
        Span("", cls="fw-stage-elapsed", id="fw-planner-elapsed",
             title="Total Planner time"),
        cls="fw-stage-pill", id="fw-planner-pill", data_status="idle",
    )


def _PlannerActions():
    return Div(
        Button("Wipe planner", id="fw-planner-wipe",
               cls="btn-outline", disabled=True,
               title=("Delete this framework's planner cache "
                      "(MinIO embeddings + Postgres checkpoints "
                      "+ browser state)")),
        Button("Start Planner", id="fw-planner-start",
               cls="btn-primary", disabled=True),
        cls="fw-planner-head-actions",
    )


def _SynthPill():
    return Div(
        Span("Idle", cls="fw-stage-pill-text", id="fw-synth-pill-text"),
        # Total synth wall-clock (cumulative chapter wall + book_harmonize) —
        # updated live by synth.js and from /synth/{slug}/study/chapters
        # (study_total_wall_ms) on load/cached studies.
        Span("", cls="fw-stage-elapsed", id="fw-synth-elapsed",
             title="Total Synth time"),
        cls="fw-stage-pill", id="fw-synth-pill", data_status="idle",
    )


def _SynthActions():
    # Refine-budget dropdown removed 2026-05-28 — it was inert (the synth
    # graph is single-pass; the v2 self-refine loop never consumed it).
    # startSynth defaults the budget to '5' when #fw-synth-budget is
    # absent (synth.js: `S.synthBudgetSel && ... || '5'`).
    return Div(
        Button("Wipe synth", id="fw-synth-wipe",
               cls="btn-outline", disabled=True),
        Button("Start Synth", id="fw-synth-start",
               cls="btn-primary", disabled=True),
        cls="fw-planner-head-actions",
    )


def _StudyTabs():
    # Reader mode switch (Learn / Flashcards) + the mobile chapter-drawer
    # toggle, relocated to the row-3 toolbar (2026-05-28). IDs/classes are
    # preserved so study.js bindings (S.studyTabBtns, #fw-study-toc-toggle)
    # keep working unchanged. ☰ Chapters is hidden on desktop (persistent
    # rail) via CSS; the Learn/Flashcards pair renders as a segmented control.
    return Div(
        Button("☰ Chapters", id="fw-study-toc-toggle",
               cls="fw-study-toc-toggle", type="button",
               title="Show chapters"),
        Div(
            Button("Learn", cls="fw-study-tab active",
                   data_tab="learn", type="button"),
            Button("Flashcards", cls="fw-study-tab",
                   data_tab="flashcards", type="button"),
            cls="fw-study-modes", role="tablist",
        ),
        cls="fw-study-toolgroup",
    )


def _StudyViewButtons():
    # Search + Focus utilities for the right side of the study toolbar.
    return (
        Button("🔍 Search", id="fw-study-search-btn",
               cls="fw-study-search-btn", type="button",
               title="Search all chapters (⌘K / Ctrl-K)"),
        Button("⛶", id="fw-study-focus-toggle",
               cls="fw-study-focus-toggle", type="button",
               title="Focus mode (distraction-free reading)"),
    )


def _CatalogSearch(catalog: list[dict] | None):
    n = len(catalog or [])
    return Div(
        Input(
            type="search", id="fw-search",
            placeholder=f"Search {n} frameworks…",
            autocomplete="off", autofocus=True,
            cls="fw-search",
        ),
        Span("", id="fw-count", cls="fw-count"),
        cls="fw-search-row",
    )


def _CategoryFilter(catalog: list[dict] | None):
    """Catalog category filter — custom popover dropdown (replaces the
    old chip row). Single-select; "All" default; each option carries a
    per-category count. Same open/close + scroll-close behavior as the
    framework picker (wired in picker.js). picker.js reads the chosen
    `data-chip` into S.activeChip and calls applyFilter()."""
    catalog = catalog or []
    counts: dict[str, int] = {}
    for f in catalog:
        c = f.get("category") or "Other"
        counts[c] = counts.get(c, 0) + 1
    cats = sorted(counts)
    options = [
        Button(
            Span("All", cls="dd-catfilter-option-label"),
            Span(str(len(catalog)), cls="dd-catfilter-count"),
            cls="dd-catfilter-option active", data_chip="All",
            type="button", role="option",
        )
    ]
    for c in cats:
        options.append(Button(
            Span(c, cls="dd-catfilter-option-label"),
            Span(str(counts[c]), cls="dd-catfilter-count"),
            cls="dd-catfilter-option", data_chip=c,
            type="button", role="option",
        ))
    return Div(
        Button(
            Span("Category:", cls="dd-catfilter-prefix"),
            Span("All", id="dd-catfilter-label", cls="dd-catfilter-label"),
            Span("▾", cls="dd-catfilter-chevron", aria_hidden="true"),
            id="dd-catfilter-trigger", cls="dd-catfilter-trigger",
            type="button", aria_haspopup="listbox", aria_expanded="false",
            aria_label="Filter frameworks by category",
        ),
        Div(*options, cls="dd-catfilter-popover", role="listbox",
            id="dd-catfilter-popover"),
        cls="dd-catfilter", id="dd-catfilter",
    )


def _StageToolbar(active_stage: str, slug: str | None,
                  catalog: list[dict] | None = None):
    """Row 3 — contextual tools on the left, framework picker on the
    right. Left content varies per stage.

    Catalog is special: the grid IS the framework list, so the Library
    picker is DROPPED (its job — surfacing ingested frameworks — is done
    inline by green-badging the already-ingested tiles, see picker.js
    markIngestedTiles). Catalog's left = search + category dropdown."""
    if active_stage == "catalog":
        left = [_CatalogSearch(catalog), _CategoryFilter(catalog)]
    elif active_stage == "planner":
        left = [_PlannerPill(), _PlannerActions()]
    elif active_stage == "synth":
        left = [_SynthPill(), _SynthActions()]
    elif active_stage == "study":
        left = [_StudyTabs()]
    else:  # ingestion — progress lives in the body; no toolbar tools
        left = []
    children = [Div(*left, cls="dd-toolbar-left")]
    if active_stage != "catalog":
        right = list(_StudyViewButtons()) if active_stage == "study" else []
        right.append(_FrameworkPicker(slug, catalog))
        children.append(Div(*right, cls="dd-toolbar-right"))
    return Div(
        *children,
        cls="dd-toolbar topbar-collapsible",
        id="dd-toolbar",
    )


# ============================================================ #
# Per-stage bodies — one panel each, no stepper, no panel       #
# toggling. Each receives the resolved framework (name + logos) #
# so the page can render its identity strip server-side rather  #
# than waiting for JS to backfill it from the catalog tiles.    #
# ============================================================ #
def _CatalogBody(catalog: list[dict]):
    if not catalog:
        return Div(
            P(
                "Could not load the framework catalog. "
                "Make sure FastAPI is reachable at /api/v1/docs-distiller/resolver.",
                cls="fw-empty",
            ),
            cls="fw-step-panel active",
            id="fw-step-1-panel",
        )
    tiles = [_tile(f) for f in catalog]
    # Search + count AND the category filter both moved to the row-3
    # toolbar (_CatalogSearch + _CategoryFilter). The body is now just
    # the tile grid.
    return Div(
        Div(
            Div(*tiles, cls="fw-grid", id="fw-grid"),
            id="fw-step-1-edit",
        ),
        cls="fw-step-panel active",
        id="fw-step-1-panel",
    )


def _IngestionBody(slug: str | None):
    return Div(
        # Live progress display — hidden by default; pollRun() reveals it
        # only while an ingestion is actually in flight (display=''). On a
        # plain visit with no active run, recoverActiveRuns() returns
        # early without touching it, so without this `display:none` the
        # box would sit visible showing a stale "—" / indeterminate bar.
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
                    Div(id="fw-progress-logos", cls="fw-progress-logos"),
                    Span("", id="fw-progress-framework",
                         cls="fw-progress-framework"),
                    cls="fw-progress-framework-box",
                ),
                Button("Cancel ingestion", id="fw-cancel", cls="btn-outline"),
                cls="fw-progress-actions",
            ),
            id="fw-progress-box", cls="fw-progress", style="display:none;",
        ),
        Div("", id="fw-step2-summary", cls="fw-pages-summary"),
        Div(
            Div(
                "Pick a framework from the Library dropdown above, or "
                "ingest a new one from the Catalog tab, to see its "
                "downloaded files." if not slug else "Loading…",
                cls="fw-empty",
            ),
            id="fw-step2-grid", cls="fw-page-grid",
        ),
        cls="fw-step-panel active",
        id="fw-step-2-panel",
    )


def _PlannerBody(slug: str | None):
    empty_msg = (
        "Pick a framework from the library to view the planner pipeline."
        if not slug else
        "Loading planner state…"
    )
    # Header moved to the row-3 toolbar (_PlannerPill + _PlannerActions).
    # The "Planner" title is redundant with the active stage tab and the
    # framework identity strip is redundant with the Library picker, so
    # both are dropped — the body is now just the empty-state + DAG.
    return Div(
        Div(empty_msg, id="fw-planner-empty", cls="fw-stage-empty"),
        Div(
            Div(id="fw-planner-canvas", cls="fw-stage-canvas"),
            id="fw-planner-graph", cls="fw-planner-graph",
        ),
        cls="fw-step-panel active",
        id="fw-step-3-panel",
    )


def _SynthBody(slug: str | None):
    empty_msg = (
        "Pick a framework from the library to view the synth pipeline."
        if not slug else
        "Loading synth state…"
    )
    # Header moved to the row-3 toolbar (_SynthPill + _SynthActions).
    # Body = empty-state + a 70/30 split: DAG canvas (left) | chapter
    # checklist (right). The chapter list lives in a narrow side panel
    # (where a vertical list belongs) instead of a full-width strip;
    # clicking a chapter focuses its sub-graph on the canvas
    # (_onStripCellClick, already wired). Canvas + #fw-chstrip both
    # start display:none — JS reveals the graph when a framework is
    # active and the chapter panel only in study mode (≥2 chapters),
    # so a non-study run keeps the graph at full width (flex).
    return Div(
        Div(empty_msg, id="fw-synth-empty", cls="fw-stage-empty"),
        Div(
            # LEFT (~70%) — Cytoscape DAG canvas.
            Div(
                Div(id="fw-synth-canvas", cls="fw-stage-canvas"),
                id="fw-synth-graph", cls="fw-planner-graph",
            ),
            # RIGHT (~30%) — chapter checklist (study mode only).
            Div(
                Div(
                    Span("Chapters", cls="fw-chstrip-title"),
                    Span(id="fw-chstrip-counter", cls="fw-chstrip-counter"),
                    cls="fw-chstrip-head",
                ),
                Div(id="fw-chstrip-cells", cls="fw-chstrip-cells"),
                id="fw-chstrip", cls="fw-chstrip",
            ),
            cls="fw-synth-split",
        ),
        cls="fw-step-panel active",
        id="fw-step-4-panel",
    )


def _StudyBody(slug: str | None):
    # Status pill moved to the row-3 toolbar (_StudyPill). The reader's
    # own README/Challenges/Flashcards tabs stay in the body — they're
    # content navigation within a chapter, not stage-level chrome.
    return Div(
        Div(
            "Pick a framework from the library, then run Synth on its "
            "chapters to populate this study viewer.",
            id="fw-study-empty", cls="fw-stage-empty",
        ),
        Div(
            Div(id="fw-study-side-backdrop", cls="fw-study-side-backdrop"),
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
            Div(
                # 2-mode reader (2026-05-28): LEARN = prose + recall in one
                # scroll; FLASHCARDS = the FSRS reviewer as a separate drill
                # mode. The mode switch (Learn/Flashcards) + Search + Focus
                # live in the row-3 toolbar now (_StudyTabs/_StudyViewButtons);
                # only the chapter-head + panes remain in the body.
                Div(id="fw-study-chapter-head",
                    cls="fw-study-chapter-head"),
                Div(
                    # LEARN pane = a scrolling column (prose + recall) +
                    # the right-rail TOC. The prose article keeps id
                    # `fw-study-readme`; the recall block keeps id
                    # `fw-study-challenges` so study.js writes to both
                    # unchanged — they just share one scroll now.
                    Div(
                        Div(
                            Div(
                                Div(
                                    "Open the ☰ Chapters window and pick a "
                                    "chapter.",
                                    cls="fw-empty",
                                ),
                                id="fw-study-readme",
                                cls="fw-study-prose",
                            ),
                            Div(
                                id="fw-study-challenges",
                                cls="fw-study-recall fw-study-prose",
                            ),
                            cls="fw-study-learn-col",
                        ),
                        Div(id="fw-study-toc", cls="fw-study-toc"),
                        cls="fw-study-pane fw-study-readme-pane active",
                        data_tab="learn",
                    ),
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
        cls="fw-step-panel active",
        id="fw-step-5-panel",
    )


# ============================================================ #
# Page composer — wraps a stage body in the shared chrome.     #
# ============================================================ #
def _DDPage(active_stage: str, slug: str | None, body, with_sticky: bool = False):
    """Compose: main panel → overlays.

    `active_stage` is exposed on the root div as `data-dd-stage` so
    main.js can branch its init sequence without re-parsing
    window.location.

    2026-05-27: library sidebar moved into a header-anchored
    `_FrameworkPicker` dropdown (passed via _Shell's title_actions).
    2026-05-28: stage sub-nav moved INTO `.topbar-wrap` as a third
    sticky row (passed via _Shell's subnav_row). `_DDPage` no longer
    renders either piece of nav — only the stage body + overlays.
    """
    notice, toast = _NoticeAndToast()
    extras = []
    if with_sticky:
        extras.append(_StickyBar())
    return Div(
        Div(
            Div(
                notice,
                toast,
                body,
                cls="fw-main",
            ),
            cls="fw-layout",
        ),
        *extras,
        _ConfirmModal(),
        _FileDrawer(),
        _NodeDrawer(),
        Script(src="/static/js/dd/main.js", type="module"),
        cls="fw-picker",
        data_dd_stage=active_stage,
        data_dd_slug=(slug or ""),
    )


def _slug_from_request(req: Request) -> str | None:
    """Read ?slug= from query string; treat empty/whitespace as None."""
    raw = (req.query_params.get("slug") or "").strip()
    return raw or None


def register(rt) -> None:
    """Attach the 5 Docs Distiller routes to `rt`."""

    # Note: title_text is intentionally omitted (None) so the H1 title
    # row is NOT rendered — the active nav pill + the stage tab strip
    # carry page identity. Row 2 = stage tabs (subnav_row), row 3 =
    # contextual toolbar (toolbar_row, holds the framework picker).
    @rt("/docs-distiller")
    def docs_distiller_catalog():
        catalog = _fetch_catalog()
        return _Shell(
            "docs-distiller",
            subnav_row=_StageSubNav("catalog", None),
            toolbar_row=_StageToolbar("catalog", None, catalog),
            body=_DDPage("catalog", None, _CatalogBody(catalog),
                         with_sticky=True),
        )

    @rt("/docs-distiller/ingestion")
    def docs_distiller_ingestion(req: Request):
        slug = _slug_from_request(req)
        return _Shell(
            "docs-distiller",
            subnav_row=_StageSubNav("ingestion", slug),
            toolbar_row=_StageToolbar("ingestion", slug),
            body=_DDPage("ingestion", slug, _IngestionBody(slug)),
        )

    @rt("/docs-distiller/planner")
    def docs_distiller_planner(req: Request):
        slug = _slug_from_request(req)
        return _Shell(
            "docs-distiller",
            subnav_row=_StageSubNav("planner", slug),
            toolbar_row=_StageToolbar("planner", slug),
            body=_DDPage("planner", slug, _PlannerBody(slug)),
        )

    @rt("/docs-distiller/synth")
    def docs_distiller_synth(req: Request):
        slug = _slug_from_request(req)
        return _Shell(
            "docs-distiller",
            subnav_row=_StageSubNav("synth", slug),
            toolbar_row=_StageToolbar("synth", slug),
            body=_DDPage("synth", slug, _SynthBody(slug)),
        )

    @rt("/docs-distiller/study")
    def docs_distiller_study(req: Request):
        slug = _slug_from_request(req)
        return _Shell(
            "docs-distiller",
            subnav_row=_StageSubNav("study", slug),
            toolbar_row=_StageToolbar("study", slug),
            body=_DDPage("study", slug, _StudyBody(slug)),
        )
