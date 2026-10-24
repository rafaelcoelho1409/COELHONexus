"""ycs/embedding_migration — pure helpers (Cosmic Python `domain.py`)."""
from __future__ import annotations

import re


def physical_collection_name(base: str, pinned_id: str, dimensions: int) -> str:
    """Deterministic, Qdrant-safe physical collection name for one
    (model, dimension) pair — versioned so a migration never writes over
    the collection it's migrating FROM, and re-running a migration to the
    SAME target model is idempotent (same name, `ensure_collection`
    no-ops if it already exists with a matching schema).

    `pinned_id` is `"{provider}/{model}"` (see the rotator's
    `domains.embeddings.keys.pinned_id`) — sanitized to `[a-z0-9_]` since
    Qdrant collection names can't contain `/` and model ids often do
    (e.g. "nim/nvidia/llama-embed-nemotron-8b")."""
    safe = re.sub(r"[^a-z0-9]+", "_", pinned_id.lower()).strip("_")
    return f"{base}__{safe}__{dimensions}d"
