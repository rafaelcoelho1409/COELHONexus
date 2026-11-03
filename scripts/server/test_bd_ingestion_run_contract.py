"""Offline checks for the Book Distiller agentic-run output contract.

    cd apps/server && .venv/bin/python ../../scripts/server/test_bd_ingestion_run_contract.py
"""
import sys
from pathlib import Path

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

from domains.bd.ingestion.ai import schemas
from domains.bd.ingestion.storage import keys


BOOK_ID = "book-123"
EXTRACTION_RUN_ID = "extract-456"
AGENTIC_RUN_ID = "agentic-789"
SOURCE_SHA256 = "a" * 64
OUTPUT_SHA256 = "b" * 64


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


def rejects(label: str, build) -> None:
    try:
        build()
    except ValidationError:
        print(f"ok  {label}")
        return
    raise AssertionError(f"{label}: expected Pydantic validation to fail")


def rejects_value_error(label: str, build) -> None:
    try:
        build()
    except ValueError:
        print(f"ok  {label}")
        return
    raise AssertionError(f"{label}: expected ValueError")


def page_artifact(page_number: int, status=schemas.AgenticPageStatus.PENDING):
    completed_hashes = {}
    if status in {schemas.AgenticPageStatus.DONE, schemas.AgenticPageStatus.REVIEW_REQUIRED}:
        completed_hashes = {
            "normalized_sha256": OUTPUT_SHA256,
            "assessment_sha256": "c" * 64,
            "patch_proposals_sha256": "d" * 64,
        }
    return schemas.IngestionPageArtifact(
        page_number = page_number,
        source_key = keys.page_key(BOOK_ID, page_number, EXTRACTION_RUN_ID),
        source_sha256 = SOURCE_SHA256,
        normalized_key = keys.agentic_page_key(BOOK_ID, AGENTIC_RUN_ID, page_number),
        assessment_key = keys.agentic_assessment_key(BOOK_ID, AGENTIC_RUN_ID, page_number),
        patch_proposals_key = keys.agentic_patch_proposals_key(
            BOOK_ID, AGENTIC_RUN_ID, page_number,
        ),
        status = status,
        **completed_hashes,
    )


def manifest(*, status=schemas.AgenticRunStatus.QUEUED, pages=None, **overrides):
    values = {
        "agentic_run_id": AGENTIC_RUN_ID,
        "book_id": BOOK_ID,
        "extraction_run_id": EXTRACTION_RUN_ID,
        "source_manifest_key": keys.manifest_key(BOOK_ID),
        "source_manifest_sha256": "e" * 64,
        "status": status,
        "expected_page_count": 2,
        "pages": [] if pages is None else pages,
        "book_metadata_key": keys.agentic_book_metadata_key(BOOK_ID, AGENTIC_RUN_ID),
        "book_metadata_sha256": "f" * 64 if status == schemas.AgenticRunStatus.DONE else None,
        "quality_report_key": keys.agentic_quality_report_key(BOOK_ID, AGENTIC_RUN_ID),
        "quality_report_sha256": "1" * 64 if status == schemas.AgenticRunStatus.DONE else None,
        "created_at": 100.0,
        "updated_at": 100.0,
    }
    values.update(overrides)
    return schemas.IngestionAIRunManifest(**values)


check(
    "agentic artifacts have a run-isolated prefix separate from immutable raw input",
    keys.agentic_prefix(BOOK_ID, AGENTIC_RUN_ID),
    "bd/books/book-123/ingestion/agentic/agentic-789/",
)
check(
    "normalized Markdown uses stable physical page numbering",
    keys.agentic_page_key(BOOK_ID, AGENTIC_RUN_ID, 3),
    "bd/books/book-123/ingestion/agentic/agentic-789/pages/0003.md",
)
check(
    "page assessments and patch proposals have distinct artifact keys",
    (
        keys.agentic_assessment_key(BOOK_ID, AGENTIC_RUN_ID, 3),
        keys.agentic_patch_proposals_key(BOOK_ID, AGENTIC_RUN_ID, 3),
    ),
    (
        "bd/books/book-123/ingestion/agentic/agentic-789/assessments/0003.json",
        "bd/books/book-123/ingestion/agentic/agentic-789/patch-proposals/0003.json",
    ),
)
check(
    "raw extraction key remains independent from agentic output keys",
    keys.page_key(BOOK_ID, 3, EXTRACTION_RUN_ID),
    "bd/books/book-123/ingestion/raw/extract-456/pages/0003.md",
)

queued = manifest(pages = [page_artifact(1), page_artifact(2)])
check("queued manifests can pin source hashes before outputs are generated",
      queued.pages[0].source_sha256, SOURCE_SHA256)
check("manifest serialization carries a schema version", queued.model_dump(mode = "json")["schema_version"], "1.0")

completed = manifest(
    status = schemas.AgenticRunStatus.DONE,
    pages = [
        page_artifact(1, schemas.AgenticPageStatus.DONE),
        page_artifact(2, schemas.AgenticPageStatus.REVIEW_REQUIRED),
    ],
    updated_at = 120.0,
    finished_at = 120.0,
)
check("completed manifests preserve review-required pages", completed.pages[1].status,
      schemas.AgenticPageStatus.REVIEW_REQUIRED)
check("completed manifest round-trips as JSON", schemas.IngestionAIRunManifest.model_validate_json(
    completed.model_dump_json(),
).agentic_run_id, AGENTIC_RUN_ID)

rejects(
    "completed manifests require every physical page",
    lambda: manifest(
        status = schemas.AgenticRunStatus.DONE,
        pages = [page_artifact(1, schemas.AgenticPageStatus.DONE)],
        updated_at = 120.0,
        finished_at = 120.0,
    ),
)
rejects(
    "completed pages require hashes for all output artifacts",
    lambda: schemas.IngestionPageArtifact(
        page_number = 1,
        source_key = keys.page_key(BOOK_ID, 1, EXTRACTION_RUN_ID),
        source_sha256 = SOURCE_SHA256,
        normalized_key = keys.agentic_page_key(BOOK_ID, AGENTIC_RUN_ID, 1),
        assessment_key = keys.agentic_assessment_key(BOOK_ID, AGENTIC_RUN_ID, 1),
        patch_proposals_key = keys.agentic_patch_proposals_key(BOOK_ID, AGENTIC_RUN_ID, 1),
        status = schemas.AgenticPageStatus.DONE,
    ),
)
rejects(
    "run manifest page references cannot be duplicated",
    lambda: manifest(pages = [page_artifact(1), page_artifact(1)]),
)
rejects(
    "run manifest pages must remain in physical order",
    lambda: manifest(pages = [page_artifact(2), page_artifact(1)]),
)
rejects(
    "run IDs cannot contain path separators",
    lambda: manifest(agentic_run_id = "../escape"),
)
rejects(
    "source hashes must be lowercase SHA-256 hex",
    lambda: schemas.IngestionPageArtifact(
        **{
            **page_artifact(1).model_dump(),
            "source_sha256": "g" * 64,
        },
    ),
)
rejects(
    "failed manifests require an error and a terminal timestamp",
    lambda: manifest(status = schemas.AgenticRunStatus.FAILED),
)
rejects(
    "failed manifests require an error even when the terminal timestamp is present",
    lambda: manifest(
        status = schemas.AgenticRunStatus.FAILED,
        updated_at = 120.0,
        finished_at = 120.0,
    ),
)
rejects(
    "completed manifests require hashes for book metadata and the quality report",
    lambda: manifest(
        status = schemas.AgenticRunStatus.DONE,
        pages = [
            page_artifact(1, schemas.AgenticPageStatus.DONE),
            page_artifact(2, schemas.AgenticPageStatus.DONE),
        ],
        book_metadata_sha256 = None,
        quality_report_sha256 = None,
        updated_at = 120.0,
        finished_at = 120.0,
    ),
)
rejects_value_error(
    "page key builders reject invalid physical page numbers",
    lambda: keys.agentic_page_key(BOOK_ID, AGENTIC_RUN_ID, 0),
)
