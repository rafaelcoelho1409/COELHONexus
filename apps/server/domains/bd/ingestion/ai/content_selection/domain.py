"""Pure page selection and versioned selection-manifest rules."""
from __future__ import annotations

import json
import re
from typing import Any

from .. import schemas
from . import params, versions


_CONTENTS_TITLE = re.compile(
    r"^(?:(?:brief|detailed)\s+)?(?:table\s+of\s+)?contents"
    r"(?:\s+(?:in\s+detail|continued|\(continued\)))?\s*[:.]?$",
    re.I,
)
_CONTENTS_LEADER = re.compile(
    r"^\s*\S.{1,180}?(?:\.{2,}|…{2,}|·{2,}|_{2,}|\t+)\s*(?:\d{1,4}|[ivxlcdm]{1,8})\s*$",
    re.I,
)
_CONTENTS_NUMBERED_ENTRY = re.compile(
    r"^\s*(?:(?:chapter|part|appendix)\s+)?(?:\d+(?:\.\d+)*|[ivxlcdm]+)[.)]?\s+\S.{2,140}?"
    r"\s{2,}(?:\d{1,4}|[ivxlcdm]{1,8})\s*$",
    re.I,
)
_CONTENTS_TRAILING_PAGE = re.compile(r"^\s*\S.{2,160}?\s{2,}(?:\d{1,4}|[ivxlcdm]{1,8})\s*$", re.I)
_CONTENTS_TABLE_PAGE = re.compile(r"^[.\s·…]*(?:\d{1,4}|[ivxlcdm]{1,8})[.\s·…]*$", re.I)


def is_source_contents_page(markdown: str, section_path: list[str] | None = None) -> bool:
    """Identify printed contents pages so they never enter the processed study-page set."""
    lines = [line.strip() for line in markdown.splitlines() if line.strip()]
    if not lines:
        return False

    normalized_lines = [re.sub(r"^\s{0,3}#{1,6}\s*|[*_`]", "", line).strip() for line in lines]
    has_contents_title = any(_CONTENTS_TITLE.fullmatch(line) for line in normalized_lines[:8])
    has_contents_section = any(
        isinstance(value, str)
        and _CONTENTS_TITLE.fullmatch(re.sub(r"^\s{0,3}#{1,6}\s*|[*_`]", "", value).strip())
        for value in (section_path or [])
    )
    leader_entries = sum(_CONTENTS_LEADER.fullmatch(line) is not None for line in lines)
    numbered_entries = sum(_CONTENTS_NUMBERED_ENTRY.fullmatch(line) is not None for line in lines)
    trailing_page_entries = sum(_CONTENTS_TRAILING_PAGE.fullmatch(line) is not None for line in lines)
    table_page_entries = sum(
        len(cells := [cell.strip() for cell in line.strip("|").split("|")]) >= 2
        and bool(cells[0])
        and _CONTENTS_TABLE_PAGE.fullmatch(cells[-1]) is not None
        for line in lines
        if line.startswith("|") and line.endswith("|")
    )

    if has_contents_title:
        return leader_entries >= 1 or numbered_entries >= 2 or trailing_page_entries >= 2 or table_page_entries >= 2
    if has_contents_section and table_page_entries >= 2:
        return True
    return leader_entries >= 4 or numbered_entries >= 6 or trailing_page_entries >= 6


def excerpt(markdown: str) -> str:
    """Keep the beginning and end of a long page for the bounded classifier prompt."""
    limit = params.CONTENT_CLASSIFICATION_EXCERPT_CHARS
    if len(markdown) <= limit:
        return markdown
    first = int(limit * 0.75)
    return f"{markdown[:first]}\n\n[Middle omitted for bounded page classification]\n\n{markdown[-(limit - first):]}"


def disposition(role: str, confidence: float, *, delivery_disposition: str, has_pictures: bool) -> tuple[str, str]:
    """Exclude only known non-learning matter with high confidence; retain ambiguous and image-only pages."""
    if delivery_disposition == "discarded_blank":
        return "exclude", "content_policy"
    if has_pictures and role == "unknown":
        return "include", "conservative_fallback"
    if role in {"navigation", "legal", "noise"} and confidence >= params.CONTENT_EXCLUSION_CONFIDENCE:
        return "exclude", "content_policy"
    return "include", "classifier"


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
        page_disposition = row.get("disposition")
        if type(page_number) is not int or page_number not in known_pages:
            raise ValueError("an override references a page outside this content selection")
        if page_disposition not in {"include", "exclude"}:
            raise ValueError("page disposition must be include or exclude")
        if page_number in override_by_page:
            raise ValueError("page selection overrides must be unique")
        override_by_page[page_number] = page_disposition

    if not override_by_page:
        return selection

    pages = []
    for page in selection.pages:
        page_disposition = override_by_page.get(page.page_number)
        if page_disposition is None:
            pages.append(page)
        else:
            pages.append(page.model_copy(update = {
                "disposition": page_disposition,
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
