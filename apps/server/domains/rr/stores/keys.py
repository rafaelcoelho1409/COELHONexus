"""stores keys — deterministic ID namespaces + key builders."""
from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5


# Point IDs — deterministic UUIDs from arxiv_id so re-upserts overwrite in
# place (Qdrant requires integer or UUID point ids; arxiv_id is a string).
QDRANT_POINT_NAMESPACE = uuid5(NAMESPACE_URL, "rr.point.arxiv")


def point_id(arxiv_id: str) -> str:
    """Deterministic UUIDv5 for the arxiv_id → repeat upserts are
    idempotent at the Qdrant layer."""
    return str(uuid5(QDRANT_POINT_NAMESPACE, arxiv_id))
