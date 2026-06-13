"""ycs/conversation — Postgres-backed thread memory (Q&A history).

Async psycopg adapter for the deprecated `conversation_history` table.
Each handler call opens its own short-lived connection — same posture
as the deprecated service, and consistent with the existing DD
AsyncPostgresSaver pattern (no shared async pool needed for this
low-volume table).

Direct port of deprecated `services/youtube/conversation.py:L14-72`."""
from __future__ import annotations

import logging

import psycopg

from .params import (
    DEFAULT_HISTORY_LIMIT,
    DEFAULT_THREAD_ID,
    INDEX_NAME,
    TABLE_NAME,
)


logger = logging.getLogger(__name__)


async def ensure_conversation_table(pg_url: str) -> None:
    """Idempotent table + index create. Called from `app.py` lifespan
    so the first request never pays the DDL cost."""
    async with await psycopg.AsyncConnection.connect(
        pg_url, autocommit = True,
    ) as conn:
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                id          SERIAL PRIMARY KEY,
                thread_id   TEXT NOT NULL,
                question    TEXT NOT NULL,
                answer      TEXT NOT NULL,
                mode        TEXT,
                created_at  TIMESTAMPTZ DEFAULT NOW()
            )
            """,
        )
        await conn.execute(
            f"""
            CREATE INDEX IF NOT EXISTS {INDEX_NAME}
            ON {TABLE_NAME}(thread_id, created_at DESC)
            """,
        )


async def get_history(
    pg_url: str,
    thread_id: str,
    limit: int = DEFAULT_HISTORY_LIMIT,
) -> list[dict]:
    """Last N Q&A pairs for `thread_id`, oldest-first (so the LLM sees
    chronological context). Returns [] for the `default` sentinel —
    deprecated convention for stateless single-turn queries.

    Shape: `[{"question": str, "answer": str}, ...]`."""
    if not thread_id or thread_id == DEFAULT_THREAD_ID:
        return []
    async with await psycopg.AsyncConnection.connect(pg_url) as conn:
        result = await conn.execute(
            f"""
            SELECT question, answer FROM {TABLE_NAME}
            WHERE thread_id = %s
            ORDER BY created_at DESC LIMIT %s
            """,
            (thread_id, limit),
        )
        rows = await result.fetchall()
    return [{"question": r[0], "answer": r[1]} for r in reversed(rows)]


async def list_threads(
    pg_url: str,
    limit: int = 50,
) -> list[dict]:
    """Distinct threads with summary metadata for the UI picker.

    Returns most-recent-first. Each row:
      `{thread_id, turn_count, last_seen, first_question}`

    The `default` sentinel is excluded — stateless single-turn queries
    never land in the picker."""
    async with await psycopg.AsyncConnection.connect(pg_url) as conn:
        result = await conn.execute(
            f"""
            SELECT
                thread_id,
                COUNT(*) AS turn_count,
                MAX(created_at) AS last_seen,
                (ARRAY_AGG(question ORDER BY created_at ASC))[1]
                    AS first_question
            FROM {TABLE_NAME}
            WHERE thread_id <> %s
            GROUP BY thread_id
            ORDER BY MAX(created_at) DESC
            LIMIT %s
            """,
            (DEFAULT_THREAD_ID, limit),
        )
        rows = await result.fetchall()
    return [
        {
            "thread_id":      r[0],
            "turn_count":     int(r[1]),
            "last_seen":      r[2].isoformat() if r[2] is not None else None,
            "first_question": r[3] or "",
        }
        for r in rows
    ]


async def list_thread_messages(
    pg_url: str,
    thread_id: str,
    limit: int = 100,
) -> list[dict]:
    """Full-detail history for the UI. Unlike `get_history` (which
    returns only Q+A pairs for the LLM contextualize node), this
    includes `mode` + `created_at` so the conversation panel can
    re-render thread state on page refresh.

    Returns [] for the `default` sentinel."""
    if not thread_id or thread_id == DEFAULT_THREAD_ID:
        return []
    async with await psycopg.AsyncConnection.connect(pg_url) as conn:
        result = await conn.execute(
            f"""
            SELECT question, answer, mode, created_at FROM {TABLE_NAME}
            WHERE thread_id = %s
            ORDER BY created_at ASC LIMIT %s
            """,
            (thread_id, limit),
        )
        rows = await result.fetchall()
    return [
        {
            "question":   r[0],
            "answer":     r[1],
            "mode":       r[2] or "",
            "created_at": r[3].isoformat() if r[3] is not None else None,
        }
        for r in rows
    ]


async def save_turn(
    pg_url: str,
    thread_id: str,
    question: str,
    answer: str,
    mode: str = "",
) -> None:
    """Insert one Q&A turn. No-op for the `default` sentinel.

    `mode` carries the adaptive-RAG decision (`fast` / `standard` /
    `deep`) so a future debug query can audit how each turn was
    answered."""
    if not thread_id or thread_id == DEFAULT_THREAD_ID:
        return
    async with await psycopg.AsyncConnection.connect(pg_url) as conn:
        await conn.execute(
            f"""
            INSERT INTO {TABLE_NAME} (thread_id, question, answer, mode)
            VALUES (%s, %s, %s, %s)
            """,
            (thread_id, question, answer, mode),
        )
        await conn.commit()
