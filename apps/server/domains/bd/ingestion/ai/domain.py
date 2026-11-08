"""Pure content-selection rules for Book Distiller's ingestion AI."""
from __future__ import annotations

import json
from typing import Any

from . import schemas, versions


def selection_json(selection: schemas.ContentSelectionManifest) -> str:
    """Serialize a selection revision to its canonical persisted representation."""
    return json.dumps(
        selection.model_dump(mode = "json"),
        ensure_ascii = False,
        separators = (",", ":"),
        sort_keys = True,
    )


def build_legacy_selection(
    manifest: dict[str, Any],
    revision: int,
    created_at: float,
) -> schemas.ContentSelectionManifest:
    """Conservatively migrate a completed run that predates page classification."""
    expected_page_count = manifest.get("expected_page_count")
    artifacts = manifest.get("pages")
    if type(expected_page_count) is not int or expected_page_count < 1 or not isinstance(artifacts, list):
        raise ValueError("the completed ingestion run has no complete page inventory for migration")

    artifacts_by_page = {
        page.get("page_number"): page
        for page in artifacts
        if isinstance(page, dict) and type(page.get("page_number")) is int
    }
    if set(artifacts_by_page) != set(range(1, expected_page_count + 1)):
        raise ValueError("the completed ingestion run does not cover every physical page")

    pages = []
    for page_number, artifact in sorted(artifacts_by_page.items()):
        is_blank = artifact.get("delivery_disposition") == "discarded_blank"
        pages.append(schemas.ContentSelectionPage(
            page_number = page_number,
            role = "noise" if is_blank else "unknown",
            confidence = 1.0 if is_blank else 0.0,
            rationale = (
                "The completed Ingestion quality pass marked this page as blank."
                if is_blank
                else (
                    "This completed Ingestion run predates page classification; the page is retained to avoid losing "
                    "book content. Review its inclusion before Distill."
                )
            ),
            disposition = "exclude" if is_blank else "include",
            decision_source = "content_policy" if is_blank else "conservative_fallback",
        ))
    return schemas.ContentSelectionManifest(
        classifier_version = versions.LEGACY_CONTENT_SELECTION_VERSION,
        book_id = manifest["book_id"],
        extraction_run_id = manifest["extraction_run_id"],
        agentic_run_id = manifest["agentic_run_id"],
        revision = revision,
        expected_page_count = expected_page_count,
        pages = pages,
        created_at = created_at,
    )


def apply_selection_overrides(
    selection: schemas.ContentSelectionManifest,
    overrides: list[dict[str, Any]],
    *,
    expected_revision: int,
    revision: int,
    created_at: float,
) -> schemas.ContentSelectionManifest:
    """Validate human page decisions and return their next immutable selection revision."""
    if selection.revision != expected_revision:
        raise ValueError("the content selection changed; reload the latest revision before saving")

    known_pages = {page.page_number: page for page in selection.pages}
    override_by_page: dict[int, str] = {}
    for row in overrides:
        page_number = row.get("page_number")
        disposition = row.get("disposition")
        if type(page_number) is not int or page_number not in known_pages:
            raise ValueError("an override references a page outside this content selection")
        if disposition not in {"include", "exclude"}:
            raise ValueError("page disposition must be include or exclude")
        if page_number in override_by_page:
            raise ValueError("page selection overrides must be unique")
        override_by_page[page_number] = disposition

    if not override_by_page:
        return selection

    pages = []
    for page in selection.pages:
        disposition = override_by_page.get(page.page_number)
        if disposition is None:
            pages.append(page)
        else:
            pages.append(page.model_copy(update = {
                "disposition": disposition,
                "decision_source": "user",
                "rationale": "Inclusion was changed by a reviewer.",
            }))
    if not any(page.disposition == "include" for page in pages):
        raise ValueError("at least one page must remain included in the Distill selection")

    return schemas.ContentSelectionManifest(
        classifier_version = selection.classifier_version,
        book_id = selection.book_id,
        extraction_run_id = selection.extraction_run_id,
        agentic_run_id = selection.agentic_run_id,
        revision = revision,
        expected_page_count = selection.expected_page_count,
        pages = pages,
        created_at = created_at,
    )
