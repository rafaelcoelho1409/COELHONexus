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
    """Idempotent table + index create + `thinking_state` column
    migration. Called from `app.py` lifespan so the first request
    never pays the DDL cost.

    2026-06-15: `thinking_state` JSONB column persists the per-turn
    progress state (stage status + per-step action + DEEP sub-question
    progress + research plan + confidence) so a hard refresh restores
    the Thinking expander to its exact state — both mid-stream and
    after completion. `ADD COLUMN IF NOT EXISTS` is the safe migration
    path for existing deployments."""
    async with await psycopg.AsyncConnection.connect(
        pg_url, autocommit = True,
    ) as conn:
        await conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                id              SERIAL PRIMARY KEY,
                thread_id       TEXT NOT NULL,
                question        TEXT NOT NULL,
                answer          TEXT NOT NULL,
                mode            TEXT,
                thinking_state  JSONB,
                created_at      TIMESTAMPTZ DEFAULT NOW()
            )
            """,
        )
        await conn.execute(
            f"""
            ALTER TABLE {TABLE_NAME}
            ADD COLUMN IF NOT EXISTS thinking_state JSONB
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
    includes `mode` + `thinking_state` + `created_at` so the
    conversation panel can re-render thread state — including the
    Thinking expander's stage status + DEEP sub-questions — on page
    refresh.

    Returns [] for the `default` sentinel."""
    if not thread_id or thread_id == DEFAULT_THREAD_ID:
        return []
    async with await psycopg.AsyncConnection.connect(pg_url) as conn:
        result = await conn.execute(
            f"""
            SELECT id, question, answer, mode, thinking_state, created_at
            FROM {TABLE_NAME}
            WHERE thread_id = %s
            ORDER BY created_at ASC LIMIT %s
            """,
            (thread_id, limit),
        )
        rows = await result.fetchall()
    return [
        {
            # 2026-06-15 — `id` exposed so the frontend can issue
            # per-turn cancellation against `POST /turns/{id}/cancel`
            # when the user clicks Stop after a page refresh (the
            # original SSE fetch's abort controller died with the
            # previous page).
            "id":             int(r[0]),
            "question":       r[1],
            "answer":         r[2],
            "mode":           r[3] or "",
            "thinking_state": r[4],  # JSONB → dict | None
            "created_at":     r[5].isoformat() if r[5] is not None else None,
        }
        for r in rows
    ]


async def get_thread_locked_scope(
    pg_url: str,
    thread_id: str,
) -> list[str] | None:
    """Return the `channel_ids` snapshot persisted on the FIRST turn of
    the thread, or `None` if the thread has no turns yet.

    Used by `POST /agents/search/stream` to enforce the
    "scope is locked once the thread has a message" UX rule server-side
    (defense in depth — frontend disables the dropdown but a
    hand-crafted POST could still try to change it). When this returns
    a list, the SSE handler overrides `payload.channel_ids` with it.
    `[]` is a valid lock value meaning "thread was started in All-
    channels mode and must stay that way".

    Returns `None` (no lock) when:
      - the thread is the DEFAULT_THREAD_ID sentinel,
      - the thread doesn't exist in `conversation_history` yet,
      - the first turn's `thinking_state` is missing or doesn't
        contain a `channel_ids` field (pre-2026-06-17 rows)."""
    if not thread_id or thread_id == DEFAULT_THREAD_ID:
        return None
    async with await psycopg.AsyncConnection.connect(pg_url) as conn:
        result = await conn.execute(
            f"""
            SELECT thinking_state
            FROM {TABLE_NAME}
            WHERE thread_id = %s
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (thread_id,),
        )
        row = await result.fetchone()
    if not row or not row[0]:
        return None
    ts = row[0]  # JSONB → dict
    val = ts.get("channel_ids") if isinstance(ts, dict) else None
    if val is None:
        return None
    if not isinstance(val, list):
        return None
    return [str(v) for v in val]


async def branch_thread(
    pg_url: str,
    source_thread_id: str,
    up_to_created_at: str | None,
    new_thread_id: str,
) -> int:
    """Fork `source_thread_id` into `new_thread_id` by copying every row
    whose `created_at <= up_to_created_at`. `None` copies everything.

    Returns the number of rows actually copied. The new thread becomes
    its own independent conversation — further turns are appended to
    `new_thread_id`, leaving the source untouched.

    Used by the per-turn "Branch" action chip in `ask.js` so the user
    can rewind to a specific point and explore an alternative path
    without losing the original conversation."""
    if not source_thread_id or source_thread_id == DEFAULT_THREAD_ID:
        return 0
    if not new_thread_id:
        return 0
    async with await psycopg.AsyncConnection.connect(pg_url) as conn:
        params: tuple = (new_thread_id, source_thread_id)
        cutoff_sql = ""
        if up_to_created_at:
            cutoff_sql = "AND created_at <= %s"
            params = (new_thread_id, source_thread_id, up_to_created_at)
        result = await conn.execute(
            f"""
            INSERT INTO {TABLE_NAME} (thread_id, question, answer, mode, created_at)
            SELECT %s, question, answer, mode, created_at
            FROM {TABLE_NAME}
            WHERE thread_id = %s
            {cutoff_sql}
            ORDER BY created_at ASC
            """,
            params,
        )
        await conn.commit()
        return int(result.rowcount or 0)


async def delete_thread(
    pg_url: str,
    thread_id: str,
) -> int:
    """Delete every turn belonging to `thread_id`. Returns the row count
    actually deleted (0 if the thread did not exist).

    The `default` sentinel is silently no-op'd — stateless single-turn
    queries never land in the table to begin with, but a misguided
    delete on `default` would otherwise be a no-op anyway."""
    if not thread_id or thread_id == DEFAULT_THREAD_ID:
        return 0
    async with await psycopg.AsyncConnection.connect(pg_url) as conn:
        result = await conn.execute(
            f"DELETE FROM {TABLE_NAME} WHERE thread_id = %s",
            (thread_id,),
        )
        await conn.commit()
        return int(result.rowcount or 0)


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


async def insert_turn(
    pg_url: str,
    thread_id: str,
    question: str,
    mode: str = "",
) -> int | None:
    """Insert a placeholder turn at stream START (empty answer) so the
    conversation survives mid-stream refresh / hang. Returns the new
    row's `id` so subsequent `update_turn_answer()` calls can target
    it. Returns `None` for the `default` sentinel."""
    if not thread_id or thread_id == DEFAULT_THREAD_ID:
        return None
    async with await psycopg.AsyncConnection.connect(pg_url) as conn:
        result = await conn.execute(
            f"""
            INSERT INTO {TABLE_NAME} (thread_id, question, answer, mode)
            VALUES (%s, %s, '', %s)
            RETURNING id
            """,
            (thread_id, question, mode),
        )
        row = await result.fetchone()
        await conn.commit()
    return int(row[0]) if row else None


async def update_turn_answer(
    pg_url: str,
    turn_id: int,
    answer: str,
    mode: str = "",
    thinking_state: dict | None = None,
) -> None:
    """Patch an in-progress turn's answer + mode + thinking_state. Used
    by the streaming endpoint to persist partial generation
    incrementally (refresh-mid-stream still shows the latest snapshot)
    AND on the final successful-stream commit. `thinking_state=None`
    leaves the column untouched; pass `{}` to clear it explicitly."""
    if turn_id is None:
        return
    import json as _json
    async with await psycopg.AsyncConnection.connect(pg_url) as conn:
        if thinking_state is None:
            await conn.execute(
                f"""
                UPDATE {TABLE_NAME}
                SET answer = %s,
                    mode   = COALESCE(NULLIF(%s, ''), mode)
                WHERE id = %s
                """,
                (answer, mode, turn_id),
            )
        else:
            await conn.execute(
                f"""
                UPDATE {TABLE_NAME}
                SET answer         = %s,
                    mode           = COALESCE(NULLIF(%s, ''), mode),
                    thinking_state = %s::jsonb
                WHERE id = %s
                """,
                (answer, mode, _json.dumps(thinking_state), turn_id),
            )
        await conn.commit()


async def delete_turn(pg_url: str, turn_id: int | None) -> None:
    """Drop the placeholder row (called when the stream errors out
    before any generation arrived — we don't want a question with an
    empty answer haunting the picker forever)."""
    if turn_id is None:
        return
    async with await psycopg.AsyncConnection.connect(pg_url) as conn:
        await conn.execute(
            f"DELETE FROM {TABLE_NAME} WHERE id = %s",
            (turn_id,),
        )
        await conn.commit()
