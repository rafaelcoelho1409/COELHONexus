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
# Non-DD task keys that share the bandit but live OUTSIDE the one-hot
# context-vector encoding (v[7+idx]). Cells are still per-task: `cell_key()`
# uses the dd_process STRING directly, so e.g. "ycs-neo4j" gets its own
# (deployment, "ycs-neo4j") cell, separate σ²_ewma + A_a from DD. The only
# thing they don't get is the dd_process one-hot feature — fine, because
# cell-level state segregation already encodes the task identity. Adding
# them to DD_PROCESSES would collide v[16] with `sin(hour)` and require a
# CONTEXT_DIM bump that invalidates every existing CellState in Redis.
NON_DD_TASKS: tuple[str, ...] = (
    # YCS Phase 3 (Neo4j LLMGraphTransformer entity extraction). Separate
    # state from DD so DD prose variance doesn't drag down JSON-strong arms.
    "ycs-neo4j",
)
_DD_PROCESS_IDX = {p: i for i, p in enumerate(DD_PROCESSES)}

# Mirrors the enabled subset of discovery/PROVIDERS keys. Maps to context-vector
# slots [19-23].
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
