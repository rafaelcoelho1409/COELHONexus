"""Assemble, validate, and publish a citation-resolvable Learning Guide."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

import domains


async def publish_report(
    storage: Any,
    state: dict[str, Any],
    review_report: Callable[[dict[str, Any]], Awaitable[domains.bd.distill.ai.schemas.ReportReviewOutput]] | None = None,
    on_unit_progress: Callable[[str, str, int], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    book_id = state["book_id"]
    run_id = state["run_id"]
    ingestion_ai_run_id = state["ingestion_ai_run_id"]
    learning_units: dict[str, dict[str, Any]] = {}
    unit_refs = state.get("completed_units", [])
    total_units = len(unit_refs)
    for ref in unit_refs:
        if on_unit_progress is not None:
            await on_unit_progress(ref["unit_id"], "running", total_units)
        artifact_key = domains.bd.distill.ai.keys.learning_unit_key(book_id, run_id, ref["unit_id"])
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
        if on_unit_progress is not None:
            await on_unit_progress(ref["unit_id"], "done", total_units)
    if not learning_units:
        raise ValueError("the Distill run has no source-grounded learning units to publish")

    def source_refs(unit_ids: list[str]) -> list[domains.bd.distill.ai.schemas.SourceRef]:
        refs = []
        seen = set()
        for unit_id in domains.bd.distill.ai.domain.validate_synthesis_unit_ids(
            unit_ids, set(learning_units),
        ):
            lesson = learning_units[unit_id]
            for evidence in lesson["evidence"]:
                identity = (lesson["chunk_id"], evidence["page_number"], evidence["quote"], lesson["attribution"])
                if identity in seen:
                    continue
                seen.add(identity)
                refs.append(domains.bd.distill.ai.schemas.SourceRef(
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
            for unit_id in domains.bd.distill.ai.domain.validate_synthesis_unit_ids(
                unit_ids, set(learning_units),
            )
        ):
            return "agent_inference"
        return "book"

    def report_lesson(item: dict[str, Any]) -> domains.bd.distill.ai.schemas.ReportLesson:
        unit_ids = item["learning_unit_ids"]
        refs = source_refs(unit_ids)
        supporting = [learning_units[unit_id] for unit_id in unit_ids]
        return domains.bd.distill.ai.schemas.ReportLesson(
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
    book_synthesis = domains.bd.distill.ai.schemas.BookSynthesis.model_validate(synthesis_record["synthesis"])
    decision_key = state.get("decision_brief_key") or domains.bd.distill.ai.keys.decision_brief_key(book_id, run_id)
    decision_brief_error_type = None
    if await storage.exists(decision_key):
        decision_output = domains.bd.distill.ai.schemas.DecisionBriefOutput.model_validate_json(
            await storage.read_text(decision_key),
        )
        decision_brief_error_type = decision_output.error_type
        book_synthesis = book_synthesis.model_copy(update = {"decision_brief": decision_output.decision_brief})
    research_key = state.get("research_context_key") or domains.bd.distill.ai.keys.research_context_key(book_id, run_id)
    research_context = domains.bd.distill.ai.schemas.ResearchContext(status = "disabled")
    if await storage.exists(research_key):
        research_context = domains.bd.distill.ai.schemas.ResearchContext.model_validate_json(
            await storage.read_text(research_key),
        )
    full_text_source_ids = {
        source.source_id for source in research_context.sources if source.full_text_sha256 is not None
    }
    allowed_external_ids = {
        quote.source_id
        for finding in research_context.findings
        for quote in finding.evidence_quotes
        if quote.source_id in full_text_source_ids
    }
    all_unit_ids = set(learning_units)
    book_referenced_ids = set(book_synthesis.central_thesis_learning_unit_ids)
    for item in [*book_synthesis.key_takeaways, *book_synthesis.cross_chapter_connections, *book_synthesis.limitations]:
        book_referenced_ids.update(item.learning_unit_ids)
    decision = book_synthesis.decision_brief
    if decision is not None:
        book_referenced_ids.update(decision.recommendation_learning_unit_ids)
        for item in [*decision.options, *decision.action_plan]:
            book_referenced_ids.update(item.learning_unit_ids)
    if book_referenced_ids - all_unit_ids:
        raise ValueError("the whole-book synthesis contains unresolved learning-unit citations")

    decision_brief = None
    invalid_decision_external_refs = False
    if decision is not None:
        decision_external_ids = set(decision.external_source_ids)
        for item in [*decision.options, *decision.action_plan]:
            decision_external_ids.update(item.external_source_ids)
        invalid_decision_external_refs = bool(decision_external_ids - allowed_external_ids)
    if decision is not None and not invalid_decision_external_refs:
        recommendation_units = [
            learning_units[unit_id] for unit_id in decision.recommendation_learning_unit_ids
        ]
        decision_brief = domains.bd.distill.ai.schemas.DecisionBrief(
            decision_question = decision.decision_question,
            recommendation = domains.bd.distill.ai.schemas.ReportLesson(
                title = "Recommended approach",
                explanation = decision.recommendation,
                source_refs = source_refs(decision.recommendation_learning_unit_ids),
                attribution = attribution_for(
                    decision.recommendation_attribution,
                    decision.recommendation_learning_unit_ids,
                ),
                verification_status = (
                    "verified" if all(unit["verification_status"] == "verified" for unit in recommendation_units)
                    else "unverified"
                ),
                confidence = min(unit["confidence"] for unit in recommendation_units),
                external_source_ids = decision.external_source_ids,
            ),
            options = [
                domains.bd.distill.ai.schemas.ReportDecisionOption(
                    title = item.title,
                    expected_outcome = item.expected_outcome,
                    tradeoffs = item.tradeoffs,
                    risks = item.risks,
                    attribution = attribution_for(item.attribution, item.learning_unit_ids),
                    source_refs = source_refs(item.learning_unit_ids),
                    external_source_ids = item.external_source_ids,
                )
                for item in decision.options
            ],
            assumptions = decision.assumptions,
            risks = decision.risks,
            action_plan = [
                domains.bd.distill.ai.schemas.ReportActionStep(
                    sequence = item.sequence,
                    action = item.action,
                    rationale = item.rationale,
                    attribution = attribution_for(item.attribution, item.learning_unit_ids),
                    prerequisites = item.prerequisites,
                    success_measure = item.success_measure,
                    source_refs = source_refs(item.learning_unit_ids),
                    external_source_ids = item.external_source_ids,
                )
                for item in decision.action_plan
            ],
        )

    chapters = []
    chapter_charts = []
    visual_review_notes = []
    citation_review_notes = []
    synthesis_review_notes = []
    coverage_review_notes = []
    ordered_chapters = []
    for ref in state.get("completed_chapters", []):
        artifact = json.loads(await storage.read_text(ref["artifact_key"]))
        chapter = domains.bd.distill.ai.schemas.ChapterSynthesis.model_validate(artifact["synthesis"])
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
        synthesis_review_notes.extend(
            f"Chapter {artifact['title']}: {flag}"
            for flag in artifact.get("synthesis_review_flags", [])
            if isinstance(flag, str) and flag
        )
        coverage_review_notes.extend(
            f"Chapter {artifact['title']}: {flag}"
            for flag in artifact.get("coverage_review_notes", [])
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
        maps = [
            map_spec.model_copy(update = {
                "geojson": map_spec.geojson.model_copy(update = {
                    "features": [
                        feature.model_copy(update = {
                            "properties": feature.properties.model_copy(update = {
                                "attribution": attribution_for(
                                    feature.properties.attribution,
                                    feature.properties.learning_unit_ids,
                                ),
                            }),
                        })
                        for feature in map_spec.geojson.features
                    ],
                }),
            })
            for map_spec in chapter.maps
        ]
        report_source_figures = []
        for item in chapter.source_figures:
            figure_bytes = await domains.bd.ingestion.source.storage.service.read_figure(
                storage,
                book_id,
                item.figure_name,
                state["extraction_run_id"],
            )
            if figure_bytes is None:
                visual_review_notes.append(
                    f"Chapter {artifact['title']}: source figure {item.figure_name} was unavailable during publication."
                )
                continue
            if not figure_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
                visual_review_notes.append(
                    f"Chapter {artifact['title']}: source figure {item.figure_name} was not a valid PNG."
                )
                continue
            report_source_figures.append(domains.bd.distill.ai.schemas.ReportSourceFigure(
                figure_name = item.figure_name,
                page_number = item.page_number,
                caption = item.caption,
                image_sha256 = hashlib.sha256(figure_bytes).hexdigest(),
                source_refs = source_refs(item.learning_unit_ids),
            ))
        chapters.append(domains.bd.distill.ai.schemas.ReportSection(
            title = artifact["title"],
            chapter_id = artifact["chapter_id"],
            overview = chapter.overview,
            overview_attribution = attribution_for(
                chapter.overview_attribution, chapter.overview_learning_unit_ids,
            ),
            overview_source_refs = source_refs(chapter.overview_learning_unit_ids),
            lessons = [report_lesson(item.model_dump(mode = "json")) for item in chapter.lessons],
            open_questions = [report_lesson(item.model_dump(mode = "json")) for item in chapter.open_questions],
            learning_objectives = [
                domains.bd.distill.ai.schemas.ReportLearningObjective(
                    objective_id = item.objective_id,
                    outcome = item.outcome,
                    kind = item.kind,
                    importance = item.importance,
                    source_refs = source_refs(item.learning_unit_ids),
                )
                for item in chapter.learning_objectives
            ],
            practice_items = [
                domains.bd.distill.ai.schemas.ReportPracticeItem(
                    practice_id = item.practice_id,
                    question = item.question,
                    answer = item.answer,
                    feedback = item.feedback,
                    kind = item.kind,
                    difficulty = item.difficulty,
                    objective_ids = item.objective_ids,
                    source_refs = source_refs(item.learning_unit_ids),
                )
                for item in chapter.practice_items
            ],
            source_figures = report_source_figures,
            coverage_review_status = artifact.get("coverage_review_status", "unreviewed"),
            coverage_review_notes = [
                item for item in artifact.get("coverage_review_notes", [])
                if isinstance(item, str) and item
            ][:8],
            diagrams = diagrams,
            equations = equations,
            charts = chapter.charts,
            timelines = [
                domains.bd.distill.ai.schemas.ReportTimeline(
                    title = timeline.title,
                    accessible_description = timeline.accessible_description,
                    events = [
                        domains.bd.distill.ai.schemas.ReportTimelineEvent(
                            label = event.label,
                            description = event.description,
                            date_or_order = event.date_or_order,
                            attribution = attribution_for(event.attribution, event.learning_unit_ids),
                            source_refs = source_refs(event.learning_unit_ids),
                        )
                        for event in timeline.events
                    ],
                )
                for timeline in chapter.timelines
            ],
            maps = maps,
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
        *(
            [f"Decision brief generation was unavailable ({decision_brief_error_type}); the source-grounded guide is complete."]
            if decision_brief_error_type
            else []
        ),
        *[
            item for item in state.get("chapter_mapping_notes", [])
            if isinstance(item, str) and item
        ],
        *visual_review_notes,
        *citation_review_notes,
        *synthesis_review_notes,
        *coverage_review_notes,
    ]
    if invalid_decision_external_refs:
        review_notes.append("The optional decision brief was omitted because it referenced unread or unknown external sources.")
    review_notes.append(
        f"Distill processed {state['selected_page_count']} of {state['expected_page_count']} physical pages "
        f"using content-selection revision {state['content_selection_revision']}. Excluded pages remain available in Ingestion."
    )
    for page in source_pages:
        if page.get("review_required"):
            review_notes.append(f"Page {page['page_number']} was marked for review during ingestion quality assurance.")
    for ref in unit_refs:
        artifact = json.loads(await storage.read_text(
            domains.bd.distill.ai.keys.learning_unit_key(book_id, run_id, ref["unit_id"]),
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
        domains.bd.distill.ai.schemas.GlossaryEntry(
            term = unit["title"],
            definition = unit["explanation"],
            attribution = attribution_for(unit["attribution"], [unit["learning_unit_id"]]),
            source_refs = source_refs([unit["learning_unit_id"]]),
            verification_status = unit["verification_status"],
        )
        for unit in sorted(glossary_items.values(), key = lambda item: item["title"].casefold())
    ]
    report = domains.bd.distill.ai.schemas.StudyGuideDocument(
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
        decision_brief = decision_brief,
        research_context = research_context,
        review_notes = sorted(set(review_notes)),
        charts = chapter_charts,
        covered_pages = covered_pages,
        uncovered_pages = uncovered_pages,
        review_required = review_required,
    )
    source_page_by_number = {page["page_number"]: page for page in source_pages}
    source_text_by_page = {}
    for page_number in domains.bd.distill.ai.evaluation.evidence_page_numbers(report):
        source_page = source_page_by_number.get(page_number)
        if source_page is None:
            continue
        page_text = await storage.read_text(source_page["source_key"])
        if hashlib.sha256(page_text.encode("utf-8")).hexdigest() != source_page["source_sha256"]:
            raise ValueError(f"normalized source page {page_number} failed its report integrity check")
        source_text_by_page[page_number] = page_text
    structural_quality = domains.bd.distill.ai.evaluation.assess_report_structure(
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
    review_packet = {
        "report_title": report.title,
        "central_thesis": report.central_thesis,
        "key_takeaways": [
            {"title": item.title, "explanation": item.explanation[:500]}
            for item in report.key_takeaways[:8]
        ],
        "cross_chapter_connections": [
            {"title": item.title, "explanation": item.explanation[:500]}
            for item in report.cross_chapter_connections[:8]
        ],
        "chapters": [
            {
                "title": chapter.title,
                "overview": chapter.overview[:700],
                "lessons": [
                    {"title": item.title, "explanation": item.explanation[:350]}
                    for item in chapter.lessons[:6]
                ],
                "learning_objectives": [
                    {"objective_id": item.objective_id, "outcome": item.outcome, "importance": item.importance}
                    for item in chapter.learning_objectives
                ],
                "practice_items": [
                    {"practice_id": item.practice_id, "question": item.question[:240], "kind": item.kind}
                    for item in chapter.practice_items
                ],
                "open_questions": [item.title for item in chapter.open_questions[:4]],
                "visual_counts": {
                    "diagrams": len(chapter.diagrams),
                    "equations": len(chapter.equations),
                    "charts": len(chapter.charts),
                    "timelines": len(chapter.timelines),
                    "maps": len(chapter.maps),
                    "source_figures": len(chapter.source_figures),
                },
            }
            for chapter in report.chapters[:40]
        ],
        "decision_brief": report.decision_brief.model_dump(mode = "json") if report.decision_brief else None,
        "limitation_notes": [item.title for item in report.limitation_notes[:8]],
        "existing_review_notes": report.review_notes[:24],
        "structural_quality": structural_quality.model_dump(mode = "json"),
    }
    try:
        review = await review_report(review_packet) if review_report is not None else None
        if not isinstance(review, domains.bd.distill.ai.schemas.ReportReviewOutput):
            raise TypeError("report reviewer returned no valid ReportReviewOutput")
        reviewer_notes = [
            f"Editorial review ({item.severity}, {item.area}): {item.issue} Recommendation: {item.recommendation}"
            for item in review.findings
        ]
        report = report.model_copy(update = {
            "review_notes": sorted({*report.review_notes, *reviewer_notes}),
            "review_required": report.review_required or any(
                finding.severity == "critical" for finding in review.findings
            ),
        })
    except Exception as error:  # noqa: BLE001 — editorial review never overrides deterministic publication checks.
        report = report.model_copy(update = {
            "review_notes": sorted({
                *report.review_notes,
                f"Editorial review was unavailable ({type(error).__name__}); deterministic checks passed.",
            }),
        })
    report_json = domains.bd.distill.ai.domain.json_body(report.model_dump(mode = "json"))
    report_html = domains.bd.distill.source.rendering.domain.render_html(report)
    report_key = domains.bd.distill.ai.keys.report_key(book_id, run_id)
    html_key = domains.bd.distill.ai.keys.html_key(book_id, run_id)
    await storage.write(report_key, report_json, content_type = domains.bd.distill.ai.params.JSON_TYPE)
    await storage.write(
        html_key,
        report_html,
        content_type = domains.bd.distill.source.rendering.params.HTML_TYPE,
    )
    return {
        "report_key": report_key,
        "html_key": html_key,
        "report_sha256": hashlib.sha256(report_json.encode("utf-8")).hexdigest(),
        "html_sha256": hashlib.sha256(report_html.encode("utf-8")).hexdigest(),
        "covered_page_count": len(covered_pages),
        "uncovered_pages": uncovered_pages,
        "review_required": report.review_required,
        "structural_quality": structural_quality.model_dump(mode = "json"),
    }
