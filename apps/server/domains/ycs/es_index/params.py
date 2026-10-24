"""ycs/es_index — bulk-write tunables (refresh policy + status codes)."""
from __future__ import annotations


# `refresh=True` blocks the bulk call until the new docs are searchable.
# Deprecated chose this so the next ES query (retriever or downstream
# `fetch_transcripts_from_es`) sees the data immediately.
BULK_REFRESH = True

# ES item "status" codes meaning "indexed OK".
INDEXED_STATUSES: frozenset[int] = frozenset({200, 201})
