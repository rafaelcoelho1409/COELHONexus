"""ycs/retriever — Neo4j graph-traversal retriever (Phase 3).

Two-step pipeline:
  1. LLM extracts entity names from the user question
     (`ENTITY_EXTRACTION_PROMPT` → `ExtractedEntities`).
  2. Cypher traversal finds:
       (a) Documents/Videos DIRECTLY linked to those entities, and
       (b) one-hop neighbors (entities connected to the matched ones).
     Both branches UNION'd into a single result set, deduplicated.
NOTE: `from langchain_neo4j import Neo4jGraph` lives at module scope
even though this file is also called `neo4j.py`. Python's absolute-
import default resolves the bare-name `neo4j` to the installed
package, not to this submodule."""
from __future__ import annotations

import logging
import time
from typing import Any

from langchain_core.documents import Document
from langchain_neo4j import Neo4jGraph

from domains.ycs.graph_builder.params import SOURCE_LABEL
from domains.ycs.rag.llm_call import resilient_ainvoke
from domains.ycs.runtime.llm_counter import set_node as _llm_set_node

from .params import INVENTORY_MAX_IDS, INVENTORY_TTL_S, NEO4J_DEFAULT_TOP_K
from .prompts import ENTITY_EXTRACTION_PROMPT
from .schemas import ExtractedEntities


logger = logging.getLogger(__name__)


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
    if now - _inventory_cache["ts"] < INVENTORY_TTL_S:
        return list(_inventory_cache["items"])
    try:
        rows = graph.query(
            f"MATCH (e:__Entity__:{SOURCE_LABEL}) "
            "WHERE e.id IS NOT NULL "
            "WITH CASE WHEN valueType(e.id) STARTS WITH 'LIST' "
            "THEN head(e.id) ELSE e.id END AS eid "
            "WHERE eid IS NOT NULL AND trim(toString(eid)) <> '' "
            "RETURN DISTINCT toString(eid) AS name "
            "LIMIT $limit",
            params = {"limit": INVENTORY_MAX_IDS},
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
        chain = ENTITY_EXTRACTION_PROMPT | self.llm.with_structured_output(
            ExtractedEntities,
        )
        try:
            _llm_set_node(node = "neo4j_entity_extract")
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
            result = await resilient_ainvoke(
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

        params: dict = {
            "entities": entity_patterns,
            "limit":    self.top_k * 2,
        }
        if channel_ids:
            params["channel_ids"] = channel_ids
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
            from domains.ycs.runtime.observability import neo4j_query_span
            with neo4j_query_span(
                operation         = "entity_lookup",
                statement_summary = "MATCH __Entity__ + Document/Video + UNION 1-hop",
            ):
                results = self.graph.query(
                # 1) Direct entity match + source documents.
                f"MATCH (e:__Entity__:{SOURCE_LABEL}) "
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
                f"MATCH (e:__Entity__:{SOURCE_LABEL}) "
                "WHERE e.id IS NOT NULL "
                f"WITH e, ({_NORMALIZE_ID}) AS eid "
                f"{entity_pred}"
                f"MATCH (e)-[r]-(neighbor:__Entity__:{SOURCE_LABEL}) "
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
