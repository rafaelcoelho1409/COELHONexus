"""Behavior check of Book ingestion's run — the start gate, the page-slice loop, the event stream, cancel with resume, failure and wipe —
against a throwaway `redis-server`, an in-memory MinIO and a stand-in for the Docling and PDF-reader modules. What the real extractor does with a real PDF is checked by
`test_bd_ingestion_extract.py`.

    cd apps/server && .venv/bin/python ../../scripts/server/test_bd_ingestion_run.py
"""
import asyncio
import atexit
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = _free_port()
_proc = subprocess.Popen(
    ["redis-server", "--port", str(PORT), "--bind", "127.0.0.1", "--save", "", "--appendonly", "no", "--dir", tempfile.mkdtemp()],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
atexit.register(_proc.terminate)
os.environ.update(REDIS_HOST="127.0.0.1", REDIS_PORT=str(PORT), REDIS_PASSWORD="", ENVIRONMENT="local")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

import domains
import domains.bd.ingestion.ai  # the API router loads this; the start gate's per-book mutation lock key lives here
import infra

R = infra.redis.service
ingestion = domains.bd.ingestion
dispatch = ingestion.runtime.dispatch
progress = ingestion.runtime.progress
cancel = ingestion.runtime.cancel
store = ingestion.storage.service
Entry = ingestion.outline.entities.OutlineEntry


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


for _ in range(50):
    try:
        with R.client_sync() as probe:
            probe.ping()
        break
    except Exception:
        time.sleep(0.1)


class FakeStorage:
    """The part of `MinIOStorage` the ingestion uses, in a dict."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def write(self, key, content, content_type = "text/markdown"):
        self.objects[key] = content.encode() if isinstance(content, str) else content
        return len(self.objects[key])

    async def read_bytes(self, key):
        return self.objects[key]

    async def read_text(self, key):
        return self.objects[key].decode()

    async def list(self, prefix):
        return sorted(key for key in self.objects if key.startswith(prefix))

    async def copy_object(self, src_key, dst_key):
        self.objects[dst_key] = self.objects[src_key]
        return len(self.objects[dst_key])

    async def copy_objects(self, objects):
        for src_key, dst_key in objects:
            self.objects[dst_key] = self.objects[src_key]
        return len(objects)

    async def list_subfolders(self, prefix):
        return sorted({k[len(prefix):].split("/")[0] for k in self.objects if k.startswith(prefix)})

    async def delete_prefix(self, prefix):
        gone = [k for k in self.objects if k.startswith(prefix)]
        for k in gone:
            del self.objects[k]
        return len(gone)


STORAGE = FakeStorage()
infra.minio.service.get_storage = lambda: STORAGE

N_PAGES = 12
OUTLINE = (Entry(1, "Part I", 1), Entry(2, "Chapter 1", 2), Entry(2, "Chapter 2", 8))
CALLS: list[tuple[int, int]] = []
EPUB_CALLS = 0
BEHAVIOR = SimpleNamespace(fail_on_first = None, cancel_after_calls = None, book_id = None)


def fake_convert_slice(pdf_path, first, last):
    CALLS.append((first, last))
    assert Path(pdf_path).read_bytes().startswith(b"%PDF-"), "the worker must hand the extractor the stored PDF"
    if BEHAVIOR.fail_on_first == first:
        raise RuntimeError(f"layout model crashed on page {first}")
    if BEHAVIOR.cancel_after_calls == len(CALLS):
        asyncio.run_coroutine_threadsafe(_raise_cancel(), LOOP).result()
    ent = ingestion.extract.entities
    pages = tuple(
        ent.PageExtract(page_no = p, markdown = f"# Page {p}\n\ntext of page {p}", headings = (ent.Heading(1, f"Page {p}"),),
                        tables = int(p % 4 == 0), pictures = int(p == 3), formulas = 0, figures = ("p0003-1.png",) if p == 3 else ())
        for p in range(first, last + 1)
    )
    figures = (ent.Figure(3, 1, "p0003-1.png", b"\x89PNG-fake", (0.0, 0.0, 1.0, 1.0), "a figure"),) if first <= 3 <= last else ()
    return ent.SliceResult(pages = pages, figures = figures, structured = {"texts": [], "pictures": []})


def fake_convert_epub(epub_path):
    global EPUB_CALLS
    EPUB_CALLS += 1
    assert Path(epub_path).suffix == ".epub", "the worker must preserve the EPUB extension for Docling"
    assert Path(epub_path).read_bytes() == EPUB, "the worker must hand the extractor the stored EPUB"
    ent = ingestion.extract.entities
    pages = (
        ent.PageExtract(1, "# Chapter One\n\nOpening text.", (ent.Heading(1, "Chapter One"),), 0, 0, 0, ()),
        ent.PageExtract(2, "# Chapter Two\n\nMiddle text.", (ent.Heading(1, "Chapter Two"),), 0, 0, 0, ()),
        ent.PageExtract(3, "# Chapter Three\n\nClosing text.", (ent.Heading(1, "Chapter Three"),), 0, 0, 0, ()),
    )
    return ent.SliceResult(pages = pages, figures = (), structured = {"texts": [], "pictures": []})


async def _raise_cancel() -> None:
    async with R.client() as r:
        await cancel.service.request_cancel(r, BEHAVIOR.book_id)


ingestion.extract.service = SimpleNamespace(convert_slice = fake_convert_slice, convert_epub = fake_convert_epub)
ingestion.outline.service = SimpleNamespace(
    inspect_pdf = lambda path: ingestion.outline.entities.PdfInfo(n_pages = N_PAGES, title = "A Test Book", outline = OUTLINE),
    inspect_epub = lambda path: ingestion.outline.entities.EpubInfo(title = "A Test EPUB"),
)

PDF = b"%PDF-1.7\n" + b"x" * 200
epub_buffer = io.BytesIO()
with zipfile.ZipFile(epub_buffer, "w") as archive:
    archive.writestr("mimetype", b"application/epub+zip", compress_type = zipfile.ZIP_STORED)
    archive.writestr("META-INF/container.xml", "<container/>")
EPUB = epub_buffer.getvalue()


async def events_of(book_id: str) -> list[dict]:
    async with R.client() as r:
        return await R.replay_snapshot(r, progress.keys.snapshot_key(book_id))


async def main() -> None:
    global LOOP
    LOOP = asyncio.get_running_loop()

    # ---- the upload ----
    try:
        await dispatch.service.accept_upload("x.pdf", b"<html>not a pdf</html>")
        check("a file that is not a PDF is refused", True, False)
    except ingestion.errors.UnreadableBook:
        check("a file that is not a PDF is refused", True, True)

    try:
        await dispatch.service.accept_upload("mismatch.pdf", EPUB)
        check("a PDF filename cannot hide EPUB content", True, False)
    except ingestion.errors.UnreadableBook:
        check("a PDF filename cannot hide EPUB content", True, True)

    gate = await dispatch.service.accept_upload("My_Book.pdf", PDF)
    book_id = gate["book_id"]
    BEHAVIOR.book_id = book_id
    check("a new book passes the gate with a run id", (gate["status"], bool(gate["run_id"]), gate["resumed"]), ("reserved", True, False))
    manifest = await store.read_manifest(STORAGE, book_id)
    check("the upload stored the PDF and a manifest that says queued", (STORAGE.objects[ingestion.storage.keys.source_key(book_id)] == PDF, manifest["status"], manifest["title"]), (True, "queued", "My Book"))
    again = await dispatch.service.accept_upload("My_Book.pdf", PDF)
    check("the same book is locked while its run holds the lock", (again["status"], again["run_id"]), ("locked", gate["run_id"]))
    check("the library lists the book", [(r["book_id"], r["status"]) for r in await store.list_library(STORAGE)], [(book_id, "queued")])

    # ---- a full run ----
    result = await dispatch.service.run(book_id, gate["run_id"])
    check("the run extracts every page", (result["status"], result["pages"]), ("done", N_PAGES))
    check("…a slice at a time", CALLS, [(1, 5), (6, 10), (11, 12)])
    manifest = await store.read_manifest(STORAGE, book_id)
    check("the manifest is done and lists the pages in order", (manifest["status"], manifest["n_pages"], [p["page"] for p in manifest["pages"]]), ("done", N_PAGES, list(range(1, 13))))
    extraction_id = manifest["extraction_id"]
    check("page, structured and figure outputs live under this run's raw prefix",
          all(entry["key"].startswith(ingestion.storage.keys.raw_prefix(book_id, extraction_id)) for entry in manifest["pages"]), True)
    run_record = await store.read_run_metadata(STORAGE, book_id, extraction_id)
    check("run metadata records the worker attempt and completion", (run_record["status"], run_record["attempts"][-1]["status"]), ("done", "done"))
    check("…with the PDF's own title and outline", (manifest["title"], [e["title"] for e in manifest["outline"]]), ("A Test Book", ["Part I", "Chapter 1", "Chapter 2"]))
    check("…and chapters as page ranges", manifest["chapters"], [{"title": "Part I", "first_page": 1, "last_page": 12}])
    check("a page knows its section", (manifest["pages"][0]["section_path"], manifest["pages"][8]["section_path"], manifest["pages"][3]["section_path"]),
          (["Part I"], ["Part I", "Chapter 2"], ["Part I", "Chapter 1"]))
    check("a page's markdown is stored by its key", await store.read_page(STORAGE, book_id, 7), "# Page 7\n\ntext of page 7")
    check("a page not extracted is None", await store.read_page(STORAGE, book_id, 99), None)
    check("a figure is stored and read back", await store.read_figure(STORAGE, book_id, "p0003-1.png"), b"\x89PNG-fake")
    check("each slice's structured document is stored", sorted(k.rsplit("/", 1)[1] for k in STORAGE.objects if "/structured/" in k), ["0001-0005.json", "0006-0010.json", "0011-0012.json"])
    kinds = [e["kind"] for e in await events_of(book_id)]
    check("the stream tells started, then each slice's pages and its end, then done",
          (kinds[0], kinds.count("page"), kinds.count("slice_done"), kinds[-1]), ("started", N_PAGES, 3, "done"))
    page_events = [e for e in await events_of(book_id) if e["kind"] == "page"]
    check("page events carry the markdown and the run's progress", (page_events[4]["markdown"], page_events[4]["completed"], page_events[-1]["completed"], page_events[-1]["total"]), ("# Page 5\n\ntext of page 5", 5, 12, 12))
    async with R.client() as r:
        state = await progress.service.read_state(r, book_id)
        check("the progress record says done, 12 of 12", (state["status"], state["completed"], state["total"]), ("done", 12, 12))
        check("the lock is released when the run ends", await progress.service.read_lock(r, book_id), None)
    check("a finished book is cached, not extracted again", (await dispatch.service.reserve_run(book_id))["status"], "cached")
    snap = await dispatch.service.read_snapshot(book_id)
    check("the snapshot joins manifest and progress", (snap["manifest"]["status"], snap["progress"]["status"]), ("done", "done"))
    check("an unknown book has no snapshot", await dispatch.service.read_snapshot("nope"), None)

    # ---- EPUB conversion ----
    epub_gate = await dispatch.service.accept_upload("A_Test_EPUB.epub", EPUB)
    epub_id = epub_gate["book_id"]
    check("an EPUB upload stores the archive under its own source key",
          STORAGE.objects[ingestion.storage.keys.source_key(epub_id, "epub")] == EPUB, True)
    epub_result = await dispatch.service.run(epub_id, epub_gate["run_id"])
    epub_manifest = await store.read_manifest(STORAGE, epub_id)
    check("Docling extracts an EPUB into ordered heading sections",
          (epub_result["status"], epub_manifest["title"], epub_manifest["n_pages"],
           [unit["section_path"] for unit in epub_manifest["pages"]]),
          ("done", "A Test EPUB", 3, [["Chapter One"], ["Chapter Two"], ["Chapter Three"]]))
    check("EPUB section markdown and structured output are persisted",
          (await store.read_page(STORAGE, epub_id, 2),
           ingestion.storage.keys.structured_key(epub_id, 1, 3, epub_manifest["extraction_id"]) in STORAGE.objects),
          ("# Chapter Two\n\nMiddle text.", True))
    check("an EPUB stream advertises section positions through the compatible reader event shape",
          ([event["kind"] for event in await events_of(epub_id)].count("page"), EPUB_CALLS), (3, 1))
    check("EPUB structured output lives under the extraction's raw prefix",
          ingestion.storage.keys.structured_key(epub_id, 1, 3, epub_manifest["extraction_id"]) in STORAGE.objects, True)
    await dispatch.service.wipe_book(epub_id)

    # ---- migration of a legacy book ----
    legacy_book_id = "legacy-demo"
    legacy_manifest = ingestion.storage.domain.new_manifest(
        book_id = legacy_book_id, filename = "Legacy.pdf", size_bytes = len(PDF), now = 1.0,
    )
    legacy_page_key = ingestion.storage.keys.page_key(legacy_book_id, 1)
    legacy_manifest.update(
        status = "done", n_pages = 1,
        pages = [ingestion.storage.domain.page_entry(
            page_no = 1, key = legacy_page_key, chars = 12, headings = 0, tables = 0, pictures = 0, formulas = 0,
            figures = [], section_path = [],
        )],
    )
    await STORAGE.write(ingestion.storage.keys.legacy_source_key(legacy_book_id), PDF)
    await STORAGE.write(legacy_page_key, "# Legacy page")
    await STORAGE.write(
        ingestion.storage.keys.legacy_manifest_key(legacy_book_id),
        json.dumps(legacy_manifest),
    )
    migrated = await store.migrate_legacy_book(STORAGE, legacy_book_id)
    migrated_manifest = await store.read_manifest(STORAGE, legacy_book_id)
    check("legacy objects copy into a stable run and the manifest points at the new page key",
          (migrated["status"], migrated_manifest["extraction_id"].startswith("legacy-"),
           migrated_manifest["pages"][0]["key"].startswith(ingestion.storage.keys.raw_prefix(legacy_book_id, migrated_manifest["extraction_id"]))),
          ("migrated", True, True))
    check("the migrated page stays readable", await store.read_page(STORAGE, legacy_book_id, 1), "# Legacy page")
    check("re-running migration is idempotent", (await store.migrate_legacy_book(STORAGE, legacy_book_id))["status"], "already_migrated")
    check("legacy cleanup can safely follow a prior copy-only migration",
          (await store.migrate_legacy_book(STORAGE, legacy_book_id, delete_legacy = True))["status"], "legacy_deleted")
    check("legacy keys are removed only after destination verification",
          await STORAGE.list(ingestion.storage.keys.legacy_book_prefix(legacy_book_id)), [])
    await store.wipe_book(STORAGE, legacy_book_id)

    # ---- migration from the previous namespaced layout ----
    previous_book_id = "previous-demo"
    previous_run_id = "previous-run"
    previous_page_key = ingestion.storage.keys.previous_page_key(previous_book_id, 1, previous_run_id)
    previous_manifest = ingestion.storage.domain.new_manifest(
        book_id = previous_book_id, filename = "Previous.pdf", size_bytes = len(PDF), now = 2.0,
    )
    previous_manifest = ingestion.storage.domain.with_extraction(previous_manifest, previous_run_id, 2.0)
    previous_manifest.update(
        status = "done", n_pages = 1,
        pages = [ingestion.storage.domain.page_entry(
            page_no = 1, key = previous_page_key, chars = 14, headings = 0, tables = 0, pictures = 0, formulas = 0,
            figures = [], section_path = [],
        )],
    )
    await STORAGE.write(ingestion.storage.keys.previous_source_key(previous_book_id), PDF)
    await STORAGE.write(previous_page_key, "# Previous page")
    await STORAGE.write(
        ingestion.storage.keys.previous_manifest_key(previous_book_id),
        json.dumps(previous_manifest),
    )
    migrated_previous = await store.migrate_legacy_book(STORAGE, previous_book_id, dry_run = True)
    check("the previous namespace dry run plans the complete copy without writing it",
          (migrated_previous["status"], migrated_previous["objects"],
           ingestion.storage.keys.manifest_key(previous_book_id) in STORAGE.objects),
          ("planned", 2, False))
    migrated_previous = await store.migrate_legacy_book(STORAGE, previous_book_id)
    canonical_previous_manifest = await store.read_manifest(STORAGE, previous_book_id)
    check("previous namespace objects migrate to the canonical book-first raw path",
          (migrated_previous["status"], canonical_previous_manifest["extraction_id"],
           canonical_previous_manifest["pages"][0]["key"], await store.read_page(STORAGE, previous_book_id, 1),
           await store.read_source(STORAGE, previous_book_id)),
          ("migrated", previous_run_id,
           ingestion.storage.keys.page_key(previous_book_id, 1, previous_run_id), "# Previous page", PDF))
    check("copy-only migration retains the previous objects",
          await STORAGE.list(ingestion.storage.keys.previous_book_prefix(previous_book_id)),
          sorted([
              ingestion.storage.keys.previous_source_key(previous_book_id), previous_page_key,
              ingestion.storage.keys.previous_manifest_key(previous_book_id),
          ]))
    check("previous namespace migration is idempotent",
          (await store.migrate_legacy_book(STORAGE, previous_book_id))["status"], "already_migrated")
    await store.wipe_book(STORAGE, previous_book_id)

    # ---- refresh starts over ----
    CALLS.clear()
    refreshed = await dispatch.service.accept_upload("My_Book.pdf", PDF, refresh = True)
    await dispatch.service.run(book_id, refreshed["run_id"], refresh = True)
    check("a refresh extracts every page again", CALLS, [(1, 5), (6, 10), (11, 12)])
    refreshed_manifest = await store.read_manifest(STORAGE, book_id)
    check("a refresh gets a new immutable extraction ID and retains the previous raw run",
          (refreshed_manifest["extraction_id"] != extraction_id,
           ingestion.storage.keys.page_key(book_id, 1, extraction_id) in STORAGE.objects), (True, True))

    # ---- start again from the stored PDF, no upload ----
    check("an unknown book cannot be restarted", await dispatch.service.accept_restart("nope"), None)
    check("a finished book restarts as cached", (await dispatch.service.accept_restart(book_id))["status"], "cached")
    restarted = await dispatch.service.accept_restart(book_id, refresh = True)
    check("…or, with refresh, as a new run that does not resume", (restarted["status"], restarted["book_id"], restarted["resumed"]), ("reserved", book_id, False))
    await dispatch.service.release_run(book_id, restarted["run_id"])

    # ---- cancel, then resume ----
    await dispatch.service.wipe_book(book_id)
    check("a wiped book leaves nothing behind", [k for k in STORAGE.objects if k.startswith(ingestion.storage.keys.book_prefix(book_id))], [])
    CALLS.clear()
    gate = await dispatch.service.accept_upload("My_Book.pdf", PDF)
    BEHAVIOR.cancel_after_calls = 2  # the cancel arrives while slice 2 is being extracted
    result = await dispatch.service.run(book_id, gate["run_id"])
    check("a cancel lands between slices: the slice in hand finishes, the next does not start", (result["status"], result["pages"], CALLS), ("cancelled", 10, [(1, 5), (6, 10)]))
    manifest = await store.read_manifest(STORAGE, book_id)
    cancelled_extraction_id = manifest["extraction_id"]
    check("the manifest says cancelled and keeps the pages it has", (manifest["status"], len(manifest["pages"])), ("cancelled", 10))
    check("the stream ends with cancelled", [e["kind"] for e in await events_of(book_id)][-1], "cancelled")
    BEHAVIOR.cancel_after_calls = None
    CALLS.clear()
    gate = await dispatch.service.accept_upload("My_Book.pdf", PDF)
    check("uploading it again resumes", (gate["status"], gate["resumed"]), ("reserved", True))
    check("resume keeps the interrupted extraction generation",
          (await store.read_manifest(STORAGE, book_id))["extraction_id"] == cancelled_extraction_id, True)
    check("the cancel flag of the last run does not stop the new one", (await dispatch.service.run(book_id, gate["run_id"]))["status"], "done")
    check("a resumed run extracts only the pages it lacks", CALLS, [(11, 12)])
    check("…and the book is complete", len((await store.read_manifest(STORAGE, book_id))["pages"]), N_PAGES)

    # ---- a failing slice ----
    await dispatch.service.wipe_book(book_id)
    CALLS.clear()
    BEHAVIOR.fail_on_first = 6
    gate = await dispatch.service.accept_upload("My_Book.pdf", PDF)
    result = await dispatch.service.run(book_id, gate["run_id"])
    check("a slice that fails fails the book and says why", (result["status"], result["pages"], "layout model crashed on page 6" in result["error"]), ("failed", 5, True))
    manifest = await store.read_manifest(STORAGE, book_id)
    check("the pages before it are kept", (manifest["status"], len(manifest["pages"]), "layout model crashed" in manifest["error"]), ("failed", 5, True))
    check("the stream ends with failed", [e["kind"] for e in await events_of(book_id)][-1], "failed")
    async with R.client() as r:
        check("a failed run releases the lock", await progress.service.read_lock(r, book_id), None)
    BEHAVIOR.fail_on_first = None

    # ---- the gate while a run holds the book, and wipe ----
    gate = await dispatch.service.accept_upload("My_Book.pdf", PDF)
    check("a failed book is run again", gate["status"], "reserved")
    async with R.client() as r:
        active = await progress.service.list_active(r)
    check("a queued run is listed as active", [(a["book_id"], a["progress"]["status"]) for a in active], [(book_id, "queued")])
    check("a book cannot be wiped while a run holds it", (await dispatch.service.wipe_book(book_id))["deleted"], False)
    await dispatch.service.release_run(book_id, gate["run_id"])
    wiped = await dispatch.service.wipe_book(book_id)
    check("once released it can", (wiped["deleted"], wiped["objects"] > 0), (True, True))
    async with R.client() as r:
        check("a wipe also clears the book's Redis keys", [k async for k in r.scan_iter(match = progress.keys.book_keys_pattern(book_id))], [])
    check("the library is empty again", await store.list_library(STORAGE), [])


asyncio.run(main())
