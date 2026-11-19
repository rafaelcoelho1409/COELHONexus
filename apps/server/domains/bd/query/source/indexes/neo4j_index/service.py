"""Read-only Neo4j query and schema operations for BD."""
from __future__ import annotations

import logging
import time
from typing import Any

import infra

from ... import domain, params

logger = logging.getLogger(__name__)


async def run_raw_query(body_text: str) -> dict[str, Any]:
    backend = params.BACKEND_NEO4J
    body_text = domain.normalize_neo4j_query(body_text)
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


async def validate_query(body_text: str) -> tuple[bool, str | None]:
    """Check generated Cypher syntax and planning without executing the query."""
    body_text = domain.normalize_neo4j_query(body_text)
    valid, error = domain.check_query_body(params.BACKEND_NEO4J, body_text)
    if not valid:
        return False, error
    try:
        async with infra.neo4j.service.session(default_access_mode = "READ") as session:
            result = await session.run(f"EXPLAIN {body_text}")
            summary = await result.consume()
        schema_warnings = [
            notification
            for notification in getattr(summary, "notifications", []) or []
            if any(
                warning_code in str(getattr(notification, "code", ""))
                for warning_code in (
                    "UnknownLabelWarning",
                    "UnknownRelationshipTypeWarning",
                    "UnknownPropertyKeyWarning",
                )
            )
        ]
        if schema_warnings:
            details = "; ".join(
                str(getattr(notification, "description", "Unknown graph label, relationship, or property"))
                for notification in schema_warnings
            )
            return False, f"Neo4j query does not match the live graph schema: {details[:300]}"
    except Exception as error:  # noqa: BLE001 - return backend validation details for one repair attempt.
        return False, f"Neo4j rejected the query plan: {type(error).__name__}: {str(error)[:300]}"
    return True, None


async def query_schema() -> dict[str, Any]:
    """Return the BD graph projection contract plus representative live values."""
    schema = {
        "base_labels": list(params.NEO4J_BASE_LABELS),
        "node_labels": list(params.NEO4J_NODE_LABELS),
        "node_properties": {
            label: [
                {"name": name, "types": [value_type]}
                for name, value_type in properties
            ]
            for label, properties in params.NEO4J_NODE_PROPERTIES_BY_LABEL.items()
        },
        "relationship_patterns": [
            {
                "source_labels": list(source_labels),
                "type": relationship_type,
                "target_labels": list(target_labels),
            }
            for source_labels, relationship_type, target_labels in params.NEO4J_RELATIONSHIP_PATTERNS
        ],
        "relationship_properties": [
            {"name": name, "types": [value_type]}
            for name, value_type in params.NEO4J_RELATIONSHIP_PROPERTIES
        ],
        "optional_relationship_properties": [
            {"name": name, "types": [value_type]}
            for name, value_type in params.NEO4J_OPTIONAL_RELATIONSHIP_PROPERTIES
        ],
        "semantic_relationship_type_pattern": "BD_REL_<UPPERCASE_SNAKE_CASE_PREDICATE>",
        "observed_semantic_relationship_types": [],
        "observed_entity_types": [],
        "book_samples": [],
        "live_data_available": False,
    }
    try:
        async with infra.neo4j.service.session(default_access_mode = "READ") as session:
            entity_result = await session.run("""
                MATCH (entity:COELHONexus:BD:Entity)
                WHERE entity.entity_type IS NOT NULL
                RETURN entity.entity_type AS entity_type, count(*) AS entity_count
                ORDER BY entity_count DESC, entity_type
                LIMIT 50
            """)
            schema["observed_entity_types"] = [
                {"name": row["entity_type"], "count": int(row["entity_count"])}
                async for row in entity_result
            ]
            relationship_result = await session.run("""
                CALL db.relationshipTypes() YIELD relationshipType
                WHERE relationshipType STARTS WITH 'BD_REL_'
                RETURN relationshipType
                ORDER BY relationshipType
                LIMIT 100
            """)
            schema["observed_semantic_relationship_types"] = [
                row["relationshipType"]
                async for row in relationship_result
            ]
            book_result = await session.run("""
                MATCH (book:COELHONexus:BD:Book)
                RETURN book.book_id AS book_id, book.name AS title
                ORDER BY title
                LIMIT 50
            """)
            schema["book_samples"] = [
                {"book_id": row["book_id"], "title": row["title"]}
                async for row in book_result
            ]
            schema["live_data_available"] = True
    except Exception as error:  # noqa: BLE001 - retain the index contract if live samples are unavailable.
        logger.warning(f"[bd:query:schema:neo4j] live value lookup failed: {type(error).__name__}: {error}")
    return schema
