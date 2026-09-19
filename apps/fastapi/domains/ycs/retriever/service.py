"""ycs/retriever — ES + Qdrant hybrid + Neo4j retrievers + the
multi-source orchestrator.

Imperative Shell (`docs/CODE-CONVENTIONS.md` §4): each class here is an
I/O adapter around one backend, all sharing the same
`retrieve(query, channel_ids)` interface so `SmartRetriever` can fan out
uniformly (Strategy pattern — one file per adapter would just re-derive
this same shared interface with extra import indirection, so per
docs/CODE-CONVENTIONS.md §8's strict-merge policy these four adapters
live together in one `service.py`, same role, same file). Pure dedup
logic lives in `domain.py`.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable

from elasticsearch import AsyncElasticsearch
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_neo4j import Neo4jGraph
from langchain_qdrant import FastEmbedSparse
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchAny,
    Prefetch,
    SearchParams,
    SparseVector,
)

import domains
from infra.elasticsearch import INDEX_METADATA, INDEX_TRANSCRIPTIONS

from . import domain, params, prompts, schemas


logger = logging.getLogger(__name__)


# Elasticsearch — full-text retriever (Phase 1)
class ElasticsearchRetriever:
    """Full-text search over the YCS transcripts index. Same
    `retrieve(query, channel_ids)` interface as the other three
    retrievers so the SmartRetriever can fan out uniformly."""

    def __init__(
        self,
        es_client: AsyncElasticsearch,
        top_k: int = params.ES_DEFAULT_TOP_K,
    ) -> None:
        self.es = es_client
        self.top_k = top_k

    async def retrieve(
        self, query: str, channel_ids: list[str] | None = None,
    ) -> list[Document]:
        # are kept separate per deprecated (vs always-wrapped) so the
        # ES query planner gets the simpler form on the common path.
        es_query: dict
        if channel_ids:
            es_query = {
                "bool": {
                    "must": {
                        "multi_match": {
                            "query":  query,
                            "fields": ["content"],
                            "type":   "best_fields",
                        },
                    },
                    "filter": {"terms": {"channel_id": channel_ids}},
                },
            }
        else:
            es_query = {
                "multi_match": {
                    "query":  query,
                    "fields": ["content"],
                    "type":   "best_fields",
                },
            }

        with domains.ycs.runtime.observability.spans.es_search_span(
            index                = INDEX_TRANSCRIPTIONS,
            top_k                = self.top_k,
            channel_filter_count = len(channel_ids) if channel_ids else 0,
        ):
            results = await self.es.search(
                index = INDEX_TRANSCRIPTIONS,
                query = es_query,
                size = self.top_k,
                _source = ["video_id", "lang", "content", "channel_id"],
            )
        hits = results["hits"]["hits"]
        if not hits:
            return []

        video_ids = list({h["_source"]["video_id"] for h in hits})
        metadata_map = await self._fetch_metadata(video_ids)

        documents: list[Document] = []
        for hit in hits:
            src = hit["_source"]
            video_id = src["video_id"]
            meta = metadata_map.get(video_id, {})
            documents.append(Document(
                page_content = src.get("content", ""),
                metadata = {
                    "video_id":    video_id,
                    "lang":        src.get("lang", "en"),
                    "title":       meta.get("title", ""),
                    "channel":     meta.get("channel", ""),
                    "channel_id":  src.get("channel_id", ""),
                    "upload_date": meta.get("upload_date", ""),
                    "webpage_url": meta.get("webpage_url", ""),
                    "score":       hit["_score"],
                    "source":      "elasticsearch",
                },
            ))
        return documents

    async def _fetch_metadata(self, video_ids: list[str]) -> dict:
        """Secondary fetch from the metadata index — the transcripts
        index only carries video_id (denormalized) + lang + content +
        channel_id, so titles / channels / upload_date / urls live in
        the separate metadata index.

        2026-09-16 fix: delegates to `domains.ycs.ingestion.service
        .fetch_metadata_from_es` instead of querying `INDEX_METADATA`
        by the raw ids directly — a split video's transcript hits
        carry the PARTITION id (`"XYZ#p3"`), which has no metadata doc
        of its own (yt-dlp metadata is written once per real video).
        The old direct `{"ids": {"values": video_ids}}` query silently
        came back empty for every partition hit, showing "(untitled)"
        / "Unknown channel" on every ES-sourced citation for a split
        video. Same shape in, same shape out — the shared helper
        already does the partition→parent resolution (see its
        docstring)."""
        if not video_ids:
            return {}
        with domains.ycs.runtime.observability.spans.es_search_span(
            index                = INDEX_METADATA,
            top_k                = len(video_ids),
            operation            = "metadata_lookup",
        ):
            return await domains.ycs.ingestion.service.fetch_metadata_from_es(self.es, video_ids)


# Qdrant — dense + sparse RRF hybrid retriever (Phase 2)
class QdrantHybridRetriever:
    """Dense (the Settings-page-configured embedding endpoint — see
    `domains/llm/embeddings`) + Sparse (`FastEmbedSparse("Qdrant/bm25")`)
    fused in one query. Replaces ES full-text on the hot path — dense
    catches semantic matches, sparse catches keyword matches, RRF blends
    the two ranked lists.

    Imperative Shell: ONE Qdrant call with `Prefetch` per vector type +
    `FusionQuery(fusion=Fusion.RRF)`. Qdrant internally fuses dense +
    sparse scores using Reciprocal Rank Fusion — no manual RRF code on
    our side."""

    def __init__(
        self,
        qdrant: AsyncQdrantClient,
        dense_embeddings: Embeddings,
        sparse_embeddings: FastEmbedSparse,
        top_k: int = params.QDRANT_DEFAULT_TOP_K,
        es_client: AsyncElasticsearch | None = None,
    ) -> None:
        self.qdrant = qdrant
        self.dense_embeddings = dense_embeddings
        self.sparse_embeddings = sparse_embeddings
        self.top_k = top_k
        # 2026-09-16: optional — only used to backfill title/channel on
        # points whose PAYLOAD has them stored empty (see `retrieve()`'s
        # post-fetch patch below). `None` (e.g. tests) just skips that
        # step; nothing else here depends on it.
        self.es_client = es_client

    async def retrieve(
        self, query: str, channel_ids: list[str] | None = None,
    ) -> list[Document]:
        # Two query-side vectors, one Qdrant call. Each Prefetch over-
        # fetches at `top_k * 2` so RRF has headroom to reorder before
        # the final `limit = top_k` truncation.
        dense_vector = await self.dense_embeddings.aembed_query(query)
        sparse_vector = self.sparse_embeddings.embed_query(query)

        # PRE-filter (not post-filter) — applied during the vector
        # search itself per Qdrant best practice.
        query_filter: Filter | None = None
        if channel_ids:
            query_filter = Filter(
                must = [
                    FieldCondition(
                        key = "channel_id",
                        match = MatchAny(any = channel_ids),
                    ),
                ],
            )

        prefetch: list[Prefetch] = [
            Prefetch(
                query =  dense_vector,
                using =  "dense",
                limit =  self.top_k * 2,
                filter = query_filter,
            ),
        ]
        if sparse_vector is not None:
            prefetch.append(
                Prefetch(
                    query = SparseVector(
                        indices = sparse_vector.indices,
                        values =  sparse_vector.values,
                    ),
                    using =  "sparse",
                    limit =  self.top_k * 2,
                    filter = query_filter,
                ),
            )

        with domains.ycs.runtime.observability.spans.qdrant_search_span(
            collection           = domains.ycs.ingestion.params.QDRANT_COLLECTION,
            top_k                = self.top_k,
            channel_filter_count = len(channel_ids) if channel_ids else 0,
        ):
            results = await self.qdrant.query_points(
                collection_name = domains.ycs.ingestion.params.QDRANT_COLLECTION,
                prefetch = prefetch,
                query = FusionQuery(fusion = Fusion.RRF),
                limit = self.top_k,
                with_payload = True,
                # 2026-09-15 (SOTA follow-up): explicit search-time
                # exploration. Server default `ef == ef_construct`
                # couples search to build quality; 128 is the
                # benchmarked probe value (recall climbs, latency
                # flat at our scale). No-op today: 72 points sit
                # under full_scan_threshold, so this is exact scan —
                # it takes effect automatically as the corpus grows
                # past 10k points toward HNSW. (1.16.1 names this
                # `search_params`; per-prefetch `params` left default.)
                search_params = SearchParams(hnsw_ef = 128),
            )

        documents: list[Document] = []
        for point in results.points:
            payload = point.payload or {}
            documents.append(Document(
                page_content = payload.get("content", ""),
                metadata = {
                    "video_id":     payload.get("video_id", ""),
                    "chunk_index":  payload.get("chunk_index", 0),
                    "title":        payload.get("title", ""),
                    "channel":      payload.get("channel", ""),
                    "channel_id":   payload.get("channel_id", ""),
                    "upload_date":  payload.get("upload_date", ""),
                    "webpage_url":  payload.get("webpage_url", ""),
                    "lang":         payload.get("lang", "en"),
                    "score":        point.score,
                    "source":       "qdrant_hybrid",
                },
            ))
        await self._backfill_missing_metadata(documents)
        return documents

    async def _backfill_missing_metadata(
        self, documents: list[Document],
    ) -> None:
        """2026-09-16 fix: patches `title`/`channel` in place for any
        document whose Qdrant PAYLOAD has them stored empty — live-
        verified on the running cluster: split-video chunk points
        (`video_id="XYZ#p3"`) carry `title=""`/`channel=""` baked in
        from an ingestion run that predates `fetch_metadata_from_es`'s
        partition→parent resolution (their content hasn't changed
        since, so the content-hash-skip in `ingest_to_qdrant` means a
        normal re-ingest never re-embeds/re-upserts them — the stale
        payload persists indefinitely). Re-deriving at RETRIEVAL time
        instead of only fixing future writes means every ALREADY-
        INGESTED video gets correct citations immediately, no backfill
        migration needed, and it's a no-op the instant a point does
        carry real values.

        No-op when `es_client` wasn't wired in, or nothing's missing —
        the common case costs one dict comprehension over an already-
        small `documents` list."""
        if self.es_client is None:
            return
        missing_ids = [
            doc.metadata["video_id"]
            for doc in documents
            if doc.metadata.get("video_id")
            and not doc.metadata.get("title")
            and not doc.metadata.get("channel")
        ]
        if not missing_ids:
            return
        fetched = await domains.ycs.ingestion.service.fetch_metadata_from_es(self.es_client, missing_ids)
        for doc in documents:
            vid = doc.metadata.get("video_id")
            meta = fetched.get(vid)
            if not meta:
                continue
            doc.metadata["title"] = meta.get("title") or doc.metadata["title"]
            doc.metadata["channel"] = meta.get("channel") or doc.metadata["channel"]
            doc.metadata["upload_date"] = (
                meta.get("upload_date") or doc.metadata["upload_date"]
            )
            doc.metadata["webpage_url"] = (
                meta.get("webpage_url") or doc.metadata["webpage_url"]
            )


# Neo4j — graph-traversal retriever (Phase 3)
class _Neo4jExtractionError(Exception):
    """LLM entity extraction failed (provider error) — distinct from
    'no entities found', so callers can break the arm for the rest of
    the request instead of re-burning 60s+ per rewrite round."""


# Live entity inventory (surface forms) — process-level cache with TTL,
# shared by every request on this worker. One indexed scan per worker
# per TTL window, not per query.
_inventory_cache: dict = {"ts": 0.0, "items": []}


def fetch_entity_inventory(graph: Neo4jGraph) -> list[str]:
    """Distinct `__Entity__` surface forms in this YCS graph, for the
    extraction prompt's prefer-exact-spellings hint (2026-09-16).

    List-valued ids (LLMGraphTransformer stores ~28% that way) resolve
    to their head element; empties dropped; capped at
    `INVENTORY_MAX_IDS`. Any Cypher failure → [] (extraction proceeds
    unhinted — the exact->CONTAINS tiers are the real safety net, this
    hint only steers spelling)."""
    now = time.monotonic()
    if now - _inventory_cache["ts"] < params.INVENTORY_TTL_S:
        return list(_inventory_cache["items"])
    try:
        rows = graph.query(
            f"MATCH (e:__Entity__:{domains.ycs.graph_builder.params.SOURCE_LABEL}) "
            "WHERE e.id IS NOT NULL "
            "WITH CASE WHEN valueType(e.id) STARTS WITH 'LIST' "
            "THEN head(e.id) ELSE e.id END AS eid "
            "WHERE eid IS NOT NULL AND trim(toString(eid)) <> '' "
            "RETURN DISTINCT toString(eid) AS name "
            "LIMIT $limit",
            params = {"limit": params.INVENTORY_MAX_IDS},
        )
        items = [r["name"] for r in rows if r.get("name")]
        _inventory_cache["ts"] = now
        _inventory_cache["items"] = items
        return list(items)
    except Exception as e:
        logger.warning(
            f"[ycs:neo4j] inventory fetch failed (unhinted extraction): "
            f"{type(e).__name__}: {str(e)[:150]}"
        )
        return list(_inventory_cache["items"])


# Cypher fragments — kept at module scope (not `params.py`) because
# they're tightly coupled to the traversal queries below and never
# imported elsewhere.

# Name of the trigram TEXT index backing the CONTAINS fallback tier
# (see `_traverse_graph`). Created lazily, once per worker process.
_TEXT_INDEX_NAME = "ycs_entity_id_text"
_text_index_ready = False


def _ensure_entity_text_index(graph: Neo4jGraph) -> None:
    """Best-effort `CREATE TEXT INDEX ... IF NOT EXISTS` on `:__Entity__(id)`.

    2026-09-16: the CONTAINS fallback tier is correct without any index
    (label scan + filter over a few hundred nodes is milliseconds), but a
    trigram TEXT index lets the planner turn it into an index probe as the
    graph grows. Runs once per worker process; any failure is swallowed —
    the fallback query runs identically with or without it."""
    global _text_index_ready
    if _text_index_ready:
        return
    try:
        graph.query(
            f"CREATE TEXT INDEX {_TEXT_INDEX_NAME} IF NOT EXISTS "
            f"FOR (e:__Entity__) ON (e.id)"
        )
        _text_index_ready = True
    except Exception as e:
        logger.warning(
            f"[ycs:neo4j] text-index ensure failed "
            f"(fallback still runs unindexed): "
            f"{type(e).__name__}: {str(e)[:150]}"
        )

# LLMGraphTransformer stores ~28% of entity IDs as Cypher LISTs (e.g.
# `['Dubai', 'UAE']`) — the `valueType` check normalizes them to their
# first element. Neo4j-5+ syntax.
_NORMALIZE_ID = (
    'CASE WHEN valueType(e.id) STARTS WITH "LIST" '
    'THEN head(e.id) ELSE e.id END'
)
_NORMALIZE_NEIGHBOR_ID = (
    'CASE WHEN valueType(neighbor.id) STARTS WITH "LIST" '
    'THEN head(neighbor.id) ELSE neighbor.id END'
)


class Neo4jRetriever:
    """Entity-aware retrieval. Excels at relationship queries that
    vector search can't handle ("what topics do channels X and Y both
    discuss"); inferior to dense for pure-content queries. The
    `SmartRetriever` runs both arms in parallel.

    Two-step pipeline:
      1. LLM extracts entity names from the user question
         (`prompts.ENTITY_EXTRACTION_PROMPT` → `schemas.ExtractedEntities`).
      2. Cypher traversal finds:
           (a) Documents/Videos DIRECTLY linked to those entities, and
           (b) one-hop neighbors (entities connected to the matched ones).
         Both branches UNION'd into a single result set, deduplicated."""

    def __init__(
        self,
        neo4j_graph: Neo4jGraph,
        llm: Any,
        top_k: int = params.NEO4J_DEFAULT_TOP_K,
    ) -> None:
        self.graph = neo4j_graph
        self.llm = llm
        self.top_k = top_k

    async def retrieve(
        self, query: str, channel_ids: list[str] | None = None,
    ) -> tuple[list[Document], str]:
        """Returns (documents, status) where status is one of:
        "ok" (docs found), "no_entities" (nothing to look up — not a
        failure), "extraction_failed" (LLM error — provider-side, likely
        to persist across rewrite rounds of the same request),
        "traversal_failed" (Cypher error). The status lets SmartRetriever
        tell a dead arm from an empty one (2026-09-15 per-request arm
        breaker)."""
        try:
            entities = await self._extract_entities(query)
        except _Neo4jExtractionError:
            return [], "extraction_failed"
        if not entities:
            return [], "no_entities"
        try:
            documents = self._traverse_graph(entities, channel_ids)
        except Exception as e:
            logger.warning(
                f"[ycs:neo4j] traversal failed: "
                f"{type(e).__name__}: {str(e)[:200]}"
            )
            return [], "traversal_failed"
        return documents[:self.top_k], ("ok" if documents else "no_entities")

    async def _extract_entities(self, query: str) -> list[str]:
        """Structured-output LLM call. Failures degrade to `[]` so the
        SmartRetriever's other arms still produce results.

        2026-09-15: wrapped in `resilient_ainvoke` (30s × 2, transient
        only) — this was the last unguarded LLM call on the retrieval
        path; a hang here stalled the whole gather with no bound."""
        # default `method="json_schema"` — see
        # `rag/standard/nodes/hallucination/node.py` for the rationale.
        chain = prompts.ENTITY_EXTRACTION_PROMPT | self.llm.with_structured_output(
            schemas.ExtractedEntities,
        )
        try:
            domains.ycs.runtime.llm_counter.service.set_node(node = "neo4j_entity_extract")
            # Prefer-exact-spellings hint (2026-09-16): real surface forms
            # from the graph so the LLM emits names that exist. Best
            # effort — empty when the graph is unreachable/empty, and
            # phrased as a preference, never a closed allowlist (new
            # topics must stay answerable).
            known = fetch_entity_inventory(self.graph)
            hint = (
                "Known entities in the knowledge graph — prefer these "
                "exact spellings when relevant, but you may also emit "
                "names not listed:\n"
                + "\n".join(f"- {name}" for name in known)
                if known else ""
            )
            result = await domains.ycs.rag.service.resilient_ainvoke(
                chain,
                {"query": query, "known_entities": hint},
                operation    = "neo4j_entity_extract",
                timeout_s    = 30.0,
                max_attempts = 2,
            )
            logger.info(f"[ycs:neo4j] extracted entities: {result.entities}")
            return result.entities
        except Exception as e:
            logger.warning(
                f"[ycs:neo4j] entity extraction failed: "
                f"{type(e).__name__}: {str(e)[:200]}"
            )
            raise _Neo4jExtractionError(str(e)[:200]) from e

    def _traverse_graph(
        self, entities: list[str], channel_ids: list[str] | None = None,
    ) -> list[Document]:
        """Two-tier entity lookup (2026-09-16):

        Tier 1 — exact: `toLower(eid) IN $entities` (unchanged legacy
        behavior, cheapest when the extractor nails the surface form).
        Tier 2 — substring fallback, only when tier 1 returns nothing:
        `any(pat IN $entities WHERE toLower(eid) CONTAINS pat)`, so
        "offshore strategies" still finds a node id "offshore" instead of
        yielding zero docs. Short patterns (<3 chars) are dropped from
        tier 2 — a 1-2 char CONTAINS matches nearly everything and would
        trade a clean miss for noise. First non-empty tier wins; an empty
        tier 2 returns [] exactly as before (caller maps it to
        "no_entities", never an error)."""
        if not entities:
            return []
        entity_patterns = [e.lower() for e in entities]
        documents = self._lookup(entity_patterns, channel_ids, fuzzy = False)
        if documents:
            return documents
        pats = [p for p in entity_patterns if len(p) >= 3]
        if not pats:
            return []
        logger.info(
            f"[ycs:neo4j] exact matched 0 for {entity_patterns!r} — "
            f"retrying CONTAINS fallback"
        )
        _ensure_entity_text_index(self.graph)
        return self._lookup(pats, channel_ids, fuzzy = True)

    def _lookup(
        self,
        entity_patterns: list[str],
        channel_ids: list[str] | None,
        *,
        fuzzy: bool,
    ) -> list[Document]:
        """Cypher UNION query:
          1. Direct entity match
          2. One-hop neighbors:   `MATCH (e)-[r]-(neighbor:__Entity__)`
        Both branches optionally JOIN against `:Channel` via `BELONGS_TO`
        when `channel_ids` is supplied. `fuzzy` switches the entity-match
        predicate from exact-`IN` to substring-`CONTAINS` (tier 2)."""

        channel_filter = ""
        channel_filter_onehop = ""
        if channel_ids:
            channel_filter = (
                "OPTIONAL MATCH (v)-[:BELONGS_TO]->(ch:Channel) "
                "WHERE ch.id IN $channel_ids "
                "WITH e, eid, doc, v, r, r2 "
                "WHERE v IS NULL OR ch IS NOT NULL "
            )
            channel_filter_onehop = (
                "OPTIONAL MATCH (v)-[:BELONGS_TO]->(ch:Channel) "
                "WHERE ch.id IN $channel_ids "
                "WITH neighbor, doc, v, r, e, eid, nid "
                "WHERE v IS NULL OR ch IS NOT NULL "
            )

        cypher_params: dict = {
            "entities": entity_patterns,
            "limit":    self.top_k * 2,
        }
        if channel_ids:
            cypher_params["channel_ids"] = channel_ids
        # Tier predicate: exact-`IN` normally, substring-`CONTAINS` on the
        # fallback pass. `eid` is bound by the preceding WITH in both UNION
        # branches, so one fragment serves both sites.
        entity_pred = (
            "WHERE any(_fpat IN $entities "
            "WHERE toLower(toString(eid)) CONTAINS _fpat) "
            if fuzzy else
            "WHERE toLower(toString(eid)) IN $entities "
        )

        try:
            with domains.ycs.runtime.observability.spans.neo4j_query_span(
                operation         = "entity_lookup",
                statement_summary = "MATCH __Entity__ + Document/Video + UNION 1-hop",
            ):
                results = self.graph.query(
                # 1) Direct entity match + source documents.
                f"MATCH (e:__Entity__:{domains.ycs.graph_builder.params.SOURCE_LABEL}) "
                "WHERE e.id IS NOT NULL "
                f"WITH e, ({_NORMALIZE_ID}) AS eid "
                f"{entity_pred}"
                "OPTIONAL MATCH (e)<-[r]-(doc:Document) "
                "OPTIONAL MATCH (e)<-[r2]-(v:Video) "
                "WITH e, eid, doc, v, r, r2 "
                + channel_filter +
                "RETURN "
                "  COALESCE(doc.text, toString(eid) + ': ' + COALESCE(e.description, '')) AS content, "
                "  COALESCE(v.id, '') AS video_id, "
                "  COALESCE(v.title, '') AS title, "
                "  COALESCE(v.webpage_url, '') AS webpage_url, "
                "  toString(eid) AS entity_id, "
                "  type(r) AS relationship, "
                "  'direct' AS match_type "
                "LIMIT $limit "
                "UNION "
                # 2) One-hop neighbors: entities connected to matched ones.
                f"MATCH (e:__Entity__:{domains.ycs.graph_builder.params.SOURCE_LABEL}) "
                "WHERE e.id IS NOT NULL "
                f"WITH e, ({_NORMALIZE_ID}) AS eid "
                f"{entity_pred}"
                f"MATCH (e)-[r]-(neighbor:__Entity__:{domains.ycs.graph_builder.params.SOURCE_LABEL}) "
                "WHERE e <> neighbor "
                "OPTIONAL MATCH (neighbor)<--(doc:Document) "
                "OPTIONAL MATCH (neighbor)<--(v:Video) "
                f"WITH neighbor, doc, v, r, e, eid, ({_NORMALIZE_NEIGHBOR_ID}) AS nid "
                + channel_filter_onehop +
                "RETURN "
                "  COALESCE(doc.text, toString(nid) + ' (' + type(r) + ' ' + toString(eid) + ')') AS content, "
                "  COALESCE(v.id, '') AS video_id, "
                "  COALESCE(v.title, '') AS title, "
                "  COALESCE(v.webpage_url, '') AS webpage_url, "
                "  toString(nid) AS entity_id, "
                "  type(r) AS relationship, "
                "  'one_hop' AS match_type "
                "LIMIT $limit",
                params = cypher_params,
                )
        except Exception as e:
            logger.warning(
                f"[ycs:neo4j] Cypher query failed: "
                f"{type(e).__name__}: {str(e)[:200]}"
            )
            return []

        seen_content: set[str] = set()
        documents: list[Document] = []
        for row in results:
            content = row.get("content", "")
            if not content or content in seen_content:
                continue
            seen_content.add(content)
            documents.append(Document(
                page_content = content,
                metadata = {
                    "video_id":      row.get("video_id", ""),
                    "title":         row.get("title", ""),
                    "webpage_url":   row.get("webpage_url", ""),
                    "entity_id":     row.get("entity_id", ""),
                    "entity_labels": row.get("entity_labels", []),
                    "relationship":  row.get("relationship", ""),
                    "source":        "neo4j_graph",
                },
            ))
        return documents


# Multi-source orchestrator with FlashRank rerank
class SmartRetriever:
    """Multi-source retrieval with graceful degradation. The agent
    consumes only this; the three underlying retrievers are
    construction-time deps.

    Strategy (2026-09-15: 3-way parallel — was Qdrant+Neo4j with ES as
    sequential fallback):
      1. Fan out Qdrant + Neo4j + ES full-text in ONE `asyncio.gather`
      2. Merge surviving results (deduped via `domain.dedupe_documents`)
      3. FlashRank cross-encoder reranks
      4. All arms empty/failed → return [] (caller's responsibility to rewrite)

    Rationale: ES is millisecond-scale local full-text — running it inline
    costs max(arms) latency, not the sum, and keyword recall complements
    vector (Qdrant) + graph (Neo4j) on exact-match questions (titles,
    names, quoted phrases) where embeddings underperform. The old
    sequential fallback saved nothing measurable and starved those
    queries of keyword hits whenever a primary returned anything."""

    def __init__(
        self,
        es_retriever: ElasticsearchRetriever,
        qdrant_retriever: QdrantHybridRetriever | None = None,
        neo4j_retriever: Neo4jRetriever | None = None,
        use_reranker: bool = True,
        top_k: int = params.SMART_DEFAULT_TOP_K,
    ) -> None:
        self.es_retriever = es_retriever
        self.qdrant_retriever = qdrant_retriever
        self.neo4j_retriever = neo4j_retriever
        self.use_reranker = use_reranker
        self.top_k = top_k

    async def retrieve(
        self, query: str, channel_ids: list[str] | None = None,
        extra_queries: list[str] | None = None,
    ) -> list[Document]:
        with domains.ycs.runtime.observability.spans.ycs_retriever_fanout_span(top_k = self.top_k):
            docs, _ = await self._retrieve_inner(
                query, channel_ids, extra_queries, frozenset(),
            )
            return docs

    async def retrieve_detailed(
        self, query: str, channel_ids: list[str] | None = None,
        extra_queries: list[str] | None = None,
        skip_arms: frozenset[str] | None = None,
    ) -> tuple[list[Document], dict[str, str]]:
        """Like `retrieve` but honors `skip_arms` (per-request breaker
        state owned by the caller) and returns per-arm statuses:
        "ok" (docs found), "empty" (ran clean, nothing), "failed"
        (exception — provider-side), "skipped" (breaker). Neo4j reports
        its own "extraction_failed" distinctly (see its `retrieve`)."""
        with domains.ycs.runtime.observability.spans.ycs_retriever_fanout_span(top_k = self.top_k):
            return await self._retrieve_inner(
                query, channel_ids, extra_queries, skip_arms or frozenset(),
            )

    @staticmethod
    def _arm_group(task_name: str) -> str:
        """Collapse per-variant task keys (qdrant_alt1, es_alt1) back to
        their arm for breaker bookkeeping."""
        if task_name.startswith("qdrant"):
            return "qdrant"
        if task_name.startswith("es"):
            return "es"
        return task_name

    async def _retrieve_inner(
        self, query: str, channel_ids: list[str] | None = None,
        extra_queries: list[str] | None = None,
        skip_arms: frozenset[str] = frozenset(),
    ) -> tuple[list[Document], dict[str, str]]:
        # Fan out ALL THREE arms (Qdrant + Neo4j + ES) in parallel —
        # ES latency hides inside max(arms), and keyword hits complement
        # vector + graph recall on exact-match questions.
        #
        # 2026-09-15 dual-query (SemEval-2026 winner pattern — multiple
        # cheap rewrites fused, NOT HyDE pseudo-documents which
        # underperform vanilla dense): when the caller passes distinct
        # extra queries (e.g. original question + contextualize's
        # standalone rewrite), the CHEAP arms (Qdrant + ES) run each
        # query variant in the same gather — zero extra serial latency.
        # Neo4j stays single-query on the primary: its retrieval spends
        # an LLM call per query, and doubling that per retrieve is real
        # money/latency for unproven gain on the graph arm.
        queries = [query]
        for extra in extra_queries or []:
            cleaned = (extra or "").strip()
            if cleaned and cleaned.lower() not in {q.lower() for q in queries}:
                queries.append(cleaned)
        tasks: dict[str, Awaitable] = {}
        if self.qdrant_retriever and "qdrant" not in skip_arms:
            for i, q in enumerate(queries):
                key = "qdrant" if i == 0 else f"qdrant_alt{i}"
                tasks[key] = self.qdrant_retriever.retrieve(q, channel_ids)
        if self.neo4j_retriever and "neo4j" not in skip_arms:
            tasks["neo4j"] = self.neo4j_retriever.retrieve(query, channel_ids)
        if "es" not in skip_arms:
            for i, q in enumerate(queries):
                key = "es" if i == 0 else f"es_alt{i}"
                tasks[key] = self.es_retriever.retrieve(q, channel_ids)

        # 2026-09-15 per-request arm breaker: a FAILED arm (provider
        # error, not clean-empty) is skipped on later rounds of the same
        # request; a clean-EMPTY arm is skipped after 2 consecutive
        # empties (a keyword-less query won't grow keywords on rewrite).
        # The caller owns the skip set and never skips Qdrant (primary
        # vector arm stays live).
        arm_docs: dict[str, int] = {}
        arm_status: dict[str, str] = {a: "skipped" for a in skip_arms}
        if tasks:
            results = await asyncio.gather(
                *tasks.values(), return_exceptions = True,
            )
            all_docs: list[Document] = []
            for name, result in zip(tasks.keys(), results):
                group = self._arm_group(name)
                if isinstance(result, Exception):
                    logger.warning(
                        f"[ycs:smart] {name} failed: "
                        f"{type(result).__name__}: {str(result)[:200]}"
                    )
                    arm_status[group] = "failed"
                    continue
                if group == "neo4j" and isinstance(result, tuple):
                    docs, neo_status = result
                    arm_docs[group] = arm_docs.get(group, 0) + len(docs)
                    if neo_status in ("extraction_failed", "traversal_failed"):
                        arm_status[group] = "failed"
                    elif arm_docs[group] > 0:
                        arm_status[group] = "ok"
                    else:
                        arm_status.setdefault(group, "empty")
                    logger.info(
                        f"[ycs:smart] {name} returned {len(docs)} "
                        f"documents ({neo_status})"
                    )
                    all_docs.extend(docs)
                    continue
                logger.info(
                    f"[ycs:smart] {name} returned {len(result)} documents"
                )
                arm_docs[group] = arm_docs.get(group, 0) + len(result)
                if len(result) > 0:
                    arm_status[group] = "ok"
                else:
                    arm_status.setdefault(group, "empty")
                all_docs.extend(result)
            # Qdrant is the load-bearing arm — its status is reported
            # like the others, but the caller (retrieve node) never adds
            # it to `skip_arms`.
            if all_docs:
                deduped = domain.dedupe_documents(all_docs)
                return self._rerank(query, deduped), arm_status

        # Every arm empty or failed — caller rewrites and retries.
        return [], arm_status

    def _rerank(
        self, query: str, documents: list[Document],
    ) -> list[Document]:
        """Two-stage retrieval: arms = high recall, rerank = high
        precision. FlashRank sees (query, document) pairs together so
        it catches interactions the bi-encoders miss. CPU-local —
        ~50ms for 20 docs."""
        if not self.use_reranker or len(documents) <= 1:
            return documents[:self.top_k]
        try:
            return domains.ycs.reranker.service.rerank_documents(query, documents, top_k = self.top_k)
        except Exception as e:
            logger.warning(
                f"[ycs:smart] rerank failed ({type(e).__name__}: {e}); "
                f"falling back to retrieval order"
            )
            return documents[:self.top_k]
