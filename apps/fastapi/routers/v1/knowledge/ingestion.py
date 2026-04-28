"""
POST /api/v1/knowledge/ingestion

Standalone ingestion endpoint — runs the ingest stage in isolation
(fetch + parse + persist to MinIO + populate corpus cache). No planning,
no synthesis, no critic. Mirrors the YouTube Ask split: ingest once per
(framework, version), then any number of synthesis / Ask / summary
operations can read the corpus.

Flow:
  1. Resolver lookup against sources.yaml (404 if name not curated).
  2. Cache check via StudyCache.get_ingestion(framework, version).
       hit  → return 200 with cache metadata, no Celery task.
       miss → enqueue Celery task, return 202 with task_id.
  3. Caller polls /api/v1/tasks/{task_id} for completion.

Cache key is `_cache/ingestion/{framework_slug}/{version_slug}/` — shared
across users (the corpus is identity-of-source, not identity-of-tenant).
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from schemas.knowledge.inputs import NonEmptyStr
from services.knowledge.cache import StudyCache
from services.resolver import lookup as resolver_lookup

logger = logging.getLogger(__name__)

router = APIRouter()


def _ingestion_study_root(user_id: str, framework: str, version: Optional[str]) -> str:
    """
    Per-call MinIO sandbox for ingestion artifacts. The durable corpus
    lives in `_cache/ingestion/...` and is the actual reuse target; this
    `study_root` is the working directory ingest_framework_docs writes
    to en route to populating the cache.
    """
    framework_part = framework.lower().strip().replace(" ", "-")
    version_part = (version or "latest").lower().strip().replace(" ", "-")
    return f"{user_id}/knowledge/{framework_part}-{version_part}"


class IngestionRequest(BaseModel):
    framework: NonEmptyStr = Field(
        description = (
            "Catalog name in apps/fastapi/files/sources.yaml. Case-insensitive. "
            "GET /api/v1/knowledge/resolve/sources for the available list."
        ),
    )
    version: Optional[NonEmptyStr] = Field(
        default = None,
        description = "Version pin or omit for 'latest' (14-day TTL).",
    )
    user_id: NonEmptyStr = Field(
        default = "default",
        description = "Multi-tenancy key. Cache is shared across users — only the working study_root is per-tenant.",
    )
    force: bool = Field(
        default = False,
        description = "Bypass the corpus cache and re-ingest from scratch.",
    )


@router.post("/ingestion")
async def create_ingestion(payload: IngestionRequest, request: Request):
    """
    Ingest a curated framework's docs into MinIO. Idempotent on cache hit.

    Error codes:
        404: framework name not in sources.yaml.
    """
    app = request.app

    # 1) Resolver lookup.
    entry = resolver_lookup(payload.framework)
    if entry is None or not entry.tiers:
        raise HTTPException(
            status_code = 404,
            detail = {
                "message": (
                    f"'{payload.framework}' is not in the curated catalog. "
                    "GET /api/v1/knowledge/resolve/sources for available names."
                ),
                "framework": payload.framework,
            },
        )

    # 2) Cache check (skipped on force).
    cache = StudyCache(storage = app.state.study_storage, latest_ttl_days = 14)
    if not payload.force:
        cached = await cache.get_ingestion(entry.name, payload.version)
        if cached is not None:
            logger.info(
                f"[ingestion] cache hit — framework={entry.name} "
                f"version={payload.version or 'latest'} "
                f"hash={cached.manifest_hash} files={len(cached.manifest)}"
            )
            return {
                "status": "cached",
                "framework": entry.name,
                "version": payload.version or "latest",
                "tier_used": cached.tier_used,
                "manifest_hash": cached.manifest_hash,
                "total_files": len(cached.manifest),
                "cached_at": cached.cached_at,
            }

    # 3) Enqueue ingestion-only Celery task.
    docs_url = entry.best.url
    tier = entry.best.tier
    repo_url = entry.github_repo
    github_org, github_repo = entry.github_org_repo
    study_root = _ingestion_study_root(payload.user_id, entry.name, payload.version)
    task_id = str(uuid.uuid4())

    from tasks.knowledge.ingestion import run_knowledge_ingestion
    run_knowledge_ingestion.apply_async(
        kwargs = {
            "study_id": task_id,
            "framework": entry.name,
            "version": payload.version,
            "docs_url": docs_url,
            "language": None,
            "user_id": payload.user_id,
            "study_root": study_root,
            "tier": tier,
            "github_discover": "homepage" if repo_url else None,
            "github_org": github_org,
            "github_repo": github_repo,
            "github_default_branch": None,
            "repo_url": repo_url,
        },
        task_id = task_id,
        # Ingestion crawls cap at ~20 min on Tier 4 Playwright; 1h is
        # the broker safety net before the message expires unconsumed.
        expires = 3600,
    )
    logger.info(
        f"[ingestion] queued — task_id={task_id} framework={entry.name} "
        f"version={payload.version or 'latest'} tier={tier} "
        f"force={payload.force}"
    )

    return {
        "status": "queued",
        "task_id": task_id,
        "endpoint": f"/api/v1/tasks/{task_id}",
        "stream_endpoint": f"/api/v1/knowledge/studies/{task_id}/stream",
        "framework": entry.name,
        "category": entry.category,
        "version": payload.version or "latest",
        "tier": tier,
        "tier_kind": entry.best.kind,
        "docs_url": docs_url,
        "study_root": study_root,
    }
