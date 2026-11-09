"""Pure transformations from a validated Learning Guide to graph records."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from . import entities


_WORD_PATTERN = re.compile(r"\s+")


def _stable_id(book_id: str, run_id: str, kind: str, source_id: str) -> str:
    raw = f"bd:{book_id}:{run_id}:{kind}:{source_id}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _citation_pages(refs: list[Any]) -> list[int]:
    return sorted({
        ref.page_number
        for ref in refs
        if type(getattr(ref, "page_number", None)) is int and ref.page_number > 0
    })


def _citation_payload(refs: list[Any]) -> str:
    return json.dumps(
        [
            {
                "page_number": ref.page_number,
                "quote": ref.quote,
                "source_key": ref.source_key,
                "source_sha256": ref.source_sha256,
            }
            for ref in refs
        ],
        ensure_ascii = False,
        separators = (",", ":"),
        sort_keys = True,
    )


def _add_node(
    nodes: dict[str, entities.GraphNode],
    *,
    book_id: str,
    run_id: str,
    kind: str,
    source_id: str,
    name: str,
    properties: dict[str, str | int | float | bool | None],
) -> str:
    node_id = _stable_id(book_id, run_id, kind, source_id)
    nodes[node_id] = entities.GraphNode(
        id = node_id,
        kind = kind,
        name = name,
        properties = properties,
    )
    return node_id


def _add_edge(
    edges: dict[str, entities.GraphEdge],
    *,
    book_id: str,
    run_id: str,
    source_id: str,
    target_id: str,
    kind: str,
    evidence: str | None = None,
) -> None:
    edge_id = hashlib.sha256(
        f"bd:{book_id}:{run_id}:{source_id}:{kind}:{target_id}:{evidence or ''}".encode("utf-8"),
    ).hexdigest()
    edges[edge_id] = entities.GraphEdge(
        id = edge_id,
        source_id = source_id,
        target_id = target_id,
        kind = kind,
        evidence = evidence,
    )


def learning_guide_graph(
    report: Any,
    *,
    book_id: str,
    run_id: str,
) -> tuple[list[entities.GraphNode], list[entities.GraphEdge]]:
    """Create a deterministic provenance graph from verified report structures."""
    nodes: dict[str, entities.GraphNode] = {}
    edges: dict[str, entities.GraphEdge] = {}
    book_node = _add_node(
        nodes,
        book_id = book_id,
        run_id = run_id,
        kind = "book",
        source_id = book_id,
        name = report.title,
        properties = {
            "book_id": book_id,
            "distill_run_id": run_id,
            "extraction_run_id": report.extraction_run_id,
            "page_count": report.source_page_count,
        },
    )

    page_nodes: dict[int, str] = {}

    def page_node(page_number: int) -> str:
        if page_number not in page_nodes:
            page_nodes[page_number] = _add_node(
                nodes,
                book_id = book_id,
                run_id = run_id,
                kind = "source_page",
                source_id = str(page_number),
                name = f"Page {page_number}",
                properties = {
                    "book_id": book_id,
                    "distill_run_id": run_id,
                    "page_number": page_number,
                },
            )
            _add_edge(
                edges,
                book_id = book_id,
                run_id = run_id,
                source_id = book_node,
                target_id = page_nodes[page_number],
                kind = "contains_page",
            )
        return page_nodes[page_number]

    def lesson_node(
        lesson: Any,
        *,
        parent_id: str,
        source_id: str,
        kind: str = "lesson",
    ) -> str:
        refs = list(getattr(lesson, "source_refs", []))
        lesson_id = _add_node(
            nodes,
            book_id = book_id,
            run_id = run_id,
            kind = kind,
            source_id = source_id,
            name = lesson.title,
            properties = {
                "book_id": book_id,
                "distill_run_id": run_id,
                "explanation": lesson.explanation,
                "attribution": lesson.attribution,
                "verification_status": lesson.verification_status,
                "confidence": lesson.confidence,
                "page_numbers": json.dumps(_citation_pages(refs)),
                "citations": _citation_payload(refs),
            },
        )
        _add_edge(
            edges,
            book_id = book_id,
            run_id = run_id,
            source_id = parent_id,
            target_id = lesson_id,
            kind = "contains",
        )
        for ref in refs:
            source_page = page_node(ref.page_number)
            _add_edge(
                edges,
                book_id = book_id,
                run_id = run_id,
                source_id = lesson_id,
                target_id = source_page,
                kind = "supported_by",
                evidence = ref.quote,
            )
        return lesson_id

    for chapter_index, chapter in enumerate(report.chapters):
        chapter_id = _add_node(
            nodes,
            book_id = book_id,
            run_id = run_id,
            kind = "chapter",
            source_id = f"{chapter_index}:{chapter.title}",
            name = chapter.title,
            properties = {
                "book_id": book_id,
                "distill_run_id": run_id,
                "overview": chapter.overview,
                "page_numbers": json.dumps(_citation_pages(chapter.overview_source_refs)),
            },
        )
        _add_edge(
            edges,
            book_id = book_id,
            run_id = run_id,
            source_id = book_node,
            target_id = chapter_id,
            kind = "contains",
        )
        for lesson_index, lesson in enumerate(chapter.lessons):
            lesson_node(
                lesson,
                parent_id = chapter_id,
                source_id = f"chapter:{chapter_index}:lesson:{lesson_index}:{lesson.title}",
            )
        for question_index, lesson in enumerate(chapter.open_questions):
            lesson_node(
                lesson,
                parent_id = chapter_id,
                source_id = f"chapter:{chapter_index}:question:{question_index}:{lesson.title}",
                kind = "open_question",
            )

    for group_name, lessons in (
        ("key_takeaway", report.key_takeaways),
        ("cross_chapter_connection", report.cross_chapter_connections),
        ("limitation", report.limitation_notes),
    ):
        for index, lesson in enumerate(lessons):
            lesson_node(
                lesson,
                parent_id = book_node,
                source_id = f"{group_name}:{index}:{lesson.title}",
                kind = group_name,
            )

    for index, entry in enumerate(report.glossary):
        normalized_term = _WORD_PATTERN.sub(" ", entry.term.strip()).casefold()
        concept_id = _add_node(
            nodes,
            book_id = book_id,
            run_id = run_id,
            kind = "concept",
            source_id = f"{index}:{normalized_term}",
            name = entry.term,
            properties = {
                "book_id": book_id,
                "distill_run_id": run_id,
                "definition": entry.definition,
                "attribution": entry.attribution,
                "verification_status": entry.verification_status,
                "page_numbers": json.dumps(_citation_pages(entry.source_refs)),
                "citations": _citation_payload(entry.source_refs),
            },
        )
        _add_edge(
            edges,
            book_id = book_id,
            run_id = run_id,
            source_id = book_node,
            target_id = concept_id,
            kind = "defines",
        )
        for ref in entry.source_refs:
            source_page = page_node(ref.page_number)
            _add_edge(
                edges,
                book_id = book_id,
                run_id = run_id,
                source_id = concept_id,
                target_id = source_page,
                kind = "supported_by",
                evidence = ref.quote,
            )

    for scope, diagrams in (
        ("book", report.diagrams),
        *[(f"chapter:{index}", chapter.diagrams) for index, chapter in enumerate(report.chapters)],
    ):
        for diagram_index, diagram in enumerate(diagrams):
            diagram_id = _add_node(
                nodes,
                book_id = book_id,
                run_id = run_id,
                kind = "diagram",
                source_id = f"{scope}:{diagram_index}:{diagram.title}",
                name = diagram.title,
                properties = {
                    "book_id": book_id,
                    "distill_run_id": run_id,
                    "description": diagram.accessible_description,
                },
            )
            _add_edge(
                edges,
                book_id = book_id,
                run_id = run_id,
                source_id = book_node,
                target_id = diagram_id,
                kind = "contains",
            )
            node_ids: dict[str, str] = {}
            for item in diagram.nodes:
                graph_node_id = _add_node(
                    nodes,
                    book_id = book_id,
                    run_id = run_id,
                    kind = "diagram_concept",
                    source_id = f"{scope}:{diagram_index}:{item.node_id}",
                    name = item.label,
                    properties = {
                        "book_id": book_id,
                        "distill_run_id": run_id,
                        "attribution": item.attribution,
                        "evidence": json.dumps(
                            [quote.model_dump(mode = "json") for quote in item.evidence],
                            ensure_ascii = False,
                            separators = (",", ":"),
                            sort_keys = True,
                        ),
                    },
                )
                node_ids[item.node_id] = graph_node_id
                _add_edge(
                    edges,
                    book_id = book_id,
                    run_id = run_id,
                    source_id = diagram_id,
                    target_id = graph_node_id,
                    kind = "contains",
                )
            for edge_index, item in enumerate(diagram.edges):
                _add_edge(
                    edges,
                    book_id = book_id,
                    run_id = run_id,
                    source_id = node_ids[item.source_id],
                    target_id = node_ids[item.target_id],
                    kind = f"diagram_{item.relation}",
                    evidence = " ".join(quote.quote for quote in item.evidence),
                )

    return list(nodes.values()), list(edges.values())


def add_extracted_graph(
    nodes: list[entities.GraphNode],
    edges: list[entities.GraphEdge],
    extraction: Any,
    *,
    book_id: str,
    run_id: str,
    section_title: str,
    source_refs: list[Any],
) -> tuple[list[entities.GraphNode], list[entities.GraphEdge]]:
    """Merge validated section graph candidates with exact page-level provenance."""
    node_map = {node.id: node for node in nodes}
    edge_map = {edge.id: edge for edge in edges}
    entity_ids: dict[str, str] = {}
    book_node_id = _stable_id(book_id, run_id, "book", book_id)
    normalized_parts = {
        _WORD_PATTERN.sub(" ", part).strip().casefold()
        for part in section_title.split(" › ")
        if part.strip()
    }
    chapter_candidates = [
        node for node in node_map.values()
        if node.kind == "chapter"
        and _WORD_PATTERN.sub(" ", node.name).strip().casefold() in normalized_parts
    ]
    parent_chapter_id = max(chapter_candidates, key = lambda node: len(node.name)).id if chapter_candidates else book_node_id
    section_id = _stable_id(book_id, run_id, "source_section", section_title)
    if section_id not in node_map:
        node_map[section_id] = entities.GraphNode(
            id = section_id,
            kind = "source_section",
            name = section_title,
            properties = {
                "book_id": book_id,
                "distill_run_id": run_id,
            },
        )
    _add_edge(
        edge_map,
        book_id = book_id,
        run_id = run_id,
        source_id = parent_chapter_id,
        target_id = section_id,
        kind = "contains_source_section",
    )

    for ref in source_refs:
        page_id = _stable_id(book_id, run_id, "source_page", str(ref.page_number))
        prior_page = node_map.get(page_id)
        page_properties = dict(prior_page.properties) if prior_page is not None else {}
        page_properties.update({
            "book_id": book_id,
            "distill_run_id": run_id,
            "page_number": ref.page_number,
            "content_role": getattr(ref, "content_role", "unknown"),
            "content_disposition": getattr(ref, "content_disposition", "include"),
        })
        node_map[page_id] = entities.GraphNode(
            id = page_id,
            kind = "source_page",
            name = f"Page {ref.page_number}",
            properties = page_properties,
        )
        _add_edge(
            edge_map,
            book_id = book_id,
            run_id = run_id,
            source_id = book_node_id,
            target_id = page_id,
            kind = "contains_page",
        )
        _add_edge(
            edge_map,
            book_id = book_id,
            run_id = run_id,
            source_id = section_id,
            target_id = page_id,
            kind = "section_contains_page",
        )

    for candidate in extraction.entities:
        normalized_name = _WORD_PATTERN.sub(" ", candidate.entity_id.strip()).casefold()
        stable_key = f"{candidate.entity_type}:{normalized_name}"
        entity_node_id = _stable_id(book_id, run_id, "agent_entity", stable_key)
        entity_ids[normalized_name] = entity_node_id
        matching_refs = [
            ref for ref in source_refs
            if re.sub(r"\s+", " ", candidate.evidence).strip().casefold()
            in re.sub(r"\s+", " ", ref.quote).strip().casefold()
        ]
        pages = _citation_pages(matching_refs)
        citations = [
            {"page_number": ref.page_number, "quote": candidate.evidence}
            for ref in matching_refs
        ]
        prior = node_map.get(entity_node_id)
        prior_pages: list[int] = []
        prior_citations: list[dict[str, Any]] = []
        if prior is not None:
            try:
                prior_pages = json.loads(str(prior.properties.get("page_numbers") or "[]"))
                prior_citations = json.loads(str(prior.properties.get("citations") or "[]"))
            except json.JSONDecodeError:
                prior_pages = []
                prior_citations = []
        combined_pages = sorted(set(prior_pages + pages))
        combined_citations = prior_citations + [
            citation for citation in citations if citation not in prior_citations
        ]
        prior_sections = []
        if prior is not None:
            try:
                prior_sections = json.loads(str(prior.properties.get("section_titles") or "[]"))
            except json.JSONDecodeError:
                prior_sections = []
        node_map[entity_node_id] = entities.GraphNode(
            id = entity_node_id,
            kind = "agent_entity",
            name = candidate.entity_id,
            properties = {
                "book_id": book_id,
                "distill_run_id": run_id,
                "entity_type": candidate.entity_type,
                "section_titles": json.dumps(
                    sorted(set(prior_sections + [section_title])),
                    ensure_ascii = False,
                    separators = (",", ":"),
                ),
                "page_numbers": json.dumps(combined_pages, separators = (",", ":")),
                "citations": json.dumps(
                    combined_citations,
                    ensure_ascii = False,
                    separators = (",", ":"),
                    sort_keys = True,
                ),
            },
        )
        _add_edge(
            edge_map,
            book_id = book_id,
            run_id = run_id,
            source_id = section_id,
            target_id = entity_node_id,
            kind = "contains_graph_entity",
        )
        for ref in matching_refs:
            page_id = _stable_id(book_id, run_id, "source_page", str(ref.page_number))
            prior_page = node_map.get(page_id)
            page_properties = dict(prior_page.properties) if prior_page is not None else {}
            page_properties.update({
                "book_id": book_id,
                "distill_run_id": run_id,
                "page_number": ref.page_number,
                "content_role": getattr(ref, "content_role", "unknown"),
                "content_disposition": getattr(ref, "content_disposition", "include"),
            })
            node_map[page_id] = entities.GraphNode(
                id = page_id,
                kind = "source_page",
                name = f"Page {ref.page_number}",
                properties = page_properties,
            )
            _add_edge(
                edge_map,
                book_id = book_id,
                run_id = run_id,
                source_id = book_node_id,
                target_id = page_id,
                kind = "contains_page",
            )
            _add_edge(
                edge_map,
                book_id = book_id,
                run_id = run_id,
                source_id = entity_node_id,
                target_id = page_id,
                kind = "supported_by",
                evidence = candidate.evidence,
            )

    for relation in extraction.relationships:
        source_key = _WORD_PATTERN.sub(" ", relation.source_id.strip()).casefold()
        target_key = _WORD_PATTERN.sub(" ", relation.target_id.strip()).casefold()
        source_node_id = entity_ids.get(source_key)
        target_node_id = entity_ids.get(target_key)
        if source_node_id is None or target_node_id is None:
            continue
        _add_edge(
            edge_map,
            book_id = book_id,
            run_id = run_id,
            source_id = source_node_id,
            target_id = target_node_id,
            kind = f"agent_{relation.relationship_type}",
            evidence = relation.evidence,
        )
        for ref in source_refs:
            if re.sub(r"\s+", " ", relation.evidence).strip().casefold() not in re.sub(
                r"\s+", " ", ref.quote,
            ).strip().casefold():
                continue
            page_id = _stable_id(book_id, run_id, "source_page", str(ref.page_number))
            prior_page = node_map.get(page_id)
            page_properties = dict(prior_page.properties) if prior_page is not None else {}
            page_properties.update({
                "book_id": book_id,
                "distill_run_id": run_id,
                "page_number": ref.page_number,
                "content_role": getattr(ref, "content_role", "unknown"),
                "content_disposition": getattr(ref, "content_disposition", "include"),
            })
            node_map[page_id] = entities.GraphNode(
                id = page_id,
                kind = "source_page",
                name = f"Page {ref.page_number}",
                properties = page_properties,
            )
            _add_edge(
                edge_map,
                book_id = book_id,
                run_id = run_id,
                source_id = book_node_id,
                target_id = page_id,
                kind = "contains_page",
            )
            _add_edge(
                edge_map,
                book_id = book_id,
                run_id = run_id,
                source_id = source_node_id,
                target_id = page_id,
                kind = "supported_by",
                evidence = relation.evidence,
            )

    return list(node_map.values()), list(edge_map.values())
