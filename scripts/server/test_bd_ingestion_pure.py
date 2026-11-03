"""Offline check of the pure helpers Book ingestion keeps in its `domain.py` files — the outline tree, the slices still to extract, the
manifest, and the shapes the run reports with. None of it needs Docling, torch, Redis or MinIO; it runs in the API image's environment.

    cd apps/server && .venv/bin/python ../../scripts/server/test_bd_ingestion_pure.py
"""
import importlib
import io
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from types import ModuleType, SimpleNamespace

for name, value in {"REDIS_HOST": "localhost", "REDIS_PORT": "6379", "REDIS_PASSWORD": "x", "ENVIRONMENT": "local"}.items():
    os.environ.setdefault(name, value)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

import domains

ingestion = domains.bd.ingestion
outline = ingestion.outline.domain
Entry = ingestion.outline.entities.OutlineEntry
extract = ingestion.extract.domain
storage = ingestion.storage.domain
keys = ingestion.storage.keys
progress = ingestion.runtime.progress.domain
dispatch = ingestion.runtime.dispatch.domain


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


# ---- the outline: rows from the reader, a tree a book can be trusted to have ------------------------------------------------
check("rows count levels and pages from 0; a bookmark that points nowhere is dropped",
      outline.entries_from_rows([(0, "Part I", 4), (1, "Chapter 1", 5), (1, "Dangling", None)]),
      [Entry(1, "Part I", 5), Entry(2, "Chapter 1", 6)])
RAW = [Entry(3, "  Deep   start ", 1), Entry(1, "Chapter 1", 3), Entry(1, "Chapter 1", 3), Entry(4, "Sub-sub", 4), Entry(2, "", 5),
       Entry(1, "Out of range", 99), Entry(1, "Chapter 2", 8)]
check("an outline is cleaned: whitespace collapsed, untitled / out-of-range / back-to-back repeated entries dropped, levels start at 1 and go down one at a time",
      outline.normalize(RAW, n_pages = 10),
      [Entry(1, "Deep start", 1), Entry(1, "Chapter 1", 3), Entry(2, "Sub-sub", 4), Entry(1, "Chapter 2", 8)])
TREE = [Entry(1, "Part I", 1), Entry(2, "Chapter 1", 2), Entry(3, "Intro", 2), Entry(3, "Setup", 5), Entry(2, "Chapter 2", 9), Entry(1, "Part II", 12)]
check("a page sits in the deepest section in effect on it",
      [outline.section_path(TREE, p) for p in (1, 2, 4, 5, 9, 11, 12)],
      [["Part I"], ["Part I", "Chapter 1", "Intro"], ["Part I", "Chapter 1", "Intro"], ["Part I", "Chapter 1", "Setup"],
       ["Part I", "Chapter 2"], ["Part I", "Chapter 2"], ["Part II"]])
check("a page before the first entry belongs to no section", outline.section_path([Entry(1, "Ch", 5)], 3), [])
check("chapters at level 2 run to the page before the next section at that level or above",
      outline.chapter_ranges(TREE, n_pages = 20, level = 2),
      [{"title": "Chapter 1", "first_page": 2, "last_page": 8}, {"title": "Chapter 2", "first_page": 9, "last_page": 11}])
check("the top level runs to the end of the book", outline.chapter_ranges(TREE, n_pages = 20)[-1],
      {"title": "Part II", "first_page": 12, "last_page": 20})
check("an outline survives the manifest's JSON shape", outline.from_dicts(outline.to_dicts(TREE)), TREE)

# ---- the slices still to extract --------------------------------------------------------------------------------------------
check("a fresh book is cut into slices of at most `size` pages", extract.pending_slices(12, set(), 5), [(1, 5), (6, 10), (11, 12)])
check("a book that stopped half way continues after the pages it has", extract.pending_slices(12, set(range(1, 8)), 5), [(8, 12)])
check("pages done in the middle split the run in two", extract.pending_slices(10, {4, 5}, 5), [(1, 3), (6, 10)])
check("a finished book has nothing left", extract.pending_slices(6, set(range(1, 7)), 5), [])
check("a slice never exceeds `size`", all(b - a < 3 for a, b in extract.pending_slices(20, {7}, 3)), True)
check("a figure is named by page and order", extract.figure_name(12, 2), "p0012-2.png")
check("an EPUB figure is named by section and order", extract.epub_figure_name(12, 2), "s0012-2.png")
check("the structured document keeps no pixels, only the picture's place",
      extract.strip_images({"texts": [{"text": "a"}], "pictures": [{"image": {"uri": "data:image/png;base64,AAAA"}, "prov": [1]}]}),
      {"texts": [{"text": "a"}], "pictures": [{"image": None, "prov": [1]}]})

# ---- the book's identity and manifest ---------------------------------------------------------------------------------------
check("the same bytes are the same book, other bytes another", (storage.book_id(b"%PDF-a") == storage.book_id(b"%PDF-a"), storage.book_id(b"%PDF-a") == storage.book_id(b"%PDF-b")), (True, False))
check("an id is 16 hex characters", len(storage.book_id(b"x")), 16)
check("a PDF starts with %PDF-, after any leading whitespace", [storage.looks_like_pdf(b"%PDF-1.7 ..."), storage.looks_like_pdf(b"\n %PDF-1.4"), storage.looks_like_pdf(b"<html>")], [True, True, False])
check("a title is the PDF's own, else the file name without extension and underscores",
      [storage.book_title("a.pdf", "  The   Book "), storage.book_title("Deep_Learning_2nd.PDF", ""), storage.book_title("", "")],
      ["The Book", "Deep Learning 2nd", "Untitled book"])
M0 = storage.new_manifest(book_id = "b1", filename = "My_Book.pdf", size_bytes = 10, now = 1.0)
check("a new book is queued with nothing known of its pages", (M0["status"], M0["n_pages"], M0["pages"], M0["title"]), ("queued", 0, [], "My Book"))
check("a new book has no extraction generation until its first run is reserved", M0["extraction_id"], None)
M1 = storage.with_info(M0, title = "", n_pages = 4, outline = [{"level": 1, "title": "A", "page": 1}], chapters = [], now = 2.0)
check("opening the file fills in the page count and keeps the file-name title", (M1["n_pages"], M1["title"], M1["updated_at"]), (4, "My Book", 2.0))


def page(n: int, **kw) -> dict:
    return storage.page_entry(page_no = n, key = keys.page_key("b1", n), chars = 10 * n, headings = 0, tables = 0, pictures = 0, formulas = 0,
                              figures = [], section_path = kw.get("section_path", []))


M2 = storage.with_pages(M1, [page(3), page(1)], now = 3.0)
check("pages are kept in book order", [p["page"] for p in M2["pages"]], [1, 3])
M3 = storage.with_pages(M2, [page(3, section_path = ["A"]), page(2)], now = 4.0)
check("a page extracted again replaces its entry", ([p["page"] for p in M3["pages"]], storage.entry_for_page(M3, 3)["section_path"]), ([1, 2, 3], ["A"]))
check("the pages done are a set", storage.done_pages(M3), {1, 2, 3})
check("a refresh starts a new empty extraction generation", (storage.with_extraction(M3, "next-run", 10, reset = True)["extraction_id"],
      storage.with_extraction(M3, "next-run", 10, reset = True)["pages"]), ("next-run", []))
check("the library row has counts, not pages", (storage.library_row(M3)["pages_done"], "pages" in storage.library_row(M3), storage.library_row(M3)["status"]), (3, False, "queued"))
check("the library lists the most recently touched first",
      [r["book_id"] for r in storage.newest_first([{"book_id": "old", "updated_at": 1}, {"book_id": "new", "updated_at": 5}])], ["new", "old"])
check("a status change carries its error", storage.with_status(M3, "failed", 9.0, "boom")["error"], "boom")
check("a manifest is read back from JSON, or the default when it cannot be", (storage.parse_json('{"a": 1}', None), storage.parse_json("{broken", None), storage.parse_json(None, [])), ({"a": 1}, None, []))

# ---- keys -------------------------------------------------------------------------------------------------------------------
check("book metadata and source use the canonical book-first prefix",
      all(k.startswith("bd/books/b1/") for k in (keys.source_key("b1"), keys.manifest_key("b1"))), True)
check("all Docling output is isolated under the extraction's raw prefix",
      all(k.startswith("bd/books/b1/ingestion/raw/run-1/") for k in (
          keys.page_key("b1", 7, "run-1"), keys.structured_key("b1", 1, 5, "run-1"),
          keys.figure_key("b1", "p0001-1.png", "run-1"),
      )), True)
check("run metadata stays separate from raw extraction artifacts",
      keys.run_metadata_key("b1", "run-1"), "bd/books/b1/ingestion/runs/run-1/run.json")
check("the previous namespaced layout remains addressable during migration",
      (keys.previous_source_key("b1"), keys.previous_page_key("b1", 7, "run-1")),
      ("bd/ingestion/books/b1/source.pdf", "bd/ingestion/books/b1/runs/run-1/raw/pages/0007.md"))
check("legacy keys remain available for migration", keys.page_key("b1", 7), "books/b1/pages/0007.md")
check("page keys sort in book order", sorted([keys.page_key("b", 10, "r"), keys.page_key("b", 9, "r"), keys.page_key("b", 100, "r")]),
      [keys.page_key("b", 9, "r"), keys.page_key("b", 10, "r"), keys.page_key("b", 100, "r")])
check("a figure name cannot climb out of the run's raw prefix",
      keys.figure_key("b1", "../../x.png", "run-1"), "bd/books/b1/ingestion/raw/run-1/figures/x.png")
lock_keys = ingestion.runtime.progress.keys
check("the lock scan prefix is an exact prefix of the lock key", lock_keys.lock_key("b1").startswith(lock_keys.lock_prefix()), True)
check("the wipe pattern covers progress, snapshot and cancel but not the lock",
      [k.startswith("coelhonexus:bd:ingestion:b1:") for k in (lock_keys.progress_key("b1"), lock_keys.snapshot_key("b1"), lock_keys.cancel_key("b1"), lock_keys.lock_key("b1"))],
      [True, True, True, False])

# ---- what the run reports ---------------------------------------------------------------------------------------------------
check("a run starts queued with nothing read", {k: progress.initial_state("r1", 1.0)[k] for k in ("status", "completed", "total")}, {"status": "queued", "completed": 0, "total": 0})
check("a queued or running run is active, a finished one is not", [progress.is_active({"status": s}) for s in ("queued", "running", "done", "failed")] + [progress.is_active(None)], [True, True, False, False, False])
long_page = {**page(2), "figures": ["p0002-1.png"], "section_path": ["A"]}
event = progress.page_event(page = long_page, markdown = "x" * 100_000, completed = 2, total = 9)
check("a page event carries the page's numbers and where the run is", (event["page"], event["completed"], event["total"], event["figures"], event["section_path"]), (2, 2, 9, ["p0002-1.png"], ["A"]))
check("…and its markdown, cut to what an event may carry", (len(event["markdown"]), event["truncated"]), (ingestion.runtime.progress.params.EVENT_MARKDOWN_MAX_CHARS, True))
check("a short page is not cut", progress.page_event(page = long_page, markdown = "short", completed = 1, total = 1)["truncated"], False)
check("a book needs a run when it was never extracted, stopped, or failed",
      [dispatch.needs_run(None), dispatch.needs_run({"status": "failed", "n_pages": 3, "pages": []}), dispatch.needs_run({"status": "done", "n_pages": 3, "pages": [1, 2]})], [True, True, True])
check("a book with every page is cached", dispatch.needs_run({"status": "done", "n_pages": 2, "pages": [1, 2]}), False)
check("the start gate answers with the book and run", (dispatch.queued("b1", "r1", True), dispatch.cached("b1")["status"], dispatch.locked("b1", "r0")["run_id"]),
      ({"status": "queued", "book_id": "b1", "run_id": "r1", "resumed": True}, "cached", "r0"))
check("an error is told by its type", dispatch.error_text(ValueError("bad")), "ValueError: bad")

# ---- the API image must be able to import all of this without Docling -------------------------------------------------------
check("importing the package did not import Docling, torch or pypdfium2", [m for m in ("docling", "torch", "pypdfium2") if m in sys.modules], [])

epub_buffer = io.BytesIO()
with zipfile.ZipFile(epub_buffer, "w") as archive:
    archive.writestr("mimetype", b"application/epub+zip", compress_type = zipfile.ZIP_STORED)
    archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="OEBPS/package.opf"/></rootfiles></container>')
    archive.writestr("OEBPS/package.opf", '<package><metadata><title>Fixture EPUB title</title></metadata><manifest><item id="chapter" href="chapter.xhtml"/></manifest><spine><itemref idref="chapter"/></spine></package>')
    archive.writestr("OEBPS/chapter.xhtml", "<html><body><h1>Chapter</h1></body></html>")
EPUB = epub_buffer.getvalue()
check("format detection distinguishes supported PDF and EPUB inputs",
      [storage.detect_format(b"%PDF-1.7"), storage.detect_format(EPUB), storage.detect_format(b"PK not a book")],
      ["pdf", "epub", None])
check("the source extension is normalized and unknown suffixes stay unsupported",
      [storage.format_from_filename("book.PDF"), storage.format_from_filename("book.epub"), storage.format_from_filename("book.zip")],
      ["pdf", "epub", None])
check("PDF and EPUB sources have distinct canonical keys", (keys.source_key("b1"), keys.source_key("b1", "epub")),
      ("bd/books/b1/source.pdf", "bd/books/b1/source.epub"))

epub_units = extract.epub_sections("Front matter\n\n# Part I\n\nOpening.\n\n## Chapter 1\n\nText.\n\n# Part II\n\nMore text.")
check("EPUB Markdown is split at its highest-level headings while preserving order and its preface",
      (len(epub_units), [unit.page_no for unit in epub_units], [unit.headings[0].text for unit in epub_units],
       "Front matter" in epub_units[0].markdown, "More text." in epub_units[1].markdown),
      (2, [1, 2], ["Part I", "Part II"], True, True))
fenced_units = extract.epub_sections("# Chapter 1\n\n```md\n# Not a section\n```\n\n# Chapter 2")
check("EPUB headings inside fenced code do not split sections", len(fenced_units), 2)
title_units = extract.epub_sections("# Book title\n\n## Chapter One\n\nText.\n\n## Chapter Two\n\nMore text.")
check("a lone title heading does not collapse repeated chapter headings into one section",
      (extract.epub_section_level("# Book title\n\n## Chapter One\n\n## Chapter Two"), len(title_units), title_units[0].headings[0].text),
      (2, 2, "Book title"))
check("EPUB headings become the shared outline and chapter navigation", outline.to_dicts(outline.from_content_units(epub_units)),
      [{"level": 1, "title": "Part I", "page": 1}, {"level": 2, "title": "Chapter 1", "page": 1},
       {"level": 1, "title": "Part II", "page": 2}])
sys.modules["pypdfium2"] = ModuleType("pypdfium2")
outline_service = importlib.import_module("domains.bd.ingestion.outline.service")

class PdfFixture:
    def __init__(self, bookmarks):
        self.bookmarks = bookmarks

    def __len__(self):
        return 5

    def get_toc(self):
        return self.bookmarks

    def get_metadata_dict(self):
        return {"Title": "Fixture PDF"}

    def close(self):
        pass


legacy_pdf = PdfFixture([
    SimpleNamespace(level = 0, title = "Legacy API", page_index = 2),
    SimpleNamespace(level = 1, title = "No destination", page_index = None),
])
outline_service.pdfium.PdfDocument = lambda _path: legacy_pdf
legacy_info = outline_service.inspect_pdf("fixture.pdf")
modern_pdf = PdfFixture([
    SimpleNamespace(level = 0, get_title = lambda: "Method API",
                    get_dest = lambda: SimpleNamespace(get_index = lambda: 4)),
    SimpleNamespace(level = 1, get_title = lambda: "No destination", get_dest = lambda: None),
])
outline_service.pdfium.PdfDocument = lambda _path: modern_pdf
modern_info = outline_service.inspect_pdf("fixture.pdf")
check("PDF outline inspection supports legacy tuple and method-wrapper bookmark APIs",
      [(info.n_pages, info.title, outline.to_dicts(info.outline)) for info in (legacy_info, modern_info)],
      [(5, "Fixture PDF", [{"level": 1, "title": "Legacy API", "page": 3}]),
       (5, "Fixture PDF", [{"level": 1, "title": "Method API", "page": 5}])])

with tempfile.NamedTemporaryFile(suffix = ".epub") as fixture:
    fixture.write(EPUB)
    fixture.flush()
    check("EPUB package inspection reads title metadata without unpacking the book", outline_service.inspect_epub(fixture.name).title,
          "Fixture EPUB title")
