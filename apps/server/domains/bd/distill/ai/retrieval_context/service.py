"""Read and persist run-scoped Distill retrieval context."""
from __future__ import annotations

import json
from typing import Any

import domains

from . import domain


async def read_persisted_retrieval_context(
    storage: Any,
    book_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    """Load and normalize the stored retrieval context for one Distill run."""
    key = domains.bd.distill.ai.keys.retrieval_context_key(book_id, run_id)
    if not await storage.exists(key):
        return None
    value = json.loads(await storage.read_text(key))
    return domain.normalize_persisted_retrieval_context(value)


async def persist_retrieval_context(
    storage: Any,
    book_id: str,
    run_id: str,
    state: dict[str, Any],
    fused: dict[str, Any],
) -> None:
    """Persist retrieved source results and the fused evidence for later stages."""
    record = domain.persisted_retrieval_record(state, fused)
    await storage.write(
        domains.bd.distill.ai.keys.retrieval_context_key(book_id, run_id),
        domains.bd.distill.ai.domain.json_body(record),
        content_type = domains.bd.distill.ai.params.JSON_TYPE,
    )
