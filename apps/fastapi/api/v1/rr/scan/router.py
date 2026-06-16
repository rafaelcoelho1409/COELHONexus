"""Scan endpoints — POST trigger, GET status, GET SSE.

Pattern mirrors `api/v1/dd/planner/router.py`:
  - Long work runs in Celery (queue=`rr-{env}`), FastAPI is HTTP/SSE.
  - SSE relays Redis pub/sub events.
  - Checkpoints land in Postgres; this layer reads via service.* paths.

Per docs/CODE-CONVENTIONS.md §service: routers are THIN — they validate,
dispatch, and shape responses. Business logic lives in domains/rr/.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import AsyncIterator
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, HTTPException, Request
from starlette.responses import StreamingResponse

from domains.dd.planner.keys import postgres_url
from domains.rr.keys import (
    PG_TABLE_FINDINGS,
    PG_TABLE_SCANS,
)
from domains.rr.runtime.events import store_task_id, subscribe_events
from domains.rr.runtime.fs_mirror import mirror_index, mirror_read
from domains.rr.runtime.llm_counter import read_counters as read_llm_counters
from domains.rr.schemas import ScanCreated, ScanRequest, ScanResult
from domains.rr.service import cancel_scan, delete_scan
from domains.rr.task import run_radar_scan


logger = logging.getLogger(__name__)


router = APIRouter()


# --------------------------------------------------------------------------- #
# GET /scans/recent — history surface for the row-2 picker
# --------------------------------------------------------------------------- #
@router.get("/scans/recent")
async def list_recent_scans(profile_id: str = "default", limit: int = 20) -> dict:
    """Most-recent scans for a profile. Drives the Recent-scans dropdown
    on the Digest page. The query pulls the per-scan request shape
    (topic / verticals / top_n) + outcome (status / counts / duration) +
    a 1-3 theme preview pulled from the rank-1 finding's digest_json.

    Themes preview implementation: we LEFT JOIN the rank-1 finding row
    and read `digest_json->'themes'`. Single-row join per scan keeps the
    query bounded; absent for in-flight or failed scans."""
    limit = max(1, min(int(limit), 100))
    async with await psycopg.AsyncConnection.connect(postgres_url()) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"""
                SELECT s.id, s.status, s.started_at, s.finished_at,
                       s.total_in_digest, s.topic, s.verticals, s.top_n,
                       f.digest_json -> 'themes' AS themes_preview
                FROM {PG_TABLE_SCANS} s
                LEFT JOIN {PG_TABLE_FINDINGS} f
                  ON f.scan_id = s.id AND f.rank = 1
                WHERE s.profile_id = %s
                ORDER BY s.started_at DESC
                LIMIT %s
                """,
                (profile_id, limit),
            )
            rows = await cur.fetchall()
    items = []
    for row in rows:
        themes_raw = row[8]
        # digest_json->'themes' is a JSONB array or null. psycopg returns it
        # as a Python list already (with the JSONB adapter); fall back to []
        # if it's something else.
        themes = themes_raw if isinstance(themes_raw, list) else []
        items.append({
            "scan_id":         str(row[0]),
            "status":          row[1],
            "started_at":      row[2].isoformat() if row[2] else None,
            "finished_at":     row[3].isoformat() if row[3] else None,
            "total_in_digest": int(row[4] or 0),
            "topic":           row[5] or "",
            "verticals":       list(row[6] or []),
            "top_n":           int(row[7]) if row[7] is not None else None,
            "themes":          [str(t) for t in themes[:3]],
        })
    return {"profile_id": profile_id, "items": items}


# --------------------------------------------------------------------------- #
# POST /scan — trigger a new radar scan
# --------------------------------------------------------------------------- #
@router.post("/scan", response_model=ScanCreated, status_code=202)
async def create_scan(body: ScanRequest) -> ScanCreated:
    """Enqueue a Celery `run_radar_scan` task; return scan_id + task_id
    immediately. Clients poll GET /scan/{id} or subscribe to SSE events
    for progress."""
    scan_id  = uuid4()
    now      = datetime.now(timezone.utc)

    # Dispatch to Celery (queue=rr-{env}). The task's first action is to
    # call service.begin_scan which writes the row + flips to running.
    task = run_radar_scan.delay(
        str(scan_id),
        body.profile_id,
        body.topic,
        body.verticals,
        body.top_n,
    )

    # Record the Celery task UUID so POST /scan/{id}/cancel can resolve
    # scan_id → task_id and revoke. Best-effort — store_task_id swallows
    # Redis errors; without the mapping, cancel returns "not found" but
    # the scan still runs.
    await store_task_id(str(scan_id), task.id)

    logger.info(
        f"[rr-api] POST /scan accepted scan_id={scan_id} "
        f"task_id={task.id} profile={body.profile_id!r}"
    )
    return ScanCreated(
        scan_id    = scan_id,
        task_id    = task.id,
        status     = "pending",
        started_at = now,
    )


# --------------------------------------------------------------------------- #
# DELETE /scan/{id} — per-row delete from the Recent-scans dropdown
# --------------------------------------------------------------------------- #
@router.delete("/scan/{scan_id}", status_code=200)
async def delete_scan_endpoint(scan_id: UUID) -> dict:
    """Drop one scan's presentation artifacts: Postgres row + findings +
    MinIO digest object. Accumulated knowledge (Neo4j graph + Qdrant
    embeddings + radar_seen markers) is intentionally preserved. Returns
    a per-layer success summary so the UI can confirm what was removed."""
    result = await delete_scan(scan_id)
    return result


# --------------------------------------------------------------------------- #
# POST /scan/{id}/cancel — revoke a running scan
# --------------------------------------------------------------------------- #
@router.post("/scan/{scan_id}/cancel", status_code=202)
async def cancel_scan_endpoint(scan_id: UUID) -> dict:
    """Stop a running scan: revoke the Celery task, mark Postgres
    `cancelled`, emit a terminal SSE event so the UI unwinds.

    Returns 202 with `revoked: true` when the task_id was found and the
    revoke was issued (Celery delivers the SIGTERM asynchronously, so
    202 not 200). Returns 404 if no task is registered for the scan_id —
    either it already finished, never existed, or the task_id TTL'd out.
    """
    ok = await cancel_scan(scan_id)
    if not ok:
        raise HTTPException(
            status_code = 404,
            detail = "No running task found for this scan_id (already finished, never started, or expired).",
        )
    return {"scan_id": str(scan_id), "revoked": True}


# --------------------------------------------------------------------------- #
# GET /scan/{id} — status + findings
# --------------------------------------------------------------------------- #
@router.get("/scan/{scan_id}", response_model=ScanResult)
async def get_scan(scan_id: UUID) -> ScanResult:
    """Snapshot of a scan's lifecycle + (when done) its full digest items.

    Returns 404 if the scan_id isn't found. Otherwise always succeeds;
    the response shape stays consistent across statuses (findings is
    empty until status='done')."""
    async with await psycopg.AsyncConnection.connect(postgres_url()) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT id, profile_id, status, started_at, finished_at, "
                f"       total_candidates, total_in_digest, error "
                f"FROM {PG_TABLE_SCANS} WHERE id = %s",
                (str(scan_id),),
            )
            row = await cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="scan not found")
            (
                _id, profile_id, status, started_at, finished_at,
                total_candidates, total_in_digest, error,
            ) = row
            findings: list[dict] = []
            if status == "done":
                await cur.execute(
                    f"SELECT digest_json FROM {PG_TABLE_FINDINGS} "
                    f"WHERE scan_id = %s ORDER BY rank ASC",
                    (str(scan_id),),
                )
                findings = [r[0] for r in await cur.fetchall()]
    return ScanResult(
        scan_id          = scan_id,
        profile_id       = profile_id,
        status           = status,
        started_at       = started_at,
        finished_at      = finished_at,
        total_candidates = int(total_candidates or 0),
        total_in_digest  = int(total_in_digest or 0),
        error            = error,
        findings         = findings,
        digest_minio_key = f"rr/scans/{scan_id}/digest.json" if status == "done" else None,
    )


# --------------------------------------------------------------------------- #
# GET /scan/{id}/fs — list mirrored fs paths
# GET /scan/{id}/fs/{path:path} — read one mirrored fs entry
# --------------------------------------------------------------------------- #
@router.get("/scan/{scan_id}/fs")
async def list_fs(scan_id: UUID) -> dict:
    """All fs paths mirrored to Redis for this scan. Empty list when the
    scan never ran OR its 6h TTL expired."""
    paths = await mirror_index(str(scan_id))
    return {"scan_id": str(scan_id), "paths": paths}


@router.get("/scan/{scan_id}/fs/{path:path}")
async def read_fs(scan_id: UUID, path: str) -> dict:
    """Read one mirrored fs entry. 404 on miss."""
    value = await mirror_read(str(scan_id), path)
    if value is None:
        raise HTTPException(
            status_code = 404,
            detail = f"fs entry {path!r} not found for scan_id {scan_id}",
        )
    return {"scan_id": str(scan_id), "path": path, "value": value}


# --------------------------------------------------------------------------- #
# GET /scan/{id}/llm-counters — per-phase LLM activity (Path A 2026-06-16)
# --------------------------------------------------------------------------- #
@router.get("/scan/{scan_id}/llm-counters")
async def scan_llm_counters(scan_id: UUID) -> dict:
    """Per-scan LLM activity counters: total + by_phase + per-model.

    Shape:
        {
          "scan_id":  "...",
          "total":    {"calls": N, "tokens_in": X, "tokens_out": Y},
          "by_phase": {
            "discovery": {"calls": ..., "tokens_in": ..., "tokens_out": ...,
                          "by_model": {"<model>": {...}, ...}},
            ...
          }
        }

    Empty totals (all zeros, empty by_phase) if the scan never ran OR
    its counters TTL expired (6h). Drives the Pipeline-page drawer's
    per-node KPI cards.
    """
    return await read_llm_counters(str(scan_id))


# --------------------------------------------------------------------------- #
# GET /scan/{id}/events — SSE phase stream
# --------------------------------------------------------------------------- #
@router.get("/scan/{scan_id}/events")
async def scan_events(scan_id: UUID, request: Request) -> StreamingResponse:
    """Server-Sent Events relay. Yields phase events as the Celery task
    publishes them to Redis pub/sub. Includes catch-up replay so a late
    subscriber sees phases that already passed."""
    return StreamingResponse(
        _sse_iter(str(scan_id), request),
        media_type = "text/event-stream",
        headers    = {
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx/ingress buffering
            "Connection": "keep-alive",
        },
    )


async def _sse_iter(scan_id: str, request: Request) -> AsyncIterator[str]:
    """Format Redis events as SSE frames. Terminates on phase=done|error
    or client disconnect."""
    async for event in subscribe_events(scan_id, replay=True):
        if await request.is_disconnected():
            return
        line = f"data: {json.dumps(event, default=str)}\n\n"
        yield line
        if event.get("phase") in ("done", "error", "cancelled"):
            return
