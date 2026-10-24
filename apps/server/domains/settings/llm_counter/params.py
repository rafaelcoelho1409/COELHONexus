"""llm_counter tunables and the per-context counter profiles."""
from __future__ import annotations
from . import entities


COUNTER_TTL_S = 24 * 60 * 60  # matches the DD / YCS pipeline-state TTL

# Node id a call is counted under when its context names none (YCS Ask calls made before a node tags itself).
UNKNOWN_NODE = "unknown"

# One entry per bounded context that bills LLM calls. DD counts per planner / synth thread, shows errors and keeps a
# MinIO snapshot of a finished run; YCS counts per extract run (node = video) and per Ask thread (node = graph node).
NAMESPACES: dict[str, entities.Namespace] = {
    "dd":  entities.Namespace(id_field = "thread_id",  errors = True,  snapshot = True),
    "ycs": entities.Namespace(id_field = "extract_id", errors = False, snapshot = False),
}
DEFAULT_NAMESPACE = entities.Namespace(id_field = "run_id", errors = False, snapshot = False)
