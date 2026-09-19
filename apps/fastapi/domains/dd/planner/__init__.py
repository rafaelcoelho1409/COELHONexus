"""Planner — LangGraph LITA-pattern pipeline. `task.py` (Celery entry point) is
deliberately NOT re-exported here — it imports `infra.celery`, which reads
strict env vars (REDIS_HOST/ENVIRONMENT/...) at module import time, and
forcing that onto every bare `import domains` would break local/standalone
use. Callers needing it use a direct `from domains.dd.planner.task import X`
(same class of exception as the LLM rotator's reverse-edge import).

Import order matters below: `runtime` (specifically `runtime.observability`)
must load before `nodes` (every node's `node.py` applies
`@domains.dd.planner.runtime.observability.traced(...)` as a module-level
decorator), and `nodes` must load before `graph` (its `NODE_REGISTRY` dict
is built at module level from `domains.dd.planner.nodes.*.node.*`).
"""
from __future__ import annotations

from . import domain, keys, params, service, state, runtime, nodes, graph
