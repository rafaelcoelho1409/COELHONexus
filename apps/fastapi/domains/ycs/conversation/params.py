"""ycs/conversation — Postgres conversation-history tunables.
`DEFAULT_THREAD_ID = "default"` is the sentinel used by the deprecated
agents router to mean "stateless single-turn" — `get_history` /
`save_turn` short-circuit when the thread is `default` so single-shot
queries never write to the history table."""
from __future__ import annotations


# Table + index names — kept verbatim so re-using an existing Postgres
# instance is a no-op.
TABLE_NAME = "conversation_history"
INDEX_NAME = "idx_conv_thread"
# `get_history` / `save_turn` guard.
DEFAULT_THREAD_ID = "default"

DEFAULT_HISTORY_LIMIT = 10
