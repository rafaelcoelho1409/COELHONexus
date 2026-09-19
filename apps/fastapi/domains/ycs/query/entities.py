"""ycs/query — validated raw-DSL request value objects.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ParsedESBody:
    """Validated ES request payload. Holds the raw dict (returned to the
    transport) plus a flag for whether we synthesized a default `size`."""
    body:           dict
    synth_size:     bool


@dataclass(frozen=True, slots=True)
class ParsedQdrantOp:
    """One of {`search`, `scroll`, `query_points`} + the validated body."""
    op:   str
    body: dict
