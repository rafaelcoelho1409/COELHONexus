"""ycs/retriever — Neo4j graph-traversal retriever (Phase 3).

Two-step pipeline:
  1. LLM extracts entity names from the user question
     (`ENTITY_EXTRACTION_PROMPT` → `ExtractedEntities`).
  2. Cypher traversal finds:
       (a) Documents/Videos DIRECTLY linked to those entities, and
       (b) one-hop neighbors (entities connected to the matched ones).
     Both branches UNION'd into a single result set, deduplicated.

Direct port of deprecated `services/youtube/retriever.py:L236-409`.

NOTE: `from langchain_neo4j import Neo4jGraph` lives at module scope
even though this file is also called `neo4j.py`. Python's absolute-
import default resolves the bare-name `neo4j` to the installed
package, not to this submodule."""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.documents import Document
from langchain_neo4j import Neo4jGraph

from .params import NEO4J_DEFAULT_TOP_K
from .prompts import ENTITY_EXTRACTION_PROMPT
from .schemas import ExtractedEntities


logger = logging.getLogger(__name__)


# Cypher fragments — kept at module scope (not `params.py`) because
# they're tightly coupled to the traversal queries below and never
# imported elsewhere.

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
    `SmartRetriever` runs both arms in parallel."""

    def __init__(
        self,
        neo4j_graph: Neo4jGraph,
        llm: Any,
        top_k: int = NEO4J_DEFAULT_TOP_K,
    ) -> None:
        self.graph = neo4j_graph
        self.llm = llm
        self.top_k = top_k

    async def retrieve(
        self, query: str, channel_ids: list[str] | None = None,
    ) -> list[Document]:
        entities = await self._extract_entities(query)
        if not entities:
            return []
        documents = self._traverse_graph(entities, channel_ids)
        return documents[:self.top_k]

    async def _extract_entities(self, query: str) -> list[str]:
        """Structured-output LLM call. Failures degrade to `[]` so the
        SmartRetriever's other arms still produce results."""
        chain = ENTITY_EXTRACTION_PROMPT | self.llm.with_structured_output(
            ExtractedEntities, method = "function_calling",
        )
        try:
            result = await chain.ainvoke({"query": query})
            logger.info(f"[ycs:neo4j] extracted entities: {result.entities}")
            return result.entities
        except Exception as e:
            logger.warning(
                f"[ycs:neo4j] entity extraction failed: "
                f"{type(e).__name__}: {str(e)[:200]}"
            )
            return []

    def _traverse_graph(
        self, entities: list[str], channel_ids: list[str] | None = None,
    ) -> list[Document]:
        """Cypher UNION query:
          1. Direct entity match: `MATCH (e:__Entity__) WHERE eid IN $entities`
          2. One-hop neighbors:   `MATCH (e)-[r]-(neighbor:__Entity__)`
        Both branches optionally JOIN against `:Channel` via `BELONGS_TO`
        when `channel_ids` is supplied."""
        if not entities:
            return []
        entity_patterns = [e.lower() for e in entities]

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

        params: dict = {
            "entities": entity_patterns,
            "limit":    self.top_k * 2,
        }
        if channel_ids:
            params["channel_ids"] = channel_ids

        try:
            results = self.graph.query(
                # 1) Direct entity match + source documents.
                "MATCH (e:__Entity__) "
                "WHERE e.id IS NOT NULL "
                f"WITH e, ({_NORMALIZE_ID}) AS eid "
                "WHERE toLower(toString(eid)) IN $entities "
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
                "MATCH (e:__Entity__) "
                "WHERE e.id IS NOT NULL "
                f"WITH e, ({_NORMALIZE_ID}) AS eid "
                "WHERE toLower(toString(eid)) IN $entities "
                "MATCH (e)-[r]-(neighbor:__Entity__) "
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
                params = params,
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
