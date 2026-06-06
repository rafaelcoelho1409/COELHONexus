"""5 stage routes — catalog/ingestion/planner/synth/study.

Each stage has its OWN URL so users can bookmark, share, and use browser
back/forward across the (often hours-long) flow. The framework slug is
carried in the `?slug=` query string.

Backend contracts (forwarded via the FastHTML proxy):
  POST /runs                      → {status: cached|queued|locked, run_id?, manifest?}
  POST /runs/{id}/cancel          → cooperative cancel
  GET  /runs/{id}                 → live progress + manifest snapshot (Redis)
  GET  /ingestion                 → sidebar data source
  GET  /ingestion/{slug}/manifest → canonical manifest from MinIO
  GET  /ingestion/{slug}/pages/{i}→ page body from MinIO"""
from starlette.requests import Request

from layout.shell import _Shell

from .cache import fetch_catalog
from .catalog.body import CatalogBody
from .ingestion.body import IngestionBody
from .page import DDPage
from .planner.body import PlannerBody
from .shared.nav import StageSubNav
from .shared.toolbar import StageToolbar
from .study.body import StudyBody
from .synth.body import SynthBody


def _slug_from_request(req: Request) -> str | None:
    raw = (req.query_params.get("slug") or "").strip()
    return raw or None


def register(rt) -> None:
    # title_text is intentionally omitted — the active nav pill + the
    # stage tab strip carry page identity. Row 2 = stage tabs
    # (subnav_row), row 3 = contextual toolbar (toolbar_row, holds the
    # framework picker).
    @rt("/docs-distiller")
    def dd_catalog():
        catalog = fetch_catalog()
        return _Shell(
            "docs-distiller",
            subnav_row = StageSubNav("catalog", None),
            toolbar_row = StageToolbar("catalog", None, catalog),
            body = DDPage("catalog", None, CatalogBody(catalog),
                          with_sticky = True),
        )

    @rt("/docs-distiller/ingestion")
    def dd_ingestion(req: Request):
        slug = _slug_from_request(req)
        return _Shell(
            "docs-distiller",
            subnav_row = StageSubNav("ingestion", slug),
            toolbar_row = StageToolbar("ingestion", slug),
            body = DDPage("ingestion", slug, IngestionBody(slug)),
        )

    @rt("/docs-distiller/planner")
    def dd_planner(req: Request):
        slug = _slug_from_request(req)
        return _Shell(
            "docs-distiller",
            subnav_row = StageSubNav("planner", slug),
            toolbar_row = StageToolbar("planner", slug),
            body = DDPage("planner", slug, PlannerBody(slug)),
        )

    @rt("/docs-distiller/synth")
    def dd_synth(req: Request):
        slug = _slug_from_request(req)
        return _Shell(
            "docs-distiller",
            subnav_row = StageSubNav("synth", slug),
            toolbar_row = StageToolbar("synth", slug),
            body = DDPage("synth", slug, SynthBody(slug)),
        )

    @rt("/docs-distiller/study")
    def dd_study(req: Request):
        slug = _slug_from_request(req)
        return _Shell(
            "docs-distiller",
            subnav_row = StageSubNav("study", slug),
            toolbar_row = StageToolbar("study", slug),
            body = DDPage("study", slug, StudyBody(slug)),
        )
