"""Shared FastMCP adapters for BD Query schema, drafting, validation, and runs."""
from __future__ import annotations
import domains
from . import params, schemas

from datetime import UTC, datetime
from typing import Any


def backend_catalog() -> schemas.QueryBackendCatalog:
    query = domains.bd.query.source
    matrix = query.service.namespaces()["matrix"][query.params.APP_BD]
    descriptions = {
        query.params.BACKEND_ELASTICSEARCH: (
            "Elasticsearch JSON search body",
            "BD processed-page and RAG-passage indices",
            query.params.ES_DEFAULT_SIZE,
            query.params.ES_MAX_SIZE,
        ),
        query.params.BACKEND_QDRANT: (
            "Qdrant read-operation JSON body",
            "BD RAG collections; semantic text uses the configured embedding model; schema lists at most 50 collections",
            query.params.QDRANT_DEFAULT_LIMIT,
            query.params.QDRANT_MAX_LIMIT,
        ),
        query.params.BACKEND_NEO4J: (
            "Read-only Cypher",
            "BD graph projection with typed Book, Section, Entity, and related labels",
            20,
            query.params.NEO4J_MAX_LIMIT,
        ),
    }
    return schemas.QueryBackendCatalog(
        backends=[
            schemas.QueryBackendDescription(
                backend=backend,
                label=matrix[backend]["label"],
                query_format=descriptions[backend][0],
                scope=descriptions[backend][1],
                default_limit=descriptions[backend][2],
                maximum_limit=descriptions[backend][3],
            )
            for backend in query.params.BACKENDS
        ],
    )


async def read_schema(backend: str) -> schemas.QuerySchemaSnapshot:
    query = domains.bd.query.source
    if backend not in query.params.BACKENDS:
        raise ValueError(f"Unsupported backend: {backend}.")
    schema_data = await query.service.query_schema(backend)
    namespace = query.service.namespaces()["matrix"][query.params.APP_BD][backend]
    live_schema_available = schema_data.get("live_schema_available")
    if not isinstance(live_schema_available, bool):
        if backend == query.params.BACKEND_QDRANT:
            live_schema_available = bool(schema_data.get("collections"))
        elif backend == query.params.BACKEND_ELASTICSEARCH:
            live_schema_available = bool(schema_data.get("live_mapping_count"))
        else:
            live_schema_available = bool(
                schema_data.get("observed_entity_types")
                or schema_data.get("observed_semantic_relationship_types")
                or schema_data.get("book_samples")
            )
    live_data_available = schema_data.get("live_data_available")
    if not isinstance(live_data_available, bool):
        if backend == query.params.BACKEND_QDRANT:
            live_data_available = any(
                isinstance(collection, dict)
                and isinstance(collection.get("points_count"), int)
                and collection["points_count"] > 0
                for collection in schema_data.get("collections", [])
            )
        elif backend == query.params.BACKEND_ELASTICSEARCH:
            live_data_available = any(
                isinstance(index.get("doc_count"), int) and index["doc_count"] > 0
                for index in schema_data.get("indices", {}).values()
                if isinstance(index, dict)
            )
        else:
            live_data_available = bool(
                schema_data.get("observed_entity_types")
                or schema_data.get("observed_semantic_relationship_types")
                or schema_data.get("book_samples")
            )
    return schemas.QuerySchemaSnapshot(
        backend=backend,
        label=namespace["label"],
        target=namespace["target"],
        live_schema_available=live_schema_available,
        live_data_available=live_data_available,
        generated_at=datetime.now(UTC).isoformat(),
        schema_fingerprint=query.domain.schema_fingerprint(schema_data),
        schema_data=schema_data,
    )


async def draft_query(
    backend: str,
    user_request: str,
    previous_query: str = "",
) -> schemas.QueryDraft:
    result = await domains.bd.query.ai.service.draft_query(
        backend=backend,
        user_prompt=user_request,
        previous=previous_query,
    )
    if result.get("validation_error") is not None:
        result["validation_error"] = str(result["validation_error"])[
            :params.MAX_VALIDATION_ERROR_CHARS
        ]
    return schemas.QueryDraft.model_validate(result)


async def validate_query(backend: str, query_text: str) -> schemas.QueryValidation:
    query = domains.bd.query.source
    schema = await query.service.query_schema(backend)
    valid, error = await query.service.validate_query(backend, query_text, schema=schema)
    return schemas.QueryValidation(
        backend=backend,
        valid=valid,
        validation_error=(error[:params.MAX_VALIDATION_ERROR_CHARS] if error else None),
        schema_fingerprint=query.domain.schema_fingerprint(schema),
    )


async def run_query(backend: str, query_text: str) -> schemas.QueryRunResult:
    response: dict[str, Any] = await domains.bd.query.source.service.run_raw_query(
        backend,
        query_text,
    )
    notes = response.get("notes")
    return schemas.QueryRunResult.model_validate({
        **response,
        "truncated": any(
            "truncated" in note.lower()
            for note in notes
            if isinstance(note, str)
        ) if isinstance(notes, list) else False,
    })
