"""In-flight extraction lifecycle: the start gate (cached / locked / reserved), the run itself, cancel and wipe. A book is extracted
by one run at a time (its Redis lock); PDFs are processed in page slices, while EPUBs are converted once and persisted in section
slices. Both are stored as ordered reader units and announced on the event stream."""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
import uuid

import infra

from ...... import settings
from ..... import ingestion
from .... import source
from . import domain

logger = logging.getLogger(__name__)


async def accept_upload(filename: str, data: bytes, refresh: bool = False, auto_chain: bool = False) -> dict:
    """Take an uploaded PDF or EPUB: validate it, keep it (and a manifest that says `queued`) unless already stored, and pass
    the start gate. The answer is `cached`, `locked`, or `reserved` with the run id — the router queues the task for a `reserved` one."""
    source_format = source.storage.domain.detect_format(data)
    if source_format is None:
        raise source.errors.UnreadableBook("the file is not a supported PDF or EPUB")
    filename_format = source.storage.domain.format_from_filename(filename)
    if filename_format is not None and filename_format != source_format:
        raise source.errors.UnreadableBook("the file extension does not match its content")
    if filename_format is None:
        filename = f"{filename or 'book'}.{source_format}"
    book_id = source.storage.domain.book_id(data)
    storage = infra.minio.service.get_storage()
    existing = await source.storage.service.read_manifest(storage, book_id)
    if existing is None:
        await source.storage.service.write_source(storage, book_id, data, source_format)
        await source.storage.service.write_manifest(storage, source.storage.domain.new_manifest(
            book_id = book_id, filename = filename, size_bytes = len(data), now = time.time(),
        ))
    else:
        existing = await source.storage.service.ensure_current_layout(storage, book_id) or existing
    gate = await reserve_run(book_id, refresh, auto_chain)
    if gate["status"] != "reserved":
        return gate
    return {**gate, "book_id": book_id, "resumed": bool(existing and existing.get("pages")) and not refresh}


async def accept_restart(book_id: str, refresh: bool = False, auto_chain: bool = False) -> dict | None:
    """Start a run for a stored PDF or EPUB (no upload): continue a stopped extraction, or with `refresh` start
    over. None when the book does not exist; otherwise the same answer as `accept_upload`."""
    storage = infra.minio.service.get_storage()
    manifest = await source.storage.service.ensure_current_layout(storage, book_id)
    if manifest is None:
        return None
    gate = await reserve_run(book_id, refresh, auto_chain)
    if gate["status"] != "reserved":
        return gate
    return {**gate, "book_id": book_id, "resumed": bool(manifest.get("pages")) and not refresh}


async def reserve_run(book_id: str, refresh: bool = False, auto_chain: bool = False) -> dict:
    async with (
        infra.redis.service.client() as redis,
        redis.lock(
            ingestion.ai.progress.keys.book_mutation_lock_key(book_id),
            timeout = 120,
            blocking_timeout = 30,
        ),
    ):
        return await _reserve_run_unlocked(book_id, refresh, auto_chain)


async def _reserve_run_unlocked(book_id: str, refresh: bool = False, auto_chain: bool = False) -> dict:
    """The start gate. `cached` when the book is fully extracted (and `refresh` is off), `locked` when a run holds it, else
    `reserved` with a new run id: the lock is taken, the last run's cancel flag and events are cleared."""
    storage = infra.minio.service.get_storage()
    manifest = await source.storage.service.ensure_current_layout(storage, book_id)
    if not refresh and not domain.needs_run(manifest):
        return domain.cached(book_id)
    if manifest is None:
        raise RuntimeError(f"book {book_id} has no manifest")
    run_id = uuid.uuid4().hex
    async with infra.redis.service.client() as r:
        taken, holder = await source.runtime.progress.service.acquire_lock(r, book_id, run_id)
        if not taken:
            return domain.locked(book_id, holder)
        await source.runtime.cancel.service.clear_cancel(r, book_id)
        await source.runtime.progress.service.clear_events(r, book_id)
        await source.runtime.progress.service.write_state(
            r, book_id, source.runtime.progress.domain.initial_state(run_id, time.time()),
        )
    now = time.time()
    extraction_id = manifest.get("extraction_id") if manifest else None
    if refresh or not extraction_id:
        extraction_id = uuid.uuid4().hex
    manifest = source.storage.domain.with_extraction(
        manifest, extraction_id, now, reset = bool(refresh),
    )
    manifest = source.storage.domain.with_status(
        manifest, source.storage.params.STATUS_QUEUED, now,
    )
    manifest["auto_chain"] = auto_chain
    try:
        await source.storage.service.write_manifest(storage, manifest)
        await source.storage.service.update_run_metadata(
            storage, manifest, source.storage.params.STATUS_QUEUED, now, attempt_id = run_id,
        )
    except Exception:
        try:
            await release_run(book_id, run_id)
        except Exception:
            logger.exception(f"[bd-run] unable to release reservation lock for {book_id}")
        raise
    return domain.reserved(run_id)


async def release_run(book_id: str, run_id: str) -> None:
    """Give the lock back when the task could not be queued."""
    async with infra.redis.service.client() as r:
        await source.runtime.progress.service.release_lock(r, book_id, run_id)


async def cancel_run(book_id: str) -> None:
    async with infra.redis.service.client() as r:
        await source.runtime.cancel.service.request_cancel(r, book_id)


async def read_active() -> list[dict]:
    async with infra.redis.service.client() as r:
        return await source.runtime.progress.service.list_active(r)


async def read_snapshot(book_id: str) -> dict | None:
    """The book's manifest and the run's progress, or None for a book that does not exist."""
    storage = infra.minio.service.get_storage()
    manifest = await source.storage.service.read_manifest(storage, book_id)
    if manifest is None:
        return None
    async with infra.redis.service.client() as r:
        progress = await source.runtime.progress.service.read_state(r, book_id)
    return domain.snapshot(manifest, progress)


async def wipe_book(book_id: str) -> dict:
    """Delete a book — its objects, its progress and its events — unless a run holds it."""
    async with (
        infra.redis.service.client() as r,
        r.lock(
            ingestion.ai.progress.keys.book_mutation_lock_key(book_id),
            timeout = 3600,
            blocking_timeout = 30,
        ),
    ):
        if await source.runtime.progress.service.read_lock(r, book_id):
            return {"book_id": book_id, "deleted": False, "reason": "an extraction is running"}
        active_agentic = await _active_agentic_run(book_id)
        if active_agentic is not None:
            return {"book_id": book_id, "deleted": False, "reason": "an Agentic AI run is running"}
        await infra.redis.service.delete_matching(r, source.runtime.progress.keys.book_keys_pattern(book_id))
        removed = await source.storage.service.wipe_book(infra.minio.service.get_storage(), book_id)
        return {"book_id": book_id, "deleted": True, "objects": removed}


async def wipe_extraction(book_id: str) -> dict:
    """Clear extraction and derived AI artifacts while retaining the uploaded source and book record."""
    storage = infra.minio.service.get_storage()
    async with (
        infra.redis.service.client() as redis,
        redis.lock(
            ingestion.ai.progress.keys.book_mutation_lock_key(book_id),
            timeout = 3600,
            blocking_timeout = 30,
        ),
    ):
        manifest = await source.storage.service.ensure_current_layout(storage, book_id)
        if manifest is None:
            raise FileNotFoundError(f"book {book_id!r} not found")
        if await source.runtime.progress.service.read_lock(redis, book_id):
            raise ValueError("stop Docling extraction and wait for it to finish before wiping")
        agentic_runs = await _agentic_runs(book_id)
        active_agentic = next(
            (
                run for run in agentic_runs
                if run["status"] in {
                    ingestion.ai.runs.schemas.AgenticRunStatus.QUEUED.value,
                    ingestion.ai.runs.schemas.AgenticRunStatus.RUNNING.value,
                }
            ),
            None,
        )
        if active_agentic is not None:
            raise ValueError("stop Agentic AI and wait for it to finish before wiping Docling extraction")

        await ingestion.ai.service.delete_ingestion_ai_checkpoints(
            agentic_runs,
            include_inventory = True,
        )
        for run in agentic_runs:
            await settings.llm_counter.service.delete_run("bd", run["agentic_run_id"])
        removed = await storage.delete_prefix(
            source.storage.keys.ingestion_prefix(book_id),
        )
        await infra.redis.service.delete_matching(
            redis,
            source.runtime.progress.keys.book_keys_pattern(book_id),
        )
        await redis.delete(source.runtime.progress.keys.cancel_key(book_id))
        now = time.time()
        reset = {
            **manifest,
            "title": source.storage.domain.book_title(manifest.get("filename", ""), ""),
            "n_pages": 0,
            "status": source.storage.params.STATUS_READY,
            "error": None,
            "outline": [],
            "chapters": [],
            "pages": [],
            "extraction_id": None,
            "updated_at": now,
        }
        await source.storage.service.write_manifest(storage, reset)
        return {"book_id": book_id, "wiped": True, "objects_deleted": removed}


async def _agentic_runs(book_id: str) -> list[dict]:
    storage = infra.minio.service.get_storage()
    run_ids = await storage.list_subfolders(source.storage.keys.agentic_runs_prefix(book_id))
    runs = await asyncio.gather(*(
        ingestion.ai.service.read_ingestion_ai_run(book_id, run_id, storage = storage)
        for run_id in run_ids
        if run_id != "current"
    ))
    return [run for run in runs if run is not None]


async def _active_agentic_run(book_id: str) -> dict | None:
    active_statuses = {
        ingestion.ai.runs.schemas.AgenticRunStatus.QUEUED.value,
        ingestion.ai.runs.schemas.AgenticRunStatus.RUNNING.value,
    }
    return next((run for run in await _agentic_runs(book_id) if run["status"] in active_statuses), None)


async def run(book_id: str, run_id: str, refresh: bool = False) -> dict:
    """Extract the book. Returns the task result; every outcome — done, failed, cancelled — is also the book's manifest status and a
    final event, so a UI that connects afterwards sees how it ended."""
    storage = infra.minio.service.get_storage()
    progress = source.runtime.progress
    manifest = await source.storage.service.read_manifest(storage, book_id)
    if manifest is None:
        return domain.result(book_id, run_id, "failed", 0, f"book {book_id} has no manifest")

    async with infra.redis.service.client() as r:
        try:
            if not await progress.service.renew_lock(r, book_id, run_id):
                return domain.result(book_id, run_id, "rejected", 0, "another run holds the book")
            manifest = await _extract(r, storage, manifest, run_id, refresh)
            await _finish(r, storage, manifest, run_id, source.storage.params.STATUS_DONE)
            return domain.result(book_id, run_id, "done", len(manifest["pages"]))
        except source.errors.Cancelled:
            manifest = await _latest(storage, manifest)
            await _finish(r, storage, manifest, run_id, source.storage.params.STATUS_CANCELLED)
            return domain.result(book_id, run_id, "cancelled", len(manifest["pages"]))
        except Exception as e:  # the failure is the book's status; the worker must not crash on it
            logger.exception(f"[bd-run] {book_id} failed")
            manifest = await _latest(storage, manifest)
            error = progress.domain.clip(domain.error_text(e))
            await _finish(r, storage, manifest, run_id, source.storage.params.STATUS_FAILED, error)
            return domain.result(book_id, run_id, "failed", len(manifest["pages"]), error)
        finally:
            await progress.service.release_lock(r, book_id, run_id)


async def _latest(storage, fallback: dict) -> dict:
    """The manifest as the run last wrote it (the one in hand may be from before a slice)."""
    return await source.storage.service.read_manifest(storage, fallback["book_id"]) or fallback


async def _finish(r, storage, manifest: dict, run_id: str, status: str, error: str | None = None) -> None:
    manifest = source.storage.domain.with_status(manifest, status, time.time(), error)
    await source.storage.service.write_manifest(storage, manifest)
    await source.storage.service.update_run_metadata(
        storage, manifest, status, manifest["updated_at"], attempt_id = run_id, error = error,
    )
    await source.runtime.progress.service.write_state(r, manifest["book_id"], {
        **source.runtime.progress.domain.initial_state(run_id, manifest["created_at"]),
        "status": status, "completed": len(manifest["pages"]), "total": manifest["n_pages"], "error": error,
    })
    if status == source.storage.params.STATUS_DONE and manifest.get("auto_chain", False):
        await _start_automatic_agentic_run(manifest)
    await source.runtime.progress.service.emit(
        manifest["book_id"], status, pages = len(manifest["pages"]), total = manifest["n_pages"], error = error,
    )


async def _start_automatic_agentic_run(manifest: dict) -> None:
    """Queue one automatic AI candidate after Docling commits a completed extraction."""
    book_id = manifest["book_id"]
    extraction_run_id = manifest.get("extraction_id")
    try:
        agentic_manifest, created = await ingestion.ai.service.create_automatic_ingestion_ai_run(
            book_id,
            extraction_run_id,
        )
        if not created:
            return
        agentic_run_id = agentic_manifest["agentic_run_id"]
        await ingestion.ai.progress.service.emit(
            book_id,
            agentic_run_id,
            "run_queued",
            status = agentic_manifest["status"],
            progress = agentic_manifest["progress"],
        )
        task = infra.celery.service.app.send_task(
            ingestion.ai.params.TASK_NAME,
            args = (book_id, extraction_run_id, agentic_run_id),
            task_id = f"bd-agentic-{book_id}-{extraction_run_id}",
        )
        infra.opentelemetry.service.tag_celery_task(task.id)
    except Exception as error:
        logger.exception(f"[bd-run] unable to queue automatic agentic processing for {book_id}")
        try:
            if _safe_run_segment(extraction_run_id):
                agentic_run_id = ingestion.ai.service.automatic_run_id(extraction_run_id)
                await ingestion.ai.service.mark_ingestion_ai_run_failed(
                    book_id,
                    agentic_run_id,
                    error,
                )
                failed = await ingestion.ai.service.read_ingestion_ai_run(
                    book_id,
                    agentic_run_id,
                )
                if failed is None:
                    return
                await ingestion.ai.progress.service.emit(
                    book_id,
                    agentic_run_id,
                    "run_failed",
                    status = ingestion.ai.runs.schemas.AgenticRunStatus.FAILED.value,
                    error = failed["error"],
                    progress = failed["progress"],
                )
        except Exception:
            logger.exception(f"[bd-run] unable to persist automatic agentic failure for {book_id}")


def _safe_run_segment(value: object) -> bool:
    return isinstance(value, str) and bool(value) and all(
        character.isascii() and (character.isalnum() or character in "-_")
        for character in value
    )


async def _extract(r, storage, manifest: dict, run_id: str, refresh: bool) -> dict:
    book_id = manifest["book_id"]
    with tempfile.TemporaryDirectory(prefix = "bd-") as workdir:
        source_format = source.storage.domain.format_from_filename(manifest.get("filename", "")) or "pdf"
        source_path = os.path.join(workdir, f"book.{source_format}")
        data = await source.storage.service.read_source(storage, book_id, source_format)
        await asyncio.to_thread(_write_file, source_path, data)

        epub_result = None
        try:
            if source_format == "epub":
                info = await asyncio.to_thread(source.outline.service.inspect_epub, source_path)
                manifest = source.storage.domain.with_status(
                    manifest, source.storage.params.STATUS_EXTRACTING, time.time(),
                )
                await source.storage.service.write_manifest(storage, manifest)
                await source.storage.service.update_run_metadata(
                    storage, manifest, source.storage.params.STATUS_EXTRACTING, manifest["updated_at"],
                    attempt_id = run_id,
                )
                await _report(r, manifest, run_id)
                await source.runtime.progress.service.emit(
                    book_id, "started", title = source.storage.domain.book_title(manifest["filename"], info.title),
                    n_pages = 0, outline = [], chapters = [], resumed = bool(manifest["pages"]),
                    completed = len(manifest["pages"]),
                )
                if await source.runtime.cancel.service.is_cancelled(r, book_id):
                    raise source.errors.Cancelled(book_id)
                epub_result = await asyncio.to_thread(source.extract.service.convert_epub, source_path)
                n_pages = len(epub_result.pages)
                outline = source.outline.domain.from_content_units(epub_result.pages)
                title = info.title
            else:
                info = await asyncio.to_thread(source.outline.service.inspect_pdf, source_path)
                n_pages, outline, title = info.n_pages, list(info.outline), info.title
        except source.errors.Cancelled:
            raise
        except Exception as e:  # document readers raise format-specific errors
            raise source.errors.UnreadableBook(f"cannot open the {source_format.upper()}: {e}") from e

        if not 1 <= n_pages <= source.params.MAX_PAGES:
            unit = "sections" if source_format == "epub" else "pages"
            raise source.errors.UnreadableBook(
                f"{n_pages} {unit}; a book must have between 1 and {source.params.MAX_PAGES}",
            )
        manifest = source.storage.domain.with_info(
            manifest, title = title, n_pages = n_pages,
            outline = source.outline.domain.to_dicts(outline),
            chapters = source.outline.domain.chapter_ranges(outline, n_pages), now = time.time(),
        )
        manifest = source.storage.domain.with_status(
            manifest, source.storage.params.STATUS_EXTRACTING, time.time(),
        )
        await source.storage.service.write_manifest(storage, manifest)
        await source.storage.service.update_run_metadata(
            storage, manifest, source.storage.params.STATUS_EXTRACTING, manifest["updated_at"], attempt_id = run_id,
        )
        await _report(r, manifest, run_id)
        await source.runtime.progress.service.emit(
            book_id, "started", title = manifest["title"], n_pages = n_pages,
            outline = manifest["outline"], chapters = manifest["chapters"],
            resumed = bool(manifest["pages"]), completed = len(manifest["pages"]),
        )

        if source_format == "epub" and epub_result.structured is not None:
            await source.storage.service.write_structured(
                storage, book_id, 1, n_pages, epub_result.structured, manifest["extraction_id"],
            )
        slices = source.extract.domain.pending_slices(
            n_pages, source.storage.domain.done_pages(manifest), source.params.SLICE_PAGES,
        )
        for first, last in slices:
            if await source.runtime.cancel.service.is_cancelled(r, book_id):
                raise source.errors.Cancelled(book_id)
            started = time.time()
            if source_format == "epub":
                result = source.extract.entities.SliceResult(
                    pages = epub_result.pages[first - 1:last],
                    figures = tuple(figure for figure in epub_result.figures if first <= figure.page_no <= last),
                    structured = None,
                )
            else:
                result = await asyncio.to_thread(source.extract.service.convert_slice, source_path, first, last)
            manifest, entries = await _store_slice(storage, manifest, outline, first, last, result)
            await source.runtime.progress.service.renew_lock(r, book_id, run_id)
            await _report(r, manifest, run_id)
            for entry, page in zip(entries, result.pages):
                await source.runtime.progress.service.emit(
                    book_id, "page",
                    **source.runtime.progress.domain.page_event(
                        page = entry, markdown = page.markdown, completed = len(manifest["pages"]), total = n_pages,
                    ),
                )
            await source.runtime.progress.service.emit(
                book_id, "slice_done", first = first, last = last, seconds = round(time.time() - started, 2),
            )
    return manifest


async def _store_slice(storage, manifest: dict, outline, first: int, last: int, result) -> tuple[dict, list[dict]]:
    """Write a slice's pages, figures and structured document, then the manifest that lists them (last, so a manifest never names a
    page that is not stored)."""
    book_id = manifest["book_id"]
    entries: list[dict] = []
    for page in result.pages:
        key = await source.storage.service.write_page(
            storage, book_id, page.page_no, page.markdown, manifest["extraction_id"],
        )
        entries.append(source.storage.domain.page_entry(
            page_no = page.page_no, key = key, chars = len(page.markdown), headings = len(page.headings),
            tables = page.tables, pictures = page.pictures, formulas = page.formulas, figures = list(page.figures),
            section_path = source.outline.domain.section_path(outline, page.page_no),
        ))
    await source.storage.service.write_figures(storage, book_id, result.figures, manifest["extraction_id"])
    if result.structured is not None:
        await source.storage.service.write_structured(
            storage, book_id, first, last, result.structured, manifest["extraction_id"],
        )
    manifest = source.storage.domain.with_pages(manifest, entries, time.time())
    await source.storage.service.write_manifest(storage, manifest)
    return manifest, entries


async def _report(r, manifest: dict, run_id: str) -> None:
    progress = source.runtime.progress
    await progress.service.write_state(r, manifest["book_id"], {
        **progress.domain.initial_state(run_id, manifest["created_at"]),
        "status": progress.params.STATUS_RUNNING, "completed": len(manifest["pages"]), "total": manifest["n_pages"],
    })


def _write_file(path: str, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)
