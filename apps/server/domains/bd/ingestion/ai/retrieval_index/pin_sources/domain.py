"""Pure source-page records used by all retrieval projections."""
from __future__ import annotations

from typing import Any


def page_record(
    *,
    book_id: str,
    extraction_run_id: str,
    agentic_run_id: str,
    revision: int,
    processed_page_number: int,
    source_page_number: int,
    section_path: list[str],
    content_role: str,
    content_key: str,
    content_sha256: str,
    content: str,
) -> dict[str, Any]:
    return {
        "id": f"page-{processed_page_number:06d}",
        "book_id": book_id,
        "extraction_run_id": extraction_run_id,
        "agentic_run_id": agentic_run_id,
        "processed_book_revision": revision,
        "processed_page_number": processed_page_number,
        "source_page_number": source_page_number,
        "section_path": section_path,
        "content_role": content_role,
        "content_key": content_key,
        "content_sha256": content_sha256,
        "content": content,
    }
