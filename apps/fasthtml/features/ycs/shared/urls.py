"""YCS stage URL table + builder.

Four-step wizard — Source (pick videos) → Ingestion (download + index) →
Ask (RAG chat over the library) → Query (browse ES / Qdrant / Neo4j
across DD / YCS / RR). Each stage owns its URL so users can bookmark,
share, and use browser back/forward. The active framework isn't
slug-scoped yet (Slice 2 will introduce a library identifier); the
builder leaves room for it via `?slug=`.

Mirror of `features/dd/shared/urls.py` — `ingestion` matches DD's stage
key and URL exactly (renamed from `ingest` on 2026-06-08 for parity).
The Query stage is library-agnostic — `stage_url("query", slug)` drops
the `?slug=` because the page explores the indexes wholesale."""
from __future__ import annotations


_STAGES = [
    ("source",    "Source",    "/youtube-content-search"),
    ("ingestion", "Ingestion", "/youtube-content-search/ingestion"),
    ("ask",       "Ask",       "/youtube-content-search/ask"),
    ("query",     "Query",     "/youtube-content-search/query"),
]


def stage_url(stage_key: str, slug: str | None) -> str:
    base = next(
        (href for key, _, href in _STAGES if key == stage_key),
        "/youtube-content-search",
    )
    # Source is the wizard entry — no slug yet. Query is library-
    # agnostic (the indexes/collections/graph are global), so it also
    # ignores slug. Everything else carries it through.
    if stage_key in ("source", "query"):
        return base
    if slug:
        return f"{base}?slug={slug}"
    return base
