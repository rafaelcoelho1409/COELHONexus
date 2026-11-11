"""Postgres persistence for book-scoped BD Ask conversations."""
from __future__ import annotations

import infra
from . import params


async def ensure_table() -> None:
    async with await infra.postgres.service.connect() as conn:
        await conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {params.TABLE_NAME} (
                id BIGSERIAL PRIMARY KEY,
                book_id TEXT NOT NULL,
                distill_run_id TEXT NOT NULL DEFAULT '',
                thread_id TEXT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL DEFAULT '',
                mode TEXT NOT NULL DEFAULT '',
                thinking_state JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute(
            f"ALTER TABLE {params.TABLE_NAME} ADD COLUMN IF NOT EXISTS "
            "distill_run_id TEXT NOT NULL DEFAULT ''"
        )
        await conn.execute(
            f"CREATE INDEX IF NOT EXISTS {params.INDEX_NAME} "
            f"ON {params.TABLE_NAME} (book_id, thread_id, created_at)"
        )
        await conn.commit()


async def history(thread_id: str) -> list[dict]:
    if not thread_id:
        return []
    async with await infra.postgres.service.connect() as conn:
        result = await conn.execute(
            f"SELECT question, answer FROM {params.TABLE_NAME} "
            "WHERE thread_id = %s ORDER BY created_at DESC LIMIT %s",
            (thread_id, params.HISTORY_LIMIT),
        )
        rows = await result.fetchall()
    return [{"question": row[0], "answer": row[1]} for row in reversed(rows)]


async def list_threads(book_id: str) -> list[dict]:
    async with await infra.postgres.service.connect() as conn:
        result = await conn.execute(f"""
            SELECT thread_id, book_id, COUNT(*) AS turn_count,
                   MAX(created_at) AS last_seen,
                   (ARRAY_AGG(question ORDER BY created_at ASC))[1] AS first_question
            FROM {params.TABLE_NAME}
            WHERE book_id = %s
            GROUP BY thread_id, book_id
            ORDER BY MAX(created_at) DESC LIMIT %s
        """, (book_id, params.THREAD_LIMIT))
        rows = await result.fetchall()
    return [
        {
            "thread_id": row[0], "book_id": row[1], "turn_count": int(row[2]),
            "last_seen": row[3].isoformat() if row[3] else "",
            "first_question": row[4] or "",
        }
        for row in rows
    ]


async def list_turns(thread_id: str) -> list[dict]:
    if not thread_id:
        return []
    async with await infra.postgres.service.connect() as conn:
        result = await conn.execute(
            f"SELECT id, question, answer, mode, thinking_state, created_at FROM {params.TABLE_NAME} "
            "WHERE thread_id = %s ORDER BY created_at ASC LIMIT 100",
            (thread_id,),
        )
        rows = await result.fetchall()
    return [
        {
            "id": int(row[0]), "question": row[1], "answer": row[2], "mode": row[3] or "",
            "thinking_state": row[4], "created_at": row[5].isoformat() if row[5] else "",
        }
        for row in rows
    ]


async def thread_book_id(thread_id: str) -> str | None:
    if not thread_id:
        return None
    async with await infra.postgres.service.connect() as conn:
        result = await conn.execute(
            f"SELECT book_id FROM {params.TABLE_NAME} WHERE thread_id = %s "
            "ORDER BY created_at ASC LIMIT 1", (thread_id,),
        )
        row = await result.fetchone()
    return row[0] if row else None


async def thread_run_id(thread_id: str) -> str | None:
    if not thread_id:
        return None
    async with await infra.postgres.service.connect() as conn:
        result = await conn.execute(
            f"SELECT distill_run_id FROM {params.TABLE_NAME} WHERE thread_id = %s "
            "ORDER BY created_at ASC LIMIT 1", (thread_id,),
        )
        row = await result.fetchone()
    return row[0] if row and row[0] else None


async def insert_turn(book_id: str, run_id: str, thread_id: str, question: str) -> int:
    async with await infra.postgres.service.connect() as conn:
        result = await conn.execute(
            f"INSERT INTO {params.TABLE_NAME} (book_id, distill_run_id, thread_id, question) "
            "VALUES (%s, %s, %s, %s) RETURNING id", (book_id, run_id, thread_id, question),
        )
        row = await result.fetchone()
        await conn.commit()
    return int(row[0])


async def update_turn(turn_id: int, answer: str, mode: str, thinking_state: dict) -> None:
    async with await infra.postgres.service.connect() as conn:
        await conn.execute(
            f"UPDATE {params.TABLE_NAME} SET answer = %s, mode = %s, thinking_state = %s "
            "WHERE id = %s",
            (answer, mode, infra.postgres.service.Jsonb(thinking_state), turn_id),
        )
        await conn.commit()


async def update_snapshot(turn_id: int, mode: str, thinking_state: dict) -> None:
    async with await infra.postgres.service.connect() as conn:
        await conn.execute(
            f"UPDATE {params.TABLE_NAME} SET mode = %s, thinking_state = %s WHERE id = %s",
            (mode, infra.postgres.service.Jsonb(thinking_state), turn_id),
        )
        await conn.commit()


async def delete_turn(turn_id: int) -> int:
    async with await infra.postgres.service.connect() as conn:
        result = await conn.execute(f"DELETE FROM {params.TABLE_NAME} WHERE id = %s", (turn_id,))
        await conn.commit()
    return int(result.rowcount or 0)


async def delete_thread(thread_id: str) -> int:
    async with await infra.postgres.service.connect() as conn:
        result = await conn.execute(f"DELETE FROM {params.TABLE_NAME} WHERE thread_id = %s", (thread_id,))
        await conn.commit()
    return int(result.rowcount or 0)


async def branch_thread(thread_id: str, new_thread_id: str, book_id: str, cutoff: str | None) -> int:
    cutoff_sql = "AND created_at <= %s::timestamptz" if cutoff else ""
    values = (new_thread_id, book_id, thread_id, cutoff) if cutoff else (new_thread_id, book_id, thread_id)
    async with await infra.postgres.service.connect() as conn:
        result = await conn.execute(f"""
            INSERT INTO {params.TABLE_NAME} (book_id, distill_run_id, thread_id, question, answer, mode, thinking_state, created_at)
            SELECT book_id, distill_run_id, %s, question, answer, mode, thinking_state, created_at
            FROM {params.TABLE_NAME}
            WHERE book_id = %s AND thread_id = %s {cutoff_sql}
            ORDER BY created_at ASC
        """, values)
        await conn.commit()
    return int(result.rowcount or 0)
