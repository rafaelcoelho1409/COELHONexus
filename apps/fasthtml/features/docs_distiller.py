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
    Button, Div, Img, Input, P, Script, Span,
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
    if f.get("logo"):
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

    # Step 3 — page grid (rendered by JS from /ingestion/{slug}/manifest)
    step3_body = Div(
        Div(id="fw-pages-summary", cls="fw-pages-summary"),
        Div(
            Div(
                "Pick an item from the sidebar or generate a new study.",
                cls="fw-empty",
            ),
            id="fw-page-grid", cls="fw-page-grid",
        ),
    )

    return Div(
        # Stepper row
        Div(
            Div(
                _Step(1, "Catalog", active=True),
                Span(cls="fw-step-connector"),
                _Step(2, "Ingestion"),
                Span(cls="fw-step-connector"),
                _Step(3, "Study"),
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
            # Main panel — holds the 3 step panels
            Div(
                Div(
                    step1_edit,
                    id="fw-step-1-panel", cls="fw-step-panel active",
                ),
                Div(step2_body, id="fw-step-2-panel", cls="fw-step-panel"),
                Div(step3_body, id="fw-step-3-panel", cls="fw-step-panel"),
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
        Script(src="/static/js/docs_distiller.js"),
        cls="fw-picker",
    )


def register(rt) -> None:
    """Attach /docs-distiller (and / aliased to it) to `rt`."""
    @rt("/")
    def index():
        return _Shell("docs-distiller", "Docs Distiller", body=_Picker())

    @rt("/docs-distiller")
    def docs_distiller():
        return _Shell("docs-distiller", "Docs Distiller", body=_Picker())
