"""ycs/ingestion/qdrant_task — ES transcripts → chunk → embed → Qdrant upsert + cache invalidate."""
from __future__ import annotations
import domains, infra
import infra.celery
from . import service

import asyncio
from typing import Any

logger = infra.celery.service.get_task_logger(__name__)


@infra.celery.service.app.task(
    bind = True,
    name = "domains.ycs.qdrant_task.task.ingest_to_qdrant",
)
def ingest_to_qdrant(
    self,
    video_ids:       list[str] | None = None,
    chunk_size:      int              = 2000,
    chunk_overlap:   int              = 200,
    collection_name: str | None       = None,
) -> dict[str, Any]:
    """Stream ES transcripts → chunk → embed → Qdrant upsert.

    `collection_name` (2026-09-15) — override for
    `domains.ycs.ingestion.embedding_migration`'s re-embed job, which targets a
    fresh versioned staging collection instead of the live one; `None`
    (every other caller) keeps the default from `ingestion.service
    .ingest_to_qdrant`'s own signature (the live `QDRANT_COLLECTION`
    alias)."""
    logger.info(
        f"[ingest_to_qdrant] Starting: video_ids={video_ids}, "
        f"chunk_size={chunk_size}",
    )
    self.update_state(state = "PROGRESS", meta = {"phase": "init"})

    def _progress(payload: dict[str, Any]) -> None:
        self.update_state(state = "PROGRESS", meta = payload)

    async def _run() -> dict[str, Any]:
        with infra.langfuse.service.session(
            "ycs-ingest-qdrant",
            session_id = self.request.id or "(no-request-id)",
        ):
            with infra.opentelemetry.service.get_tracer().start_as_current_span(
                "ycs.ingest.qdrant.run",
                attributes = {
                    infra.langfuse.keys.KEEP: True,
                    infra.langfuse.keys.KIND: "workflow_root",
                    infra.langfuse.keys.TRACE_NAME: "ycs.ingest.qdrant.run",
                    infra.langfuse.keys.OBS_META_WORKFLOW: "ycs_ingest",
                    "ycs.ingest.kind": "qdrant",
                    "ycs.chunk_size": int(chunk_size),
                    "ycs.chunk_overlap": int(chunk_overlap),
                    "ycs.video_count": len(video_ids or []),
                },
            ):
                infra.langfuse.service.set_current_span_langfuse_io(input_data = {
                    "kind": "qdrant",
                    "video_ids_preview": list(video_ids or [])[:10],
                    "video_count": len(video_ids or []),
                    "chunk_size": chunk_size,
                    "chunk_overlap": chunk_overlap,
                    "task_id": self.request.id or "",
                })
                infra.langfuse.service.set_current_span_langfuse_trace_metadata({
                    "pipeline": "ycs_ingest",
                    "kind": "qdrant",
                    "task_id": self.request.id or "",
                    "video_count": len(video_ids or []),
                })
                infra.langfuse.service.set_current_span_langfuse_observation_metadata({
                    "kind": "qdrant",
                    "video_count": len(video_ids or []),
                })
                es = infra.elasticsearch.service.new_es()
                qdrant = infra.qdrant.service.new_qdrant()
                try:
                    try:
                        _kwargs: dict[str, Any] = {
                            "es":            es,
                            "qdrant":        qdrant,
                            "video_ids":     video_ids,
                            "chunk_size":    chunk_size,
                            "chunk_overlap": chunk_overlap,
                            "progress_cb":   _progress,
                        }
                        if collection_name:
                            _kwargs["collection_name"] = collection_name
                        result = await domains.ycs.ingestion.service.ingest_to_qdrant(**_kwargs)
                    except Exception as e:
                        infra.langfuse.service.set_current_span_langfuse_io(output_data = {
                            "status": "failed",
                            "kind": "qdrant",
                            "task_id": self.request.id or "",
                            "error": f"{type(e).__name__}: {e}",
                        })
                        raise
                    infra.langfuse.service.set_current_span_langfuse_io(output_data = {
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


@infra.celery.service.app.task(
    bind = True,
    name = "domains.ycs.qdrant_task.task.stream_video_to_qdrant",
)
def stream_video_to_qdrant(
    self,
    video_id:      str,
    extract_id:    str,
    chunk_size:    int = 2000,
    chunk_overlap: int = 200,
) -> dict[str, Any]:
    """Celery adapter for the reusable streaming Qdrant worker."""
    result = asyncio.run(service.stream_video_to_qdrant_async(
        video_id,
        extract_id,
        chunk_size,
        chunk_overlap,
    ))
    logger.info(f"[stream_video_to_qdrant] {extract_id}: {video_id} done: {result}")
    return result


@infra.celery.service.app.task(
    bind = True,
    name = "domains.ycs.qdrant_task.task.invalidate_cache",
)
def invalidate_cache(self) -> dict[str, Any]:
    """Clear all RAG search cache after new data ingestion.
"""
    async def _run() -> None:
        async with infra.redis.service.client() as r:
            await domains.ycs.ask.cache.service.invalidate_cache(r)
    asyncio.run(_run())
    return {"status": "cache_cleared"}
