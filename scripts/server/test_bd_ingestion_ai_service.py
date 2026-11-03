"""Offline checks for the read-only Book Distiller AI ingestion integration.

    cd apps/server && .venv/bin/python ../../scripts/server/test_bd_ingestion_ai_service.py
"""
import asyncio
import json
import sys
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

from ai.bd.agents import schemas
from domains.bd.ingestion.ai.service import run_inventory, run_page_audit
from domains.bd.ingestion.storage.keys import manifest_key, page_key


class FakeStorage:
    def __init__(self, book_id: str, manifest: dict):
        self.values = {manifest_key(book_id): json.dumps(manifest)}
        self.reads: list[str] = []
        self.writes = 0

    async def read_text(self, key: str) -> str:
        self.reads.append(key)
        try:
            return self.values[key]
        except KeyError as error:
            raise FileNotFoundError(key) from error

    async def write(self, *args, **kwargs) -> None:
        self.writes += 1
        raise AssertionError("inventory integration must not write to storage")


class FakeAuditor:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def ainvoke(self, payload):
        self.calls.append(payload)
        return {"structured_response": self.response}


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


def rejects(label: str, operation, exception_type: type[Exception]) -> None:
    try:
        asyncio.run(operation())
    except exception_type:
        print(f"ok  {label}")
        return
    raise AssertionError(f"{label}: expected {exception_type.__name__}")


book_id = "book-123"
extraction_id = "run-abc"
page_contents = {
    page_key(book_id, 1, extraction_id): "A readable first page.",
    page_key(book_id, 2, extraction_id): "  \n",
}
manifest = {
    "book_id": book_id,
    "extraction_id": extraction_id,
    "status": "done",
    "n_pages": 2,
    "pages": [
        {"page": 1, "key": page_key(book_id, 1, extraction_id), "pictures": 0},
        {"page": 2, "key": page_key(book_id, 2, extraction_id), "pictures": 0},
    ],
}

storage = FakeStorage(book_id, manifest)
storage.values.update(page_contents)
result = asyncio.run(run_inventory(
    book_id, expected_extraction_id = extraction_id, storage = storage,
))
check("the integration returns a completed inventory", result["status"], "complete")
check("every page in the manifest is inventoried", result["inventory_summary"]["pages_total"], 2)
check("the exact run-specific page objects are read", storage.reads[1:], list(page_contents))
check("the integration does not write to MinIO", storage.writes, 0)

page_auditor = FakeAuditor(schemas.PageAssessment(
    page_number = 2,
    page_kind = schemas.PageKind.TEXT,
    confidence = 0.95,
    issues = [],
))
audit_storage = FakeStorage(book_id, manifest)
audit_storage.values.update(page_contents)
audit_markdown = "A readable audited page. PRIVATE_TAIL_MUST_NOT_ENTER_STATE"
audit_storage.values[page_key(book_id, 2, extraction_id)] = audit_markdown
audit_result = asyncio.run(run_page_audit(
    book_id,
    2,
    expected_extraction_id = extraction_id,
    storage = audit_storage,
    auditor = page_auditor,
))
check("the page-audit integration returns the requested assessment",
      audit_result["assessment"]["page_number"], 2)
check("page audit reads only the requested canonical page",
      audit_storage.reads, [manifest_key(book_id), page_key(book_id, 2, extraction_id)])
check("the auditor receives the raw Markdown from MinIO",
      json.loads(page_auditor.calls[0]["messages"][0]["content"])["markdown"], audit_markdown)
check("page-audit integration does not write to MinIO", audit_storage.writes, 0)
check("page-audit result does not retain raw Markdown",
      "PRIVATE_TAIL_MUST_NOT_ENTER_STATE" in repr(audit_result), False)

rejects(
    "invalid page numbers are rejected before reading MinIO",
    lambda: run_page_audit(book_id, 0, storage = audit_storage, auditor = page_auditor),
    ValueError,
)
rejects(
    "page numbers outside the manifest are rejected",
    lambda: run_page_audit(book_id, 3, storage = audit_storage, auditor = page_auditor),
    ValueError,
)

rejects(
    "stale extraction IDs are rejected",
    lambda: run_inventory(book_id, expected_extraction_id = "old-run", storage = storage),
    ValueError,
)

incomplete_manifest = {**manifest, "status": "extracting"}
incomplete_storage = FakeStorage(book_id, incomplete_manifest)
rejects(
    "unfinished extraction runs are rejected",
    lambda: run_inventory(book_id, storage = incomplete_storage),
    ValueError,
)
check("unfinished runs are rejected before page reads", len(incomplete_storage.reads), 1)

short_manifest = {**manifest, "pages": manifest["pages"][:1]}
short_storage = FakeStorage(book_id, short_manifest)
rejects(
    "incomplete page manifests are rejected",
    lambda: run_inventory(book_id, storage = short_storage),
    ValueError,
)

checkpoint_storage = FakeStorage(book_id, manifest)
checkpoint_storage.values.update(page_contents)
checkpointer = InMemorySaver()
first_result = asyncio.run(run_inventory(
    book_id, storage = checkpoint_storage, checkpointer = checkpointer,
))
page_read_keys = [key for key in checkpoint_storage.reads if key != manifest_key(book_id)]
resumed_result = asyncio.run(run_inventory(
    book_id, storage = checkpoint_storage, checkpointer = checkpointer,
))
check("a completed checkpoint returns its inventory", resumed_result["status"], first_result["status"])
check("a completed checkpoint does not reread page objects",
      [key for key in checkpoint_storage.reads if key != manifest_key(book_id)], page_read_keys)
