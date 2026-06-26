"""ycs/qdrant_task — ES transcripts → chunk → embed → Qdrant upsert + cache invalidate."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import redis.asyncio as redis_aio
from celery.utils.log import get_task_logger
from elasticsearch import AsyncElasticsearch
from qdrant_client import AsyncQdrantClient

from domains.ycs.cache import invalidate_cache as _invalidate_cache
from domains.ycs.ingestion import ingest_to_qdrant as run_ingestion
from infra.celery import app


logger = get_task_logger(__name__)


@app.task(
    bind = True,
    name = "domains.ycs.qdrant_task.task.ingest_to_qdrant",
)
def ingest_to_qdrant(
    self,
    video_ids:     list[str] | None = None,
    chunk_size:    int              = 2000,
    chunk_overlap: int              = 200,
) -> dict[str, Any]:
    """Stream ES transcripts → chunk → embed → Qdrant upsert."""
    logger.info(
        f"[ingest_to_qdrant] Starting: video_ids={video_ids}, "
        f"chunk_size={chunk_size}",
    )
    self.update_state(state = "PROGRESS", meta = {"phase": "init"})

    def _progress(payload: dict[str, Any]) -> None:
        self.update_state(state = "PROGRESS", meta = payload)

    async def _run() -> dict[str, Any]:
        from infra.langfuse import (
            set_current_span_langfuse_io,
            set_current_span_langfuse_observation_metadata,
            set_current_span_langfuse_trace_metadata,
        )
        from infra.langfuse.sessions import session as _lf_session
        from infra.otel import get_tracer
        with _lf_session(
            "ycs-ingest-qdrant",
            session_id = self.request.id or "(no-request-id)",
        ):
            with get_tracer().start_as_current_span(
                "ycs.ingest.qdrant.run",
                attributes = {
                    "coelho.langfuse.keep": True,
                    "coelho.langfuse.kind": "workflow_root",
                    "langfuse.trace.name": "ycs.ingest.qdrant.run",
                    "langfuse.observation.metadata.workflow": "ycs_ingest",
                    "ycs.ingest.kind": "qdrant",
                    "ycs.chunk_size": int(chunk_size),
                    "ycs.chunk_overlap": int(chunk_overlap),
                    "ycs.video_count": len(video_ids or []),
                },
            ):
                set_current_span_langfuse_io(input_data = {
                    "kind": "qdrant",
                    "video_ids_preview": list(video_ids or [])[:10],
                    "video_count": len(video_ids or []),
                    "chunk_size": chunk_size,
                    "chunk_overlap": chunk_overlap,
                    "task_id": self.request.id or "",
                })
                set_current_span_langfuse_trace_metadata({
                    "pipeline": "ycs_ingest",
                    "kind": "qdrant",
                    "task_id": self.request.id or "",
                    "video_count": len(video_ids or []),
                })
                set_current_span_langfuse_observation_metadata({
                    "kind": "qdrant",
                    "video_count": len(video_ids or []),
                })
                es = AsyncElasticsearch(
                    hosts      = [os.environ["ELASTICSEARCH_HOST"]],
                    basic_auth = (
                        os.environ["ELASTICSEARCH_USERNAME"],
                        os.environ.get("ELASTICSEARCH_PASSWORD", ""),
                    ),
                    verify_certs = False,
                )
                qdrant_url = os.environ.get("QDRANT_URL", "http://localhost:6333")
                qdrant_port = int(os.environ.get("QDRANT_PORT", "6333"))
                qdrant_api_key = os.environ.get("QDRANT_API_KEY")
                qdrant = AsyncQdrantClient(
                    url     = qdrant_url,
                    port    = qdrant_port,
                    api_key = qdrant_api_key if qdrant_api_key else None,
                )
                try:
                    try:
                        result = await run_ingestion(
                            es            = es,
                            qdrant        = qdrant,
                            video_ids     = video_ids,
                            chunk_size    = chunk_size,
                            chunk_overlap = chunk_overlap,
                            progress_cb   = _progress,
                        )
                    except Exception as e:
                        set_current_span_langfuse_io(output_data = {
                            "status": "failed",
                            "kind": "qdrant",
                            "task_id": self.request.id or "",
                            "error": f"{type(e).__name__}: {e}",
                        })
                        raise
                    set_current_span_langfuse_io(output_data = {
                        "status": "done",
                        "kind": "qdrant",
                        "task_id": self.request.id or "",
                        "result": result,
                    })
                    return result
                finally:
                    await qdrant.close()
                    await es.close()

    result = asyncio.run(_run())
    logger.info(f"[ingest_to_qdrant] Done: {result}")
    return result


@app.task(
    bind = True,
    name = "domains.ycs.qdrant_task.task.invalidate_cache",
)
def invalidate_cache(self) -> dict[str, Any]:
    """Clear all RAG search cache after new data ingestion.
"""
    async def _run() -> None:
        redis_host = os.environ.get("REDIS_HOST", "localhost")
        redis_port = os.environ.get("REDIS_PORT", "6379")
        redis_password = os.environ.get("REDIS_PASSWORD", "")
        url = (
            f"redis://:{redis_password}@{redis_host}:{redis_port}"
            if redis_password
            else f"redis://{redis_host}:{redis_port}"
        )
        r = redis_aio.from_url(url)
        try:
            await _invalidate_cache(r)
        finally:
            await r.close()
    asyncio.run(_run())
    return {"status": "cache_cleared"}
