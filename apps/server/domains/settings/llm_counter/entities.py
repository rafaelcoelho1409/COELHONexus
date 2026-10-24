"""llm_counter entities — one call's parsed usage, a context's counter profile, and who a call is billed to."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen = True, slots = True)
class Usage:
    """Model id (None when the response names none) and the token counts of one call."""
    model:            str | None
    tokens_in:        int
    tokens_out:       int
    reasoning_tokens: int


@dataclass(frozen = True, slots = True)
class Namespace:
    """How one bounded context's counters are shown and kept: the id field of its usage payload,
    whether failed calls are counted (and shown), and whether a finished run is snapshotted to MinIO."""
    id_field: str
    errors:   bool
    snapshot: bool


@dataclass(frozen = True, slots = True)
class Attribution:
    """Who the LLM calls made in the current async task are billed to: counters live under `(ns, run_id)`,
    split by `node_id`; `stage` (DD: planner / synth) is stored next to them."""
    ns:      str
    run_id:  str
    node_id: str | None = None
    stage:   str | None = None
