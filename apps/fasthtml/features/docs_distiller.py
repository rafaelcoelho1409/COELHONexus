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
    A, Button, Div, Img, Input, Nav, Option, P, Script, Select, Span,
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
def _Sidebar():
    """Library sidebar — list of ingested frameworks. JS hydrates it
    from GET /api/v1/docs-distiller/ingestion on every page."""
    return Div(
        P("Library", cls="fw-sidebar-title"),
        Div(
            Div("Loading…", cls="fw-sidebar-empty"),
            id="fw-sidebar-list",
        ),
        id="fw-sidebar", cls="fw-sidebar",
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
    cats = sorted({(f.get("category") or "Other") for f in catalog})
    chips = [Span("All", cls="fw-chip active", data_chip="All")] + [
        Span(c, cls="fw-chip", data_chip=c) for c in cats
    ]
    tiles = [_tile(f) for f in catalog]
    return Div(
        Div(
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
        ),
        cls="fw-step-panel active",
        id="fw-step-1-panel",
    )


def _IngestionBody(slug: str | None):
    return Div(
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
        Div("", id="fw-step2-summary", cls="fw-pages-summary"),
        Div(
            Div(
                "Pick a framework in the catalog or the sidebar to see "
                "its downloaded files." if not slug else "Loading…",
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
    return Div(
        # Header — title row + status pill + framework strip + actions
        Div(
            Div(
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
                Div(
                    Div(id="fw-planner-fw-logos",
                        cls="fw-planner-fw-logos"),
                    Span(
                        "Pick a framework to start." if not slug else slug,
                        id="fw-planner-fw-name",
                        cls=("fw-planner-fw-name fw-planner-fw-name-empty"
                             if not slug else "fw-planner-fw-name"),
                    ),
                    id="fw-planner-fw", cls="fw-planner-fw",
                ),
                cls="fw-planner-head-text",
            ),
            Div(
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
    return Div(
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
                    Span(
                        "Pick a framework to start." if not slug else slug,
                        id="fw-synth-fw-name",
                        cls=("fw-planner-fw-name fw-planner-fw-name-empty"
                             if not slug else "fw-planner-fw-name"),
                    ),
                    id="fw-synth-fw", cls="fw-planner-fw",
                ),
                cls="fw-planner-head-text",
            ),
            Div(
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
        Div(empty_msg, id="fw-synth-empty", cls="fw-stage-empty"),
        Div(
            Div(
                Span("Chapters", cls="fw-chstrip-title"),
                Span(id="fw-chstrip-counter", cls="fw-chstrip-counter"),
                cls="fw-chstrip-head",
            ),
            Div(id="fw-chstrip-cells", cls="fw-chstrip-cells"),
            id="fw-chstrip", cls="fw-chstrip",
        ),
        Div(
            Div(id="fw-synth-canvas", cls="fw-stage-canvas"),
            id="fw-synth-graph", cls="fw-planner-graph",
        ),
        cls="fw-step-panel active",
        id="fw-step-4-panel",
    )


def _StudyBody(slug: str | None):
    return Div(
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
                Span(
                    "Pick a framework with synthesized chapters."
                    if not slug else slug,
                    id="fw-study-fw-name",
                    cls=("fw-planner-fw-name fw-planner-fw-name-empty"
                         if not slug else "fw-planner-fw-name"),
                ),
                id="fw-study-fw", cls="fw-planner-fw",
            ),
            cls="fw-planner-head-text",
        ),
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
                Div(id="fw-study-chapter-head",
                    cls="fw-study-chapter-head"),
                Div(
                    Div(
                        Div(
                            "Open the ☰ Chapters window and pick a chapter.",
                            cls="fw-empty",
                        ),
                        id="fw-study-readme",
                        cls="fw-study-pane fw-study-prose active",
                        data_tab="readme",
                    ),
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
    """Compose: stage sub-nav → layout(sidebar + body) → overlays.

    `active_stage` highlights the matching link in the sub-nav AND
    is exposed on the root div as `data-dd-stage` so main.js can
    branch its init sequence without re-parsing window.location.
    """
    notice, toast = _NoticeAndToast()
    extras = []
    if with_sticky:
        extras.append(_StickyBar())
    return Div(
        _StageSubNav(active_stage, slug),
        Div(
            _Sidebar(),
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

    @rt("/docs-distiller")
    def docs_distiller_catalog():
        catalog = _fetch_catalog()
        return _Shell(
            "docs-distiller", "Docs Distiller",
            body=_DDPage("catalog", None, _CatalogBody(catalog),
                         with_sticky=True),
        )

    @rt("/docs-distiller/ingestion")
    def docs_distiller_ingestion(req: Request):
        slug = _slug_from_request(req)
        return _Shell(
            "docs-distiller", "Docs Distiller",
            body=_DDPage("ingestion", slug, _IngestionBody(slug)),
        )

    @rt("/docs-distiller/planner")
    def docs_distiller_planner(req: Request):
        slug = _slug_from_request(req)
        return _Shell(
            "docs-distiller", "Docs Distiller",
            body=_DDPage("planner", slug, _PlannerBody(slug)),
        )

    @rt("/docs-distiller/synth")
    def docs_distiller_synth(req: Request):
        slug = _slug_from_request(req)
        return _Shell(
            "docs-distiller", "Docs Distiller",
            body=_DDPage("synth", slug, _SynthBody(slug)),
        )

    @rt("/docs-distiller/study")
    def docs_distiller_study(req: Request):
        slug = _slug_from_request(req)
        return _Shell(
            "docs-distiller", "Docs Distiller",
            body=_DDPage("study", slug, _StudyBody(slug)),
        )
