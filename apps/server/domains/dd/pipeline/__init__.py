"""Pipeline — the Pipeline tab. The planner and the synth run as agentic graphs in `ai.dd.graphs.pipeline.{planner,synth}`; what runs them
lives here: `ai` (composition roots and Celery tasks), `runtime` (Redis keys, cancel, dispatch, progress events, checkpoint pruning),
`storage` (MinIO keys and readers), `observability`.

Import order matters: `storage`, `observability` and `runtime` load before `ai` (the stages' nodes import `observability.service` at
module level for their `@traced` decorators)."""
from __future__ import annotations
from . import storage, observability, runtime, ai
