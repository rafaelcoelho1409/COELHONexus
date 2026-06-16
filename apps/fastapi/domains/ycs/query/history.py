"""ycs/query — Postgres-backed per-user query history.

Tiny table (one schema, three queries). Uses `psycopg` v3 — same driver
the YCS conversation service uses, the only async-Postgres lib actually
present in the FastAPI image (`asyncpg` would have to be added to
`pyproject.toml` + a wheel rebuild; staying on psycopg keeps the
deploy surface unchanged).

There's no auth yet — every user sees every row. Add an `owner` column
+ filter when SSO lands; the schema below already accommodates it as a
nullable text."""
from __future__ import annotations

import logging
from typing import Any

import psycopg


logger = logging.getLogger(__name__)


_TABLE_NAME = "query_history"


async def ensure_table(pg_url: str) -> None:
    """Idempotent table init. Called lazily on first read/write — keeps
    Query out of the lifespan hot path (cheap when already created)."""
    async with await psycopg.AsyncConnection.connect(
        pg_url, autocommit = True,
    ) as conn:
        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {_TABLE_NAME} (
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
        await conn.execute(f"""
            CREATE INDEX IF NOT EXISTS query_history_created_idx
                ON {_TABLE_NAME} (created_at DESC)
        """)
        await conn.execute(f"""
            CREATE INDEX IF NOT EXISTS query_history_backend_idx
                ON {_TABLE_NAME} (backend, created_at DESC)
        """)


async def save_entry(
    pg_url: str, *, backend: str, app: str, body: str, prompt: str,
    favorite: bool = False,
) -> int:
    """Insert one row, return its id. Errors propagate up so the router
    can 5xx on Postgres outages instead of silently no-op'ing."""
    await ensure_table(pg_url)
    async with await psycopg.AsyncConnection.connect(pg_url) as conn:
        result = await conn.execute(
            f"INSERT INTO {_TABLE_NAME} "
            f"(backend, app, body, prompt, favorite) "
            f"VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (backend, app, body, prompt, favorite),
        )
        row = await result.fetchone()
        await conn.commit()
    return int(row[0]) if row else 0


async def list_entries(
    pg_url: str, *, backend: str | None = None, limit: int = 50,
) -> list[dict[str, Any]]:
    """Return the latest `limit` entries, optionally filtered to one
    backend. Body is included so the UI can show a snippet without an
    extra round-trip."""
    await ensure_table(pg_url)
    async with await psycopg.AsyncConnection.connect(pg_url) as conn:
        if backend:
            result = await conn.execute(
                f"SELECT id, backend, app, body, prompt, favorite, created_at "
                f"FROM {_TABLE_NAME} WHERE backend = %s "
                f"ORDER BY created_at DESC LIMIT %s",
                (backend, limit),
            )
        else:
            result = await conn.execute(
                f"SELECT id, backend, app, body, prompt, favorite, created_at "
                f"FROM {_TABLE_NAME} "
                f"ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
        rows = await result.fetchall()
    return [
        {
            "id":         int(r[0]),
            "backend":    r[1],
            "app":        r[2],
            "body":       r[3],
            "prompt":     r[4] or "",
            "favorite":   bool(r[5]),
            "created_at": r[6].isoformat() if r[6] is not None else "",
        }
        for r in rows
    ]


async def delete_entry(pg_url: str, entry_id: int) -> int:
    """DELETE one row by id. Returns 1 if removed, 0 if not found."""
    await ensure_table(pg_url)
    async with await psycopg.AsyncConnection.connect(pg_url) as conn:
        result = await conn.execute(
            f"DELETE FROM {_TABLE_NAME} WHERE id = %s",
            (entry_id,),
        )
        await conn.commit()
    # psycopg cursor.rowcount carries the affected-row count.
    return int(getattr(result, "rowcount", 0) or 0)
