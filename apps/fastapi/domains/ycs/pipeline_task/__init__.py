"""ycs/pipeline_task — dispatches `extract_videos`, which self-fans-out
Neo4j + Qdrant streaming work per video as transcripts land in ES; see
`service.py`'s docstring for the full model (dispatch + per-video
streaming-coordination live together there — see `docs/CODE-CONVENTIONS.md`
§8 strict-merge: both are I/O orchestration, one role, one file).

`task.py` is excluded from this eager chain (§8 Exception 1 —
`import infra.celery.service` (use `infra.celery.service.app`) at module level); reach it via a direct
`from domains.ycs.pipeline_task.task import full_channel_pipeline`."""
from __future__ import annotations

from . import keys, params, service
