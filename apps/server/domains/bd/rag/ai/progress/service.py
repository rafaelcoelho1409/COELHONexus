"""Best-effort publication of durable RAG index status changes."""

from __future__ import annotations

import logging
import time
from typing import Any

import infra

from . import keys, params

logger = logging.getLogger(__name__)


async def emit(book_id: str, agentic_run_id: str, manifest: dict[str, Any]) -> None:
    try:
        await infra.redis.service.emit_event(
            keys.event_channel(book_id, agentic_run_id),
            keys.snapshot_key(book_id, agentic_run_id),
            {
                "kind": "run_updated",
                "ts": time.time(),
                "status": manifest.get("status"),
                "current_node": manifest.get("current_node"),
                "node_status": manifest.get("node_status") or {},
                "qdrant_point_count": manifest.get("qdrant_point_count", 0),
                "elasticsearch_passage_count": manifest.get(
                    "elasticsearch_passage_count", 0
                ),
                "neo4j_node_count": manifest.get("neo4j_node_count", 0),
                "updated_at": manifest.get("updated_at"),
            },
            snapshot_max_events=params.SNAPSHOT_MAX_EVENTS,
            snapshot_ttl_s=params.SNAPSHOT_TTL_S,
            error_label=f"[bd-rag-progress] emit failed for {book_id}/{agentic_run_id}",
        )
    except Exception:
        logger.warning(
            "[bd-rag-progress] unable to publish an index event for %s/%s",
            book_id,
            agentic_run_id,
            exc_info=True,
        )
