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
    page = payload.get("page_number")
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


async def _retrieve_vectors(book_id: str, run_id: str, query: str, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    collection = manifest.get("collection_name")
    expected_model = manifest.get("embedding_model")
    if not collection or not expected_model:
        return []
    vectors, model = await domains.settings.embeddings.service.embed_texts_async([query])
    if model != expected_model or not vectors or len(vectors[0]) != manifest.get("embedding_dimensions"):
        raise ValueError("The configured embedding model does not match this book's retrieval index")
    sparse = infra.qdrant.service.FastEmbedSparse(model_name = params.SPARSE_MODEL_NAME)
    sparse_vector = sparse.embed_query(query)
    models = infra.qdrant.service.models
    scope = models.Filter(must = [
        models.FieldCondition(key = "book_id", match = models.MatchValue(value = book_id)),
        models.FieldCondition(key = "distill_run_id", match = models.MatchValue(value = run_id)),
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
        if not content:
            continue
        evidence.append({
            "id": f"qdrant:{point.id}",
            "text": content[:params.MAX_EVIDENCE_CHARS],
            "score": float(point.score or 0),
            "source": "qdrant_hybrid",
            "citation": _citation(payload, book_id, "qdrant_hybrid"),
        })
    return evidence


async def _retrieve_graph(book_id: str, run_id: str, query: str) -> list[dict[str, Any]]:
    terms = list(dict.fromkeys(word.casefold() for word in _WORD_RE.findall(query)))[:params.MAX_GRAPH_TERMS]
    if not terms:
        return []
    cypher = """
        MATCH (node:COELHONexus:BD:BDNode)
        WHERE node.book_id = $book_id
          AND node.distill_run_id = $run_id
          AND any(term IN $terms WHERE
              toLower(coalesce(node.name, '')) CONTAINS term OR
              toLower(coalesce(node.explanation, '')) CONTAINS term OR
              toLower(coalesce(node.attribution, '')) CONTAINS term)
        OPTIONAL MATCH (node)-[relationship:BD_RELATES]-(related:COELHONexus:BD:BDNode {
            book_id: $book_id, distill_run_id: $run_id
        })
        RETURN node.id AS id, node.kind AS kind, node.name AS name,
               node.explanation AS explanation, node.attribution AS attribution,
               node.page_numbers AS page_numbers, node.citations AS citations,
               collect(DISTINCT {kind: relationship.kind, evidence: relationship.evidence,
                                target: related.name})[..4] AS relationships
        LIMIT $limit
    """
    async with infra.neo4j.service.session(default_access_mode = "READ") as session:
        result = await session.run(
            cypher, book_id = book_id, run_id = run_id, terms = terms, limit = params.GRAPH_TOP_K,
        )
        rows = await result.data()
    evidence = []
    for row in rows:
        details = [str(row.get("name") or "")]
        details.extend(str(row.get(key) or "") for key in ("explanation", "attribution"))
        relationships = row.get("relationships") or []
        details.extend(
            f"{item.get('kind') or 'related to'} {item.get('target') or ''}: {item.get('evidence') or ''}"
            for item in relationships if isinstance(item, dict) and item.get("target")
        )
        page_numbers = row.get("page_numbers") or "[]"
        citations = row.get("citations") or "[]"
        try:
            pages = json.loads(page_numbers) if isinstance(page_numbers, str) else page_numbers
        except json.JSONDecodeError:
            pages = []
        try:
            citation_rows = json.loads(citations) if isinstance(citations, str) else citations
        except json.JSONDecodeError:
            citation_rows = []
        evidence_text = "\n".join(part for part in details if part).strip()
        if not evidence_text:
            continue
        primary_page = next((page for page in pages if type(page) is int and page > 0), None) if isinstance(pages, list) else None
        snippet = next((str(item.get("quote")) for item in citation_rows if isinstance(item, dict) and item.get("quote")), "") if isinstance(citation_rows, list) else ""
        citation = {
            "title": str(row.get("name") or row.get("kind") or "Book graph evidence"),
            "url": "", "book_id": book_id, "page": primary_page,
            "chapter": "", "section": str(row.get("kind") or ""),
            "snippet": snippet[:params.MAX_EVIDENCE_CHARS], "source": "neo4j_graph",
        }
        evidence.append({
            "id": f"neo4j:{row.get('id')}", "text": evidence_text[:params.MAX_EVIDENCE_CHARS],
            "score": 0.0, "source": "neo4j_graph", "citation": citation,
        })
    return evidence


async def retrieve(book_id: str, run_id: str, query: str) -> list[dict[str, Any]]:
    """Retrieve from the exact published run, pre-filtered by book and run in both stores."""
    query = query.strip()[:params.MAX_QUERY_CHARS]
    if not query:
        return []
    manifest = await domains.bd.rag.service.read_index(book_id, run_id)
    if manifest is None or manifest.get("status") != "done":
        raise ValueError("The selected book version is not fully indexed for Ask")
    vector_results, graph_results = await asyncio.gather(
        asyncio.wait_for(
            _retrieve_vectors(book_id, run_id, query, manifest),
            timeout = params.RETRIEVAL_TIMEOUT_S,
        ),
        asyncio.wait_for(
            _retrieve_graph(book_id, run_id, query),
            timeout = params.RETRIEVAL_TIMEOUT_S,
        ),
        return_exceptions = True,
    )
    evidence = []
    for result in (vector_results, graph_results):
        if isinstance(result, BaseException):
            continue
        evidence.extend(result)
    seen: set[str] = set()
    unique = []
    for item in evidence:
        fingerprint = str(item.get("text", "")).casefold()
        if fingerprint and fingerprint not in seen:
            seen.add(fingerprint)
            unique.append(item)
    return unique
