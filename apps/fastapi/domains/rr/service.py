"""I/O orchestration for the RR domain — Imperative Shell.

Per docs/CODE-CONVENTIONS.md §service: thin orchestrator. Pure math
lives in domain.py; per-store ops live under stores/. This module
composes them into the operations the agent (graph_build, report) and
the FastAPI router will call.

Public surface:

  bootstrap_stores()       — startup: ensure all 4 stores' schemas exist
  persist_paper(...)       — graph + vector together (one paper)
  persist_scan_result(...) — final write at scan end: findings + digest
                             + seen set
  begin_scan / complete_scan / fail_scan
                           — Postgres lifecycle re-exports for the API +
                             Celery task layer to call without reaching
                             into stores/postgres directly
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


# --------------------------------------------------------------------------- #
# Bootstrap — fan out to all 4 stores in parallel. Each is idempotent.
# Call once at FastAPI lifespan startup (or before the first scan dispatches).
# --------------------------------------------------------------------------- #
async def bootstrap_stores() -> None:
    """Initialize all 4 RR stores. Idempotent + safe to re-run.

    Runs the 4 bootstraps concurrently — they touch different services so
    there's no contention. On any failure, the others may still complete;
    the exception propagates with `asyncio.gather(return_exceptions=False)`.
    """
    results = await asyncio.gather(
        postgres_store.bootstrap_postgres(),
        neo4j_store.bootstrap_neo4j(),
        qdrant_store.bootstrap_qdrant(),
        minio_store.bootstrap_minio(),
    )
    logger.info(f"[rr-service] bootstrap_stores: 4/4 complete ({len(results)})")


# --------------------------------------------------------------------------- #
# Per-paper persistence — graph + vector together. The orchestrator's
# graph_build subagent calls this once per paper (parallel fan-out).
# --------------------------------------------------------------------------- #
async def persist_paper(
    paper: NormalizedPaper,
    *,
    embedding: list[float] | tuple[float, ...] | None,
    signal: float | None = None,
) -> None:
    """Write a paper to Neo4j (always) + Qdrant (only when embedding given).

    Skip rule: if `paper.arxiv_id is None`, the paper has no stable identity
    for cross-source dedup; both stores are bypassed and a warning logged.
    The orchestrator's report subagent still persists it via radar_findings
    (Postgres uses (scan_id, arxiv_id) as PK; a no-id paper is dropped at
    Postgres write time).
    """
    if not paper.arxiv_id:
        logger.warning(
            f"[rr-service] persist_paper skipped — no arxiv_id "
            f"(title={paper.title[:40]!r})"
        )
        return
    # Run graph + vector in parallel — they touch unrelated services.
    coros: list[Any] = [neo4j_store.upsert_paper(paper, signal=signal)]
    if embedding is not None:
        coros.append(
            qdrant_store.upsert_paper_vector(paper, embedding=embedding, signal=signal)
        )
    await asyncio.gather(*coros)


# --------------------------------------------------------------------------- #
# Scan lifecycle — re-exports the Postgres ops for the API/Celery layer.
# Centralizing them here lets callers import from one place + lets us
# evolve the schema without touching every callsite.
# --------------------------------------------------------------------------- #
async def begin_scan(
    scan_id:    UUID,
    profile_id: str,
    *,
    topic:      str | None       = None,
    verticals:  list[str] | None = None,
    top_n:      int | None       = None,
) -> None:
    """Create the radar_scans row (status=pending, with the request shape
    persisted alongside) then immediately flip to running. Idempotent —
    the running flip is a no-op if already past."""
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
    """Mark the scan done. Called by the Celery task after persist_scan_result."""
    await postgres_store.mark_scan_done(
        scan_id,
        total_candidates = total_candidates,
        total_in_digest  = total_in_digest,
    )


async def fail_scan(scan_id: UUID, error: str, *, cancelled: bool = False) -> None:
    """Mark the scan terminated with a short error string. `cancelled=True`
    uses SCAN_STATUS_CANCELLED instead of SCAN_STATUS_ERROR."""
    status = SCAN_STATUS_CANCELLED if cancelled else SCAN_STATUS_ERROR
    await postgres_store.mark_scan_error(scan_id, status=status, error=error)


async def delete_scan(scan_id: UUID) -> dict:
    """Per-row delete from the Recent-scans dropdown.

    Tier-1 scope — clears the per-scan presentation artifacts only:
      * Postgres `radar_scans` row (CASCADE → `radar_findings`)
        — the `llm_counters` JSONB column on this row goes with it
          atomically, so the per-scan telemetry archive (2026-06-17)
          needs no separate delete call
      * MinIO `rr/scans/{scan_id}/digest.json` object

    INTENTIONALLY leaves the accumulated knowledge intact:
      * Neo4j Paper / Author / Concept nodes (cross-scan graph)
      * Qdrant abstract embeddings (cross-scan retrieval index)
      * Postgres `radar_seen` markers (profile-scoped `is_new` memory)

    Idempotent — returns a dict telling the caller what was actually
    removed so the UI can confirm ("deleted scan: pg=True, minio=False"
    means the scan was orphaned in Postgres only)."""
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
    # Build-tab synthesized .py artifacts share the scan's MinIO prefix
    # but aren't covered by delete_digest_json. Wipe them so the
    # Recent-scans dropdown's delete button doesn't leak code blobs.
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
    """Revoke the Celery task driving `scan_id`, mark Postgres + emit a
    terminal phase event so any SSE subscriber unwinds cleanly.

    Returns True if a live task was found and revoked, False if no task_id
    was registered (already finished, never started, or TTL'd out).

    Order matters:
      1. Revoke first — frees the Celery worker slot ASAP.
      2. Mark Postgres cancelled — any subsequent GET /scan/{id} reads
         the terminal status, not 'running'.
      3. Emit phase=cancelled — SSE subscribers see the terminal frame
         and close their stream.
      4. Drop the task_id key — a second cancel becomes a clean 404.

    A failure in step 2/3/4 doesn't roll back step 1; the worker is
    already dead, so the user expectation (scan stopped) is met. The
    inconsistency would clear up on the next reload via the GET.
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


# --------------------------------------------------------------------------- #
# Final scan write — findings + digest + seen set, in that order so a
# partial failure leaves the relational store as the source of truth.
# --------------------------------------------------------------------------- #
async def persist_scan_result(
    scan_id: UUID,
    profile_id: str,
    *,
    findings: list[Finding],
    digest_payload: dict[str, Any],
) -> dict[str, Any]:
    """Persist the scan's ranked digest:
      1. INSERT findings into Postgres (radar_findings)
      2. PUT digest.json to MinIO
      3. UPSERT arxiv_ids into radar_seen (so the next scan can diff_vs_seen)

    Returns a summary dict suitable for SSE emission + Celery task return.
    """
    n_findings = await postgres_store.record_findings(scan_id, findings)
    digest_key = await minio_store.put_digest_json(str(scan_id), digest_payload)
    # 2026-06-17: surface scan-wide synthesis on the scan row so the
    # Digest page's themes filter strip + executive summary can render
    # without a second MinIO fetch. Best-effort — a failed UPDATE here
    # only loses the convenience field, not the canonical MinIO blob.
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


# --------------------------------------------------------------------------- #
# Read paths used by the API + the agent (re-exports for one-stop import)
# --------------------------------------------------------------------------- #
async def get_seen_ids(profile_id: str) -> frozenset[str]:
    """Re-export: all arxiv_ids the profile has seen. Used by domain.diff_vs_seen."""
    return await postgres_store.get_seen_ids(profile_id)


async def get_profile(profile_id: str) -> dict[str, Any] | None:
    """Re-export: profile blob (interests + weights)."""
    return await postgres_store.get_profile(profile_id)


async def upsert_profile(
    profile_id: str, *, interests: dict[str, Any], weights: dict[str, Any],
) -> None:
    """Re-export: create or update a profile."""
    await postgres_store.upsert_profile(
        profile_id, interests=interests, weights=weights,
    )
