"""
Conversation History Service — PostgreSQL-backed thread memory

CONCEPT: Stores Q&A pairs per thread_id so follow-up questions
("tell me more about that", "what about her views on X?") can be
contextualized by the LLM before retrieval.

Table auto-created at startup. History is loaded per-request and
passed through AdaptiveRAGState to the contextualize_question node.
"""
import psycopg


async def ensure_conversation_table(pg_url: str):
    """Create the conversation_history table if it doesn't exist."""
    async with await psycopg.AsyncConnection.connect(pg_url, autocommit=True) as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS conversation_history (
                id SERIAL PRIMARY KEY,
                thread_id TEXT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                mode TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_conv_thread
            ON conversation_history(thread_id, created_at DESC)
        """)


async def get_history(pg_url: str, thread_id: str, limit: int = 10) -> list[dict]:
    """
    Fetch the last N Q&A pairs for a thread, ordered oldest-first.
    Returns [{"question": "...", "answer": "..."}, ...]
    """
    if not thread_id or thread_id == "default":
        return []
    async with await psycopg.AsyncConnection.connect(pg_url) as conn:
        result = await conn.execute(
            "SELECT question, answer FROM conversation_history "
            "WHERE thread_id = %s "
            "ORDER BY created_at DESC LIMIT %s",
            (thread_id, limit),
        )
        rows = await result.fetchall()
    # Reverse so oldest first
    return [{"question": r[0], "answer": r[1]} for r in reversed(rows)]


async def save_turn(pg_url: str, thread_id: str, question: str, answer: str, mode: str = ""):
    """Insert one Q&A turn into conversation history."""
    if not thread_id or thread_id == "default":
        return
    async with await psycopg.AsyncConnection.connect(pg_url) as conn:
        await conn.execute(
            "INSERT INTO conversation_history (thread_id, question, answer, mode) "
            "VALUES (%s, %s, %s, %s)",
            (thread_id, question, answer, mode),
        )
        await conn.commit()
