"""Book/run-scoped hybrid evidence retrieval for BD Ask."""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import domains
import infra

from . import params

_WORD_RE = re.compile(r"[\w'-]{2,}", re.UNICODE)


def _citation(payload: dict[str, Any], book_id: str, arm: str) -> dict[str, Any]:
    section_path = payload.get("section_path") or []
    section = " › ".join(str(part) for part in section_path if part)
    page = payload.get("processed_page_number", payload.get("page_number"))
    if type(page) is not int or page < 1:
        page_numbers = payload.get("page_numbers") or []
        page = next((item for item in page_numbers if type(item) is int and item > 0), None)
    source_type = str(payload.get("source_type") or "book source")
    title = section or (f"Page {page}" if page else source_type.replace("_", " ").title())
    return {
        "title": title,
        "url": "",
        "book_id": book_id,
        "page": page,
        "chapter": section,
        "section": section,
        "snippet": str(payload.get("content") or "")[:params.MAX_EVIDENCE_CHARS],
        "source": arm,
    }


async def _retrieve_ingestion_vectors(
    book_id: str,
    query: str,
    index: dict[str, Any],
) -> list[dict[str, Any]]:
    collection = index.get("qdrant_collection")
    expected_model = index.get("embedding_model")
    dimensions = index.get("embedding_dimensions")
    if not collection or not expected_model:
        return []
    vectors, model = await domains.settings.embeddings.service.embed_texts_async([query])
    if model != expected_model or not vectors or len(vectors[0]) != dimensions:
        raise ValueError("The configured embedding model does not match this book's retrieval index")
    sparse = infra.qdrant.service.FastEmbedSparse(
        model_name = index.get("qdrant_sparse_model") or params.SPARSE_MODEL_NAME,
    )
    sparse_vector = sparse.embed_query(query)
    models = infra.qdrant.service.models
    scope_conditions = [
        models.FieldCondition(key = "book_id", match = models.MatchValue(value = book_id)),
        models.FieldCondition(key = "agentic_run_id", match = models.MatchValue(value = index["agentic_run_id"])),
        models.FieldCondition(
            key = "processed_book_revision",
            match = models.MatchValue(value = index["processed_book_revision"]),
        ),
    ]
    profile_fingerprint = index.get("qdrant_index_profile_fingerprint")
    if isinstance(profile_fingerprint, str) and profile_fingerprint:
        scope_conditions.append(models.FieldCondition(
            key = "index_profile_fingerprint",
            match = models.MatchValue(value = profile_fingerprint),
        ))
    scope = models.Filter(must = scope_conditions)
    prefetch = [models.Prefetch(query = vectors[0], using = "dense", limit = params.TOP_K * 2, filter = scope)]
    if sparse_vector is not None:
        prefetch.append(models.Prefetch(
            query = models.SparseVector(indices = sparse_vector.indices, values = sparse_vector.values),
            using = "sparse", limit = params.TOP_K * 2, filter = scope,
        ))
    result = await infra.qdrant.service.get_qdrant().query_points(
        collection_name = collection,
        prefetch = prefetch,
        query = models.FusionQuery(fusion = models.Fusion.RRF),
        limit = params.TOP_K,
        with_payload = True,
    )
    evidence = []
    for point in result.points:
        payload = dict(point.payload or {})
        content = str(payload.get("content") or "").strip()
        if content:
            evidence.append({
                "id": f"qdrant:{point.id}",
                "text": content[:params.MAX_EVIDENCE_CHARS],
                "score": float(point.score or 0),
                "source": "qdrant_hybrid",
                "citation": _citation(payload, book_id, "qdrant_hybrid"),
            })
    return evidence


async def _retrieve_ingestion_full_text(book_id: str, query: str, index: dict[str, Any]) -> list[dict[str, Any]]:
    index_name = index.get("elasticsearch_passage_index") or infra.elasticsearch.keys.INDEX_BD_RAG_PASSAGES
    if not index_name:
        return []
    filters = [
        {"term": {"book_id": book_id}},
        {"term": {"agentic_run_id": index["agentic_run_id"]}},
        {"term": {"processed_book_revision": index["processed_book_revision"]}},
    ]
    profile_fingerprint = index.get("qdrant_index_profile_fingerprint")
    if profile_fingerprint:
        filters.append({"term": {"index_profile_fingerprint": profile_fingerprint}})
    index_domain = domains.bd.rag.source.indexes.elasticsearch_index.domain
    filters.append({"term": {"book_fingerprint": index_domain.book_fingerprint(book_id)}})
    client = infra.elasticsearch.service.new_es()
    try:
        result = await client.search(
            index = index_name,
            size = params.TOP_K,
            query = {
                "bool": {
                    "must": [{
                        "multi_match": {
                            "query": query,
                            "fields": ["content^2", "section_path.text"],
                            "type": "best_fields",
                        },
                    }],
                    "filter": filters,
                },
            },
        )
    finally:
        await client.close()
    hits = result.get("hits", {}).get("hits", [])
    evidence = []
    for hit in hits:
        payload = hit.get("_source") or {}
        content = str(payload.get("content") or "").strip()
        if not content:
            continue
        evidence.append({
            "id": f"elasticsearch:{hit.get('_id')}",
            "text": content[:params.MAX_EVIDENCE_CHARS],
            "score": float(hit.get("_score") or 0),
            "source": "elasticsearch_bm25",
            "citation": _citation(payload, book_id, "elasticsearch_bm25"),
        })
    return evidence


async def _retrieve_ingestion_graph(
    book_id: str,
    query: str,
    index: dict[str, Any],
) -> list[dict[str, Any]]:
    terms = list(dict.fromkeys(word.casefold() for word in _WORD_RE.findall(query)))[:params.MAX_GRAPH_TERMS]
    if not terms:
        return []
    cypher = """
        MATCH (node:COELHONexus:BD)
        WHERE node.book_id = $book_id
          AND node.agentic_run_id = $agentic_run_id
          AND node.processed_book_revision = $revision
          AND node:Entity
          AND any(term IN $terms WHERE toLower(coalesce(node.name, '')) CONTAINS term)
        OPTIONAL MATCH (node)-[relationship]-(related:COELHONexus:BD:Entity {
            book_id: $book_id, agentic_run_id: $agentic_run_id,
            processed_book_revision: $revision
        })
        WHERE relationship IS NULL
           OR (relationship.book_id = $book_id
               AND relationship.agentic_run_id = $agentic_run_id
               AND relationship.processed_book_revision = $revision)
        WITH node,
             collect(DISTINCT CASE WHEN relationship IS NULL THEN null ELSE {
                 type: type(relationship), kind: relationship.kind,
                 source: startNode(relationship).name, target: endNode(relationship).name,
                 citations: relationship.citations, evidence: relationship.evidence,
                 page_numbers: relationship.processed_page_numbers
             } END)[..4] AS relationships
        OPTIONAL MATCH (evidence_source:COELHONexus:BD:Entity)-[:BD_HAS_EVIDENCE]->
                       (graph_evidence:COELHONexus:BD:GraphEvidence)-[:BD_EVIDENCE_TARGET]->
                       (evidence_target:COELHONexus:BD:Entity)
        WHERE node.id IN [evidence_source.id, evidence_target.id]
          AND evidence_source.book_id = $book_id
          AND evidence_source.agentic_run_id = $agentic_run_id
          AND evidence_source.processed_book_revision = $revision
          AND evidence_target.book_id = $book_id
          AND evidence_target.agentic_run_id = $agentic_run_id
          AND evidence_target.processed_book_revision = $revision
          AND graph_evidence.book_id = $book_id
          AND graph_evidence.agentic_run_id = $agentic_run_id
          AND graph_evidence.processed_book_revision = $revision
        OPTIONAL MATCH (graph_evidence)-[:BD_SUPPORTED_BY]->
                       (evidence_page:COELHONexus:BD:ProcessedPage {
                           book_id: $book_id, agentic_run_id: $agentic_run_id,
                           processed_book_revision: $revision
                       })
        RETURN node.id AS id, node.kind AS kind, node.name AS name,
               node.entity_type AS entity_type, node.page_numbers AS page_numbers,
               node.citations AS citations,
               relationships,
               collect(DISTINCT CASE WHEN graph_evidence IS NULL THEN null ELSE {
                   id: graph_evidence.id, source: evidence_source.name,
                   relation: graph_evidence.relation_kind, target: evidence_target.name,
                   quote: graph_evidence.quote,
                   processed_page_number: graph_evidence.processed_page_number,
                   source_page_number: graph_evidence.source_page_number
               } END)[..6] AS graph_evidence
        LIMIT $limit
    """
    async with infra.neo4j.service.session(default_access_mode = "READ") as session:
        result = await session.run(
            cypher,
            book_id = book_id,
            agentic_run_id = index["agentic_run_id"],
            revision = index["processed_book_revision"],
            terms = terms,
            limit = params.GRAPH_TOP_K,
        )
        rows = await result.data()
    evidence = []
    for row in rows:
        relationships = row.get("relationships") or []
        graph_evidence = row.get("graph_evidence") or []
        details = [str(row.get("name") or ""), str(row.get("entity_type") or "")]
        details.extend(
            f"{item.get('source') or ''} {item.get('kind') or item.get('type') or 'related to'} "
            f"{item.get('target') or ''}"
            for item in relationships if isinstance(item, dict) and item.get("target")
        )
        relation_evidence: list[str] = []
        relation_citations: list[dict[str, Any]] = []
        for item in graph_evidence:
            if not isinstance(item, dict):
                continue
            source_name = str(item.get("source") or "")
            relation_name = str(item.get("relation") or "related to")
            target_name = str(item.get("target") or "")
            if source_name or target_name:
                details.append(f"{source_name} {relation_name} {target_name}".strip())
            quote_value = str(item.get("quote") or "").strip()
            if quote_value:
                relation_evidence.append(quote_value)
            relation_citations.append({
                "processed_page_number": item.get("processed_page_number"),
                "source_page_number": item.get("source_page_number"),
                "quote": quote_value,
            })
        for relationship in relationships:
            if not isinstance(relationship, dict):
                continue
            evidence_values = relationship.get("evidence") or []
            if isinstance(evidence_values, str):
                evidence_values = [evidence_values]
            relation_evidence.extend(
                str(value) for value in evidence_values
                if isinstance(value, str) and value.strip()
            )
            try:
                citations_value = json.loads(relationship.get("citations") or "[]")
            except (json.JSONDecodeError, TypeError):
                citations_value = []
            if isinstance(citations_value, list):
                relation_citations.extend(
                    item for item in citations_value if isinstance(item, dict)
                )
        details.extend(f"Evidence: {item}" for item in dict.fromkeys(relation_evidence))
        try:
            pages = json.loads(row.get("page_numbers") or "[]")
        except json.JSONDecodeError:
            pages = []
        try:
            citations = json.loads(row.get("citations") or "[]")
        except json.JSONDecodeError:
            citations = []
        page = next((
            item.get("processed_page_number") for item in relation_citations
            if type(item.get("processed_page_number")) is int
            and item["processed_page_number"] > 0
        ), None)
        if not page and isinstance(pages, list):
            page = next((item for item in pages if type(item) is int and item > 0), None)
        quote = next((
            str(item.get("quote")) for item in relation_citations if item.get("quote")
        ), "")
        if not quote and isinstance(citations, list):
            quote = next((
                str(item.get("quote")) for item in citations
                if isinstance(item, dict) and item.get("quote")
            ), "")
        citation = {
            "title": str(row.get("name") or "Book graph evidence"),
            "url": "",
            "book_id": book_id,
            "page": page,
            "chapter": "",
            "section": "book graph",
            "snippet": quote[:params.MAX_EVIDENCE_CHARS],
            "source": "neo4j_graph",
        }
        evidence.append({
            "id": f"neo4j:{row.get('id')}",
            "text": "\n".join(part for part in details if part)[:params.MAX_EVIDENCE_CHARS],
            "score": 0.0,
            "source": "neo4j_graph",
            "citation": citation,
        })
    return evidence


async def retrieve(book_id: str, run_id: str, query: str) -> list[dict[str, Any]]:
    """Retrieve from the current published Ingestion index and its exact revision."""
    query = query.strip()[:params.MAX_QUERY_CHARS]
    if not query:
        return []
    ingestion_index = await domains.bd.rag.ai.runs.service.read_current_index(book_id)
    if ingestion_index is None:
        raise ValueError("The book has no completed Ingestion retrieval index")
    if ingestion_index.get("agentic_run_id") != run_id:
        raise ValueError("The selected Ask thread is bound to a different Ingestion retrieval index")
    index = {**ingestion_index["index"], **ingestion_index}
    results = await asyncio.gather(
        asyncio.wait_for(_retrieve_ingestion_full_text(book_id, query, index), timeout = params.RETRIEVAL_TIMEOUT_S),
        asyncio.wait_for(_retrieve_ingestion_vectors(book_id, query, index), timeout = params.RETRIEVAL_TIMEOUT_S),
        asyncio.wait_for(_retrieve_ingestion_graph(book_id, query, index), timeout = params.RETRIEVAL_TIMEOUT_S),
        return_exceptions = True,
    )
    ranked_lists = [result for result in results if not isinstance(result, BaseException)]
    return fuse_evidence(ranked_lists)


def fuse_evidence(ranked_lists: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Fuse backend or query rankings without comparing incomparable raw scores."""
    fused: dict[str, dict[str, Any]] = {}
    for list_index, ranked in enumerate(ranked_lists):
        for rank, item in enumerate(ranked, start = 1):
            text = " ".join(str(item.get("text") or "").split())
            fingerprint = text.casefold()
            if not fingerprint:
                continue
            reciprocal_rank = 1.0 / (params.RRF_K + rank)
            matched_sources = {str(item.get("source") or "unknown")}
            prior = fused.get(fingerprint)
            if prior is None:
                fused[fingerprint] = {
                    "item": dict(item),
                    "score": reciprocal_rank,
                    "best_rank": (rank, list_index),
                    "matched_sources": matched_sources,
                }
                continue
            prior["score"] += reciprocal_rank
            prior["matched_sources"].update(matched_sources)
            if (rank, list_index) < prior["best_rank"]:
                prior["item"] = dict(item)
                prior["best_rank"] = (rank, list_index)

    ordered = sorted(
        fused.items(),
        key = lambda pair: (-pair[1]["score"], pair[0]),
    )[:params.MAX_MERGED_EVIDENCE]
    evidence = []
    for _, value in ordered:
        item = value["item"]
        item["backend_score"] = item.get("score", 0.0)
        item["score"] = value["score"]
        item["matched_sources"] = sorted(value["matched_sources"])
        evidence.append(item)
    return evidence
