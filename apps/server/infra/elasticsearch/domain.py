"""elasticsearch domain — pure client/index helpers (no I/O, deterministic)."""
from __future__ import annotations

from . import keys, schemas


def basic_auth(username: str, password: str | None) -> tuple[str, str] | None:
    """`(username, password)` for the AsyncElasticsearch client, or None
    for anonymous access when no password is configured."""
    if password:
        return (username, password)
    return None


def index_pairs() -> tuple[tuple[str, dict], ...]:
    """`(stable alias, mapping)` pairs `ensure_indexes` provisions."""
    return (
        (keys.INDEX_METADATA, schemas.METADATA_MAPPING),
        (keys.INDEX_TRANSCRIPTIONS, schemas.TRANSCRIPTIONS_MAPPING),
        (keys.INDEX_BD_INGESTION, schemas.BD_INGESTION_MAPPING),
        (keys.INDEX_YCS_RAG_PASSAGES, schemas.YCS_RAG_PASSAGES_MAPPING),
        (keys.INDEX_BD_RAG_PASSAGES, schemas.BD_RAG_PASSAGES_MAPPING),
    )
