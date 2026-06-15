"""ycs/conversation — Postgres conversation-history table.

Direct port of deprecated `services/youtube/conversation.py`. Replaces
the DD `AsyncPostgresSaver` path that the previous 13-slice ship used
for thread memory (Wave 1.x revert) — deprecated had its own table."""
from .params import (
    DEFAULT_HISTORY_LIMIT,
    DEFAULT_THREAD_ID,
    INDEX_NAME,
    TABLE_NAME,
)
from .service import (
    branch_thread,
    delete_thread,
    delete_turn,
    ensure_conversation_table,
    get_history,
    insert_turn,
    list_thread_messages,
    list_threads,
    save_turn,
    update_turn_answer,
)


__all__ = [
    "DEFAULT_HISTORY_LIMIT",
    "DEFAULT_THREAD_ID",
    "INDEX_NAME",
    "TABLE_NAME",
    "branch_thread",
    "delete_thread",
    "delete_turn",
    "ensure_conversation_table",
    "get_history",
    "insert_turn",
    "list_thread_messages",
    "list_threads",
    "save_turn",
    "update_turn_answer",
]
