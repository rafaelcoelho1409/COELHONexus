"""tool_credentials entities — catalog value objects (no behavior)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ToolKeyDef:
    """`provider` (NOT `source`) — avoids collision with `KeyStatus.source` when flattened in _view()."""

    key_env: str                # storage key + injected env-var name
    display_name: str           # UI label, e.g. "Semantic Scholar API Key"
    provider: str               # human-readable provider, "api.semanticscholar.org"
    signup_url: str             # where to obtain the key
    summary: str                # one-line description shown beneath the input
    benefit: str                # what the operator gains when the key is set
