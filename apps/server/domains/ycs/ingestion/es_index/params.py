"""ycs/ingestion/es_index — bulk-write tunables (refresh policy + status codes)."""
from __future__ import annotations


# Wait for the normal refresh cycle instead of forcing an immediate refresh.
# The transcripts writer sends canonical transcript and passage documents in
# one bulk request, so both projections become searchable together.
BULK_REFRESH = "wait_for"

# ES item "status" codes meaning "indexed OK".
INDEXED_STATUSES: frozenset[int] = frozenset({200, 201})
