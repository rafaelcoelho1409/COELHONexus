"""Read-only Neo4j query and schema operations for BD."""
from __future__ import annotations
import infra
from ... import domain, params

import logging
import time
from typing import Any


logger = logging.getLogger(__name__)


async def run_raw_query(body_text: str) -> dict[str, Any]:
    backend = params.BACKEND_NEO4J
    valid, error = domain.check_query_body(backend, body_text)
    if not valid:
        return domain.raw_response(backend, ok = False, error = error)

    started = time.monotonic()
    try:
        async with infra.neo4j.service.session(default_access_mode = "READ") as session:
            result = await session.run(body_text)
            records = [dict(record) async for record in result]
    except Exception as error:  # noqa: BLE001 - backend failures belong in the query result envelope.
        logger.warning(f"[bd:query:neo4j] failed: {type(error).__name__}: {error}")
        return domain.raw_response(
            backend,
            ok = False,
            error = f"{type(error).__name__}: {str(error)[:400]}",
            took_ms = int((time.monotonic() - started) * 1000),
        )

    hits = []
    for record in records:
        summary = "  ".join(
            f"{key}={str(value)[:80]}"
            for key, value in record.items()
            if value is not None
        )
        hits.append({
            "summary": summary or "(empty row)",
            "raw": domain.neo4j_jsonify(record),
        })
    return domain.raw_response(
        backend,
        took_ms = int((time.monotonic() - started) * 1000),
        total = len(hits),
        hits = hits,
    )


def query_schema() -> dict[str, Any]:
    return {
        "labels": ["BD"],
        "node_properties": {
            "BD": [
                {"name": name, "types": [value_type]}
                for name, value_type in params.NEO4J_NODE_PROPERTIES
            ],
        },
        "relationship_patterns": [
            {"src": "BD", "rel": relationship_type, "dst": "BD"}
            for relationship_type in params.NEO4J_RELATIONSHIP_TYPES
        ],
    }
