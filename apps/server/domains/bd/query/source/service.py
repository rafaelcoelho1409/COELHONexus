"""Coordinate BD Query access to retrieval stores and saved-query history."""
from __future__ import annotations
import infra
from . import domain, indexes, keys, params

from typing import Any


def namespaces() -> dict[str, Any]:
    return {
        "apps": [params.APP_BD],
        "backends": list(params.BACKENDS),
        "matrix": {
            params.APP_BD: {
                params.BACKEND_ELASTICSEARCH: {
                    "available": True,
                    "label": "BD · processed pages and passages",
                    "target": ",".join(params.ELASTICSEARCH_INDEX_NAMES),
                },
                params.BACKEND_QDRANT: {
                    "available": True,
                    "label": "BD · embedded page passages",
                    "target": params.QDRANT_COLLECTION_PREFIX,
                },
                params.BACKEND_NEO4J: {
                    "available": True,
                    "label": "BD · books, sections, and concepts",
                    "target": "(:COELHONexus:BD)",
                },
            },
        },
    }


async def run_raw_query(backend: str, body_text: str) -> dict[str, Any]:
    if backend not in params.BACKENDS:
        return domain.raw_response(
            backend,
            ok = False,
            error = f"Unsupported backend: {backend}.",
        )
    if len(body_text) > params.MAX_QUERY_BODY_CHARS:
        return domain.raw_response(
            backend,
            ok = False,
            error = f"Query body exceeds {params.MAX_QUERY_BODY_CHARS} characters.",
        )
    if backend == params.BACKEND_ELASTICSEARCH:
        return await indexes.elasticsearch_index.service.run_raw_query(body_text)
    if backend == params.BACKEND_QDRANT:
        return await indexes.qdrant_index.service.run_raw_query(body_text)
    return await indexes.neo4j_index.service.run_raw_query(body_text)


async def query_schema(backend: str) -> dict[str, Any]:
    if backend == params.BACKEND_ELASTICSEARCH:
        return await indexes.elasticsearch_index.service.query_schema()
    if backend == params.BACKEND_QDRANT:
        return await indexes.qdrant_index.service.query_schema()
    if backend == params.BACKEND_NEO4J:
        return indexes.neo4j_index.service.query_schema()
    return {}


async def list_query_history(
    pg_url: str,
    *,
    backend: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Read recent BD Query history from Postgres."""
    await _ensure_query_history_table(pg_url)
    with infra.postgres.service.span(
        "select",
        **{"db.postgres.table": keys.HISTORY_TABLE_NAME},
    ):
        async with await infra.postgres.service.connect(pg_url) as connection:
            filters = ["app = %s"]
            values: list[Any] = [params.APP_BD]
            if backend:
                filters.append("backend = %s")
                values.append(backend)
            values.append(limit)
            result = await connection.execute(
                f"SELECT id, backend, app, body, prompt, favorite, created_at "
                f"FROM {keys.HISTORY_TABLE_NAME} "
                f"WHERE {' AND '.join(filters)} "
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


async def save_query_history(
    pg_url: str,
    *,
    backend: str,
    body: str,
    prompt: str,
) -> int:
    """Save one BD Query history entry in Postgres."""
    await _ensure_query_history_table(pg_url)
    with infra.postgres.service.span(
        "insert",
        **{"db.postgres.table": keys.HISTORY_TABLE_NAME},
    ):
        async with await infra.postgres.service.connect(pg_url) as connection:
            result = await connection.execute(
                f"INSERT INTO {keys.HISTORY_TABLE_NAME} "
                "(backend, app, body, prompt, favorite) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (backend, params.APP_BD, body, prompt, False),
            )
            row = await result.fetchone()
            await connection.commit()
    return int(row[0]) if row else 0


async def delete_query_history(pg_url: str, entry_id: int) -> int:
    """Delete one BD Query history entry from Postgres."""
    await _ensure_query_history_table(pg_url)
    with infra.postgres.service.span(
        "delete",
        **{"db.postgres.table": keys.HISTORY_TABLE_NAME},
    ):
        async with await infra.postgres.service.connect(pg_url) as connection:
            result = await connection.execute(
                f"DELETE FROM {keys.HISTORY_TABLE_NAME} WHERE id = %s AND app = %s",
                (entry_id, params.APP_BD),
            )
            await connection.commit()
    return int(getattr(result, "rowcount", 0) or 0)


async def _ensure_query_history_table(pg_url: str) -> None:
    """Create the shared history table and its indexes when first needed."""
    with infra.postgres.service.span(
        "bootstrap",
        **{"db.postgres.table": keys.HISTORY_TABLE_NAME},
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
