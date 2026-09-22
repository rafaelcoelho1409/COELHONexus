"""ycs/query — validated raw-DSL request value objects.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AppNamespace:
    """What an app owns inside ONE backend.

    `available` False = the app has no presence in that backend (DD is
    this everywhere today). The UI greys out the chip and the service
    short-circuits to an empty response. Keeping the entry (vs deleting)
    lets the frontend render a uniform 3x3 grid + a clear "no data"
    explanation."""
    available: bool
    # Human-readable namespace label for the response (and the UI's
    # "Searching in: …" caption). Empty when unavailable.
    label:     str = ""
    # The actual store-side identifier:
    #   - ES:     comma-joined index names ("idx_a,idx_b")
    #   - Qdrant: collection name
    #   - Neo4j:  comma-joined node labels searched
    target:    str = ""


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
