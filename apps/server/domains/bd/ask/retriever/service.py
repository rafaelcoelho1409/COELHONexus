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
    sparse = infra.qdrant.service.FastEmbedSparse(model_name = params.SPARSE_MODEL_NAME)
    sparse_vector = sparse.embed_query(query)
    models = infra.qdrant.service.models
    scope = models.Filter(must = [
        models.FieldCondition(key = "book_id", match = models.MatchValue(value = book_id)),
        models.FieldCondition(key = "agentic_run_id", match = models.MatchValue(value = index["agentic_run_id"])),
        models.FieldCondition(
            key = "processed_book_revision",
            match = models.MatchValue(value = index["processed_book_revision"]),
        ),
    ])
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
    index_name = index.get("elasticsearch_index") or infra.elasticsearch.keys.INDEX_BD_INGESTION
    if not index_name:
        return []
    filters = [
        {"term": {"book_id": book_id}},
        {"term": {"agentic_run_id": index["agentic_run_id"]}},
        {"term": {"processed_book_revision": index["processed_book_revision"]}},
    ]
    if index_name == infra.elasticsearch.keys.INDEX_BD_INGESTION:
        index_domain = domains.bd.ingestion.ai.retrieval_index.elasticsearch_index.domain
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
                            "fields": ["content^2", "section_path"],
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
        MATCH (node:COELHONexus:BD:BDNode)
        WHERE node.book_id = $book_id
          AND node.agentic_run_id = $agentic_run_id
          AND node.processed_book_revision = $revision
          AND node.kind = 'entity'
          AND any(term IN $terms WHERE toLower(coalesce(node.name, '')) CONTAINS term)
        OPTIONAL MATCH (node)-[relationship:BD_RELATES]-(related:COELHONexus:BD:BDNode {
            book_id: $book_id, agentic_run_id: $agentic_run_id,
            processed_book_revision: $revision
        })
        RETURN node.id AS id, node.kind AS kind, node.name AS name,
               node.entity_type AS entity_type, node.page_numbers AS page_numbers,
               node.citations AS citations,
               collect(DISTINCT {kind: relationship.kind, target: related.name})[..4] AS relationships
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
        details = [str(row.get("name") or ""), str(row.get("entity_type") or "")]
        details.extend(
            f"{item.get('kind') or 'related to'} {item.get('target') or ''}"
            for item in relationships if isinstance(item, dict) and item.get("target")
        )
        try:
            pages = json.loads(row.get("page_numbers") or "[]")
        except json.JSONDecodeError:
            pages = []
        try:
            citations = json.loads(row.get("citations") or "[]")
        except json.JSONDecodeError:
            citations = []
        page = next((item for item in pages if type(item) is int and item > 0), None) if isinstance(pages, list) else None
        quote = next((str(item.get("quote")) for item in citations if isinstance(item, dict) and item.get("quote")), "") if isinstance(citations, list) else ""
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
    ingestion_index = await domains.bd.ingestion.ai.retrieval_index.service.read_current_index(book_id)
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
    evidence = [item for result in results if not isinstance(result, BaseException) for item in result]
    return _unique_evidence(evidence)


def _unique_evidence(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique = []
    for item in evidence:
        fingerprint = str(item.get("text", "")).casefold()
        if fingerprint and fingerprint not in seen:
            seen.add(fingerprint)
            unique.append(item)
    return unique
