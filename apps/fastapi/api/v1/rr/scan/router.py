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
from domains.rr.runtime.events import subscribe_events
from domains.rr.schemas import ScanCreated, ScanRequest, ScanResult
from domains.rr.task import run_radar_scan


logger = logging.getLogger(__name__)


router = APIRouter()


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
