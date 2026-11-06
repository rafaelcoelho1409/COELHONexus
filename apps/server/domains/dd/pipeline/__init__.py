"""Pipeline — the Pipeline tab. The planner runs as an agentic graph in `ai.dd.graphs.pipeline.planner`; what runs it lives here:
`ai` (composition root and Celery tasks), `runtime` (Redis keys, cancel, dispatch, progress events, checkpoint pruning),
`storage` (MinIO keys and readers), `observability`. `synth` is still a package of its own (graph, nodes, runtime, task).

Import order matters: `storage`, `observability` and `runtime` load before `ai` (the planner's nodes import `observability.service` at
module level for their `@traced` decorator)."""
from __future__ import annotations
from . import storage, observability, runtime, ai, synth
