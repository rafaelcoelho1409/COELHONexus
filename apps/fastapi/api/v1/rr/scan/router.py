"""Scan endpoints — Celery dispatch, Postgres status reads, Redis SSE relay."""
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


@router.get("/scans/recent")
async def list_recent_scans(profile_id: str = "default", limit: int = 20) -> dict:
    """Most-recent scans for a profile. LEFT JOIN rank-1 finding for a 1-3 theme preview."""
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
        # psycopg's JSONB adapter returns list directly; non-list rows are legacy str.
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


@router.post("/scan", response_model=ScanCreated, status_code=202)
async def create_scan(body: ScanRequest) -> ScanCreated:
    """Enqueue a Celery scan task and return immediately. Clients poll GET /scan/{id} or subscribe to SSE."""
    scan_id  = uuid4()
    now      = datetime.now(timezone.utc)

    task = run_radar_scan.delay(
        str(scan_id),
        body.profile_id,
        body.topic,
        body.verticals,
        body.top_n,
    )

    # Best-effort: store_task_id swallows Redis errors; cancel returns "not found" but scan still runs.
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


@router.delete("/scan/{scan_id}", status_code=200)
async def delete_scan_endpoint(scan_id: UUID) -> dict:
    """Drop scan artifacts (Postgres + findings + MinIO). Neo4j / Qdrant / radar_seen are intentionally preserved."""
    result = await delete_scan(scan_id)
    return result


@router.post("/scan/{scan_id}/cancel", status_code=202)
async def cancel_scan_endpoint(scan_id: UUID) -> dict:
    """Revoke the Celery task and mark Postgres `cancelled`. 202 not 200 because SIGTERM is async; 404 if task_id unknown or TTL'd."""
    ok = await cancel_scan(scan_id)
    if not ok:
        raise HTTPException(
            status_code = 404,
            detail = "No running task found for this scan_id (already finished, never started, or expired).",
        )
    return {"scan_id": str(scan_id), "revoked": True}


@router.get("/scan/{scan_id}", response_model=ScanResult)
async def get_scan(scan_id: UUID) -> ScanResult:
    """Scan lifecycle snapshot + digest findings when done. Findings is empty until status='done'."""
    async with await psycopg.AsyncConnection.connect(postgres_url()) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT id, profile_id, status, started_at, finished_at, "
                f"       total_candidates, total_in_digest, error, topic, "
                f"       synthesis_themes, synthesis_summary "
                f"FROM {PG_TABLE_SCANS} WHERE id = %s",
                (str(scan_id),),
            )
            row = await cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="scan not found")
            (
                _id, profile_id, status, started_at, finished_at,
                total_candidates, total_in_digest, error, topic,
                synthesis_themes_raw, synthesis_summary,
            ) = row
            findings: list[dict] = []
            if status == "done":
                await cur.execute(
                    f"SELECT digest_json FROM {PG_TABLE_FINDINGS} "
                    f"WHERE scan_id = %s ORDER BY rank ASC",
                    (str(scan_id),),
                )
                findings = [r[0] for r in await cur.fetchall()]
    # psycopg3 returns JSONB as list directly; guard against legacy str rows.
    synthesis_themes: list[str] = []
    if isinstance(synthesis_themes_raw, list):
        synthesis_themes = [str(t) for t in synthesis_themes_raw if t]
    elif isinstance(synthesis_themes_raw, str):
        try:
            import json as _json
            parsed = _json.loads(synthesis_themes_raw)
            if isinstance(parsed, list):
                synthesis_themes = [str(t) for t in parsed if t]
        except Exception:
            pass
    return ScanResult(
        scan_id           = scan_id,
        profile_id        = profile_id,
        status            = status,
        started_at        = started_at,
        finished_at       = finished_at,
        total_candidates  = int(total_candidates or 0),
        total_in_digest   = int(total_in_digest or 0),
        error             = error,
        topic             = topic,
        synthesis_themes  = synthesis_themes,
        synthesis_summary = synthesis_summary,
        findings          = findings,
        digest_minio_key  = f"rr/scans/{scan_id}/digest.json" if status == "done" else None,
    )


@router.get("/scan/{scan_id}/fs")
async def list_fs(scan_id: UUID) -> dict:
    """All fs paths mirrored to Redis for this scan. Empty after 6h TTL."""
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


@router.get("/scan/{scan_id}/llm-counters")
async def scan_llm_counters(scan_id: UUID) -> dict:
    """Per-scan LLM counters: total + by_phase + per-model. Empty on miss or after 6h TTL."""
    return await read_llm_counters(str(scan_id))


@router.get("/scan/{scan_id}/finding/{arxiv_id}/code")
async def scan_finding_code(
    scan_id:    UUID,
    arxiv_id:   str,
    check_only: bool = False,
) -> dict:
    """Synthesize a Python file from a finding's extraction fields. Cache-first (MinIO).
    Lazy: most Build tabs are never opened; pre-computing would 2-3× rotator budget."""
    from domains.rr.agent.tools.code_synth import (
        CODE_SYNTH_PROMPT_VERSION, synth_code,
    )
    from domains.rr.stores import minio as minio_store

    cached = await minio_store.get_code_py(
        str(scan_id), arxiv_id, CODE_SYNTH_PROMPT_VERSION,
    )
    if cached is not None:
        return {
            "scan_id":        str(scan_id),
            "arxiv_id":       arxiv_id,
            "code":           cached,
            "prompt_version": CODE_SYNTH_PROMPT_VERSION,
            "cached":         True,
            "model_id":       None,
        }

    if check_only:
        raise HTTPException(
            status_code = 404,
            detail = "no cached code for this finding yet — click Generate to synthesize",
        )

    async with await psycopg.AsyncConnection.connect(postgres_url()) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT digest_json FROM {PG_TABLE_FINDINGS} "
                f"WHERE scan_id = %s AND arxiv_id = %s",
                (str(scan_id), arxiv_id),
            )
            row = await cur.fetchone()
            if row is None:
                raise HTTPException(
                    status_code = 404,
                    detail = (
                        f"finding {arxiv_id!r} not found for scan "
                        f"{scan_id} (scan still running or paper not in digest)"
                    ),
                )
            finding = row[0] or {}

    try:
        result = await synth_code(finding)
    except Exception as e:
        logger.exception(
            f"[rr-api] code_synth failed scan_id={scan_id} arxiv_id={arxiv_id}"
        )
        raise HTTPException(
            status_code = 502,
            detail = f"code_synth failed: {type(e).__name__}: {e}",
        )

    try:
        await minio_store.put_code_py(
            str(scan_id), arxiv_id,
            CODE_SYNTH_PROMPT_VERSION, result["code"],
        )
    except Exception as e:
        logger.warning(
            f"[rr-api] code_synth cache write failed for {arxiv_id}: {e}"
        )

    return {
        "scan_id":        str(scan_id),
        "arxiv_id":       arxiv_id,
        "code":           result["code"],
        "prompt_version": CODE_SYNTH_PROMPT_VERSION,
        "cached":         False,
        "model_id":       result.get("model_id"),
    }


@router.get("/scan/{scan_id}/events")
async def scan_events(scan_id: UUID, request: Request) -> StreamingResponse:
    """SSE relay over Redis pub/sub. Includes catch-up replay for late subscribers."""
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
    """Format Redis events as SSE frames. Terminates on phase=done|error|cancelled or disconnect."""
    async for event in subscribe_events(scan_id, replay=True):
        if await request.is_disconnected():
            return
        line = f"data: {json.dumps(event, default=str)}\n\n"
        yield line
        if event.get("phase") in ("done", "error", "cancelled"):
            return
