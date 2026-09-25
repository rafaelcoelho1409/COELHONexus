"""RR resource tunables — per docs/CODE-CONVENTIONS.md §2.

Loose scalars that don't form a frozen-dataclass group (each bounds a
different store, tuned independently). Timeouts are wall-clock backstops:
a store hiccup must degrade to an error envelope, never hang the worker.
"""
from __future__ import annotations


# Postgres connect timeout for the latest-done-scan lookup.
PG_CONNECT_TIMEOUT_S: float = 5

# MinIO connect/read timeouts for the digest.json fetch (single attempt,
# no retry — a missing/slow object is a normal miss, not an outage).
MINIO_CONNECT_TIMEOUT_S: float = 3
MINIO_READ_TIMEOUT_S: float = 10

# Neo4j subgraph query cap (covers session.run + single record fetch).
NEO4J_QUERY_TIMEOUT_S: float = 30

# Longest concept name worth querying — Neo4j MERGE was case-preserving
# but names are short slugs; anything longer is never a real concept.
CONCEPT_NAME_MAX_CHARS: int = 200
