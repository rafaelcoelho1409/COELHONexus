"""YCS stage URL table + builder.

Three-step wizard — Source (pick videos) → Ingest (download + index) →
Ask (RAG chat over the library). Each stage owns its URL so users can
bookmark, share, and use browser back/forward. The active framework
isn't slug-scoped yet (Slice 2 will introduce a library identifier);
the builder leaves room for it via `?slug=`.

Mirror of `features/dd/shared/urls.py`."""
from __future__ import annotations


_STAGES = [
    ("source", "Source", "/youtube-content-search"),
    ("ingest", "Ingest", "/youtube-content-search/ingest"),
    ("ask",    "Ask",    "/youtube-content-search/ask"),
]


def stage_url(stage_key: str, slug: str | None) -> str:
    base = next(
        (href for key, _, href in _STAGES if key == stage_key),
        "/youtube-content-search",
    )
    if stage_key != "source" and slug:
        return f"{base}?slug={slug}"
    return base
