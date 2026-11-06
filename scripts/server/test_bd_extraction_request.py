"""Offline checks of the extraction request the Book Distiller audit validates before it reads any page.

    cd apps/server && .venv/bin/python ../../scripts/server/test_bd_extraction_request.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

import domains  # noqa: F401  (imported first, as the application does)
from domains.bd.ingestion.ai.schemas import ExtractionRequest


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


def rejects(label: str, **changes) -> None:
    try:
        build(**changes)
    except ValueError:
        print(f"ok  {label}")
        return
    raise AssertionError(f"{label}: expected ValueError")


def key(page: int, book: str = "book-1", run: str = "run-1") -> str:
    return f"bd/books/{book}/ingestion/raw/{run}/pages/{page:04d}.md"


def build(**changes) -> ExtractionRequest:
    values = {
        "book_id": "book-1",
        "extraction_run_id": "run-1",
        "extraction_status": "done",
        "expected_page_count": 2,
        "page_refs": [{"page": 1, "key": key(1)}, {"page": 2, "key": key(2), "pictures": 3}],
        **changes,
    }
    return ExtractionRequest(**values)


request = build()
check(
    "the pages come back as the plain dicts the graph state keeps, pictures defaulting to none",
    request.page_references(),
    [{"page": 1, "key": key(1), "pictures": 0}, {"page": 2, "key": key(2), "pictures": 3}],
)

rejects("an unfinished extraction is rejected", extraction_status = "extracting")
rejects("a path-like book id is rejected", book_id = "../book")
rejects("a run id with a trailing newline is rejected", extraction_run_id = "run-1\n")
rejects("an empty run id is rejected", extraction_run_id = "")
rejects("a page count of zero is rejected", expected_page_count = 0, page_refs = [])
rejects("a boolean is not a page count", expected_page_count = True)
rejects("fewer pages than expected are rejected", page_refs = [{"page": 1, "key": key(1)}])
rejects(
    "pages out of physical order are rejected",
    page_refs = [{"page": 2, "key": key(2)}, {"page": 1, "key": key(1)}],
)
rejects(
    "a boolean is not a page number",
    page_refs = [{"page": True, "key": key(1)}, {"page": 2, "key": key(2)}],
)
rejects(
    "a key outside the run's raw folder is rejected",
    page_refs = [{"page": 1, "key": key(1)}, {"page": 2, "key": "outside/pages/0002.md"}],
)
rejects(
    "a key of another run is rejected",
    page_refs = [{"page": 1, "key": key(1)}, {"page": 2, "key": key(2, run = "run-2")}],
)
rejects(
    "a negative picture count is rejected",
    page_refs = [{"page": 1, "key": key(1), "pictures": -1}, {"page": 2, "key": key(2)}],
)
rejects(
    "a page entry with an unknown field is rejected",
    page_refs = [{"page": 1, "key": key(1), "note": "x"}, {"page": 2, "key": key(2)}],
)
rejects("a missing page count is rejected", expected_page_count = None)

print("extraction request passed")
