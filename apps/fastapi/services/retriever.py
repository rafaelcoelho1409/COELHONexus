"""
Retriever Service — ES + Qdrant Hybrid + Neo4j Graph Traversal

CONCEPT: This module provides retrievers that share the same interface:
  - ElasticsearchRetriever: full-text search (Phase 1)
  - QdrantHybridRetriever: dense + sparse hybrid search (Phase 2)
  - Neo4jRetriever: knowledge graph traversal (Phase 3)
  - SmartRetriever: orchestrates all three with graceful fallback

All have the same method: retrieve(query) → list[Document]
The LangGraph agent doesn't know or care which retriever it's using.

RETRIEVAL STRATEGY (Phase 3):
1. Qdrant hybrid (semantic + keyword) for content matching
2. Neo4j graph traversal for entity-based and multi-hop queries
3. Results from both are merged and deduplicated
4. ES as final fallback if Qdrant is unavailable
"""
import asyncio
from elasticsearch import AsyncElasticsearch
from qdrant_client import AsyncQdrantClient
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_qdrant import FastEmbedSparse
from langchain_neo4j import Neo4jGraph
from langchain_openai import ChatOpenAI

from services.ingestion import QDRANT_COLLECTION


ES_INDEX_TRANSCRIPTIONS = "coelhonexus-youtube-transcriptions"
ES_INDEX_METADATA = "coelhonexus-youtube-metadata"


# =============================================================================
# Elasticsearch Retriever (Phase 1 — full-text search)
# =============================================================================
class ElasticsearchRetriever:
    """Full-text search over YouTube transcriptions in Elasticsearch."""

    def __init__(self, es_client: AsyncElasticsearch, top_k: int = 10):
        self.es = es_client
        self.top_k = top_k

    async def retrieve(self, query: str) -> list[Document]:
        """Search transcriptions using ES full-text search."""
        results = await self.es.search(
            index = ES_INDEX_TRANSCRIPTIONS,
            query = {
                "multi_match": {
                    "query": query,
                    "fields": ["content"],
                    "type": "best_fields",
                }
            },
            size = self.top_k,
            _source = ["video_id", "lang", "content", "channel_id"],
        )
        hits = results["hits"]["hits"]
        if not hits:
            return []
        video_ids = list({h["_source"]["video_id"] for h in hits})
        metadata_map = await self._fetch_metadata(video_ids)
        documents = []
        for hit in hits:
            src = hit["_source"]
            video_id = src["video_id"]
            meta = metadata_map.get(video_id, {})
            documents.append(Document(
                page_content = src.get("content", ""),
                metadata = {
                    "video_id": video_id,
                    "lang": src.get("lang", "en"),
                    "title": meta.get("title", ""),
                    "channel": meta.get("channel", ""),
                    "channel_id": src.get("channel_id", ""),
                    "upload_date": meta.get("upload_date", ""),
                    "webpage_url": meta.get("webpage_url", ""),
                    "score": hit["_score"],
                    "source": "elasticsearch",
                },
            ))
        return documents

    async def _fetch_metadata(self, video_ids: list[str]) -> dict:
        if not video_ids:
            return {}
        results = await self.es.search(
            index = ES_INDEX_METADATA,
            query = {"ids": {"values": video_ids}},
            size = len(video_ids),
            _source = ["title", "channel", "upload_date", "webpage_url"],
        )
        return {h["_id"]: h["_source"] for h in results["hits"]["hits"]}


# =============================================================================
# Qdrant Hybrid Retriever (Phase 2 — dense + sparse in one query)
# =============================================================================
class QdrantHybridRetriever:
    """
    Hybrid search using Qdrant's built-in dense + sparse fusion.

    CONCEPT: One Qdrant query searches BOTH vector types:
    - Dense vectors find semantically similar content
      ("frontend state management" matches "React hooks tutorial")
    - Sparse vectors find keyword matches
      ("React hooks" matches documents containing those exact words)

    Qdrant internally fuses scores from both vector types using
    Reciprocal Rank Fusion (RRF), returning a unified ranked list.

    This replaces the need to:
    1. Run ES full-text search separately
    2. Run Qdrant semantic search separately
    3. Implement manual RRF fusion code
    """

    def __init__(
        self,
        qdrant: AsyncQdrantClient,
        dense_embeddings: Embeddings,
        sparse_embeddings: FastEmbedSparse,
        top_k: int = 10,
    ):
        self.qdrant = qdrant
        self.dense_embeddings = dense_embeddings
        self.sparse_embeddings = sparse_embeddings
        self.top_k = top_k

    async def retrieve(self, query: str) -> list[Document]:
        """
        Hybrid search: dense + sparse vectors in one Qdrant query.

        Steps:
        1. Embed the query with both models (dense + sparse)
        2. Send a single prefetch query to Qdrant
        3. Qdrant searches both vector spaces and fuses results
        4. Convert scored points to LangChain Documents
        """
        # Generate both embeddings for the query
        dense_vector = self.dense_embeddings.embed_query(query)
        sparse_vector = self.sparse_embeddings.embed_query(query)  # Returns SparseVector directly
        # Build Qdrant query with hybrid prefetch
        from qdrant_client.http.models import (
            QueryRequest,
            Prefetch,
            FusionQuery,
            Fusion,
            models,
        )
        prefetch = []
        # Dense search
        prefetch.append(Prefetch(
            query = dense_vector,
            using = "dense",
            limit = self.top_k * 2,  # Fetch more for fusion
        ))
        # Sparse search
        if sparse_vector is not None:
            prefetch.append(Prefetch(
                query = models.SparseVector(
                    indices = sparse_vector.indices,
                    values = sparse_vector.values,
                ),
                using = "sparse",
                limit = self.top_k * 2,
            ))
        # Fused query with RRF
        results = await self.qdrant.query_points(
            collection_name = QDRANT_COLLECTION,
            prefetch = prefetch,
            query = FusionQuery(fusion=Fusion.RRF),
            limit = self.top_k,
            with_payload = True,
        )
        # Convert to Documents
        documents = []
        for point in results.points:
            payload = point.payload or {}
            documents.append(Document(
                page_content = payload.get("content", ""),
                metadata = {
                    "video_id": payload.get("video_id", ""),
                    "chunk_index": payload.get("chunk_index", 0),
                    "title": payload.get("title", ""),
                    "channel": payload.get("channel", ""),
                    "channel_id": payload.get("channel_id", ""),
                    "upload_date": payload.get("upload_date", ""),
                    "webpage_url": payload.get("webpage_url", ""),
                    "lang": payload.get("lang", "en"),
                    "score": point.score,
                    "source": "qdrant_hybrid",
                },
            ))
        return documents


# =============================================================================
# Neo4j Graph Retriever (Phase 3 — entity-based traversal)
# =============================================================================
class Neo4jRetriever:
    """
    Knowledge graph retrieval via entity extraction + Cypher traversal.

    CONCEPT: This retriever works in two steps:
    1. EXTRACT: Use the LLM to identify entities in the user's question
       "What does Karpathy say about transformers?" → ["Karpathy", "transformers"]
    2. TRAVERSE: Run a Cypher query that finds content connected to those entities
       MATCH (e)<-[:DISCUSSES|MENTIONS]-(v:Video) WHERE e.id IN entities

    This excels at RELATIONSHIP queries that vector search can't handle:
    - "What topics do channels X and Y both discuss?" (graph intersection)
    - "Who discusses transformers?" (reverse traversal)
    - "What other topics does Karpathy talk about?" (neighbor exploration)

    For pure content queries ("explain attention mechanism"), Qdrant hybrid
    is better. The SmartRetriever runs both in parallel.
    """

    def __init__(self, neo4j_graph: Neo4jGraph, llm: ChatOpenAI, top_k: int = 10):
        self.graph = neo4j_graph
        self.llm = llm
        self.top_k = top_k

    async def retrieve(self, query: str) -> list[Document]:
        """
        Extract entities from query, then traverse the knowledge graph.
        """
        # Step 1: Extract entities from the query using the LLM
        entities = await self._extract_entities(query)
        if not entities:
            return []
        # Step 2: Find content connected to those entities via Cypher
        documents = self._traverse_graph(entities)
        return documents[:self.top_k]

    async def _extract_entities(self, query: str) -> list[str]:
        """
        Use the LLM to identify entity names in the query.

        CONCEPT: We use with_structured_output to get a clean list of entities.
        The LLM understands context: "Karpathy" → person, "transformers" → topic.
        """
        from pydantic import BaseModel, Field
        class ExtractedEntities(BaseModel):
            entities: list[str] = Field(
                description = "List of entity names (people, topics, technologies, channels) mentioned in the query"
            )
        from langchain_core.prompts import ChatPromptTemplate
        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                "Extract entity names from the user's question. "
                "Entities are: people, topics, technologies, concepts, channels. "
                "Return only the entity names as a list. Be concise.",
            ),
            ("human", "{query}"),
        ])
        chain = prompt | self.llm.with_structured_output(ExtractedEntities, method = "function_calling")
        try:
            result = await chain.ainvoke({"query": query})
            return result.entities
        except Exception:
            return []

    def _traverse_graph(self, entities: list[str]) -> list[Document]:
        """
        Run Cypher queries to find content related to extracted entities.

        CONCEPT: This query pattern:
        1. Finds any node whose id or name matches an entity (case-insensitive)
        2. Follows relationships to find connected Video nodes
        3. Returns the Document (source chunk) nodes linked to those entities
        4. Also returns Video metadata for citations

        The __Entity__ label (set by baseEntityLabel=True during ingestion)
        lets us search across ALL entity types in one query.
        """
        if not entities:
            return []
        # Normalize entities for case-insensitive matching
        entity_patterns = [e.lower() for e in entities]
        # Query 1: Find source documents connected to matching entities
        try:
            results = self.graph.query(
                "MATCH (e) "
                "WHERE any(label IN labels(e) WHERE label <> '__Entity__') "
                "AND (toLower(e.id) IN $entities OR toLower(e.name) IN $entities) "
                "OPTIONAL MATCH (e)<-[r]-(doc:Document) "
                "OPTIONAL MATCH (e)<-[:DISCUSSES|MENTIONS]-(v:Video) "
                "RETURN "
                "  COALESCE(doc.text, e.id + ': ' + COALESCE(e.description, '')) AS content, "
                "  COALESCE(v.id, '') AS video_id, "
                "  COALESCE(v.title, '') AS title, "
                "  COALESCE(v.webpage_url, '') AS webpage_url, "
                "  labels(e) AS entity_labels, "
                "  e.id AS entity_id, "
                "  type(r) AS relationship "
                "LIMIT $limit",
                params = {"entities": entity_patterns, "limit": self.top_k * 2},
            )
        except Exception:
            return []
        # Convert to Documents, deduplicate by content
        seen_content = set()
        documents = []
        for row in results:
            content = row.get("content", "")
            if not content or content in seen_content:
                continue
            seen_content.add(content)
            documents.append(Document(
                page_content = content,
                metadata = {
                    "video_id": row.get("video_id", ""),
                    "title": row.get("title", ""),
                    "webpage_url": row.get("webpage_url", ""),
                    "entity_id": row.get("entity_id", ""),
                    "entity_labels": row.get("entity_labels", []),
                    "relationship": row.get("relationship", ""),
                    "source": "neo4j_graph",
                },
            ))
        return documents


# =============================================================================
# Smart Retriever — orchestrates all retrievers with fallback
# =============================================================================
class SmartRetriever:
    """
    CONCEPT: Multi-source retrieval with graceful degradation.

    Phase 4 strategy:
    1. Run Qdrant hybrid + Neo4j graph in PARALLEL (asyncio.gather)
    2. Merge results, deduplicate
    3. RERANK with FlashRank cross-encoder (precision optimization)
    4. If Qdrant/Neo4j fail → fall back to ES full-text
    5. If all fail → return empty (agent rewrites and retries)

    The reranker runs AFTER fusion because it needs the full candidate set
    to make accurate relative comparisons.
    """

    def __init__(
        self,
        es_retriever: ElasticsearchRetriever,
        qdrant_retriever: QdrantHybridRetriever | None = None,
        neo4j_retriever: Neo4jRetriever | None = None,
        use_reranker: bool = True,
        top_k: int = 10,
    ):
        self.es_retriever = es_retriever
        self.qdrant_retriever = qdrant_retriever
        self.neo4j_retriever = neo4j_retriever
        self.use_reranker = use_reranker
        self.top_k = top_k

    async def retrieve(self, query: str) -> list[Document]:
        # Build list of retriever coroutines to run in parallel
        tasks = {}
        if self.qdrant_retriever:
            tasks["qdrant"] = self.qdrant_retriever.retrieve(query)
        if self.neo4j_retriever:
            tasks["neo4j"] = self.neo4j_retriever.retrieve(query)
        # Run available retrievers in parallel
        if tasks:
            results = await asyncio.gather(
                *tasks.values(),
                return_exceptions = True,
            )
            # Collect successful results
            all_docs = []
            for name, result in zip(tasks.keys(), results):
                if isinstance(result, Exception):
                    continue
                all_docs.extend(result)
            if all_docs:
                deduped = self._deduplicate(all_docs)
                return self._rerank(query, deduped)
        # Fallback to ES full-text
        try:
            docs = await self.es_retriever.retrieve(query)
            return self._rerank(query, docs)
        except Exception:
            return []

    def _rerank(self, query: str, documents: list[Document]) -> list[Document]:
        """
        CONCEPT: Two-stage retrieval.
        Stage 1 (retrievers): high recall, fast, approximate ranking
        Stage 2 (reranker): high precision, slower, accurate ranking

        FlashRank cross-encoder processes (query, document) pairs together,
        capturing interactions that bi-encoders miss. Runs locally on CPU.
        """
        if not self.use_reranker or len(documents) <= 1:
            return documents[:self.top_k]
        try:
            from services.reranker import rerank_documents
            return rerank_documents(query, documents, top_k = self.top_k)
        except Exception:
            return documents[:self.top_k]

    def _deduplicate(self, documents: list[Document]) -> list[Document]:
        """Remove duplicate documents based on content prefix."""
        seen = set()
        unique = []
        for doc in documents:
            key = (
                doc.metadata.get("video_id", ""),
                doc.metadata.get("chunk_index", ""),
                doc.page_content[:100],
            )
            if key not in seen:
                seen.add(key)
                unique.append(doc)
        return unique
