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
    """`(index name, mapping)` pairs `ensure_indexes` iterates — single
    source for which indexes exist, so bootstrap and readers can't drift."""
    return (
        (keys.INDEX_METADATA, schemas.METADATA_MAPPING),
        (keys.INDEX_TRANSCRIPTIONS, schemas.TRANSCRIPTIONS_MAPPING),
    )
