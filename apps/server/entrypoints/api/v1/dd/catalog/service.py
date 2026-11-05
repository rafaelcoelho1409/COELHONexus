"""catalog service — catalog endpoint logic shared across routers.

`get_catalog_entry` is the single api-side home for "slug → catalog
dict or 404" (wrapping `domains.dd.catalog.service` with HTTP error
mapping). `runs` calls it directly; `catalog`/`debug` consume it
through `schemas.CatalogEntry`.
"""
import domains

from fastapi import HTTPException


async def get_catalog_entry(slug: str) -> dict:
    entry = domains.dd.catalog.service.index_by_slug().get(slug)
    if entry is None:
        raise HTTPException(
            status_code = 404,
            detail = f"unknown slug: {slug!r}",
        )
    return entry
