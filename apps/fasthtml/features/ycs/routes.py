"""YCS — four stage routes for the wizard.

  GET /youtube-content-search             → Step 1 · Source
  GET /youtube-content-search/ingestion   → Step 2 · Ingestion
  GET /youtube-content-search/ask         → Step 3 · Ask
  GET /youtube-content-search/query       → Step 4 · Query

Each stage has its own URL so users can bookmark + browser
back/forward. The shell renders the row-2 stage tab strip via
`subnav_row = StageSubNav(...)` and the row-3 contextual toolbar via
`toolbar_row = StageToolbar(...)` — same shape as Docs Distiller, so
the H1 + tab row are auto-derived from `layout/urls.py:FEATURES`.
Wave 5 of `docs/YCS-PORT-PLAN-2026-06-06.md` will reintroduce
server-side data fetches (ES-aggregation channels + playlists for
the Ingest step, recent searches for Source) once the Wave 4
endpoints are live."""
from __future__ import annotations

from starlette.requests import Request

from layout.shell import _Shell

from .ask.body import AskBody
from .ingest.body import IngestBody
from .page import YCSPage
from .query.body import QueryBody
from .shared.nav import StageSubNav
from .shared.toolbar import StageToolbar
from .source.body import SourceBody


def _slug_from_request(req: Request) -> str | None:
    raw = (req.query_params.get("slug") or "").strip()
    return raw or None


def register(rt) -> None:
    @rt("/youtube-content-search")
    def ycs_source(req: Request):
        slug = _slug_from_request(req)
        return _Shell(
            "youtube-content-search",
            subnav_row = StageSubNav("source", slug),
            toolbar_row = StageToolbar("source", slug),
            body = YCSPage("source", slug, SourceBody(slug)),
        )

    @rt("/youtube-content-search/ingestion")
    def ycs_ingestion(req: Request):
        # Stage key "ingestion" matches DD's stage key + URL exactly
        # (was "ingest" / "/ingest" pre-2026-06-08 rename). The
        # underlying Python module path `features/ycs/ingest/` is
        # unchanged — internal-only and renaming would touch dozens
        # of imports with no user-visible benefit.
        slug = _slug_from_request(req)
        return _Shell(
            "youtube-content-search",
            subnav_row = StageSubNav("ingestion", slug),
            toolbar_row = StageToolbar("ingestion", slug),
            body = YCSPage("ingestion", slug, IngestBody(slug)),
        )

    @rt("/youtube-content-search/ask")
    def ycs_ask(req: Request):
        slug = _slug_from_request(req)
        return _Shell(
            "youtube-content-search",
            subnav_row = StageSubNav("ask", slug),
            toolbar_row = StageToolbar("ask", slug),
            body = YCSPage("ask", slug, AskBody(slug)),
        )

    @rt("/youtube-content-search/query")
    def ycs_query(req: Request):
        # Library-agnostic — Query browses the indexes wholesale, so
        # `slug` is ignored at URL build time (see shared/urls.py).
        # Still resolved + threaded through for symmetry.
        slug = _slug_from_request(req)
        return _Shell(
            "youtube-content-search",
            subnav_row = StageSubNav("query", slug),
            toolbar_row = StageToolbar("query", slug),
            body = YCSPage("query", slug, QueryBody(slug)),
        )
