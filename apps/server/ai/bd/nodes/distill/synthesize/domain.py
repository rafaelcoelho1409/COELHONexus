"""Pure validation and transformation rules for BD Distill synthesis."""
from __future__ import annotations
import domains
from . import patterns

from typing import Any


def chapter_required_text_ids(synthesis: domains.bd.distill.schemas.ChapterSynthesis) -> set[str]:
    """Collect IDs for required chapter prose, leaving optional visuals independently checkable."""
    ids = set(synthesis.overview_learning_unit_ids)
    for lesson in [*synthesis.lessons, *synthesis.open_questions]:
        ids.update(lesson.learning_unit_ids)
    return ids


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
