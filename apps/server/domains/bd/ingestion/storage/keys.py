"""Object keys for Book Distiller books and immutable ingestion runs."""
from __future__ import annotations


ROOT_PREFIX = "bd/"
PREVIOUS_BOOKS_PREFIX = "bd/ingestion/books/"
LEGACY_BOOKS_PREFIX = "books/"


def books_prefix() -> str:
    return f"{ROOT_PREFIX}books/"


def book_prefix(book_id: str) -> str:
    return f"{books_prefix()}{book_id.strip().strip('/')}/"


def previous_book_prefix(book_id: str) -> str:
    return PREVIOUS_BOOKS_PREFIX + book_id.strip().strip("/") + "/"


def legacy_book_prefix(book_id: str) -> str:
    return LEGACY_BOOKS_PREFIX + book_id.strip().strip("/") + "/"


def source_key(book_id: str, source_format: str = "pdf") -> str:
    if source_format not in ("pdf", "epub"):
        raise ValueError(f"unsupported book format: {source_format}")
    return f"{book_prefix(book_id)}source.{source_format}"


def previous_source_key(book_id: str, source_format: str = "pdf") -> str:
    if source_format not in ("pdf", "epub"):
        raise ValueError(f"unsupported book format: {source_format}")
    return f"{previous_book_prefix(book_id)}source.{source_format}"


def legacy_source_key(book_id: str, source_format: str = "pdf") -> str:
    if source_format not in ("pdf", "epub"):
        raise ValueError(f"unsupported book format: {source_format}")
    return f"{legacy_book_prefix(book_id)}source.{source_format}"


def manifest_key(book_id: str) -> str:
    return f"{book_prefix(book_id)}manifest.json"


def previous_manifest_key(book_id: str) -> str:
    return f"{previous_book_prefix(book_id)}manifest.json"


def legacy_manifest_key(book_id: str) -> str:
    return f"{legacy_book_prefix(book_id)}manifest.json"


def ingestion_prefix(book_id: str) -> str:
    return f"{book_prefix(book_id)}ingestion/"


def extraction_prefix(book_id: str, extraction_id: str) -> str:
    return f"{ingestion_prefix(book_id)}runs/{_safe_id(extraction_id)}/"


def previous_extraction_prefix(book_id: str, extraction_id: str) -> str:
    return f"{previous_book_prefix(book_id)}runs/{_safe_id(extraction_id)}/"


def raw_prefix(book_id: str, extraction_id: str) -> str:
    return f"{ingestion_prefix(book_id)}raw/{_safe_id(extraction_id)}/"


def agentic_prefix(book_id: str, agentic_run_id: str) -> str:
    _safe_segment(book_id, "a book ID")
    _safe_segment(agentic_run_id, "an agentic run ID")
    return f"{ingestion_prefix(book_id)}agentic/{agentic_run_id}/"


def agentic_runs_prefix(book_id: str) -> str:
    _safe_segment(book_id, "a book ID")
    return f"{ingestion_prefix(book_id)}agentic/"


def current_agentic_run_key(book_id: str) -> str:
    return f"{agentic_runs_prefix(book_id)}current.json"


def agentic_manifest_key(book_id: str, agentic_run_id: str) -> str:
    return f"{agentic_prefix(book_id, agentic_run_id)}manifest.json"


def agentic_page_key(book_id: str, agentic_run_id: str, page_no: int) -> str:
    _positive_page(page_no)
    return f"{agentic_prefix(book_id, agentic_run_id)}pages/{page_no:04d}.md"


def agentic_assessment_key(book_id: str, agentic_run_id: str, page_no: int) -> str:
    _positive_page(page_no)
    return f"{agentic_prefix(book_id, agentic_run_id)}assessments/{page_no:04d}.json"


def agentic_patch_proposals_key(book_id: str, agentic_run_id: str, page_no: int) -> str:
    _positive_page(page_no)
    return f"{agentic_prefix(book_id, agentic_run_id)}patch-proposals/{page_no:04d}.json"


def agentic_book_metadata_key(book_id: str, agentic_run_id: str) -> str:
    return f"{agentic_prefix(book_id, agentic_run_id)}book-metadata.json"


def agentic_quality_report_key(book_id: str, agentic_run_id: str) -> str:
    return f"{agentic_prefix(book_id, agentic_run_id)}quality-report.json"


def agentic_content_selection_key(book_id: str, agentic_run_id: str, revision: int) -> str:
    _positive_page(revision)
    return f"{agentic_prefix(book_id, agentic_run_id)}content-selection/revisions/{revision:05d}.json"


def agentic_current_content_selection_key(book_id: str, agentic_run_id: str) -> str:
    return f"{agentic_prefix(book_id, agentic_run_id)}content-selection/current.json"


def previous_raw_prefix(book_id: str, extraction_id: str) -> str:
    return f"{previous_extraction_prefix(book_id, extraction_id)}raw/"


def run_metadata_key(book_id: str, extraction_id: str) -> str:
    return f"{extraction_prefix(book_id, extraction_id)}run.json"


def previous_run_metadata_key(book_id: str, extraction_id: str) -> str:
    return f"{previous_extraction_prefix(book_id, extraction_id)}run.json"


def page_key(book_id: str, page_no: int, extraction_id: str | None = None) -> str:
    """Page Markdown in the current run's raw payload; no ID means the oldest layout."""
    if extraction_id is None:
        return f"{legacy_book_prefix(book_id)}pages/{page_no:04d}.md"
    return f"{raw_prefix(book_id, extraction_id)}pages/{page_no:04d}.md"


def previous_page_key(book_id: str, page_no: int, extraction_id: str) -> str:
    return f"{previous_raw_prefix(book_id, extraction_id)}pages/{page_no:04d}.md"


def structured_key(book_id: str, first: int, last: int, extraction_id: str | None = None) -> str:
    """Docling's structured slice output, retained alongside the Markdown."""
    if extraction_id is None:
        return f"{legacy_book_prefix(book_id)}structured/{first:04d}-{last:04d}.json"
    return f"{raw_prefix(book_id, extraction_id)}structured/{first:04d}-{last:04d}.json"


def previous_structured_key(book_id: str, first: int, last: int, extraction_id: str) -> str:
    return f"{previous_raw_prefix(book_id, extraction_id)}structured/{first:04d}-{last:04d}.json"


def figure_key(book_id: str, name: str, extraction_id: str | None = None) -> str:
    """Figure PNG path; only the basename is accepted from the document."""
    filename = name.strip().replace(chr(92), "/").rsplit("/", 1)[-1].replace("..", "")
    prefix = (
        legacy_book_prefix(book_id) + "figures/"
        if extraction_id is None else raw_prefix(book_id, extraction_id) + "figures/"
    )
    return f"{prefix}{filename}"


def previous_figure_key(book_id: str, name: str, extraction_id: str) -> str:
    filename = name.strip().replace(chr(92), "/").rsplit("/", 1)[-1].replace("..", "")
    return f"{previous_raw_prefix(book_id, extraction_id)}figures/{filename}"


def _safe_id(value: str) -> str:
    extraction_id = str(value)
    if not extraction_id or any(not (char.isalnum() or char in "-_") for char in extraction_id):
        raise ValueError("an extraction ID must contain letters, numbers, '-' or '_'")
    return extraction_id


def _safe_segment(value: str, label: str) -> str:
    segment = str(value)
    if not _is_safe_segment(segment):
        raise ValueError(f"{label} must contain letters, numbers, '-' or '_'")
    return segment


def _is_safe_segment(value: str) -> bool:
    return bool(value) and all(char.isascii() and (char.isalnum() or char in "-_") for char in value)


def _positive_page(page_no: int) -> None:
    if type(page_no) is not int or page_no <= 0:
        raise ValueError("page_no must be a positive integer")
