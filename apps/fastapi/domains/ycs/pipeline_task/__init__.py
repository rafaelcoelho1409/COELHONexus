"""ycs/pipeline_task — Celery chain: extract → Qdrant → Neo4j → invalidate cache.

Direct port of deprecated `tasks/youtube/pipeline.py` (channel pipeline)
+ fresh `dispatch_videos_pipeline` (Wave 5 polish — Videos tab UI shows
3 live progress bars, so the API needs to surface the 3 chain task_ids
back to the client)."""
from .params import NEO4J_BATCH_SIZE
from .service import (
    dispatch_videos_pipeline,
    load_pipeline_state,
    persist_pipeline_state,
    revoke_pipeline_phases,
)
from .task import full_channel_pipeline


__all__ = [
    "NEO4J_BATCH_SIZE",
    "dispatch_videos_pipeline",
    "full_channel_pipeline",
    "load_pipeline_state",
    "persist_pipeline_state",
    "revoke_pipeline_phases",
]
