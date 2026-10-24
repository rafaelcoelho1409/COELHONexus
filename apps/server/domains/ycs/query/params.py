"""ycs/query — limits, app/backend identifiers, app→namespace map.

Per docs/CODE-CONVENTIONS.md §2 — loose scalars + identifier constants
live here, not in `service.py` (where the I/O orchestration lives).

The `APP_BACKENDS` map is the single source of truth for which (app,
backend) pairs are queryable. Today: DD has no presence in ES / Qdrant /
Neo4j (writes only to MinIO + Postgres), so DD is registered as
unsupported in every backend — endpoints return an empty result + the
unsupported flag rather than 404, so the UI can keep the tab visible
and grey-out the chip."""
from __future__ import annotations

from . import entities


# Result-set sizing. `MAX_LIMIT` is the hard ceiling — clamps any
# client-supplied `limit` so a stray `?limit=10000` can't fan out into a
# multi-megabyte ES + Qdrant + Neo4j payload.
DEFAULT_LIMIT: int = 20
MAX_LIMIT:     int = 100

# Per-hit text snippet cap. The raw `Document.text` / transcript `content`
# fields can be tens of kilobytes; we truncate at projection time so the
# JSON response stays bounded.
SNIPPET_CHARS: int = 600


# Identifier strings — kept here, not as inlined literals scattered
# through schemas / service / router. Rename in one place → propagates.
APP_DD:  str = "dd"
APP_YCS: str = "ycs"
APP_RR:  str = "rr"

BACKEND_ES:     str = "elasticsearch"
BACKEND_QDRANT: str = "qdrant"
BACKEND_NEO4J:  str = "neo4j"

APPS:     tuple[str, ...] = (APP_DD, APP_YCS, APP_RR)
BACKENDS: tuple[str, ...] = (BACKEND_ES, BACKEND_QDRANT, BACKEND_NEO4J)


# Source of truth — the 3x3 (app x backend) matrix. Service + router
# both read from here so adding RR-to-ES later is a one-line change.
APP_BACKENDS: dict[str, dict[str, entities.AppNamespace]] = {
    APP_DD: {
        BACKEND_ES:     entities.AppNamespace(available = False),
        BACKEND_QDRANT: entities.AppNamespace(available = False),
        BACKEND_NEO4J:  entities.AppNamespace(available = False),
    },
    APP_YCS: {
        BACKEND_ES: entities.AppNamespace(
            available = True,
            label     = "YCS · metadata + transcripts",
            target    = "coelhonexus-youtube-metadata,coelhonexus-youtube-transcriptions",
        ),
        BACKEND_QDRANT: entities.AppNamespace(
            available = True,
            label     = "YCS · transcript chunks",
            target    = "youtube-transcripts",
        ),
        BACKEND_NEO4J: entities.AppNamespace(
            available = True,
            label     = "YCS · entities + videos",
            target    = "__Entity__,Document,Video,Channel",
        ),
    },
    APP_RR: {
        BACKEND_ES:     entities.AppNamespace(available = False),
        BACKEND_QDRANT: entities.AppNamespace(
            available = True,
            label     = "RR · paper abstracts",
            target    = "radar_papers",
        ),
        BACKEND_NEO4J: entities.AppNamespace(
            available = True,
            label     = "RR · papers + authors + concepts",
            target    = "Paper,Author,Concept,Source",
        ),
    },
}


# Raw-DSL read-only safety guard tunables (`domain.py`'s
# assert_cypher_readonly / parse_es_body / parse_qdrant_body).

# Match a write-keyword as a WHOLE TOKEN (word boundaries) outside of
# string literals. Matched in lowercase against a literal-stripped copy
# of the query so a sneaky `"CREATE ..."` inside a string property doesn't
# graph-projection procedures that mutate state.
CYPHER_WRITE_KEYWORDS: tuple[str, ...] = (
    "create", "merge", "delete", "set", "remove",
    "drop", "load", "foreach", "detach",
)

ES_MAX_SIZE = 200

QDRANT_READ_OPS: tuple[str, ...] = ("search", "scroll", "query_points", "count")
QDRANT_MAX_LIMIT = 200

# Qdrant payload field shape — must match `domains.ycs.ingestion.domain
# .build_payload`. Keep this list in sync if the writer changes.
QDRANT_EXPECTED_PAYLOAD_KEYS = (
    "content",
    "video_id",
    "chunk_index",
    "total_chunks",
    "title",
    "channel",
    "channel_id",
    "lang",
    "upload_date",
    "webpage_url",
    "content_hash",
)


# Schema-cache TTL — declared floor is static, live overlay refreshes cheaply.
SCHEMA_TTL_S = 300
