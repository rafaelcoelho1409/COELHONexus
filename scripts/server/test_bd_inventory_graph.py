"""Offline checks for the read-only Book Distiller inventory graph.

    cd apps/server && .venv/bin/python ../../scripts/server/test_bd_inventory_graph.py
"""
import asyncio
import hashlib
import sys
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

from ai.bd.agents.schemas import IssueCode, PageAssessment, PageKind
from ai.bd.graphs.ingestion.graph import build_inventory_graph


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


def rejects(label: str, operation) -> None:
    try:
        operation()
    except ValueError:
        print(f"ok  {label}")
        return
    raise AssertionError(f"{label}: expected ValueError")


book_id = "book-1"
extraction_run_id = "run-1"
markdown_by_key = {
    f"bd/books/{book_id}/ingestion/raw/{extraction_run_id}/pages/0001.md": "  \n",
    f"bd/books/{book_id}/ingestion/raw/{extraction_run_id}/pages/0002.md": "",
    f"bd/books/{book_id}/ingestion/raw/{extraction_run_id}/pages/0003.md": (
        "Plain book text with a unique long marker that must remain outside checkpoint state."
    ),
    f"bd/books/{book_id}/ingestion/raw/{extraction_run_id}/pages/0004.md": "Caption text ![figure](figures/p0004-1.png)",
    f"bd/books/{book_id}/ingestion/raw/{extraction_run_id}/pages/0005.md": "soft\u00adhyphen",
    f"bd/books/{book_id}/ingestion/raw/{extraction_run_id}/pages/0006.md": "control\x00character",
    f"bd/books/{book_id}/ingestion/raw/{extraction_run_id}/pages/0007.md": (
        "```markdown\n![literal example](not-a-real-image.png)\n```"
    ),
}
page_refs = [
    {"page": page_number, "key": key, "pictures": int(page_number in (2, 4))}
    for page_number, key in enumerate(markdown_by_key, start = 1)
]
reads: list[str] = []


async def read_page(key: str) -> str | None:
    reads.append(key)
    return markdown_by_key.get(key)


graph = build_inventory_graph(read_page, checkpointer = InMemorySaver())
config = {"configurable": {"thread_id": "bd-inventory-test"}}
initial_state = {
    "book_id": book_id,
    "extraction_run_id": extraction_run_id,
    "extraction_status": "done",
    "expected_page_count": len(page_refs),
    "page_refs": page_refs,
}
result = asyncio.run(graph.ainvoke(initial_state, config = config))

assessments = [PageAssessment.model_validate(row["assessment"]) for row in result["inventory"]]
check(
    "all physical pages receive an assessment in page order",
    [assessment.page_number for assessment in assessments],
    list(range(1, len(page_refs) + 1)),
)
check(
    "empty text without media is blank while media-only content stays distinct",
    [assessments[0].page_kind, assessments[1].page_kind],
    [PageKind.BLANK, PageKind.IMAGE_ONLY],
)
check(
    "text and media are classified as mixed while prose-only content remains text",
    [assessments[2].page_kind, assessments[3].page_kind],
    [PageKind.TEXT, PageKind.MIXED],
)
check(
    "the inventory surfaces only deterministic near-empty, control-character and soft-hyphen findings",
    [
        [issue.code for issue in assessment.issues]
        for assessment in assessments[4:]
    ],
    [[IssueCode.NEAR_EMPTY, IssueCode.SOFT_HYPHEN], [IssueCode.CONTROL_CHARACTERS], []],
)
check("image syntax in a code sample is not mistaken for extracted media",
      assessments[6].page_kind, PageKind.TEXT)
check("every page is read once by the injected read-only adapter", len(reads), len(page_refs))
check(
    "inventory records a hash of the exact raw Markdown",
    result["inventory"][2]["source_sha256"],
    hashlib.sha256(markdown_by_key[page_refs[2]["key"]].encode("utf-8")).hexdigest(),
)
check(
    "the inventory reports physical-page and quality-signal counts",
    result["inventory_summary"]["page_kinds"],
    {"blank": 1, "image_only": 1, "mixed": 1, "text": 4},
)
check("the graph marks completion only after inventorying all pages", result["status"], "complete")
checkpoint_state = repr(graph.get_state(config).values)
check("large raw Markdown payloads are not copied into checkpoint state",
      "unique long marker that must remain outside checkpoint state" in checkpoint_state, False)
resumed = asyncio.run(graph.ainvoke(None, config = config))
check("a completed checkpoint resumes with the completed inventory", resumed["status"], "complete")
check("resuming a completed inventory does not reread the raw pages", len(reads), len(page_refs))

bad_state = {**initial_state, "expected_page_count": len(page_refs) + 1}
rejects("incomplete manifests are rejected before pages are read",
        lambda: asyncio.run(build_inventory_graph(read_page).ainvoke(bad_state)))
failed_state = {**initial_state, "extraction_status": "failed"}
rejects("failed extractions are rejected before pages are read",
        lambda: asyncio.run(build_inventory_graph(read_page).ainvoke(failed_state)))
bad_key_state = {
    **initial_state,
    "page_refs": [{**page_refs[0], "key": "outside/raw/pages/0001.md"}, *page_refs[1:]],
}
rejects("page object keys must match the book and extraction raw prefix",
        lambda: asyncio.run(build_inventory_graph(read_page).ainvoke(bad_key_state)))
bad_run_id_state = {**initial_state, "extraction_run_id": "../outside"}
rejects("unsafe run identifiers cannot escape the canonical storage prefix",
        lambda: asyncio.run(build_inventory_graph(read_page).ainvoke(bad_run_id_state)))
