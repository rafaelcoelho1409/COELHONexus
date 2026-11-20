"""llm_counter tunables and the per-context counter profiles."""
from __future__ import annotations
from . import entities


COUNTER_TTL_S = 24 * 60 * 60

# Node id a call is counted under when its context names none (YCS Ask calls made before a node tags itself).
UNKNOWN_NODE = "unknown"

# One entry per bounded context that bills LLM calls. Book Distiller keeps a finished-run snapshot; YCS counts
# extraction runs (node = video) and Ask threads (node = graph node).
NAMESPACES: dict[str, entities.Namespace] = {
    "ycs": entities.Namespace(id_field = "extract_id", errors = False, snapshot = False),
    "bd":  entities.Namespace(id_field = "run_id",     errors = True,  snapshot = True),
}
DEFAULT_NAMESPACE = entities.Namespace(id_field = "run_id", errors = False, snapshot = False)
