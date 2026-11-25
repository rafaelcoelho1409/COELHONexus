"""Load replay state and prepare the Distill retrieval query."""
from __future__ import annotations

import domains

from . import domain
from typing import Any


async def prepare_retrieval_query(
    state: dict[str, Any],
    dependencies: Any,
) -> dict[str, Any]:
    """Prepare a book-level retrieval query and restore persisted replay data."""
    source_names = domains.bd.distill.ai.params.RETRIEVAL_SOURCE_NODES
    replay_target = state.get("replay_target_node")
    persisted_context = None
    if replay_target in {*source_names, "fuse_evidence"}:
        persisted_context = await domains.bd.distill.ai.retrieval_context.service.read_persisted_retrieval_context(
            dependencies.storage,
            dependencies.book_id,
            dependencies.run_id,
        )
    return domain.prepare_retrieval_query(
        state,
        research_enabled=dependencies.research_enabled,
        max_query_chars=domains.bd.distill.ai.params.RETRIEVAL_QUERY_MAX_CHARS,
        source_names=source_names,
        persisted_context=persisted_context,
    )
