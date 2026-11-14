"""Compose and persist one citation-resolvable Learning Guide."""
from __future__ import annotations

import hashlib
import json
from typing import Any

import domains

from .... import graphs


def _json_body(value: Any) -> str:
    return json.dumps(value, ensure_ascii = False, separators = (",", ":"), sort_keys = True)


async def publish_report(
    dependencies: graphs.distill.entities.DistillDependencies,
    state: dict[str, Any],
) -> dict[str, Any]:
    storage = dependencies.storage
    book_id = state["book_id"]
    run_id = state["run_id"]
    ingestion_ai_run_id = state["ingestion_ai_run_id"]
    learning_units: dict[str, dict[str, Any]] = {}
    unit_refs = state.get("completed_units", [])
    for ref in unit_refs:
        artifact_key = domains.bd.distill.keys.learning_unit_key(book_id, run_id, ref["unit_id"])
        artifact = json.loads(await storage.read_text(artifact_key))
        for lesson in artifact.get("lessons", []):
            unit_id = lesson.get("learning_unit_id")
            if isinstance(unit_id, str):
                learning_units[unit_id] = {
                    **lesson,
                    "chunk_id": artifact["unit_id"],
                    "source_key": artifact["source_key"],
                    "section_path": artifact.get("section_path", []),
                    "source_sha256": artifact["source_sha256"],
                }
    if not learning_units:
        raise ValueError("the Distill run has no source-grounded learning units to publish")

    def source_refs(unit_ids: list[str]) -> list[domains.bd.distill.schemas.SourceRef]:
        refs = []
        seen = set()
        for unit_id in domains.bd.distill.domain.validate_synthesis_unit_ids(
            unit_ids, set(learning_units),
        ):
            lesson = learning_units[unit_id]
            for evidence in lesson["evidence"]:
                identity = (lesson["chunk_id"], evidence["page_number"], evidence["quote"], lesson["attribution"])
                if identity in seen:
                    continue
                seen.add(identity)
                refs.append(domains.bd.distill.schemas.SourceRef(
                    source_type = lesson["attribution"],
                    book_id = book_id,
                    extraction_run_id = state["extraction_run_id"],
                    ingestion_ai_run_id = ingestion_ai_run_id,
                    chunk_id = lesson["chunk_id"],
                    source_key = lesson["source_key"],
                    page_number = evidence["page_number"],
                    processed_page_number = lesson.get("processed_page_number"),
                    section_path = lesson.get("section_path", []),
                    source_sha256 = lesson["source_sha256"],
                    quote = evidence["quote"],
                ))
        return refs

    def attribution_for(model_attribution: str, unit_ids: list[str]) -> str:
        if model_attribution == "agent_inference" or any(
            learning_units[unit_id]["attribution"] == "agent_inference"
            for unit_id in domains.bd.distill.domain.validate_synthesis_unit_ids(
                unit_ids, set(learning_units),
            )
        ):
            return "agent_inference"
        return "book"

    def report_lesson(item: dict[str, Any]) -> domains.bd.distill.schemas.ReportLesson:
        unit_ids = item["learning_unit_ids"]
        refs = source_refs(unit_ids)
        supporting = [learning_units[unit_id] for unit_id in unit_ids]
        return domains.bd.distill.schemas.ReportLesson(
            title = item["title"],
            explanation = item["explanation"],
            source_refs = refs,
            attribution = attribution_for(item.get("attribution", "book"), unit_ids),
            verification_status = (
                "verified" if all(unit["verification_status"] == "verified" for unit in supporting) else "unverified"
            ),
            confidence = min(unit["confidence"] for unit in supporting),
        )

    synthesis_record = json.loads(await storage.read_text(state["book_synthesis_key"]))
    book_synthesis = domains.bd.distill.schemas.BookSynthesis.model_validate(synthesis_record["synthesis"])
    all_unit_ids = set(learning_units)
    book_referenced_ids = set(book_synthesis.central_thesis_learning_unit_ids)
    for item in [*book_synthesis.key_takeaways, *book_synthesis.cross_chapter_connections, *book_synthesis.limitations]:
        book_referenced_ids.update(item.learning_unit_ids)
    if book_referenced_ids - all_unit_ids:
        raise ValueError("the whole-book synthesis contains unresolved learning-unit citations")

    chapters = []
    chapter_charts = []
    visual_review_notes = []
    citation_review_notes = []
    ordered_chapters = []
    for ref in state.get("completed_chapters", []):
        artifact = json.loads(await storage.read_text(ref["artifact_key"]))
        chapter = domains.bd.distill.schemas.ChapterSynthesis.model_validate(artifact["synthesis"])
        ordered_chapters.append((min(
            (learning_units[unit_id]["evidence"][0]["page_number"] for unit_id in ref["unit_ids"]),
            default = 0,
        ), artifact, chapter))
    ordered_chapters.sort(key = lambda item: item[0])
    for _, artifact, chapter in ordered_chapters:
        visual_review_notes.extend(
            f"Chapter {artifact['title']}: {flag}"
            for flag in artifact.get("visual_review_flags", [])
            if isinstance(flag, str) and flag
        )
        citation_review_notes.extend(
            f"Chapter {artifact['title']}: {flag}"
            for flag in artifact.get("citation_review_flags", [])
            if isinstance(flag, str) and flag
        )
        diagrams = [
            diagram.model_copy(update = {
                "nodes": [
                    node.model_copy(update = {
                        "attribution": attribution_for(node.attribution, node.learning_unit_ids),
                    })
                    for node in diagram.nodes
                ],
                "edges": [
                    edge.model_copy(update = {
                        "attribution": attribution_for(edge.attribution, edge.learning_unit_ids),
                    })
                    for edge in diagram.edges
                ],
            })
            for diagram in chapter.diagrams
        ]
        equations = [
            equation.model_copy(update = {
                "attribution": attribution_for(equation.attribution, equation.learning_unit_ids),
            })
            for equation in chapter.equations
        ]
        chapters.append(domains.bd.distill.schemas.ReportSection(
            title = artifact["title"],
            overview = chapter.overview,
            overview_attribution = attribution_for(
                chapter.overview_attribution, chapter.overview_learning_unit_ids,
            ),
            overview_source_refs = source_refs(chapter.overview_learning_unit_ids),
            lessons = [report_lesson(item.model_dump(mode = "json")) for item in chapter.lessons],
            open_questions = [report_lesson(item.model_dump(mode = "json")) for item in chapter.open_questions],
            diagrams = diagrams,
            equations = equations,
            charts = chapter.charts,
        ))
        chapter_charts.extend(chapter.charts)

    covered_pages = sorted({
        evidence["page_number"]
        for lesson in learning_units.values()
        for evidence in lesson["evidence"]
    })
    source_pages = state.get("source_pages", [])
    uncovered_pages = sorted({page["page_number"] for page in source_pages} - set(covered_pages))
    review_notes = [
        "This is an automated draft. No representative human-reviewed evaluation set has been run for this book.",
        *visual_review_notes,
        *citation_review_notes,
    ]
    review_notes.append(
        f"Distill processed {state['selected_page_count']} of {state['expected_page_count']} physical pages "
        f"using content-selection revision {state['content_selection_revision']}. Excluded pages remain available in Ingestion."
    )
    for page in source_pages:
        if page.get("review_required"):
            review_notes.append(f"Page {page['page_number']} was marked for review during ingestion quality assurance.")
    for ref in unit_refs:
        artifact = json.loads(await storage.read_text(
            domains.bd.distill.keys.learning_unit_key(book_id, run_id, ref["unit_id"]),
        ))
        review_notes.extend(artifact.get("review_flags", []))
        review_notes.extend(
            f"Page {ref['page_number']} omitted material during learning extraction: {omission}"
            for omission in artifact.get("omitted_material", [])
        )
    for page_number in uncovered_pages:
        review_notes.append(f"No verified learning unit cited page {page_number}; inspect this page for omitted material.")
    review_required = True
    glossary_items = {}
    for unit in learning_units.values():
        if unit["kind"] == "definition":
            key = unit["title"].casefold()
            current = glossary_items.get(key)
            if current is None or unit["confidence"] > current["confidence"]:
                glossary_items[key] = unit
    glossary = [
        domains.bd.distill.schemas.GlossaryEntry(
            term = unit["title"],
            definition = unit["explanation"],
            attribution = attribution_for(unit["attribution"], [unit["learning_unit_id"]]),
            source_refs = source_refs([unit["learning_unit_id"]]),
            verification_status = unit["verification_status"],
        )
        for unit in sorted(glossary_items.values(), key = lambda item: item["title"].casefold())
    ]
    report = domains.bd.distill.schemas.StudyGuideDocument(
        title = f"{state.get('title') or 'Book'} — Learning Guide",
        book_id = book_id,
        extraction_run_id = state["extraction_run_id"],
        ingestion_ai_run_id = ingestion_ai_run_id,
        distill_run_id = run_id,
        source_page_count = state["expected_page_count"],
        selected_page_count = state["selected_page_count"],
        excluded_page_count = state["expected_page_count"] - state["selected_page_count"],
        content_selection_revision = state["content_selection_revision"],
        processed_page_count = state.get("processed_page_count", state["selected_page_count"]),
        processed_book_revision = state.get("processed_book_revision", state["content_selection_revision"]),
        generated_at = state["run_created_at"],
        central_thesis = book_synthesis.central_thesis,
        central_thesis_attribution = attribution_for(
            book_synthesis.central_thesis_attribution,
            book_synthesis.central_thesis_learning_unit_ids,
        ),
        central_thesis_source_refs = source_refs(book_synthesis.central_thesis_learning_unit_ids),
        key_takeaways = [report_lesson(item.model_dump(mode = "json")) for item in book_synthesis.key_takeaways],
        cross_chapter_connections = [
            report_lesson(item.model_dump(mode = "json")) for item in book_synthesis.cross_chapter_connections
        ],
        chapters = chapters,
        glossary = glossary,
        limitation_notes = [report_lesson(item.model_dump(mode = "json")) for item in book_synthesis.limitations],
        review_notes = sorted(set(review_notes)),
        charts = chapter_charts,
        covered_pages = covered_pages,
        uncovered_pages = uncovered_pages,
        review_required = review_required,
    )
    source_page_by_number = {page["page_number"]: page for page in source_pages}
    source_text_by_page = {}
    for page_number in domains.bd.distill.evaluation.evidence_page_numbers(report):
        source_page = source_page_by_number.get(page_number)
        if source_page is None:
            continue
        page_text = await storage.read_text(source_page["source_key"])
        if hashlib.sha256(page_text.encode("utf-8")).hexdigest() != source_page["source_sha256"]:
            raise ValueError(f"normalized source page {page_number} failed its report integrity check")
        source_text_by_page[page_number] = page_text
    structural_quality = domains.bd.distill.evaluation.assess_report_structure(
        report,
        expected_page_count = state["expected_page_count"],
        expected_page_numbers = [page["page_number"] for page in source_pages],
        source_pages = source_pages,
        source_text_by_page = source_text_by_page,
    )
    if structural_quality.errors:
        raise ValueError(
            "Learning Guide failed deterministic citation or page-accounting checks: "
            + ", ".join(structural_quality.errors)
        )
    report_json = _json_body(report.model_dump(mode = "json"))
    report_html = domains.bd.distill.domain.render_html(report)
    report_key = domains.bd.distill.keys.report_key(book_id, run_id)
    html_key = domains.bd.distill.keys.html_key(book_id, run_id)
    await storage.write(report_key, report_json, content_type = domains.bd.distill.params.JSON_TYPE)
    await storage.write(html_key, report_html, content_type = domains.bd.distill.params.HTML_TYPE)
    return {
        "report_key": report_key,
        "html_key": html_key,
        "report_sha256": hashlib.sha256(report_json.encode("utf-8")).hexdigest(),
        "html_sha256": hashlib.sha256(report_html.encode("utf-8")).hexdigest(),
        "covered_page_count": len(covered_pages),
        "uncovered_pages": uncovered_pages,
        "review_required": review_required,
        "structural_quality": structural_quality.model_dump(mode = "json"),
    }
