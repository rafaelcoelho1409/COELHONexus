"""ycs/runtime/llm_counter — Redis key builders.

Generic over `(id, sub_id)` — the SAME two builders back both the
ingestion attribution pair (`extract_id`, `video_id`) and the Ask-path
attribution pair (`thread_id`, `node`); see `service.py::bump_current_call`."""
from __future__ import annotations


def counters_key(id_: str) -> str:
    return f"coelhonexus:ycs:{id_}:llm:counters"


def models_key(id_: str, sub_id: str) -> str:
    return f"coelhonexus:ycs:{id_}:llm:models:{sub_id}"
