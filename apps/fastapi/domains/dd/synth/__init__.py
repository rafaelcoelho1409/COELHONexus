"""Synth pipeline — chapter markdown generation from planner outputs. `task.py` is
deliberately NOT re-exported here — it imports `infra.celery`, which reads strict
env vars (REDIS_HOST/ENVIRONMENT/...) at module import time, and forcing that onto
every bare `import domains` would break local/standalone use. Callers needing it
use a direct `from domains.dd.synth.task import X` (same class of exception as
`dd/planner/task.py` and the LLM rotator's reverse-edge import).

`params.py`'s `STUDY_SEM` env read is lazy (`study_sem()`, not a module-level
constant) specifically so this file has no import-time env dependency — see
docs/CODE-CONVENTIONS.md §8 Exception 1.

Import order matters below: `runtime` (specifically `runtime.observability`)
must load before `nodes` (every node's `node.py` applies
`@traced(...)` — imported via a plain `from
domains.dd.synth.runtime.observability.service import traced`, not the dotted
chase, per §8 Exception 2), and `nodes` must load before `graph` (its
`NODE_REGISTRY` dict is built at module level from
`nodes.<node>.node.<fn>`).
"""
from __future__ import annotations
from . import domain, keys, params, state, runtime, nodes, graph, service
