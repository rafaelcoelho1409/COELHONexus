"""HTTP-aware Depends() composers for dd/ routers. Lives here (not in
domains/) so HTTPException doesn't couple the domain to FastAPI. Tests
override via `app.dependency_overrides[get_catalog_entry] = ...`."""
from __future__ import annotations

import json
from typing import Annotated

from fastapi import Depends, HTTPException

from domains.dd.ingestion.storage import get_storage
from domains.dd.planner.keys import plan_latest_key
from domains.dd.resolver import index_by_slug


async def get_catalog_entry(slug: str) -> dict:
    entry = index_by_slug().get(slug)
    if entry is None:
        raise HTTPException(
            status_code = 404,
            detail = f"unknown slug: {slug!r}",
        )
    return entry


CatalogEntry = Annotated[dict, Depends(get_catalog_entry)]


async def get_plan(slug: str) -> dict:
    minio = get_storage()
    key = plan_latest_key(slug)
    if not await minio.exists(key):
        raise HTTPException(
            status_code = 404,
            detail = (
                f"no planner plan for {slug!r} — run the planner first "
                f"(POST /planner/{slug})"
            ),
        )
    try:
        text = await minio.read_text(key)
        return json.loads(text) or {}
    except Exception as e:
        raise HTTPException(
            status_code = 500,
            detail = f"plan {key!r} unreadable: {type(e).__name__}: {e}",
        )


Plan = Annotated[dict, Depends(get_plan)]
