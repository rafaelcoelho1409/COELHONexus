"""Deterministic page batching and Neo4j graph projection for BD books."""
from __future__ import annotations
from . import entities, params, patterns

import hashlib
import json
import re
import unicodedata
from collections.abc import Callable
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter


def _estimate_tokens(text: str) -> int:
    """Conservative fallback when the configured model has no known tokenizer."""
    return max(1, (len(text) + 2) // 3)


def graph_batches(
    rows: list[dict[str, Any]],
    *,
    token_counter: Callable[[str], int] | None = None,
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Split processed pages into section batches within token and character budgets."""
    count_tokens = token_counter or _estimate_tokens
    sections: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        section_path = row.get("section_path")
        title = " › ".join(
            item for item in section_path if isinstance(item, str) and item.strip()
        ) if isinstance(section_path, list) else "Book contents"
        title = title or "Book contents"
        sections.setdefault(title, []).append(row)

    batches: list[tuple[str, list[dict[str, Any]]]] = []
    for title, source_rows in sections.items():
        section_header = f"Book section: {title}\n\n"
        chunk_size = max(
            1,
            params.GRAPH_BATCH_INPUT_TOKEN_BUDGET
            - count_tokens(section_header)
            - 256,
        )
        splitter = RecursiveCharacterTextSplitter(
            chunk_size = chunk_size,
            chunk_overlap = min(params.GRAPH_BATCH_TOKEN_OVERLAP, max(0, chunk_size - 1)),
            add_start_index = True,
            length_function = count_tokens,
            separators = ["\n\n", "\n", " ", ""],
        )
        section_rows: list[dict[str, Any]] = []
        for row in source_rows:
            for chunk in splitter.create_documents([row["content"]]):
                content = chunk.page_content.strip()
                if content:
                    section_rows.append({
                        **row,
                        "content": content,
                        "source_char_start": int(row.get("source_char_start") or 0)
                        + int(chunk.metadata.get("start_index") or 0),
                    })

        batch: list[dict[str, Any]] = []
        batch_chars = 0
        for row in section_rows:
            row_chars = len(row["content"])
            candidate = [*batch, row]
            candidate_text = section_header + source_material(candidate)
            exceeds_token_budget = count_tokens(candidate_text) > params.GRAPH_BATCH_INPUT_TOKEN_BUDGET
            exceeds_character_budget = batch_chars + row_chars > params.GRAPH_BATCH_CHARS
            if batch and (exceeds_token_budget or exceeds_character_budget):
                batches.append((title, batch))
                batch = []
                batch_chars = 0
            batch.append(row)
            batch_chars += row_chars
        if batch:
            batches.append((title, batch))
    return batches


def source_material(rows: list[dict[str, Any]]) -> str:
    """Format a readable source document with processed and original page numbers."""
    return "\n\n".join(
        f"Processed page {row['processed_page_number']} (source page {row['source_page_number']}):\n"
        f"{row['content']}"
        for row in rows
    )


def graph_facts(extraction: Any) -> dict[str, list[dict[str, Any]]]:
    """Project Docling Graph's typed extraction into the storage contract."""
    return {
        "entities": list(extraction.entities),
        "relationships": list(extraction.relationships),
    }


def _string_value(value: Any) -> str:
    """Return a usable evidence string from a graph property value."""
    if isinstance(value, (list, tuple)):
        value = next((item for item in value if item is not None), None)
    return str(value or "").strip()


def _normalized(value: str) -> str:
    return patterns.SPACE_RE.sub(" ", value).strip().casefold()


def _normalized_entity_type(value: str) -> str:
    return _normalized(value) or "entity"


def _resolve_entity_id(
    name: str,
    entity_type: str,
    entity_ids: dict[tuple[str, str], str],
    entity_ids_by_name: dict[str, set[tuple[str, str]]],
) -> str | None:
    normalized_name = _normalized(name)
    if not normalized_name:
        return None
    if entity_type:
        return entity_ids.get((normalized_name, _normalized_entity_type(entity_type)))
    candidates = entity_ids_by_name.get(normalized_name, set())
    if len(candidates) != 1:
        return None
    return entity_ids.get(next(iter(candidates)))


def _normalized_relation_kind(value: str) -> str:
    """Normalize an open-vocabulary predicate into a safe Neo4j token."""
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    ascii_value = "".join(
        character
        for character in decomposed
        if unicodedata.category(character) != "Mn" and character.isascii()
    )
    return re.sub(r"[^A-Z0-9]+", "_", ascii_value.upper()).strip("_")


def neo4j_relationship_type(kind: str, *, semantic: bool = True) -> str:
    """Return a stable structural type or a sanitized semantic type.

    Semantic predicates are intentionally open-vocabulary: retaining a
    meaningful model-extracted relation is preferable to mapping it to a
    generic edge. Normalization keeps the dynamic Cypher token safe.
    """
    if not semantic:
        return params.GRAPH_STRUCTURAL_RELATIONSHIP_TYPES_BY_KIND.get(
            kind,
            params.GRAPH_FALLBACK_RELATIONSHIP_TYPE,
        )
    semantic_kind = _normalized_relation_kind(kind)
    semantic_prefix = "BD_REL_"
    max_semantic_length = (
        params.GRAPH_MAX_SEMANTIC_RELATIONSHIP_TYPE_LENGTH - len(semantic_prefix)
    )
    if len(semantic_kind) > max_semantic_length:
        digest = hashlib.sha256(semantic_kind.encode("utf-8")).hexdigest()[:12]
        semantic_kind = f"{semantic_kind[:max_semantic_length - len(digest) - 1]}_{digest}"
    if semantic_kind:
        return f"{semantic_prefix}{semantic_kind}"
    return f"{semantic_prefix}RELATES"


def _name_pattern(name: str) -> re.Pattern[str] | None:
    parts = [re.escape(part) for part in patterns.SPACE_RE.split(name.strip()) if part]
    if not parts:
        return None
    expression = r"\s+".join(parts)
    return re.compile(rf"(?<!\w){expression}(?!\w)", re.IGNORECASE)


def _contains_name(name: str, content: str) -> bool:
    pattern = _name_pattern(name)
    return bool(pattern and pattern.search(content))


def _source_excerpt(name: str, content: str) -> str:
    pattern = _name_pattern(name)
    match = pattern.search(content) if pattern else None
    if match is None:
        return ""
    start = max(0, match.start() - 80)
    end = min(len(content), match.end() + 80)
    return content[start:end].strip()


def _relationship_sentence(source_name: str, target_name: str, content: str) -> str:
    """Return an exact sentence that mentions both relation endpoints."""
    for match in patterns.SENTENCE_RE.finditer(content):
        sentence = match.group().strip()
        if _contains_name(source_name, sentence) and _contains_name(target_name, sentence):
            return sentence
    return ""


def _relationship_citation(
    source_name: str,
    target_name: str,
    row: dict[str, Any],
    supplied_evidence: str,
) -> dict[str, Any] | None:
    content = str(row.get("content") or "")
    sentence = _relationship_sentence(source_name, target_name, content)
    if not sentence:
        return None
    quote = sentence
    if (
        supplied_evidence
        and supplied_evidence.strip() in sentence
        and _contains_name(source_name, supplied_evidence)
        and _contains_name(target_name, supplied_evidence)
    ):
        quote = supplied_evidence.strip()
    return {
        "processed_page_number": row.get("processed_page_number"),
        "source_page_number": row.get("source_page_number"),
        "quote": quote,
    } if quote else None


def _merge_edge_properties(previous: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = dict(previous)
    for field in ("processed_page_numbers", "source_page_numbers"):
        values = set(merged.get(field, [])) | set(incoming.get(field, []))
        merged[field] = sorted(value for value in values if type(value) is int)
    citations = {
        (item.get("processed_page_number"), item.get("source_page_number")): item
        for item in [*merged.get("citations", []), *incoming.get("citations", [])]
        if isinstance(item, dict)
    }
    merged["citations"] = list(citations.values())
    evidence = list(dict.fromkeys([
        *merged.get("evidence", []),
        *incoming.get("evidence", []),
    ]))
    merged["evidence"] = [item for item in evidence if isinstance(item, str) and item.strip()]
    evidence_ids = list(dict.fromkeys([
        *merged.get("evidence_ids", []),
        *incoming.get("evidence_ids", []),
    ]))
    merged["evidence_ids"] = [item for item in evidence_ids if isinstance(item, str) and item.strip()]
    return merged


def _stable_id(book_id: str, run_id: str, revision: int, kind: str, source_id: str) -> str:
    raw = f"bd:{book_id}:{run_id}:{revision}:{kind}:{source_id}".encode()
    return hashlib.sha256(raw).hexdigest()


def _edge(
    edges: dict[str, entities.GraphEdge],
    *,
    book_id: str,
    run_id: str,
    revision: int,
    source_id: str,
    target_id: str,
    kind: str,
    properties: dict[str, Any] | None = None,
    semantic: bool = False,
) -> None:
    edge_id = hashlib.sha256(
        f"bd:{book_id}:{run_id}:{revision}:{source_id}:{kind}:{target_id}".encode(),
    ).hexdigest()
    edge_properties = properties or {}
    existing = edges.get(edge_id)
    if existing is not None:
        edge_properties = _merge_edge_properties(existing.properties, edge_properties)
    edges[edge_id] = entities.GraphEdge(
        id = edge_id,
        source_id = source_id,
        target_id = target_id,
        kind = kind,
        properties = edge_properties,
        semantic = semantic,
    )


def build_graph(
    rows: list[dict[str, Any]],
    extractions: list[dict[str, Any]],
    *,
    book_id: str,
    agentic_run_id: str,
    revision: int,
) -> tuple[list[entities.GraphNode], list[entities.GraphEdge]]:
    """Project source pages, sections, extracted entities, and relations into graph rows."""
    nodes: dict[str, entities.GraphNode] = {}
    edges: dict[str, entities.GraphEdge] = {}
    book_node_id = _stable_id(book_id, agentic_run_id, revision, "book", book_id)
    nodes[book_node_id] = entities.GraphNode(
        id = book_node_id,
        kind = "book",
        name = book_id,
        properties = {
            "book_id": book_id,
            "agentic_run_id": agentic_run_id,
            "processed_book_revision": revision,
        },
    )

    section_nodes: dict[tuple[str, ...], str] = {}
    page_nodes: dict[int, str] = {}
    for row in rows:
        section_path = tuple(
            part for part in row.get("section_path", [])
            if isinstance(part, str) and part.strip()
        )
        parent_id = book_node_id
        for depth in range(1, len(section_path) + 1):
            path = section_path[:depth]
            section_node_id = section_nodes.get(path)
            if section_node_id is None:
                section_node_id = _stable_id(
                    book_id,
                    agentic_run_id,
                    revision,
                    "section",
                    " › ".join(path),
                )
                section_nodes[path] = section_node_id
                nodes[section_node_id] = entities.GraphNode(
                    id = section_node_id,
                    kind = "section",
                    name = path[-1],
                    properties = {
                        "book_id": book_id,
                        "agentic_run_id": agentic_run_id,
                        "processed_book_revision": revision,
                        "section_path": json.dumps(path, ensure_ascii = False),
                    },
                )
                _edge(
                    edges,
                    book_id = book_id,
                    run_id = agentic_run_id,
                    revision = revision,
                    source_id = parent_id,
                    target_id = section_node_id,
                    kind = "contains_section",
                )
            parent_id = section_node_id

        page_number = row["processed_page_number"]
        page_node_id = _stable_id(book_id, agentic_run_id, revision, "page", str(page_number))
        page_nodes[page_number] = page_node_id
        nodes[page_node_id] = entities.GraphNode(
            id = page_node_id,
            kind = "source_page",
            name = f"Page {page_number}",
            properties = {
                "book_id": book_id,
                "agentic_run_id": agentic_run_id,
                "processed_book_revision": revision,
                "processed_page_number": page_number,
                "source_page_number": row["source_page_number"],
                "content_role": row["content_role"],
                "content_sha256": row["content_sha256"],
            },
        )
        _edge(
            edges,
            book_id = book_id,
            run_id = agentic_run_id,
            revision = revision,
            source_id = parent_id,
            target_id = page_node_id,
            kind = "contains_page",
        )

    entity_ids: dict[tuple[str, str], str] = {}
    entity_ids_by_name: dict[str, set[tuple[str, str]]] = {}
    entity_names: dict[str, str] = {}
    for batch in extractions:
        section_title = batch["section_title"]
        source_rows = batch["rows"]
        extraction = batch["extraction"]
        section_path = tuple(part for part in section_title.split(" › ") if part)
        parent_id = section_nodes.get(section_path, book_node_id)
        for candidate in extraction["entities"]:
            name = candidate["id"]
            entity_type = candidate["type"]
            normalized_name = _normalized(name)
            if not normalized_name:
                continue
            entity_key = (normalized_name, _normalized_entity_type(entity_type))
            entity_id = entity_ids.get(entity_key)
            if entity_id is None:
                entity_id = _stable_id(
                    book_id,
                    agentic_run_id,
                    revision,
                    "entity",
                    f"{entity_type}:{normalized_name}",
                )
                entity_ids[entity_key] = entity_id
                entity_ids_by_name.setdefault(normalized_name, set()).add(entity_key)
                entity_names[entity_id] = name

            matches = [row for row in source_rows if _contains_name(name, row["content"])]
            page_numbers = sorted({row["processed_page_number"] for row in matches})
            citations_by_page = {
                (row["processed_page_number"], row["source_page_number"]): {
                    "processed_page_number": row["processed_page_number"],
                    "source_page_number": row["source_page_number"],
                    "quote": _source_excerpt(name, row["content"]),
                }
                for row in matches
            }
            existing_node = nodes.get(entity_id)
            if existing_node is not None:
                page_numbers = sorted(
                    set(json.loads(existing_node.properties.get("page_numbers", "[]")))
                    | set(page_numbers)
                )
                previous_citations = json.loads(existing_node.properties.get("citations", "[]"))
                citations_by_page.update({
                    (item["processed_page_number"], item["source_page_number"]): item
                    for item in previous_citations
                })
                entity_type = str(existing_node.properties.get("entity_type") or entity_type)

            nodes[entity_id] = entities.GraphNode(
                id = entity_id,
                kind = "entity",
                name = name,
                properties = {
                    "book_id": book_id,
                    "agentic_run_id": agentic_run_id,
                    "processed_book_revision": revision,
                    "entity_type": entity_type,
                    "page_numbers": json.dumps(page_numbers),
                    "citations": json.dumps(list(citations_by_page.values()), ensure_ascii = False),
                },
            )
            _edge(
                edges,
                book_id = book_id,
                run_id = agentic_run_id,
                revision = revision,
                source_id = parent_id,
                target_id = entity_id,
                kind = "contains_entity",
            )
            for row in matches:
                page_node_id = page_nodes.get(row["processed_page_number"])
                if page_node_id is not None:
                    _edge(
                        edges,
                        book_id = book_id,
                        run_id = agentic_run_id,
                        revision = revision,
                        source_id = entity_id,
                        target_id = page_node_id,
                        kind = "supported_by",
                        properties = {
                            "processed_page_numbers": [row["processed_page_number"]],
                            "source_page_numbers": [row["source_page_number"]],
                            "citations": [{
                                "processed_page_number": row["processed_page_number"],
                                "source_page_number": row["source_page_number"],
                                "quote": _source_excerpt(name, row["content"]),
                            }],
                            "evidence": [],
                        },
                    )

    for batch in extractions:
        for relationship in batch["extraction"]["relationships"]:
            source_id = _resolve_entity_id(
                relationship["source_id"],
                str(relationship.get("source_type") or ""),
                entity_ids,
                entity_ids_by_name,
            )
            target_id = _resolve_entity_id(
                relationship["target_id"],
                str(relationship.get("target_type") or ""),
                entity_ids,
                entity_ids_by_name,
            )
            if source_id is not None and target_id is not None:
                raw_properties = relationship.get("properties", {})
                supplied_evidence = _string_value(
                    raw_properties.get("evidence") or raw_properties.get("quote")
                ) if isinstance(raw_properties, dict) else ""
                citations = [
                    citation
                    for row in batch["rows"]
                    if (citation := _relationship_citation(
                        entity_names.get(source_id, relationship["source_id"]),
                        entity_names.get(target_id, relationship["target_id"]),
                        row,
                        supplied_evidence,
                    )) is not None
                ]
                if not citations:
                    continue
                relation_kind = relationship["type"]
                evidence_ids: list[str] = []
                for citation in citations:
                    page_number = citation.get("processed_page_number")
                    source_page_number = citation.get("source_page_number")
                    quote = str(citation.get("quote") or "")
                    if type(page_number) is not int or not quote:
                        continue
                    evidence_id = _stable_id(
                        book_id,
                        agentic_run_id,
                        revision,
                        "relationship_evidence",
                        ":".join((
                            source_id,
                            relation_kind,
                            target_id,
                            str(page_number),
                            str(source_page_number),
                            hashlib.sha256(quote.encode("utf-8")).hexdigest(),
                        )),
                    )
                    evidence_ids.append(evidence_id)
                    nodes[evidence_id] = entities.GraphNode(
                        id = evidence_id,
                        kind = "relationship_evidence",
                        name = f"{entity_names.get(source_id, relationship['source_id'])} {relation_kind} {entity_names.get(target_id, relationship['target_id'])}",
                        properties = {
                            "book_id": book_id,
                            "agentic_run_id": agentic_run_id,
                            "processed_book_revision": revision,
                            "relation_kind": relation_kind,
                            "source_entity_id": source_id,
                            "target_entity_id": target_id,
                            "processed_page_number": page_number,
                            "source_page_number": source_page_number,
                            "quote": quote,
                        },
                    )
                    _edge(
                        edges,
                        book_id = book_id,
                        run_id = agentic_run_id,
                        revision = revision,
                        source_id = source_id,
                        target_id = evidence_id,
                        kind = "has_evidence",
                    )
                    _edge(
                        edges,
                        book_id = book_id,
                        run_id = agentic_run_id,
                        revision = revision,
                        source_id = evidence_id,
                        target_id = target_id,
                        kind = "evidence_target",
                    )
                    page_node_id = page_nodes.get(page_number)
                    if page_node_id is not None:
                        _edge(
                            edges,
                            book_id = book_id,
                            run_id = agentic_run_id,
                            revision = revision,
                            source_id = evidence_id,
                            target_id = page_node_id,
                            kind = "supported_by",
                            properties = {
                                "processed_page_numbers": [page_number],
                                "source_page_numbers": [source_page_number],
                                "citations": [citation],
                                "evidence": [quote],
                            },
                        )
                _edge(
                    edges,
                    book_id = book_id,
                    run_id = agentic_run_id,
                    revision = revision,
                    source_id = source_id,
                    target_id = target_id,
                    kind = relation_kind,
                    semantic = True,
                    properties = {
                        "processed_page_numbers": sorted({
                            item["processed_page_number"]
                            for item in citations
                            if type(item.get("processed_page_number")) is int
                        }),
                        "source_page_numbers": sorted({
                            item["source_page_number"]
                            for item in citations
                            if type(item.get("source_page_number")) is int
                        }),
                        "citations": citations,
                        "evidence_ids": evidence_ids,
                        "evidence": list(dict.fromkeys(
                            item["quote"] for item in citations if item.get("quote")
                        )),
                        "model_properties": json.dumps(
                            raw_properties if isinstance(raw_properties, dict) else {},
                            ensure_ascii = False,
                        ),
                    },
                )
    return list(nodes.values()), list(edges.values())
