"""ycs/embedding_migration — Redis key builder."""
from __future__ import annotations


_PREFIX = "ycs:embedding_migration:"


def migration_state_key() -> str:
    """Single global key (not per-run) — the embedding model is a
    Nexus-wide setting, not scoped to one ingestion run, so there is
    exactly one migration state at a time, corpus-wide."""
    return f"{_PREFIX}state"
