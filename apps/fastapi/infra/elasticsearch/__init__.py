"""infra/elasticsearch — shared async ES client + YCS index bootstrap.

Single consumer today (YCS metadata + transcripts), but it lives at
the infra layer so future DD code can reuse the client.

Mirror of `infra/qdrant/` shape. See `docs/CODE-CONVENTIONS.md` §8 +
`docs/YCS-PORT-PLAN-2026-06-06.md` Wave 2."""
from .params import (
    ES_HOST,
    ES_USERNAME,
    INDEX_METADATA,
    INDEX_TRANSCRIPTIONS,
)
from .service import close_es, ensure_indexes, get_es


__all__ = [
    "ES_HOST",
    "ES_USERNAME",
    "INDEX_METADATA",
    "INDEX_TRANSCRIPTIONS",
    "close_es",
    "ensure_indexes",
    "get_es",
]
