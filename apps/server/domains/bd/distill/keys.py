"""Object keys for immutable Book Distiller learning-guide runs."""
from __future__ import annotations


def _safe_segment(value: str, label: str) -> str:
    if not value or any(not (character.isascii() and (character.isalnum() or character in "-_")) for character in value):
        raise ValueError(f"{label} must be a safe path segment")
    return value


def run_prefix(book_id: str, run_id: str) -> str:
    book = _safe_segment(book_id, "book_id")
    run = _safe_segment(run_id, "distill_run_id")
    return f"bd/books/{book}/distill/runs/{run}/"


def manifest_key(book_id: str, run_id: str) -> str:
    return f"{run_prefix(book_id, run_id)}manifest.json"


def current_run_key(book_id: str) -> str:
    return f"bd/books/{_safe_segment(book_id, 'book_id')}/distill/current.json"


def runs_prefix(book_id: str) -> str:
    return f"bd/books/{_safe_segment(book_id, 'book_id')}/distill/runs/"


def input_unit_key(book_id: str, run_id: str, unit_id: str) -> str:
    return f"{run_prefix(book_id, run_id)}inputs/{_safe_segment(unit_id, 'unit_id')}.json"


def learning_unit_key(book_id: str, run_id: str, unit_id: str) -> str:
    return f"{run_prefix(book_id, run_id)}learning-units/{_safe_segment(unit_id, 'unit_id')}.json"


def chapter_key(book_id: str, run_id: str, chapter_id: str) -> str:
    return f"{run_prefix(book_id, run_id)}chapters/{_safe_segment(chapter_id, 'chapter_id')}.json"


def chapter_input_key(book_id: str, run_id: str, chapter_id: str) -> str:
    return f"{run_prefix(book_id, run_id)}chapter-inputs/{_safe_segment(chapter_id, 'chapter_id')}.json"


def report_key(book_id: str, run_id: str) -> str:
    return f"{run_prefix(book_id, run_id)}report.json"


def html_key(book_id: str, run_id: str) -> str:
    return f"{run_prefix(book_id, run_id)}report.html"


def pdf_key(book_id: str, run_id: str) -> str:
    return f"{run_prefix(book_id, run_id)}report.pdf"


def synthesis_key(book_id: str, run_id: str, level: int, batch: int) -> str:
    if level < 0 or batch < 0:
        raise ValueError("synthesis level and batch must be non-negative")
    return f"{run_prefix(book_id, run_id)}synthesis/{level:03d}-{batch:05d}.json"
