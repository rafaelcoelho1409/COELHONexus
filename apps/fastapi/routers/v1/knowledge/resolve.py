"""
Curated-list resolver endpoints.

  GET  /api/v1/knowledge/resolve/sources
       List every technology in the curated catalog (sources.yaml).

  POST /api/v1/knowledge/resolve
       body: {"name": "Docker"}                  single
        OR   {"names": ["Docker", "Kubernetes"]} batch
       Returns each name's docs URLs in tier order:
         1: llms_full_txt
         2: llms_txt
         3: sitemap_xml
         4: docs_url
       Unknown names are returned in `not_found`.

There is no online discovery, search, or heuristic resolution. Only names
present in apps/fastapi/files/sources.yaml are accepted.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.resolver import SourceEntry, list_sources, lookup

router = APIRouter()


class ResolveRequest(BaseModel):
    name: Optional[str] = Field(
        default=None,
        description="Single technology name (e.g., 'Docker').",
    )
    names: Optional[list[str]] = Field(
        default=None,
        description="Batch of technology names.",
    )


class TierOut(BaseModel):
    tier: int
    kind: str
    url: str


class ResolvedOut(BaseModel):
    name: str
    category: Optional[str] = None
    best: Optional[TierOut] = None
    tiers: list[TierOut]
    github_repo: Optional[str] = None


class ResolveResponse(BaseModel):
    results: list[ResolvedOut]
    not_found: list[str]


class SourceItemOut(BaseModel):
    name: str
    category: Optional[str] = None
    available_tiers: list[str]
    has_github: bool


class SourcesListOut(BaseModel):
    total: int
    sources: list[SourceItemOut]


def _to_resolved(e: SourceEntry) -> ResolvedOut:
    tiers = [TierOut(tier=t.tier, kind=t.kind, url=t.url) for t in e.tiers]
    return ResolvedOut(
        name=e.name,
        category=e.category,
        best=tiers[0] if tiers else None,
        tiers=tiers,
        github_repo=e.github_repo,
    )


@router.get("/resolve/sources", response_model=SourcesListOut)
async def resolve_sources():
    """Every technology in the curated catalog with available tiers per entry."""
    items = list_sources()
    return SourcesListOut(
        total=len(items),
        sources=[
            SourceItemOut(
                name=e.name,
                category=e.category,
                available_tiers=e.available_tier_kinds,
                has_github=e.github_repo is not None,
            )
            for e in items
        ],
    )


@router.post("/resolve", response_model=ResolveResponse)
async def resolve(payload: ResolveRequest):
    """Resolve one or more curated names to tier-ordered docs URLs."""
    requested: list[str] = []
    if payload.names:
        requested.extend(payload.names)
    if payload.name:
        requested.append(payload.name)
    requested = [n.strip() for n in requested if n and n.strip()]

    if not requested:
        raise HTTPException(
            status_code=400,
            detail="must supply 'name' or 'names'",
        )

    results: list[ResolvedOut] = []
    not_found: list[str] = []
    seen: set[str] = set()
    for n in requested:
        key = n.lower()
        if key in seen:
            continue
        seen.add(key)
        entry = lookup(n)
        if entry is None:
            not_found.append(n)
        else:
            results.append(_to_resolved(entry))

    return ResolveResponse(results=results, not_found=not_found)
