"""ycs/query — declared (writer-side) schemas.

The "floor" of the two-layer schema (live + declared) the AI prompt
grounds on. Why we need a declared floor:

  · ES   — mappings come back from `GET _mapping` even on an empty
           index, so the floor is mostly redundant there. But if the
           cluster is briefly unreachable we still want the AI to
           know the field shapes.
  · Qdrant — `get_collection` returns the declared payload-index
           keys, but the FULL payload shape (unindexed fields like
           `content`, `chunk_index`) is only observable via `scroll`.
           Empty collection → no observable keys → AI flies blind.
  · Neo4j — `db.labels()` / `db.relationshipTypes()` / `db.schema.*`
           only return what EXISTS. An empty graph returns nothing.
           Without a declared floor the AI has no shape to ground on
           at day-zero.

Sources of truth (so the declared schema stays in sync with what
gets WRITTEN to each store):

  ES     → infra/elasticsearch/mappings.py  (METADATA_MAPPING,
                                              TRANSCRIPTIONS_MAPPING)
  Qdrant → domains/ycs/ingestion/domain.py  (build_payload — the
                                              writer's payload shape)
  Neo4j  → domains/ycs/graph_builder/*       + the entity-merger Cypher
           (build_video_metadata_graph creates Video+Channel+BELONGS_TO;
            LLMGraphTransformer creates __Entity__ + Document with
            the .video_id tag).

Update this file when the writer shape changes."""
from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------- #
# Elasticsearch
# ---------------------------------------------------------------------- #
def declared_es_schema() -> dict[str, Any]:
    """Pull the canonical mappings out of `infra/elasticsearch/mappings.py`
    so we never drift from what `ensure_indexes()` actually creates."""
    from infra.elasticsearch.mappings import (
        METADATA_MAPPING,
        TRANSCRIPTIONS_MAPPING,
    )
    from infra.elasticsearch.params import (
        INDEX_METADATA,
        INDEX_TRANSCRIPTIONS,
    )
    return {
        "indices": {
            INDEX_METADATA: {
                "mappings":     METADATA_MAPPING["mappings"],
                "doc_count":    0,
                "samples":      [],
                "field_values": {},
            },
            INDEX_TRANSCRIPTIONS: {
                "mappings":     TRANSCRIPTIONS_MAPPING["mappings"],
                "doc_count":    0,
                "samples":      [],
                "field_values": {},
            },
        },
    }


# ---------------------------------------------------------------------- #
# Qdrant
# ---------------------------------------------------------------------- #
# Payload field shape — must match `domains/ycs/ingestion/domain.py:
# build_payload`. Keep this list in sync if the writer changes.
_QDRANT_EXPECTED_PAYLOAD_KEYS = (
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


def declared_qdrant_schema() -> dict[str, Any]:
    """YCS Qdrant collection — vectors are NAMED (`dense` + `sparse`)
    so the AI knows to use `("dense", vector)` tuples on raw search.
    Payload shape from `build_payload` (the writer in ingestion/
    domain.py).

    `text_indexed_fields` is empty because the YCS ingestion bootstrap
    only creates KEYWORD indexes on `channel_id` + `video_id`. Without
    a TEXT index, Qdrant's `match: {text: ...}` operator fails at the
    Pydantic layer ("Extra inputs not permitted"). The prompt
    renderer surfaces this so the LLM stops generating bogus
    `match_text` / `match: {text: ...}` filters."""
    from domains.ycs.ingestion.params import QDRANT_COLLECTION
    return {
        "collections": [{
            "name":           QDRANT_COLLECTION,
            "points_count":   0,
            "vectors_config": {
                "dense":  {"size": 2048, "distance": "Cosine"},
                "sparse": {"distance": "BM25 (sparse)"},
            },
            "payload_schema": {
                # `channel_id` + `video_id` are the indexed keys
                # ingestion sets up at bootstrap; everything else is
                # in the payload but unindexed.
                "channel_id": {"data_type": "keyword"},
                "video_id":   {"data_type": "keyword"},
            },
            "observed_payload_keys": list(_QDRANT_EXPECTED_PAYLOAD_KEYS),
            # Fields with a TEXT payload index — required for
            # `match: {text: ...}`. YCS ingestion creates none today;
            # for full-text search on transcripts the user should
            # switch to the Elasticsearch backend.
            "text_indexed_fields":   [],
            "samples":               [],
        }],
    }


# ---------------------------------------------------------------------- #
# Neo4j
# ---------------------------------------------------------------------- #
def declared_neo4j_schema() -> dict[str, Any]:
    """YCS graph shape — what the writers actually create:

      · LLMGraphTransformer  → (:__Entity__) + (:Document) +
                               (Document)-[:MENTIONS]->(__Entity__)
      · build_video_metadata_graph → (:Video) + (:Channel) +
                               (Video)-[:BELONGS_TO]->(Channel)

    Property names confirmed by reading `graph_builder/service.py`
    (lines 804-823) + the retriever's Cypher (`retriever/neo4j.py`)."""
    return {
        "labels": ["__Entity__", "Document", "Video", "Channel"],
        "relationship_types": ["MENTIONS", "BELONGS_TO"],
        "node_properties": {
            "Video": [
                {"name": "id",          "types": ["String"]},
                {"name": "title",       "types": ["String"]},
                {"name": "channel_id",  "types": ["String"]},
                {"name": "upload_date", "types": ["String"]},
                {"name": "webpage_url", "types": ["String"]},
            ],
            "Channel": [
                {"name": "id",   "types": ["String"]},
                {"name": "name", "types": ["String"]},
            ],
            "Document": [
                # LangChain's GraphDocument writer keeps the doc's
                # `text` and our YCS code stamps `.video_id`
                # explicitly so re-ingest can skip processed videos.
                {"name": "video_id", "types": ["String"]},
                {"name": "text",     "types": ["String"]},
            ],
            "__Entity__": [
                {"name": "id",          "types": ["String"]},
                {"name": "description", "types": ["String"]},
            ],
        },
        # Declared relationships — `count=None` flag = "we know this
        # pattern exists in the writer code; we just don't know how
        # many instances are live". Live discovery fills in the
        # count + may add patterns the writer didn't predict (e.g.
        # the LLMGraphTransformer invents inter-entity rels with
        # type names derived from the LLM's output).
        "relationship_patterns": [
            {"src": "Document", "rel": "MENTIONS",   "dst": "__Entity__", "count": None, "declared": True},
            {"src": "Video",    "rel": "BELONGS_TO", "dst": "Channel",    "count": None, "declared": True},
        ],
        "node_samples": {},
    }
