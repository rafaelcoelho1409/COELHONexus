"""In-flight extraction lifecycle: the start gate (cached / locked / reserved), the run itself, cancel and wipe. A book is extracted
by one run at a time (its Redis lock); PDFs are processed in page slices, while EPUBs are converted once and persisted in section
slices. Both are stored as ordered reader units and announced on the event stream."""
from __future__ import annotations
import domains, infra
from . import domain

import asyncio
import importlib
import logging
import os
import tempfile
import time
import uuid


logger = logging.getLogger(__name__)


async def accept_upload(filename: str, data: bytes, refresh: bool = False) -> dict:
    """Take an uploaded PDF or EPUB: validate it, keep it (and a manifest that says `queued`) unless already stored, and pass
    the start gate. The answer is `cached`, `locked`, or `reserved` with the run id — the router queues the task for a `reserved` one."""
    ingestion = domains.bd.ingestion
    source_format = ingestion.storage.domain.detect_format(data)
    if source_format is None:
        raise ingestion.errors.UnreadableBook("the file is not a supported PDF or EPUB")
    filename_format = ingestion.storage.domain.format_from_filename(filename)
    if filename_format is not None and filename_format != source_format:
        raise ingestion.errors.UnreadableBook("the file extension does not match its content")
    if filename_format is None:
        filename = f"{filename or 'book'}.{source_format}"
    book_id = ingestion.storage.domain.book_id(data)
    storage = infra.minio.service.get_storage()
    existing = await ingestion.storage.service.read_manifest(storage, book_id)
    if existing is None:
        await ingestion.storage.service.write_source(storage, book_id, data, source_format)
        await ingestion.storage.service.write_manifest(storage, ingestion.storage.domain.new_manifest(
            book_id = book_id, filename = filename, size_bytes = len(data), now = time.time(),
        ))
    gate = await reserve_run(book_id, refresh)
    if gate["status"] != "reserved":
        return gate
    return {**gate, "book_id": book_id, "resumed": bool(existing and existing.get("pages")) and not refresh}


async def accept_restart(book_id: str, refresh: bool = False) -> dict | None:
    """Start a run for a stored PDF or EPUB (no upload): continue a stopped extraction, or with `refresh` start
    over. None when the book does not exist; otherwise the same answer as `accept_upload`."""
    ingestion = domains.bd.ingestion
    manifest = await ingestion.storage.service.read_manifest(infra.minio.service.get_storage(), book_id)
    if manifest is None:
        return None
    gate = await reserve_run(book_id, refresh)
    if gate["status"] != "reserved":
        return gate
    return {**gate, "book_id": book_id, "resumed": bool(manifest.get("pages")) and not refresh}


async def reserve_run(book_id: str, refresh: bool = False) -> dict:
    """The start gate. `cached` when the book is fully extracted (and `refresh` is off), `locked` when a run holds it, else
    `reserved` with a new run id: the lock is taken, the last run's cancel flag and events are cleared."""
    storage = infra.minio.service.get_storage()
    manifest = await domains.bd.ingestion.storage.service.read_manifest(storage, book_id)
    if not refresh and not domain.needs_run(manifest):
        return domain.cached(book_id)
    run_id = uuid.uuid4().hex
    async with infra.redis.service.client() as r:
        taken, holder = await domains.bd.ingestion.runtime.progress.service.acquire_lock(r, book_id, run_id)
        if not taken:
            return domain.locked(book_id, holder)
        await domains.bd.ingestion.runtime.cancel.service.clear_cancel(r, book_id)
        await domains.bd.ingestion.runtime.progress.service.clear_events(r, book_id)
        await domains.bd.ingestion.runtime.progress.service.write_state(
            r, book_id, domains.bd.ingestion.runtime.progress.domain.initial_state(run_id, time.time()),
        )
    return domain.reserved(run_id)


async def release_run(book_id: str, run_id: str) -> None:
    """Give the lock back when the task could not be queued."""
    async with infra.redis.service.client() as r:
        await domains.bd.ingestion.runtime.progress.service.release_lock(r, book_id, run_id)


async def cancel_run(book_id: str) -> None:
    async with infra.redis.service.client() as r:
        await domains.bd.ingestion.runtime.cancel.service.request_cancel(r, book_id)


async def read_active() -> list[dict]:
    async with infra.redis.service.client() as r:
        return await domains.bd.ingestion.runtime.progress.service.list_active(r)


async def read_snapshot(book_id: str) -> dict | None:
    """The book's manifest and the run's progress, or None for a book that does not exist."""
    storage = infra.minio.service.get_storage()
    manifest = await domains.bd.ingestion.storage.service.read_manifest(storage, book_id)
    if manifest is None:
        return None
    async with infra.redis.service.client() as r:
        progress = await domains.bd.ingestion.runtime.progress.service.read_state(r, book_id)
    return domain.snapshot(manifest, progress)


async def wipe_book(book_id: str) -> dict:
    """Delete a book — its objects, its progress and its events — unless a run holds it."""
    async with infra.redis.service.client() as r:
        if await domains.bd.ingestion.runtime.progress.service.read_lock(r, book_id):
            return {"book_id": book_id, "deleted": False, "reason": "an extraction is running"}
        await infra.redis.service.delete_matching(r, domains.bd.ingestion.runtime.progress.keys.book_keys_pattern(book_id))
    removed = await domains.bd.ingestion.storage.service.wipe_book(infra.minio.service.get_storage(), book_id)
    return {"book_id": book_id, "deleted": True, "objects": removed}


async def run(book_id: str, run_id: str, refresh: bool = False) -> dict:
    """Extract the book. Returns the task result; every outcome — done, failed, cancelled — is also the book's manifest status and a
    final event, so a UI that connects afterwards sees how it ended."""
    storage = infra.minio.service.get_storage()
    progress = domains.bd.ingestion.runtime.progress
    manifest = await domains.bd.ingestion.storage.service.read_manifest(storage, book_id)
    if manifest is None:
        return domain.result(book_id, run_id, "failed", 0, f"book {book_id} has no manifest")

    async with infra.redis.service.client() as r:
        try:
            if not await progress.service.renew_lock(r, book_id, run_id):
                return domain.result(book_id, run_id, "rejected", 0, "another run holds the book")
            manifest = await _extract(r, storage, manifest, run_id, refresh)
            await _finish(r, storage, manifest, run_id, domains.bd.ingestion.storage.params.STATUS_DONE)
            return domain.result(book_id, run_id, "done", len(manifest["pages"]))
        except domains.bd.ingestion.errors.Cancelled:
            manifest = await _latest(storage, manifest)
            await _finish(r, storage, manifest, run_id, domains.bd.ingestion.storage.params.STATUS_CANCELLED)
            return domain.result(book_id, run_id, "cancelled", len(manifest["pages"]))
        except Exception as e:  # the failure is the book's status; the worker must not crash on it
            logger.exception(f"[bd-run] {book_id} failed")
            manifest = await _latest(storage, manifest)
            error = progress.domain.clip(domain.error_text(e))
            await _finish(r, storage, manifest, run_id, domains.bd.ingestion.storage.params.STATUS_FAILED, error)
            return domain.result(book_id, run_id, "failed", len(manifest["pages"]), error)
        finally:
            await progress.service.release_lock(r, book_id, run_id)


async def _latest(storage, fallback: dict) -> dict:
    """The manifest as the run last wrote it (the one in hand may be from before a slice)."""
    return await domains.bd.ingestion.storage.service.read_manifest(storage, fallback["book_id"]) or fallback


async def _finish(r, storage, manifest: dict, run_id: str, status: str, error: str | None = None) -> None:
    ingestion = domains.bd.ingestion
    manifest = ingestion.storage.domain.with_status(manifest, status, time.time(), error)
    await ingestion.storage.service.write_manifest(storage, manifest)
    await ingestion.runtime.progress.service.write_state(r, manifest["book_id"], {
        **ingestion.runtime.progress.domain.initial_state(run_id, manifest["created_at"]),
        "status": status, "completed": len(manifest["pages"]), "total": manifest["n_pages"], "error": error,
    })
    await ingestion.runtime.progress.service.emit(
        manifest["book_id"], status, pages = len(manifest["pages"]), total = manifest["n_pages"], error = error,
    )


async def _extract(r, storage, manifest: dict, run_id: str, refresh: bool) -> dict:
    ingestion = domains.bd.ingestion
    if not hasattr(ingestion.extract, "service"):
        importlib.import_module("domains.bd.ingestion.extract.service")
    if not hasattr(ingestion.outline, "service"):
        importlib.import_module("domains.bd.ingestion.outline.service")

    book_id = manifest["book_id"]
    with tempfile.TemporaryDirectory(prefix = "bd-") as workdir:
        source_format = ingestion.storage.domain.format_from_filename(manifest.get("filename", "")) or "pdf"
        source_path = os.path.join(workdir, f"book.{source_format}")
        data = await ingestion.storage.service.read_source(storage, book_id, source_format)
        await asyncio.to_thread(_write_file, source_path, data)

        epub_result = None
        try:
            if source_format == "epub":
                info = await asyncio.to_thread(ingestion.outline.service.inspect_epub, source_path)
                if refresh:
                    manifest = {**manifest, "pages": []}
                manifest = ingestion.storage.domain.with_status(
                    manifest, ingestion.storage.params.STATUS_EXTRACTING, time.time(),
                )
                await ingestion.storage.service.write_manifest(storage, manifest)
                await _report(r, manifest, run_id)
                await ingestion.runtime.progress.service.emit(
                    book_id, "started", title = ingestion.storage.domain.book_title(manifest["filename"], info.title),
                    n_pages = 0, outline = [], chapters = [], resumed = bool(manifest["pages"]),
                    completed = len(manifest["pages"]),
                )
                if await ingestion.runtime.cancel.service.is_cancelled(r, book_id):
                    raise ingestion.errors.Cancelled(book_id)
                epub_result = await asyncio.to_thread(ingestion.extract.service.convert_epub, source_path)
                n_pages = len(epub_result.pages)
                outline = ingestion.outline.domain.from_content_units(epub_result.pages)
                title = info.title
            else:
                info = await asyncio.to_thread(ingestion.outline.service.inspect_pdf, source_path)
                n_pages, outline, title = info.n_pages, list(info.outline), info.title
        except ingestion.errors.Cancelled:
            raise
        except Exception as e:  # document readers raise format-specific errors
            raise ingestion.errors.UnreadableBook(f"cannot open the {source_format.upper()}: {e}") from e

        if not 1 <= n_pages <= ingestion.params.MAX_PAGES:
            unit = "sections" if source_format == "epub" else "pages"
            raise ingestion.errors.UnreadableBook(
                f"{n_pages} {unit}; a book must have between 1 and {ingestion.params.MAX_PAGES}",
            )
        manifest = ingestion.storage.domain.with_info(
            manifest, title = title, n_pages = n_pages,
            outline = ingestion.outline.domain.to_dicts(outline),
            chapters = ingestion.outline.domain.chapter_ranges(outline, n_pages), now = time.time(),
        )
        if refresh and source_format == "pdf":
            manifest = {**manifest, "pages": []}
        manifest = ingestion.storage.domain.with_status(
            manifest, ingestion.storage.params.STATUS_EXTRACTING, time.time(),
        )
        await ingestion.storage.service.write_manifest(storage, manifest)
        await _report(r, manifest, run_id)
        await ingestion.runtime.progress.service.emit(
            book_id, "started", title = manifest["title"], n_pages = n_pages,
            outline = manifest["outline"], chapters = manifest["chapters"],
            resumed = bool(manifest["pages"]), completed = len(manifest["pages"]),
        )

        if source_format == "epub" and epub_result.structured is not None:
            await ingestion.storage.service.write_structured(storage, book_id, 1, n_pages, epub_result.structured)
        slices = ingestion.extract.domain.pending_slices(
            n_pages, ingestion.storage.domain.done_pages(manifest), ingestion.params.SLICE_PAGES,
        )
        for first, last in slices:
            if await ingestion.runtime.cancel.service.is_cancelled(r, book_id):
                raise ingestion.errors.Cancelled(book_id)
            started = time.time()
            if source_format == "epub":
                result = ingestion.extract.entities.SliceResult(
                    pages = epub_result.pages[first - 1:last],
                    figures = tuple(figure for figure in epub_result.figures if first <= figure.page_no <= last),
                    structured = None,
                )
            else:
                result = await asyncio.to_thread(ingestion.extract.service.convert_slice, source_path, first, last)
            manifest, entries = await _store_slice(storage, manifest, outline, first, last, result)
            await ingestion.runtime.progress.service.renew_lock(r, book_id, run_id)
            await _report(r, manifest, run_id)
            for entry, page in zip(entries, result.pages):
                await ingestion.runtime.progress.service.emit(
                    book_id, "page",
                    **ingestion.runtime.progress.domain.page_event(
                        page = entry, markdown = page.markdown, completed = len(manifest["pages"]), total = n_pages,
                    ),
                )
            await ingestion.runtime.progress.service.emit(
                book_id, "slice_done", first = first, last = last, seconds = round(time.time() - started, 2),
            )
    return manifest


async def _store_slice(storage, manifest: dict, outline, first: int, last: int, result) -> tuple[dict, list[dict]]:
    """Write a slice's pages, figures and structured document, then the manifest that lists them (last, so a manifest never names a
    page that is not stored)."""
    ingestion = domains.bd.ingestion
    book_id = manifest["book_id"]
    entries: list[dict] = []
    for page in result.pages:
        key = await ingestion.storage.service.write_page(storage, book_id, page.page_no, page.markdown)
        entries.append(ingestion.storage.domain.page_entry(
            page_no = page.page_no, key = key, chars = len(page.markdown), headings = len(page.headings),
            tables = page.tables, pictures = page.pictures, formulas = page.formulas, figures = list(page.figures),
            section_path = ingestion.outline.domain.section_path(outline, page.page_no),
        ))
    await ingestion.storage.service.write_figures(storage, book_id, result.figures)
    if result.structured is not None:
        await ingestion.storage.service.write_structured(storage, book_id, first, last, result.structured)
    manifest = ingestion.storage.domain.with_pages(manifest, entries, time.time())
    await ingestion.storage.service.write_manifest(storage, manifest)
    return manifest, entries


async def _report(r, manifest: dict, run_id: str) -> None:
    progress = domains.bd.ingestion.runtime.progress
    await progress.service.write_state(r, manifest["book_id"], {
        **progress.domain.initial_state(run_id, manifest["created_at"]),
        "status": progress.params.STATUS_RUNNING, "completed": len(manifest["pages"]), "total": manifest["n_pages"],
    })


def _write_file(path: str, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)
