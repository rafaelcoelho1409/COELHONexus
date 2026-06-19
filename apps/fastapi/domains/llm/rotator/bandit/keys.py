from __future__ import annotations


CACHE_PREFIX = "dd:rotator:pareto:cell:"
RESERVATION_PREFIX = "dd:rotator:pareto:reserved:"
PROVIDER_SLOT_PREFIX = "dd:rotator:provider_slot:"


DD_PROCESSES: tuple[str, ...] = (
    "dd-all",
    "dd-synth",
    "dd-reduce-label",
    "dd-keylm",
    "dd-embed",
    "dd-plan",
    "dd-curator",
    "dd-grader",
    "dd-critic",
)
# Non-DD task keys share the bandit but skip the v[7+idx] one-hot — cells stay
# per-task via cell_key(). Adding them to DD_PROCESSES would collide with v[16]
# sin(hour) and require a CONTEXT_DIM bump invalidating every Redis CellState.
NON_DD_TASKS: tuple[str, ...] = (
    "ycs-neo4j",
)
_DD_PROCESS_IDX = {p: i for i, p in enumerate(DD_PROCESSES)}

# Maps to context-vector slots [19-23].
CONTEXT_PROVIDERS: tuple[str, ...] = (
    "groq",
    "nim",
    "cerebras",
    "mistral",
    "gemini",
)
_PROVIDER_IDX = {p: i for i, p in enumerate(CONTEXT_PROVIDERS)}


def cell_key(deployment: str, dd_process: str) -> str:
    return f"{CACHE_PREFIX}{deployment}:{dd_process}"


def reservation_key(deployment: str, dd_process: str) -> str:
    return f"{RESERVATION_PREFIX}{dd_process}:{deployment}"


def provider_slot_key(provider: str, slot_idx: int) -> str:
    return f"{PROVIDER_SLOT_PREFIX}{provider}:{slot_idx}"
