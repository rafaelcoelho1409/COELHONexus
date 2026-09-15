"""ycs/pipeline_task — dispatches `extract_videos`, which self-fans-out
Neo4j + Qdrant streaming work per video as transcripts land in ES; see
`service.py` and `streaming.py` docstrings for the full model."""
from .service import (
    dispatch_videos_pipeline,
    is_pipeline_cancelled,
    load_pipeline_state,
    persist_pipeline_state,
    request_cancel,
    revoke_pipeline_phases,
    wipe_videos_data,
)
from .streaming import get_dispatched_task_ids, get_phase_progress
from .task import full_channel_pipeline


__all__ = [
    "dispatch_videos_pipeline",
    "full_channel_pipeline",
    "get_dispatched_task_ids",
    "get_phase_progress",
    "is_pipeline_cancelled",
    "load_pipeline_state",
    "persist_pipeline_state",
    "request_cancel",
    "revoke_pipeline_phases",
    "wipe_videos_data",
]
