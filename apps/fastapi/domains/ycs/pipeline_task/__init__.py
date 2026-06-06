"""ycs/pipeline_task — Celery chain: extract → Qdrant → Neo4j → invalidate cache.

Direct port of deprecated `tasks/youtube/pipeline.py`."""
from .task import full_channel_pipeline


__all__ = ["full_channel_pipeline"]
