"""Deterministic report checks and human learning-outcome evaluation."""
from __future__ import annotations

from collections.abc import Iterable

import domains

from pydantic import BaseModel, ConfigDict, Field


MIN_REVIEWED_BOOKS = 3
MIN_COVERAGE_RATING = 4.0
MIN_CITATION_RATING = 4.5
MIN_CLARITY_RATING = 4.0
MIN_USEFULNESS_RATING = 4.0
MAX_LEARNING_SCORE_REGRESSION = 5.0


def _all_source_refs(report: domains.bd.distill.schemas.StudyGuideDocument):
    yield from report.central_thesis_source_refs
    for lesson in [*report.key_takeaways, *report.cross_chapter_connections, *report.limitation_notes]:
        yield from lesson.source_refs
    for chapter in report.chapters:
        yield from chapter.overview_source_refs
        for lesson in [*chapter.lessons, *chapter.open_questions]:
            yield from lesson.source_refs
    for glossary in report.glossary:
        yield from glossary.source_refs


def _all_evidence_quotes(report: domains.bd.distill.schemas.StudyGuideDocument):
    for reference in _all_source_refs(report):
        yield reference.page_number, reference.quote
    for diagram in report.diagrams:
        for node in diagram.nodes:
            yield from ((quote.page_number, quote.quote) for quote in node.evidence)
        for edge in diagram.edges:
            yield from ((quote.page_number, quote.quote) for quote in edge.evidence)
    for equation in report.equations:
        yield from ((quote.page_number, quote.quote) for quote in equation.evidence)
    charts = [*report.charts, *(chart for chapter in report.chapters for chart in chapter.charts)]
    for chart in charts:
        for row in chart.dataset.rows:
            yield from ((quote.page_number, quote.quote) for quote in row.evidence)


def assess_report_structure(
    report: domains.bd.distill.schemas.StudyGuideDocument,
    *,
    expected_page_count: int,
    expected_page_numbers: list[int] | None = None,
    source_pages: list[dict],
    source_text_by_page: dict[int, str],
) -> domains.bd.distill.schemas.StructuralQuality:
    """Check report coverage and each citation against the pinned normalized source pages."""
    if expected_page_count < 1:
        raise ValueError("expected_page_count must be positive")
    errors: list[str] = []
    expected_pages = set(expected_page_numbers or range(1, expected_page_count + 1))
    if not expected_pages or any(type(number) is not int or number < 1 or number > expected_page_count for number in expected_pages):
        raise ValueError("expected_page_numbers must be non-empty physical page numbers in range")
    pages = {
        page["page_number"]: page
        for page in source_pages
        if type(page.get("page_number")) is int
    }
    if len(pages) != len(source_pages):
        errors.append("source_page_manifest_has_invalid_or_duplicate_page_numbers")
    if set(pages) != expected_pages:
        errors.append("source_page_manifest_does_not_cover_expected_page_range")

    substantive_pages = {
        number for number, page in pages.items()
        if page.get("disposition") != "discarded_blank"
    }
    covered_pages = set(report.covered_pages)
    covered_substantive = substantive_pages & covered_pages
    uncovered = sorted(substantive_pages - covered_pages)
    coverage_rate = len(covered_substantive) / len(substantive_pages) if substantive_pages else 1.0
    accounted_pages = covered_pages | set(report.uncovered_pages)
    if covered_pages & set(report.uncovered_pages):
        errors.append("report_page_coverage_sets_overlap")
    if accounted_pages != expected_pages:
        errors.append("report_page_coverage_does_not_account_for_every_page")
    if set(report.covered_pages) - expected_pages:
        errors.append("report_contains_out_of_range_covered_pages")

    references = list(_all_source_refs(report))
    citation_errors: list[str] = []
    for ref in references:
        source_page = pages.get(ref.page_number)
        if (
            ref.book_id != report.book_id
            or ref.extraction_run_id != report.extraction_run_id
            or ref.ingestion_ai_run_id != report.ingestion_ai_run_id
            or source_page is None
            or ref.source_key != source_page.get("source_key")
            or ref.source_sha256 != source_page.get("source_sha256")
        ):
            citation_errors.append("source_reference_does_not_match_pinned_page")
    if citation_errors:
        errors.extend(sorted(set(citation_errors)))

    quotes = list(_all_evidence_quotes(report))
    supported_quotes = 0
    for page_number, quote in quotes:
        source_text = source_text_by_page.get(page_number)
        if source_text is not None and domains.bd.distill.domain.quote_is_supported(quote, source_text):
            supported_quotes += 1
    if supported_quotes != len(quotes):
        errors.append("one_or_more_evidence_quotes_do_not_resolve_to_source_text")

    unverified_count = sum(
        lesson.verification_status != "verified"
        for lesson in [
            *report.key_takeaways,
            *report.cross_chapter_connections,
            *report.limitation_notes,
            *(lesson for chapter in report.chapters for lesson in [*chapter.lessons, *chapter.open_questions]),
        ]
    ) + sum(glossary.verification_status != "verified" for glossary in report.glossary)
    return domains.bd.distill.schemas.StructuralQuality(
        expected_page_count = expected_page_count,
        selected_page_count = len(expected_pages),
        excluded_page_count = expected_page_count - len(expected_pages),
        substantive_page_count = len(substantive_pages),
        covered_substantive_pages = len(covered_substantive),
        page_coverage_rate = coverage_rate,
        source_reference_count = len(references),
        valid_source_reference_count = len(references) - len(citation_errors),
        evidence_quote_count = len(quotes),
        supported_evidence_quote_count = supported_quotes,
        unverified_lesson_count = unverified_count,
        uncovered_substantive_pages = uncovered,
        citation_integrity_passed = not citation_errors and set(pages) == expected_pages,
        quote_support_passed = supported_quotes == len(quotes),
        errors = sorted(set(errors)),
    )


def evidence_page_numbers(report: domains.bd.distill.schemas.StudyGuideDocument) -> list[int]:
    """Return sorted unique pages needed to validate all report evidence."""
    return sorted({page_number for page_number, _ in _all_evidence_quotes(report)})


class HumanLearningReview(BaseModel):
    """One paired human review of a book and its Learning Guide."""

    model_config = ConfigDict(extra = "forbid")

    book_id: str = Field(min_length = 1)
    distill_run_id: str = Field(min_length = 1)
    coverage_rating: int = Field(ge = 1, le = 5, strict = True)
    citation_accuracy_rating: int = Field(ge = 1, le = 5, strict = True)
    clarity_rating: int = Field(ge = 1, le = 5, strict = True)
    usefulness_rating: int = Field(ge = 1, le = 5, strict = True)
    source_comprehension_score: float = Field(ge = 0, le = 100)
    guide_comprehension_score: float = Field(ge = 0, le = 100)
    source_delayed_recall_score: float = Field(ge = 0, le = 100)
    guide_delayed_recall_score: float = Field(ge = 0, le = 100)
    guide_transfer_score: float = Field(ge = 0, le = 100)
    source_reading_minutes: float = Field(gt = 0)
    guide_reading_minutes: float = Field(gt = 0)
    missed_topics: list[str] = Field(default_factory = list)
    unsupported_claims: list[str] = Field(default_factory = list)


def _mean(values: Iterable[float]) -> float | None:
    samples = list(values)
    return round(sum(samples) / len(samples), 2) if samples else None


def aggregate_human_reviews(reviews: list[HumanLearningReview]) -> dict:
    """Summarize paired reviews and apply the initial small-beta gate."""
    reviews_by_book: dict[str, list[HumanLearningReview]] = {}
    for review in reviews:
        reviews_by_book.setdefault(review.book_id, []).append(review)

    per_book = {}
    for book_id, book_reviews in sorted(reviews_by_book.items()):
        source_comprehension = _mean(review.source_comprehension_score for review in book_reviews)
        guide_comprehension = _mean(review.guide_comprehension_score for review in book_reviews)
        source_recall = _mean(review.source_delayed_recall_score for review in book_reviews)
        guide_recall = _mean(review.guide_delayed_recall_score for review in book_reviews)
        per_book[book_id] = {
            "review_count": len(book_reviews),
            "coverage_rating": _mean(review.coverage_rating for review in book_reviews),
            "citation_accuracy_rating": _mean(review.citation_accuracy_rating for review in book_reviews),
            "clarity_rating": _mean(review.clarity_rating for review in book_reviews),
            "usefulness_rating": _mean(review.usefulness_rating for review in book_reviews),
            "comprehension_delta": None if source_comprehension is None or guide_comprehension is None else round(
                guide_comprehension - source_comprehension, 2,
            ),
            "delayed_recall_delta": None if source_recall is None or guide_recall is None else round(
                guide_recall - source_recall, 2,
            ),
            "time_saved_rate": _mean(
                (review.source_reading_minutes - review.guide_reading_minutes) / review.source_reading_minutes
                for review in book_reviews
            ),
            "unsupported_claim_count": sum(len(review.unsupported_claims) for review in book_reviews),
            "missed_topic_count": sum(len(review.missed_topics) for review in book_reviews),
        }

    # Weight books equally so a large number of reviews for one title cannot
    # dominate the results for the rest of the pilot set.
    source_comprehension = _mean(
        _mean(review.source_comprehension_score for review in group)
        for group in reviews_by_book.values()
    )
    guide_comprehension = _mean(
        _mean(review.guide_comprehension_score for review in group)
        for group in reviews_by_book.values()
    )
    source_recall = _mean(
        _mean(review.source_delayed_recall_score for review in group)
        for group in reviews_by_book.values()
    )
    guide_recall = _mean(
        _mean(review.guide_delayed_recall_score for review in group)
        for group in reviews_by_book.values()
    )
    saved = _mean(book["time_saved_rate"] for book in per_book.values())
    rating_means = {
        field: _mean(book[field] for book in per_book.values())
        for field in ("coverage_rating", "citation_accuracy_rating", "clarity_rating", "usefulness_rating")
    }
    means = {
        **rating_means,
        "source_comprehension_score": source_comprehension,
        "guide_comprehension_score": guide_comprehension,
        "source_delayed_recall_score": source_recall,
        "guide_delayed_recall_score": guide_recall,
        "guide_transfer_score": _mean(review.guide_transfer_score for review in reviews),
        "time_saved_rate": saved,
        "comprehension_delta": None if source_comprehension is None or guide_comprehension is None else round(
            guide_comprehension - source_comprehension, 2,
        ),
        "delayed_recall_delta": None if source_recall is None or guide_recall is None else round(
            guide_recall - source_recall, 2,
        ),
    }
    no_critical_review_findings = all(book["unsupported_claim_count"] == 0 for book in per_book.values())
    each_book_meets_quality_floor = all(
        book["coverage_rating"] is not None
        and book["coverage_rating"] >= MIN_COVERAGE_RATING
        and book["citation_accuracy_rating"] is not None
        and book["citation_accuracy_rating"] >= MIN_CITATION_RATING
        and book["clarity_rating"] is not None
        and book["clarity_rating"] >= MIN_CLARITY_RATING
        and book["usefulness_rating"] is not None
        and book["usefulness_rating"] >= MIN_USEFULNESS_RATING
        and book["comprehension_delta"] is not None
        and book["comprehension_delta"] >= -MAX_LEARNING_SCORE_REGRESSION
        and book["delayed_recall_delta"] is not None
        and book["delayed_recall_delta"] >= -MAX_LEARNING_SCORE_REGRESSION
        and book["time_saved_rate"] is not None
        and book["time_saved_rate"] > 0
        and book["unsupported_claim_count"] == 0
        for book in per_book.values()
    )
    eligible = (
        len(reviews_by_book) >= MIN_REVIEWED_BOOKS
        and means["coverage_rating"] is not None
        and means["coverage_rating"] >= MIN_COVERAGE_RATING
        and means["citation_accuracy_rating"] is not None
        and means["citation_accuracy_rating"] >= MIN_CITATION_RATING
        and means["clarity_rating"] is not None
        and means["clarity_rating"] >= MIN_CLARITY_RATING
        and means["usefulness_rating"] is not None
        and means["usefulness_rating"] >= MIN_USEFULNESS_RATING
        and means["comprehension_delta"] is not None
        and means["comprehension_delta"] >= -MAX_LEARNING_SCORE_REGRESSION
        and means["delayed_recall_delta"] is not None
        and means["delayed_recall_delta"] >= -MAX_LEARNING_SCORE_REGRESSION
        and means["time_saved_rate"] is not None
        and means["time_saved_rate"] > 0
        and no_critical_review_findings
        and each_book_meets_quality_floor
    )
    return {
        "review_count": len(reviews),
        "reviewed_book_count": len(reviews_by_book),
        "per_book": per_book,
        "means": means,
        "unsupported_claim_count": sum(len(review.unsupported_claims) for review in reviews),
        "missed_topic_count": sum(len(review.missed_topics) for review in reviews),
        "small_beta_gate_passed": eligible,
    }
