"""I/O orchestration for the RR domain — Imperative Shell.

Per docs/CODE-CONVENTIONS.md §service: thin orchestrator. Pure math
lives in domain.py; per-store ops live under stores/. This module
composes them into the operations the agent (graph_build, report) and
the FastAPI router will call.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from .entities import Finding, NormalizedPaper
from .keys import SCAN_STATUS_CANCELLED, SCAN_STATUS_ERROR
from .stores import minio as minio_store
from .stores import neo4j as neo4j_store
from .stores import postgres as postgres_store
from .stores import qdrant as qdrant_store


logger = logging.getLogger(__name__)


async def bootstrap_stores() -> None:
    """Initialize all 4 RR stores. Idempotent + safe to re-run."""
    results = await asyncio.gather(
        postgres_store.bootstrap_postgres(),
        neo4j_store.bootstrap_neo4j(),
        qdrant_store.bootstrap_qdrant(),
        minio_store.bootstrap_minio(),
    )
    logger.info(f"[rr-service] bootstrap_stores: 4/4 complete ({len(results)})")


async def persist_paper(
    paper: NormalizedPaper,
    *,
    embedding: list[float] | tuple[float, ...] | None,
    signal: float | None = None,
) -> None:
    """Write a paper to Neo4j (always) + Qdrant (only when embedding given)."""
    if not paper.arxiv_id:
        logger.warning(
            f"[rr-service] persist_paper skipped — no arxiv_id "
            f"(title={paper.title[:40]!r})"
        )
        return
    coros: list[Any] = [neo4j_store.upsert_paper(paper, signal=signal)]
    if embedding is not None:
        coros.append(
            qdrant_store.upsert_paper_vector(paper, embedding=embedding, signal=signal)
        )
    await asyncio.gather(*coros)


async def begin_scan(
    scan_id:    UUID,
    profile_id: str,
    *,
    topic:      str | None       = None,
    verticals:  list[str] | None = None,
    top_n:      int | None       = None,
) -> None:
    """Create the radar_scans row (status=pending) then immediately flip to running."""
    await postgres_store.create_scan(
        scan_id, profile_id,
        topic     = topic,
        verticals = verticals,
        top_n     = top_n,
    )
    await postgres_store.mark_scan_running(scan_id)


async def complete_scan(
    scan_id: UUID,
    *,
    total_candidates: int,
    total_in_digest: int,
) -> None:
    await postgres_store.mark_scan_done(
        scan_id,
        total_candidates = total_candidates,
        total_in_digest  = total_in_digest,
    )


async def fail_scan(scan_id: UUID, error: str, *, cancelled: bool = False) -> None:
    status = SCAN_STATUS_CANCELLED if cancelled else SCAN_STATUS_ERROR
    await postgres_store.mark_scan_error(scan_id, status=status, error=error)


async def delete_scan(scan_id: UUID) -> dict:
    """Drop per-scan presentation artifacts: Postgres row + findings + MinIO digest.

    Intentionally preserves accumulated knowledge: Neo4j graph, Qdrant embeddings, radar_seen markers.
    """
    from .stores import minio as minio_store

    pg_deleted    = False
    minio_deleted = False
    code_deleted  = 0
    try:
        pg_deleted = await postgres_store.delete_scan_record(scan_id)
    except Exception as e:
        logger.warning(f"[rr-service] delete_scan {scan_id} pg failed: {e}")
    try:
        minio_deleted = await minio_store.delete_digest_json(str(scan_id))
    except Exception as e:
        logger.warning(f"[rr-service] delete_scan {scan_id} minio failed: {e}")
    try:
        code_deleted = await minio_store.delete_code_dir(str(scan_id))
    except Exception as e:
        logger.warning(f"[rr-service] delete_scan {scan_id} code failed: {e}")
    logger.info(
        f"[rr-service] delete_scan {scan_id} pg={pg_deleted} "
        f"minio={minio_deleted} code={code_deleted}"
    )
    return {
        "scan_id": str(scan_id),
        "pg":      pg_deleted,
        "minio":   minio_deleted,
        "code":    code_deleted,
    }


async def cancel_scan(scan_id: UUID, *, reason: str = "cancelled by user") -> bool:
    """Revoke the Celery task, mark Postgres cancelled, emit terminal SSE event.

    Returns True when a live task was found and revoked, False when no task_id registered.
    Order: revoke → mark Postgres → emit SSE → drop task_id key.
    A failure in step 2/3/4 doesn't roll back step 1; the worker is already dead.
    """
    from infra.celery.service import app as celery_app
    from .runtime.events import clear_task_id, emit_event, get_task_id

    task_id = await get_task_id(str(scan_id))
    if not task_id:
        logger.info(f"[rr-service] cancel_scan {scan_id}: no task_id found")
        return False

    try:
        celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
        logger.info(f"[rr-service] cancel_scan {scan_id} revoked task_id={task_id}")
    except Exception as e:
        logger.warning(
            f"[rr-service] cancel_scan {scan_id} revoke failed "
            f"task_id={task_id}: {type(e).__name__}: {e}"
        )

    try:
        await fail_scan(scan_id, reason, cancelled=True)
    except Exception as e:
        logger.warning(f"[rr-service] cancel_scan {scan_id} fail_scan failed: {e}")

    try:
        await emit_event(str(scan_id), "cancelled", message=reason)
    except Exception as e:
        logger.warning(f"[rr-service] cancel_scan {scan_id} emit_event failed: {e}")

    try:
        await clear_task_id(str(scan_id))
    except Exception:
        pass

    return True


async def persist_scan_result(
    scan_id: UUID,
    profile_id: str,
    *,
    findings: list[Finding],
    digest_payload: dict[str, Any],
) -> dict[str, Any]:
    """Persist ranked digest: findings → Postgres, digest.json → MinIO, arxiv_ids → radar_seen."""
    n_findings = await postgres_store.record_findings(scan_id, findings)
    digest_key = await minio_store.put_digest_json(str(scan_id), digest_payload)
    try:
        await postgres_store.write_synthesis_meta(
            scan_id,
            themes  = list(digest_payload.get("themes") or []),
            summary = digest_payload.get("summary"),
        )
    except Exception as e:
        logger.warning(
            f"[rr-service] persist_scan_result synthesis meta failed: "
            f"{type(e).__name__}: {e}"
        )
    arxiv_ids = [f.arxiv_id for f in findings if f.arxiv_id]
    n_seen = await postgres_store.mark_seen_batch(profile_id, arxiv_ids)
    summary = {
        "scan_id":           str(scan_id),
        "profile_id":        profile_id,
        "n_findings":        n_findings,
        "n_marked_seen":     n_seen,
        "digest_minio_key":  digest_key,
        "persisted_at":      datetime.now(timezone.utc).isoformat(),
    }
    logger.info(
        f"[rr-service] persist_scan_result {scan_id} "
        f"findings={n_findings} seen={n_seen} key={digest_key}"
    )
    return summary


async def get_seen_ids(profile_id: str) -> frozenset[str]:
    return await postgres_store.get_seen_ids(profile_id)


async def get_profile(profile_id: str) -> dict[str, Any] | None:
    return await postgres_store.get_profile(profile_id)


async def upsert_profile(
    profile_id: str, *, interests: dict[str, Any], weights: dict[str, Any],
) -> None:
    await postgres_store.upsert_profile(
        profile_id, interests=interests, weights=weights,
    )
