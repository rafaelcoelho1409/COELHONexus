"""Redis/MinIO key builders for DD LLM-usage counters."""
from __future__ import annotations

from urllib.parse import quote


SNAPSHOT_PREFIX = "observability/dd/llm-counters"


def counters_key(thread_id: str) -> str:
    return f"coelhonexus:dd:{thread_id}:llm:counters"


def models_key(thread_id: str, node_id: str) -> str:
    return f"coelhonexus:dd:{thread_id}:llm:models:{node_id}"


def snapshot_key(thread_id: str) -> str:
    return f"{SNAPSHOT_PREFIX}/{quote(thread_id, safe='')}.json"
