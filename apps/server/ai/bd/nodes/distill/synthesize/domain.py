"""Pure validation and transformation rules for BD Distill synthesis."""
from __future__ import annotations
import domains
from . import patterns

from typing import Any


def book_synthesis_context(
    guides: list[dict[str, Any]],
    max_citation_ids: int,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Build a compact, citation-grounded context for whole-book synthesis."""
    ids_by_guide = [
        list(dict.fromkeys(_book_citation_ids(guide.get("summary", {}))))
        for guide in guides
    ]
    selected_ids: list[str] = []
    seen_ids: set[str] = set()
    for offset in range(max((len(ids) for ids in ids_by_guide), default = 0)):
        for ids in ids_by_guide:
            if offset >= len(ids):
                continue
            unit_id = ids[offset]
            if unit_id in seen_ids:
                continue
            selected_ids.append(unit_id)
            seen_ids.add(unit_id)
            if len(selected_ids) >= max_citation_ids:
                break
        if len(selected_ids) >= max_citation_ids:
            break

    allowed_ids = set(selected_ids)
    prompt_guides = []
    for guide in guides:
        summary = _filter_book_summary(guide.get("summary", {}), allowed_ids)
        if not isinstance(summary, dict) or not _book_citation_ids(summary):
            continue
        prompt_guide = {
            key: guide[key]
            for key in ("chapter_id", "synthesis_id", "title", "section_path", "first_page", "last_page")
            if key in guide
        }
        prompt_guide["summary"] = summary
        prompt_guides.append(prompt_guide)
    return selected_ids, prompt_guides


def _book_citation_ids(value: Any) -> list[str]:
    """Collect only citations already present in a chapter or partial-book summary."""
    found: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if key in domains.bd.distill.params.BOOK_CITATION_FIELDS and isinstance(child, list):
                    found.extend(unit_id for unit_id in child if isinstance(unit_id, str))
                else:
                    visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return found


def _filter_book_summary(value: Any, allowed_ids: set[str]) -> Any:
    """Remove summary claims whose citations are outside the compact prompt evidence set."""
    if isinstance(value, list):
        return [filtered for item in value if (filtered := _filter_book_summary(item, allowed_ids)) is not None]
    if not isinstance(value, dict):
        return value

    filtered = {}
    for key, item in value.items():
        if key in domains.bd.distill.params.BOOK_CITATION_FIELDS:
            retained = list(dict.fromkeys(
                unit_id for unit_id in item if isinstance(unit_id, str) and unit_id in allowed_ids
            )) if isinstance(item, list) else []
            if retained:
                filtered[key] = retained
            continue
        child = _filter_book_summary(item, allowed_ids)
        if child is not None:
            filtered[key] = child

    if "learning_unit_ids" in value and not filtered.get("learning_unit_ids"):
        return None
    if "overview_learning_unit_ids" in value and not filtered.get("overview_learning_unit_ids"):
        filtered.pop("overview", None)
        filtered.pop("overview_attribution", None)
    if "central_thesis_learning_unit_ids" in value and not filtered.get("central_thesis_learning_unit_ids"):
        filtered.pop("central_thesis", None)
        filtered.pop("central_thesis_attribution", None)
    return filtered


def chapter_required_text_ids(synthesis: domains.bd.distill.schemas.ChapterSynthesis) -> set[str]:
    """Collect IDs for required chapter prose, leaving optional visuals independently checkable."""
    ids = set(synthesis.overview_learning_unit_ids)
    for lesson in [*synthesis.lessons, *synthesis.open_questions]:
        ids.update(lesson.learning_unit_ids)
    return ids


def chapter_citation_issues(
    synthesis: domains.bd.distill.schemas.ChapterSynthesis,
    available_ids: set[str],
) -> list[dict[str, Any]]:
    """Describe prose citations that do not resolve to this chapter's input units."""
    fields = [("overview_learning_unit_ids", synthesis.overview_learning_unit_ids)]
    fields.extend(
        [
            (f"lessons[{index}].learning_unit_ids", lesson.learning_unit_ids)
            for index, lesson in enumerate(synthesis.lessons)
        ]
    )
    fields.extend(
        [
            (f"open_questions[{index}].learning_unit_ids", question.learning_unit_ids)
            for index, question in enumerate(synthesis.open_questions)
        ]
    )
    return [
        {"field": field, "unknown_ids": sorted(set(unit_ids) - available_ids)}
        for field, unit_ids in fields
        if set(unit_ids) - available_ids
    ]


def repair_chapter_citations(
    synthesis: domains.bd.distill.schemas.ChapterSynthesis,
    available_ids: set[str],
) -> tuple[domains.bd.distill.schemas.ChapterSynthesis, list[str]]:
    """Keep only resolvable prose citations and omit lessons left without support."""
    if not available_ids:
        raise ValueError("chapter synthesis cannot be repaired without input learning-unit IDs")

    review_flags: list[str] = []

    def retain_supported(
        items: list[domains.bd.distill.schemas.ChapterLesson],
        field: str,
    ) -> list[domains.bd.distill.schemas.ChapterLesson]:
        item_label = "lesson" if field == "lessons" else "open question"
        retained = []
        for item in items:
            valid_ids = list(dict.fromkeys(
                unit_id for unit_id in item.learning_unit_ids if unit_id in available_ids
            ))
            unknown_count = len(set(item.learning_unit_ids) - available_ids)
            if unknown_count:
                review_flags.append(
                    f"Removed {unknown_count} unresolved citation(s) from {field}."
                )
            if not valid_ids:
                review_flags.append(
                    f"Omitted {item_label} without a resolvable source citation: {item.title}."
                )
                continue
            retained.append(item.model_copy(update = {"learning_unit_ids": valid_ids}))
        return retained

    lessons = retain_supported(synthesis.lessons, "lessons")
    open_questions = retain_supported(synthesis.open_questions, "open_questions")
    overview_ids = list(dict.fromkeys(
        unit_id for unit_id in synthesis.overview_learning_unit_ids if unit_id in available_ids
    ))
    unknown_overview_count = len(set(synthesis.overview_learning_unit_ids) - available_ids)
    if unknown_overview_count:
        review_flags.append(
            f"Removed {unknown_overview_count} unresolved citation(s) from the overview."
        )
    overview_attribution = synthesis.overview_attribution
    if not overview_ids:
        overview_ids = list(dict.fromkeys(
            unit_id
            for item in [*lessons, *open_questions]
            for unit_id in item.learning_unit_ids
        ))
        if not overview_ids:
            # The overview summarizes this supplied section; broad source refs are safer than
            # attaching an invented ID or failing all otherwise completed chapter work.
            overview_ids = sorted(available_ids)
        overview_attribution = "agent_inference"
        review_flags.append(
            "Rebuilt overview citations from resolvable section sources; review its attribution."
        )

    repaired = synthesis.model_copy(update = {
        "overview_attribution": overview_attribution,
        "overview_learning_unit_ids": overview_ids,
        "lessons": lessons,
        "open_questions": open_questions,
    })
    return repaired, review_flags


def _normalize_evidence_quote(value: str) -> str:
    """Match harmless line-wrap/spacing changes while preserving quote wording and punctuation."""
    return " ".join(value.split())


def _evidence_matches(
    evidence: list[Any],
    learning_unit_ids: list[str],
    units_by_id: dict[str, dict[str, Any]],
) -> bool:
    allowed = {
        (quote["page_number"], _normalize_evidence_quote(quote["quote"]))
        for unit_id in learning_unit_ids
        for quote in units_by_id[unit_id]["evidence"]
    }
    return bool(evidence) and all(
        (item.page_number, _normalize_evidence_quote(item.quote)) in allowed
        for item in evidence
    )


def _number_occurs_in_evidence(value: float, evidence: list[Any]) -> bool:
    try:
        expected = float(value)
    except (TypeError, ValueError):
        return False
    for item in evidence:
        for match in patterns.NUMBER_RE.findall(item.quote):
            normalized = match.rstrip("%").replace(",", "")
            try:
                if float(normalized) == expected:
                    return True
            except ValueError:
                continue
    return False


def visual_evidence_issues(
    synthesis: domains.bd.distill.schemas.ChapterSynthesis,
    units: list[dict[str, Any]],
) -> list[tuple[str, int, str, str]]:
    """Return unsupported optional visual items without invalidating chapter prose."""
    by_id = {unit["learning_unit_id"]: unit for unit in units}
    available_ids = set(by_id)
    issues: list[tuple[str, int, str, str]] = []

    for index, diagram in enumerate(synthesis.diagrams):
        issue = None
        for node in diagram.nodes:
            if not set(node.learning_unit_ids) <= available_ids:
                issue = "diagram node cites an unavailable learning unit"
                break
            if not _evidence_matches(node.evidence, node.learning_unit_ids, by_id):
                issue = "diagram node evidence does not match its cited learning units"
                break
        if issue is None:
            node_ids = {node.node_id for node in diagram.nodes}
            for edge in diagram.edges:
                if edge.source_id not in node_ids or edge.target_id not in node_ids:
                    issue = "diagram edge refers to an undeclared node"
                    break
                if not set(edge.learning_unit_ids) <= available_ids:
                    issue = "diagram edge cites an unavailable learning unit"
                    break
                if not _evidence_matches(edge.evidence, edge.learning_unit_ids, by_id):
                    issue = "diagram edge evidence does not match its cited learning units"
                    break
        if issue is not None:
            issues.append(("diagram", index, diagram.title, issue))

    for index, equation in enumerate(synthesis.equations):
        issue = None
        if not set(equation.learning_unit_ids) <= available_ids or not _evidence_matches(
            equation.evidence, equation.learning_unit_ids, by_id,
        ):
            issue = "equation evidence does not match its cited learning units"
        numeric_tokens = patterns.NUMBER_RE.findall(equation.latex)
        if issue is None:
            for token in numeric_tokens:
                if not _number_occurs_in_evidence(float(token.rstrip("%").replace(",", "")), equation.evidence):
                    issue = "equation contains a number absent from its cited evidence"
                    break
        if issue is not None:
            issues.append(("equation", index, equation.title, issue))

    for index, chart in enumerate(synthesis.charts):
        issue = None
        if not set(chart.learning_unit_ids) <= available_ids:
            issue = "chart cites an unavailable learning unit"
        elif not set(chart.dataset.learning_unit_ids) <= available_ids:
            issue = "chart dataset cites an unavailable learning unit"
        for row in chart.dataset.rows:
            if issue is not None:
                break
            if not set(row.learning_unit_ids) <= set(by_id) or not _evidence_matches(
                row.evidence, row.learning_unit_ids, by_id,
            ):
                issue = "chart row evidence does not match its cited learning units"
                break
            for value in row.values.values():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    if not _number_occurs_in_evidence(value, row.evidence):
                        issue = "chart row contains a number absent from its cited evidence"
                        break
                elif isinstance(value, str) and not any(
                    _normalize_evidence_quote(value).casefold()
                    in _normalize_evidence_quote(item.quote).casefold()
                    for item in row.evidence
                ):
                    issue = "chart row contains text absent from its cited evidence"
                    break
        if issue is not None:
            issues.append(("chart", index, chart.title, issue))
    return issues


def omit_unsupported_visuals(
    synthesis: domains.bd.distill.schemas.ChapterSynthesis,
    issues: list[tuple[str, int, str, str]],
) -> tuple[domains.bd.distill.schemas.ChapterSynthesis, list[str]]:
    invalid = {(kind, index) for kind, index, _, _ in issues}
    updated = synthesis.model_copy(update = {
        "diagrams": [
            item for index, item in enumerate(synthesis.diagrams) if ("diagram", index) not in invalid
        ],
        "equations": [
            item for index, item in enumerate(synthesis.equations) if ("equation", index) not in invalid
        ],
        "charts": [
            item for index, item in enumerate(synthesis.charts) if ("chart", index) not in invalid
        ],
    })
    notes = [
        f"Omitted unsupported {kind} '{title}' ({reason})."
        for kind, _, title, reason in issues
    ]
    return updated, notes


def subsection_paths(unit_ids: list[str], refs_by_unit: dict[str, dict[str, Any]]) -> list[list[str]]:
    paths = {
        tuple(path)
        for unit_id in unit_ids
        for path in [refs_by_unit[unit_id].get("section_path", [])]
        if isinstance(path, list) and all(isinstance(item, str) for item in path)
    }
    return [list(path) for path in sorted(paths)]


def book_summary(synthesis: domains.bd.distill.schemas.BookSynthesis) -> dict[str, Any]:
    return {
        "central_thesis": synthesis.central_thesis[:1200],
        "central_thesis_attribution": synthesis.central_thesis_attribution,
        "central_thesis_learning_unit_ids": synthesis.central_thesis_learning_unit_ids,
        "key_takeaways": [
            {
                "title": item.title,
                "explanation": item.explanation[:400],
                "attribution": item.attribution,
                "learning_unit_ids": item.learning_unit_ids,
            }
            for item in synthesis.key_takeaways[:8]
        ],
        "cross_chapter_connections": [
            {
                "title": item.title,
                "explanation": item.explanation[:400],
                "attribution": item.attribution,
                "learning_unit_ids": item.learning_unit_ids,
            }
            for item in synthesis.cross_chapter_connections[:6]
        ],
        "limitations": [
            {
                "title": item.title,
                "explanation": item.explanation[:400],
                "attribution": item.attribution,
                "learning_unit_ids": item.learning_unit_ids,
            }
            for item in synthesis.limitations[:6]
        ],
    }
