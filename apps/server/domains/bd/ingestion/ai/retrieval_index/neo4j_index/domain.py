"""Deterministic page batching and Neo4j graph projection for BD books."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

from .. import params
from . import patterns


@dataclass(frozen = True, slots = True)
class GraphNode:
    id: str
    kind: str
    name: str
    properties: dict[str, str | int | float | bool | None]


@dataclass(frozen = True, slots = True)
class GraphEdge:
    id: str
    source_id: str
    target_id: str
    kind: str


def graph_batches(rows: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """Split processed page text into chapter-scoped transformer documents."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size = params.GRAPH_BATCH_CHARS // 2,
        chunk_overlap = 240,
        add_start_index = True,
        separators = ["\n\n", "\n", " ", ""],
    )
    sections: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        section_path = row.get("section_path")
        title = " › ".join(
            item for item in section_path if isinstance(item, str) and item.strip()
        ) if isinstance(section_path, list) else "Book contents"
        title = title or "Book contents"
        for chunk in splitter.create_documents([row["content"]]):
            content = chunk.page_content.strip()
            if content:
                sections.setdefault(title, []).append({
                    **row,
                    "content": content,
                    "source_char_start": chunk.metadata.get("start_index", 0),
                })

    batches: list[tuple[str, list[dict[str, Any]]]] = []
    for title, section_rows in sections.items():
        batch: list[dict[str, Any]] = []
        batch_chars = 0
        for row in section_rows:
            row_chars = len(row["content"])
            if batch and batch_chars + row_chars > params.GRAPH_BATCH_CHARS:
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


def graph_facts(candidate: Any) -> dict[str, list[dict[str, str]]]:
    """Convert LangChain's GraphDocument into plain facts for the storage layer."""
    entities: list[dict[str, str]] = []
    for node in _value(candidate, "nodes", []) or []:
        name = _string_value(_value(node, "id"))
        entity_type = _string_value(_value(node, "type")) or "Entity"
        if name:
            entities.append({"id": name, "type": entity_type})

    relationships: list[dict[str, str]] = []
    for relationship in _value(candidate, "relationships", []) or []:
        source_id = _string_value(_value(_value(relationship, "source"), "id"))
        target_id = _string_value(_value(_value(relationship, "target"), "id"))
        relation_type = _string_value(_value(relationship, "type"))
        if source_id and target_id and relation_type:
            relationships.append({
                "source_id": source_id,
                "target_id": target_id,
                "type": relation_type,
            })
    return {"entities": entities, "relationships": relationships}


def _value(candidate: Any, name: str, default: Any = None) -> Any:
    return candidate.get(name, default) if isinstance(candidate, dict) else getattr(candidate, name, default)


def _string_value(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        value = next((item for item in value if item is not None), None)
    return str(value or "").strip()


def _normalized(value: str) -> str:
    return patterns.SPACE_RE.sub(" ", value).strip().casefold()


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


def _stable_id(book_id: str, run_id: str, revision: int, kind: str, source_id: str) -> str:
    raw = f"bd:{book_id}:{run_id}:{revision}:{kind}:{source_id}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _edge(
    edges: dict[str, GraphEdge],
    *,
    book_id: str,
    run_id: str,
    revision: int,
    source_id: str,
    target_id: str,
    kind: str,
) -> None:
    edge_id = hashlib.sha256(
        f"bd:{book_id}:{run_id}:{revision}:{source_id}:{kind}:{target_id}".encode(),
    ).hexdigest()
    edges[edge_id] = GraphEdge(edge_id, source_id, target_id, kind)


def build_graph(
    rows: list[dict[str, Any]],
    extractions: list[dict[str, Any]],
    *,
    book_id: str,
    agentic_run_id: str,
    revision: int,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Project source pages, sections, transformer entities, and relations into graph rows."""
    nodes: dict[str, GraphNode] = {}
    edges: dict[str, GraphEdge] = {}
    book_node_id = _stable_id(book_id, agentic_run_id, revision, "book", book_id)
    nodes[book_node_id] = GraphNode(
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
                nodes[section_node_id] = GraphNode(
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
        nodes[page_node_id] = GraphNode(
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

    entity_ids: dict[str, str] = {}
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
            entity_id = entity_ids.get(normalized_name)
            if entity_id is None:
                entity_id = _stable_id(
                    book_id,
                    agentic_run_id,
                    revision,
                    "entity",
                    f"{entity_type}:{normalized_name}",
                )
                entity_ids[normalized_name] = entity_id

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

            nodes[entity_id] = GraphNode(
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
                    )

    for batch in extractions:
        for relationship in batch["extraction"]["relationships"]:
            source_key = _normalized(relationship["source_id"])
            target_key = _normalized(relationship["target_id"])
            source_id = entity_ids.get(source_key)
            target_id = entity_ids.get(target_key)
            if source_id is not None and target_id is not None:
                _edge(
                    edges,
                    book_id = book_id,
                    run_id = agentic_run_id,
                    revision = revision,
                    source_id = source_id,
                    target_id = target_id,
                    kind = relationship["type"],
                )
    return list(nodes.values()), list(edges.values())
