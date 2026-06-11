"""Catalog of tool API keys the Settings UI exposes for user input.

Each entry is rendered as one row in the "Source Tool Keys" section. Adding
a new key = one row here + (optionally) a `_TESTERS` test-call mapping in
router.py. The env-var name must ALSO be present in
apps.fastapi.domains.llm.credentials.keys.MANAGED_KEY_ENVS (the storage
whitelist) — keep both in sync.

Frozen-dataclass per docs/CODE-CONVENTIONS.md §3 (≥3 grouped tunables).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ToolKeyDef:
    """One UI-visible tool API key the operator may supply.

    NOTE on field naming: `provider` (NOT `source`) to avoid colliding with
    `KeyStatus.source` (Literal["user", "env"] | None) when both are flattened
    into the same dict by the router's _view(). Keep these names distinct.
    """

    key_env: str                # storage key + injected env-var name
    display_name: str           # UI label, e.g. "Semantic Scholar API Key"
    provider: str               # human-readable provider, "api.semanticscholar.org"
    signup_url: str             # where to obtain the key
    summary: str                # one-line description shown beneath the input
    benefit: str                # what the operator gains when the key is set


# Order matters — this is the rendering order in the UI.
TOOL_KEYS: tuple[ToolKeyDef, ...] = (
    ToolKeyDef(
        key_env="SEMANTIC_SCHOLAR_API_KEY",
        display_name="Semantic Scholar API Key",
        provider="api.semanticscholar.org",
        signup_url="https://www.semanticscholar.org/product/api#api-key-form",
        summary=(
            "Optional. Used by the Research Radar's semantic_scholar tool. "
            "Without it, calls share Semantic Scholar's global unauth pool "
            "(frequently saturated). Free signup, no payment."
        ),
        benefit=(
            "Unlocks ~1 RPS sustained vs the shared 100 req / 5 min pool — "
            "rate-limit middleware drops the per-call interval from 3 s to 1 s."
        ),
    ),
)


def get_tool_key_def(key_env: str) -> ToolKeyDef | None:
    """Lookup by env-var name. Returns None for unknown keys."""
    for d in TOOL_KEYS:
        if d.key_env == key_env:
            return d
    return None
