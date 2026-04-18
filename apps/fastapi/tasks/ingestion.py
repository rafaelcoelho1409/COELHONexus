"""
Ingestion Tasks — ES → Qdrant Vector Store

CONCEPT: Wraps the streaming ingestion pipeline (services/ingestion.py)
as a Celery task. Runs in the embedding worker with ONNX models.

Progress is reported as: {current: N, total: M, status: "embedding"}
which Flower and GET /tasks/{id} display in real-time.
"""
import asyncio
import os
import sys
if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from celery_app import app
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


@app.task(
    bind = True, 
    name = "tasks.ingestion.ingest_to_qdrant")
def ingest_to_qdrant(
    self, 
    video_ids = None, 
    chunk_size = 2000, 
    chunk_overlap = 200):
    """
    Stream ES transcripts → chunk → embed (ONNX) → Qdrant.

    This is the task that was OOMKilling and getting killed by uvicorn reload.
    Now it runs in its own Celery worker process — survives any FastAPI restart.
    """
    logger.info(f"[ingest_to_qdrant] Starting: video_ids={video_ids}, chunk_size={chunk_size}")
    self.update_state(
        state = "PROGRESS", 
        meta = {"status": "initializing"})

    async def _run():
        from elasticsearch import AsyncElasticsearch
        from qdrant_client import AsyncQdrantClient
        from services.ingestion import ingest_to_qdrant as run_ingestion
        es = AsyncElasticsearch(
            hosts = [os.environ["ELASTICSEARCH_HOST"]],
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
            url = qdrant_url,
            port = qdrant_port,
            api_key = qdrant_api_key if qdrant_api_key else None,
        )
        try:
            result = await run_ingestion(
                es = es,
                qdrant = qdrant,
                video_ids = video_ids,
                chunk_size = chunk_size,
                chunk_overlap = chunk_overlap,
            )
            return result
        finally:
            await qdrant.close()
            await es.close()
    result = asyncio.run(_run())
    logger.info(f"[ingest_to_qdrant] Done: {result}")
    return result


@app.task(
    bind = True, 
    name = "tasks.ingestion.invalidate_cache")
def invalidate_cache(self):
    """Clear all RAG search cache after new data ingestion."""
    import asyncio
    import redis.asyncio as redis_aio
    from services.cache import invalidate_cache as _invalidate

    async def _run():
        REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
        REDIS_PORT = os.environ.get("REDIS_PORT", "6379")
        REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD", "")
        url = f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}" if REDIS_PASSWORD else f"redis://{REDIS_HOST}:{REDIS_PORT}"
        r = redis_aio.from_url(url)
        try:
            await _invalidate(r)
        finally:
            await r.close()
    asyncio.run(_run())
    return {"status": "cache_cleared"}
