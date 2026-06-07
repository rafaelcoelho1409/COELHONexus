"""ycs/pipeline_task — Redis key builder for pipeline dispatch state.

Per `docs/CODE-CONVENTIONS.md` §2, storage-path helpers belong in
`keys.py`. Single source of truth for the
`PIPELINE_STATE_PREFIX + <extract_task_id>` shape so producers
(`service.persist_pipeline_state`) and consumers
(`service.load_pipeline_state`) agree without trafficking string
literals."""
from __future__ import annotations

from .params import PIPELINE_STATE_PREFIX


def pipeline_state_key(extract_id: str) -> str:
    """Redis key storing the dispatch params (`video_ids`, flags) for
    one Videos-tab chain. Keyed by the Phase A (extract) task id so the
    FastHTML poller's URL (`?extract=<id>`) is the lookup token."""
    return f"{PIPELINE_STATE_PREFIX}{extract_id}"
