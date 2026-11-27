"""Coordinate read-only YCS queries, schema access, and saved-query history."""
from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from typing import Any

from fastapi import Request

import infra

from ... import ask
from . import indexes, keys, params

logger = logging.getLogger(__name__)
_LOCAL_SCHEMA_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


@contextmanager
def query_span(operation: str, *, backend: str, app: str):
    """Attach one root trace/session to a direct YCS Query operation."""
    session_id = f"ycs-query-{backend}-{uuid.uuid4().hex[:12]}"
    with infra.langfuse.service.session(
        "ycs-query",
        session_id = session_id,
        user_id = app or "default",
    ), infra.opentelemetry.service.get_tracer().start_as_current_span(
        f"ycs.query.{operation}",
        attributes = {
            infra.langfuse.keys.KEEP: True,
            infra.langfuse.keys.KIND: "workflow_root",
            infra.langfuse.keys.TRACE_NAME: f"ycs.query.{operation}",
            "ycs.query.backend": backend,
            "ycs.query.app": app or "",
            infra.langfuse.keys.OBS_META_WORKFLOW: "ycs_query",
        },
    ):
        yield


def namespaces() -> dict[str, Any]:
    """Return YCS's supported database targets for the Query workbench."""
    return {
        "apps": list(params.APPS),
        "backends": list(params.BACKENDS),
        "matrix": {
            app: {
                backend: {
                    "available": namespace.available,
                    "label": namespace.label,
                    "target": namespace.target,
                }
                for backend, namespace in backend_map.items()
            }
            for app, backend_map in params.APP_BACKENDS.items()
        },
    }


async def query_simple(
    backend: str,
    *,
    app: str,
    query: str,
    limit: int,
    offset: int,
    request: Request,
) -> dict[str, Any]:
    """Keep the legacy free-text YCS search endpoints working."""
    if backend == params.BACKEND_ES:
        with query_span("es.simple", backend = backend, app = app):
            return await indexes.elasticsearch_index.service.query_simple(
                app = app,
                q = query,
                limit = limit,
                offset = offset,
                request = request,
            )
    if backend == params.BACKEND_QDRANT:
        with query_span("qdrant.simple", backend = backend, app = app):
            return await indexes.qdrant_index.service.query_simple(
                app = app,
                q = query,
                limit = limit,
                request = request,
            )
    if backend == params.BACKEND_NEO4J:
        with query_span("neo4j.simple", backend = backend, app = app):
            return await indexes.neo4j_index.service.query_simple(
                app = app,
                q = query,
                limit = limit,
                request = request,
            )
    raise ValueError(f"Unsupported YCS query backend: {backend}.")


async def run_raw_query(
    backend: str,
    *,
    app: str,
    body_text: str,
    request: Request | None = None,
) -> dict[str, Any]:
    """Run one policy-checked, read-only query against the selected store."""
    if backend == params.BACKEND_ES:
        with query_span("es.raw", backend = backend, app = app):
            return await indexes.elasticsearch_index.service.run_raw_query(
                app = app,
                body_text = body_text,
                request = request,
            )
    if backend == params.BACKEND_QDRANT:
        with query_span("qdrant.raw", backend = backend, app = app):
            return await indexes.qdrant_index.service.run_raw_query(
                app = app,
                body_text = body_text,
                request = request,
            )
    if backend == params.BACKEND_NEO4J:
        with query_span("neo4j.raw", backend = backend, app = app):
            return await indexes.neo4j_index.service.run_raw_query(
                app = app,
                body_text = body_text,
                request = request,
            )
    raise ValueError(f"Unsupported YCS query backend: {backend}.")


async def query_schema(
    backend: str,
    *,
    request: Request | None = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Return the cached live-plus-declared schema for one YCS store."""
    builders: dict[str, Callable[[], Awaitable[dict[str, Any]]]] = {
        params.BACKEND_ES: indexes.elasticsearch_index.service.query_schema,
        params.BACKEND_QDRANT: indexes.qdrant_index.service.query_schema,
        params.BACKEND_NEO4J: indexes.neo4j_index.service.query_schema,
    }
    builder = builders.get(backend)
    if builder is None:
        raise ValueError(f"Unsupported YCS query backend: {backend}.")
    return await _schema_cached(backend = backend, request = request, refresh = refresh, builder = builder)


async def validate_query(
    backend: str,
    body_text: str,
    *,
    schema: dict[str, Any],
) -> tuple[bool, str | None]:
    """Validate generated query syntax and store-specific schema references."""
    validators = {
        params.BACKEND_ES: indexes.elasticsearch_index.service.validate_query,
        params.BACKEND_QDRANT: indexes.qdrant_index.service.validate_query,
        params.BACKEND_NEO4J: indexes.neo4j_index.service.validate_query,
    }
    validator = validators.get(backend)
    if validator is None:
        return False, f"Unsupported YCS query backend: {backend}."
    if backend == params.BACKEND_QDRANT:
        return await validator(body_text, schema = schema)
    return await validator(body_text)


async def _schema_cached(
    *,
    backend: str,
    request: Request | None,
    refresh: bool,
    builder: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Read-through Redis cache for a backend schema snapshot."""
    if request is None and not refresh:
        cached = _LOCAL_SCHEMA_CACHE.get(backend)
        if cached is not None and time.monotonic() - cached[0] < params.SCHEMA_TTL_S:
            return cached[1]
    redis_aio = (
        getattr(request.app.state, "redis_aio", None)
        if request is not None
        else None
    )
    key = keys.SCHEMA_KEY.format(backend = backend)
    if redis_aio is not None and not refresh:
        try:
            raw = await redis_aio.get(key)
        except Exception:  # noqa: BLE001
            raw = None
        if raw:
            try:
                cached = json.loads(raw)
                if isinstance(cached, dict):
                    return cached
            except (TypeError, ValueError):
                pass

    result = await builder()
    result["cached_at"] = int(time.time())
    if request is None:
        _LOCAL_SCHEMA_CACHE[backend] = (time.monotonic(), result)
    elif redis_aio is not None:
        try:
            await redis_aio.set(key, json.dumps(result), ex = params.SCHEMA_TTL_S)
        except Exception as error:  # noqa: BLE001
            logger.warning(f"[ycs:query:schema] Redis cache write failed: {type(error).__name__}")
    return result


async def save_query_history_entry(
    pg_url: str,
    *,
    backend: str,
    app: str,
    body: str,
    prompt: str,
    favorite: bool = False,
) -> int:
    """Save one query history row and return its id."""
    await _ensure_query_history_table(pg_url)
    with ask.observability.service.postgres_span(
        operation = "insert",
        table = keys.HISTORY_TABLE_NAME,
    ):
        async with await infra.postgres.service.connect(pg_url) as connection:
            result = await connection.execute(
                f"INSERT INTO {keys.HISTORY_TABLE_NAME} "
                "(backend, app, body, prompt, favorite) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (backend, app, body, prompt, favorite),
            )
            row = await result.fetchone()
            await connection.commit()
    return int(row[0]) if row else 0


async def list_query_history_entries(
    pg_url: str,
    *,
    app: str | None = None,
    backend: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return recent saved queries, optionally filtered by app and backend."""
    await _ensure_query_history_table(pg_url)
    with ask.observability.service.postgres_span(
        operation = "select",
        table = keys.HISTORY_TABLE_NAME,
    ):
        async with await infra.postgres.service.connect(pg_url) as connection:
            filters: list[str] = []
            values: list[Any] = []
            if app:
                filters.append("app = %s")
                values.append(app)
            if backend:
                filters.append("backend = %s")
                values.append(backend)
            where = f" WHERE {' AND '.join(filters)}" if filters else ""
            values.append(limit)
            result = await connection.execute(
                f"SELECT id, backend, app, body, prompt, favorite, created_at "
                f"FROM {keys.HISTORY_TABLE_NAME}{where} "
                "ORDER BY created_at DESC LIMIT %s",
                tuple(values),
            )
            rows = await result.fetchall()
    return [
        {
            "id": int(row[0]),
            "backend": row[1],
            "app": row[2],
            "body": row[3],
            "prompt": row[4] or "",
            "favorite": bool(row[5]),
            "created_at": row[6].isoformat() if row[6] is not None else "",
        }
        for row in rows
    ]


async def delete_query_history_entry(
    pg_url: str,
    entry_id: int,
    *,
    app: str | None = None,
) -> int:
    """Delete one saved query, optionally scoped to its owning app."""
    await _ensure_query_history_table(pg_url)
    with ask.observability.service.postgres_span(
        operation = "delete",
        table = keys.HISTORY_TABLE_NAME,
    ):
        async with await infra.postgres.service.connect(pg_url) as connection:
            if app:
                result = await connection.execute(
                    f"DELETE FROM {keys.HISTORY_TABLE_NAME} WHERE id = %s AND app = %s",
                    (entry_id, app),
                )
            else:
                result = await connection.execute(
                    f"DELETE FROM {keys.HISTORY_TABLE_NAME} WHERE id = %s",
                    (entry_id,),
                )
            await connection.commit()
    return int(getattr(result, "rowcount", 0) or 0)


async def _ensure_query_history_table(pg_url: str) -> None:
    """Create the shared history table and indexes on first use."""
    with ask.observability.service.postgres_span(
        operation = "bootstrap",
        table = keys.HISTORY_TABLE_NAME,
    ):
        async with await infra.postgres.service.connect(pg_url, autocommit = True) as connection:
            await connection.execute(f"""
                CREATE TABLE IF NOT EXISTS {keys.HISTORY_TABLE_NAME} (
                    id           BIGSERIAL PRIMARY KEY,
                    backend      TEXT      NOT NULL,
                    app          TEXT      NOT NULL DEFAULT 'ycs',
                    body         TEXT      NOT NULL,
                    prompt       TEXT      NOT NULL DEFAULT '',
                    favorite     BOOLEAN   NOT NULL DEFAULT FALSE,
                    owner        TEXT,
                    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            await connection.execute(f"""
                CREATE INDEX IF NOT EXISTS query_history_created_idx
                    ON {keys.HISTORY_TABLE_NAME} (created_at DESC)
            """)
            await connection.execute(f"""
                CREATE INDEX IF NOT EXISTS query_history_backend_idx
                    ON {keys.HISTORY_TABLE_NAME} (backend, created_at DESC)
            """)
