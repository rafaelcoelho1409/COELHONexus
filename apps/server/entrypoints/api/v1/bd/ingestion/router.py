"""Book ingestion over HTTP: upload a PDF or EPUB, follow extraction by reader unit (SSE), and read the extracted content. The extraction itself runs in the
`bd` Celery queue; this router is HTTP only (see `domains.bd.ingestion.runtime.dispatch`)."""
import domains, infra
import domains.bd.ingestion.task
import domains.bd.ingestion.ai.task
from . import params, schemas

import asyncio
import hashlib
import json
import logging
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile
from starlette.responses import StreamingResponse


logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("", response_model = schemas.StartResponse)
async def upload_book(
    file: UploadFile = File(...),
    refresh: bool = Form(False),
    auto_chain: bool = Form(False),
) -> dict:
    """Status: queued (the task is dispatched; `resumed` when it continues a stopped extraction) / cached (already extracted, no
    `refresh`) / locked (an extraction of this book is in flight). The same file is the same book."""
    data = await _read_upload(file)
    try:
        gate = await domains.bd.ingestion.runtime.dispatch.service.accept_upload(
            file.filename or "", data, refresh, auto_chain,
        )
    except domains.bd.ingestion.errors.UnreadableBook as e:
        raise HTTPException(status_code = 400, detail = str(e))
    return await _queue(gate, refresh)


@router.post("/{book_id}/run", response_model = schemas.StartResponse)
async def run_book(book_id: str, refresh: bool = False, auto_chain: bool = False) -> dict:
    """Start (or continue) the extraction of a stored book: the way to resume a cancelled or failed
    book, or with `refresh` to extract it again, without uploading it again. Same answers as the upload."""
    gate = await domains.bd.ingestion.runtime.dispatch.service.accept_restart(book_id, refresh, auto_chain)
    if gate is None:
        raise HTTPException(status_code = 404, detail = f"book {book_id!r} not found")
    return await _queue(gate, refresh)


@router.get("", response_model = list[schemas.BookRow])
async def list_books() -> list[dict]:
    storage = infra.minio.service.get_storage()
    books = await domains.bd.ingestion.storage.service.list_library(storage)
    semaphore = asyncio.Semaphore(8)

    async def _with_normalized_title(book: dict) -> dict:
        async with semaphore:
            metadata = await domains.bd.ingestion.ai.service.read_current_ingestion_ai_metadata(
                book["book_id"], storage = storage,
            )
        return {**book, "title": metadata["title"]} if metadata else book

    return await asyncio.gather(*(_with_normalized_title(book) for book in books))


@router.get("/active", response_model = schemas.ActiveRuns)
async def list_active() -> dict:
    return {"active": await domains.bd.ingestion.runtime.dispatch.service.read_active()}


@router.get("/{book_id}", response_model = schemas.Snapshot)
async def get_book(book_id: str) -> dict:
    """The manifest (outline, chapters, the pages extracted so far) and the run's progress."""
    snapshot = await domains.bd.ingestion.runtime.dispatch.service.read_snapshot(book_id)
    if snapshot is None:
        raise HTTPException(status_code = 404, detail = f"book {book_id!r} not found")
    agentic_run = await domains.bd.ingestion.ai.service.read_current_ingestion_ai_run(book_id)
    snapshot["agentic"] = await _agentic_status(agentic_run) if agentic_run is not None else None
    metadata = await domains.bd.ingestion.ai.service.read_current_ingestion_ai_metadata(book_id)
    if metadata is not None:
        snapshot["manifest"]["title"] = metadata["title"]
    return snapshot


@router.post("/{book_id}/agentic/runs", response_model = schemas.AgenticRunStart, status_code = 202)
async def start_agentic_run(book_id: str) -> dict:
    """Queue one manual candidate run against the book's current completed extraction."""
    try:
        manifest = await domains.bd.ingestion.ai.service.create_ingestion_ai_run(book_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code = 404, detail = str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code = 409, detail = str(error)) from error

    await domains.bd.ingestion.ai.progress.service.emit(
        book_id,
        manifest["agentic_run_id"],
        "run_queued",
        status = manifest["status"],
        progress = manifest["progress"],
    )
    try:
        task = domains.bd.ingestion.ai.task.run_ingestion_ai.apply_async(args = (
            book_id,
            manifest["extraction_run_id"],
            manifest["agentic_run_id"],
        ))
    except Exception as error:
        await domains.bd.ingestion.ai.service.mark_ingestion_ai_run_failed(
            book_id, manifest["agentic_run_id"], error,
        )
        logger.exception(f"[bd-agentic] unable to queue run for {book_id}")
        raise HTTPException(status_code = 503, detail = "Unable to queue the agentic ingestion run") from error
    infra.opentelemetry.service.tag_celery_task(task.id)
    return {
        "status": "queued",
        "book_id": book_id,
        "extraction_run_id": manifest["extraction_run_id"],
        "agentic_run_id": manifest["agentic_run_id"],
    }


@router.post(
    "/{book_id}/agentic/runs/{agentic_run_id}/resume",
    response_model = schemas.AgenticRunStart,
    status_code = 202,
)
async def resume_agentic_run(book_id: str, agentic_run_id: str) -> dict:
    """Requeue a failed or cancelled candidate, preserving its source and LangGraph thread identity."""
    try:
        manifest = await domains.bd.ingestion.ai.service.queue_ingestion_ai_resume(
            book_id, agentic_run_id,
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code = 404, detail = str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code = 409, detail = str(error)) from error

    await domains.bd.ingestion.ai.progress.service.emit(
        book_id,
        agentic_run_id,
        "run_queued",
        status = manifest["status"],
        progress = manifest["progress"],
    )
    try:
        task = domains.bd.ingestion.ai.task.run_ingestion_ai.apply_async(args = (
            book_id,
            manifest["extraction_run_id"],
            manifest["agentic_run_id"],
        ))
    except Exception as error:
        await domains.bd.ingestion.ai.service.mark_ingestion_ai_run_failed(
            book_id, agentic_run_id, error,
        )
        failed = await domains.bd.ingestion.ai.service.read_ingestion_ai_run(book_id, agentic_run_id)
        if failed is not None:
            await domains.bd.ingestion.ai.progress.service.emit(
                book_id,
                agentic_run_id,
                "run_failed",
                status = failed["status"],
                error = failed["error"],
                progress = failed["progress"],
            )
        logger.exception(f"[bd-agentic] unable to resume run {agentic_run_id}")
        raise HTTPException(status_code = 503, detail = "Unable to resume the agentic ingestion run") from error
    infra.opentelemetry.service.tag_celery_task(task.id)
    return {
        "status": "queued",
        "book_id": book_id,
        "extraction_run_id": manifest["extraction_run_id"],
        "agentic_run_id": agentic_run_id,
    }


@router.post(
    "/{book_id}/agentic/runs/{agentic_run_id}/cancel",
    response_model = schemas.AgenticRunCancelResponse,
)
async def cancel_agentic_run(book_id: str, agentic_run_id: str) -> dict:
    """Request a cooperative Agentic AI stop at the next safe page or graph-node boundary."""
    try:
        return await domains.bd.ingestion.ai.service.cancel_ingestion_ai_run(book_id, agentic_run_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code = 404, detail = str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code = 409, detail = str(error)) from error


@router.delete(
    "/{book_id}/agentic/runs/{agentic_run_id}",
    response_model = schemas.AgenticRunWipeResponse,
)
async def wipe_agentic_run(book_id: str, agentic_run_id: str) -> dict:
    """Delete one stopped/finished Agentic candidate without touching Docling extraction."""
    try:
        return await domains.bd.ingestion.ai.service.wipe_ingestion_ai_run(book_id, agentic_run_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code = 404, detail = str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code = 409, detail = str(error)) from error


@router.get(
    "/{book_id}/agentic/runs/{agentic_run_id}/llm-counters",
)
async def get_agentic_llm_counters(book_id: str, agentic_run_id: str) -> dict:
    manifest = await domains.bd.ingestion.ai.service.read_ingestion_ai_run(
        book_id, agentic_run_id,
    )
    if manifest is None:
        raise HTTPException(status_code = 404, detail = "Agentic ingestion run not found")
    return await domains.settings.llm_counter.service.read_counters("bd", agentic_run_id)


@router.get(
    "/{book_id}/agentic/runs/{agentic_run_id}",
    response_model = schemas.AgenticRunStatus,
)
async def get_agentic_run(book_id: str, agentic_run_id: str) -> dict:
    """Read queued/running/completed candidate status from its durable MinIO manifest."""
    try:
        manifest = await domains.bd.ingestion.ai.service.read_ingestion_ai_run(
            book_id, agentic_run_id,
        )
    except ValueError as error:
        raise HTTPException(status_code = 404, detail = str(error)) from error
    if manifest is None:
        raise HTTPException(status_code = 404, detail = "Agentic ingestion run not found")
    return await _agentic_status(manifest)


@router.get(
    "/{book_id}/agentic/runs/{agentic_run_id}/content-selection",
    response_model = schemas.ContentSelectionResponse,
)
async def get_agentic_content_selection(book_id: str, agentic_run_id: str) -> dict:
    manifest = await domains.bd.ingestion.ai.service.read_ingestion_ai_run(book_id, agentic_run_id)
    if manifest is None:
        raise HTTPException(status_code = 404, detail = "Agentic ingestion run not found")
    if manifest["status"] != "done":
        raise HTTPException(status_code = 409, detail = "Content selection is available after ingestion completes")
    try:
        selected = await domains.bd.ingestion.ai.service.ensure_selection(
            book_id, agentic_run_id, run_manifest = manifest,
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code = 404, detail = str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code = 409, detail = str(error)) from error
    selection, _, digest = selected
    return _content_selection_response(selection, digest)


@router.put(
    "/{book_id}/agentic/runs/{agentic_run_id}/content-selection",
    response_model = schemas.ContentSelectionResponse,
)
async def update_agentic_content_selection(
    book_id: str,
    agentic_run_id: str,
    body: schemas.ContentSelectionUpdate,
) -> dict:
    manifest = await domains.bd.ingestion.ai.service.read_ingestion_ai_run(book_id, agentic_run_id)
    if manifest is None:
        raise HTTPException(status_code = 404, detail = "Agentic ingestion run not found")
    if manifest["status"] != "done":
        raise HTTPException(status_code = 409, detail = "Content selection can be reviewed after ingestion completes")
    try:
        await domains.bd.ingestion.ai.service.ensure_selection(
            book_id, agentic_run_id, run_manifest = manifest,
        )
        selection = await domains.bd.ingestion.ai.service.apply_overrides(
            book_id,
            agentic_run_id,
            [row.model_dump() for row in body.overrides],
            expected_revision = body.expected_revision,
        )
        selected = await domains.bd.ingestion.ai.service.read_selection(book_id, agentic_run_id)
        if selected is None:
            raise RuntimeError("content selection disappeared after review")
        _, _, digest = selected
        return _content_selection_response(selection, digest)
    except FileNotFoundError as error:
        raise HTTPException(status_code = 404, detail = str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code = 409, detail = str(error)) from error


def _content_selection_response(selection, digest: str) -> dict:
    return {
        "book_id": selection.book_id,
        "extraction_run_id": selection.extraction_run_id,
        "agentic_run_id": selection.agentic_run_id,
        "classifier_version": selection.classifier_version,
        "revision": selection.revision,
        "expected_page_count": selection.expected_page_count,
        "included_page_count": sum(page.disposition == "include" for page in selection.pages),
        "excluded_page_count": sum(page.disposition == "exclude" for page in selection.pages),
        "sha256": digest,
        "pages": [page.model_dump(mode = "json") for page in selection.pages],
    }


@router.get(
    "/{book_id}/agentic/runs/{agentic_run_id}/pages/{page_no}",
    response_model = schemas.PageBody,
)
async def get_agentic_run_page(book_id: str, agentic_run_id: str, page_no: int) -> dict:
    """Read the normalized page from one exact immutable ingestion QA run."""
    try:
        manifest = await domains.bd.ingestion.ai.service.read_ingestion_ai_run(book_id, agentic_run_id)
    except ValueError as error:
        raise HTTPException(status_code = 404, detail = str(error)) from error
    if manifest is None:
        raise HTTPException(status_code = 404, detail = "Agentic ingestion run not found")
    page = next((item for item in manifest["pages"] if item["page_number"] == page_no), None)
    if page is None or page["status"] not in {"done", "review_required"}:
        raise HTTPException(status_code = 404, detail = f"no normalized page {page_no} exists in this ingestion run")
    storage = infra.minio.service.get_storage()
    try:
        markdown = await storage.read_text(page["normalized_key"])
    except FileNotFoundError as error:
        raise HTTPException(status_code = 404, detail = f"page {page_no} artifact is missing") from error
    if hashlib.sha256(markdown.encode("utf-8")).hexdigest() != page["normalized_sha256"]:
        raise HTTPException(status_code = 409, detail = f"page {page_no} failed its integrity check")
    return {
        "book_id": book_id,
        "page": page_no,
        "markdown": markdown,
        "content_source": "agentic",
    }


@router.get("/{book_id}/agentic/runs/{agentic_run_id}/events")
async def agentic_run_events(book_id: str, agentic_run_id: str) -> StreamingResponse:
    """Replay and stream graph-node and per-page progress for one candidate run."""
    manifest = await domains.bd.ingestion.ai.service.read_ingestion_ai_run(
        book_id, agentic_run_id,
    )
    if manifest is None:
        raise HTTPException(status_code = 404, detail = "Agentic ingestion run not found")
    done = object()

    async def _gen():
        yield b": stream open\n\n"
        queue: asyncio.Queue = asyncio.Queue()

        async def _pump():
            try:
                async for event in domains.bd.ingestion.ai.progress.service.subscribe(
                    book_id, agentic_run_id,
                ):
                    await queue.put(event)
            except asyncio.CancelledError:
                pass
            except Exception as error:
                logger.warning(
                    f"[bd-agentic-events] {book_id}/{agentic_run_id}: pump crashed "
                    f"({type(error).__name__}: {error})"
                )
            finally:
                await queue.put(done)

        pump_task = asyncio.create_task(_pump())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout = params._SSE_HEARTBEAT_S)
                except asyncio.TimeoutError:
                    yield b": keepalive\n\n"
                    continue
                if event is done:
                    return
                yield f"data: {json.dumps(event, default = str)}\n\n".encode("utf-8")
                if event.get("kind") in {"run_done", "run_failed", "run_cancelled"}:
                    return
        except asyncio.CancelledError:
            return
        finally:
            pump_task.cancel()
            try:
                await pump_task
            except (asyncio.CancelledError, Exception):
                pass

    return StreamingResponse(
        _gen(),
        media_type = "text/event-stream",
        headers = {
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{book_id}/pages/{page_no}", response_model = schemas.PageBody)
async def get_page(
    book_id: str,
    page_no: int,
    source: Literal["docling", "agentic"] | None = None,
) -> dict:
    """The page as the reader shows it: the AI page when the current run has one, else the raw Docling page.
    `source` pins one side, so the reader can compare the raw page with the AI-processed one."""
    markdown, content_source = await domains.bd.ingestion.ai.service.read_display_page(
        book_id, page_no, storage = infra.minio.service.get_storage(), source = source,
    )
    if markdown is None:
        raise HTTPException(
            status_code = 404,
            detail = (
                f"page {page_no} of book {book_id!r} has no AI-processed version"
                if source == "agentic"
                else f"page {page_no} of book {book_id!r} is not extracted"
            ),
        )
    return {
        "book_id": book_id,
        "page": page_no,
        "markdown": markdown,
        "content_source": content_source,
    }


@router.get("/{book_id}/pages/{page_no}/quality", response_model = schemas.PageQuality)
async def get_page_quality(book_id: str, page_no: int) -> dict:
    """What the current Agentic AI run found on this page (issues, fixes applied, what still needs a reviewer)."""
    quality = await domains.bd.ingestion.ai.service.read_page_quality(
        book_id, page_no, storage = infra.minio.service.get_storage(),
    )
    if quality is None:
        raise HTTPException(status_code = 404, detail = f"no Agentic AI result for page {page_no} of book {book_id!r}")
    return quality


@router.get("/{book_id}/figures/{name}")
async def get_figure(book_id: str, name: str) -> Response:
    png = await domains.bd.ingestion.storage.service.read_figure(infra.minio.service.get_storage(), book_id, name)
    if png is None:
        raise HTTPException(status_code = 404, detail = f"figure {name!r} of book {book_id!r} not found")
    return Response(
        content = png,
        media_type = domains.bd.ingestion.extract.params.FIGURE_CONTENT_TYPE,
        headers = {"Cache-Control": "no-cache"},
    )


@router.get("/{book_id}/events")
async def book_events(book_id: str) -> StreamingResponse:
    """Server-sent events of the book's extraction: the history first (a late subscriber replays it), then live. Kinds: started,
    page, slice_done, then one of done / failed / cancelled. A 15 s comment keeps the connection alive through the ingress."""
    done = object()

    async def _gen():
        yield b": stream open\n\n"
        queue: asyncio.Queue = asyncio.Queue()

        async def _pump():
            try:
                async for event in domains.bd.ingestion.runtime.progress.service.subscribe(book_id):
                    await queue.put(event)
            except asyncio.CancelledError:
                pass
            except Exception as e:  # noqa: BLE001 — a dead pump ends the stream, not the server
                logger.warning(f"[bd-events] {book_id}: pump crashed ({type(e).__name__}: {e})")
            finally:
                await queue.put(done)

        pump_task = asyncio.create_task(_pump())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout = params._SSE_HEARTBEAT_S)
                except asyncio.TimeoutError:
                    yield b": keepalive\n\n"
                    continue
                if event is done:
                    return
                yield f"data: {json.dumps(event, default = str)}\n\n".encode("utf-8")
        except asyncio.CancelledError:
            return
        finally:
            pump_task.cancel()
            try:
                await pump_task
            except (asyncio.CancelledError, Exception):
                pass

    return StreamingResponse(
        _gen(),
        media_type = "text/event-stream",
        headers = {"Cache-Control": "no-cache, no-transform", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.post("/{book_id}/cancel", response_model = schemas.CancelResponse)
async def cancel_book(book_id: str) -> dict:
    await domains.bd.ingestion.runtime.dispatch.service.cancel_run(book_id)
    return domains.bd.ingestion.runtime.dispatch.domain.cancel_requested(book_id)


@router.delete("/{book_id}", response_model = schemas.DeleteResponse)
async def delete_book(book_id: str) -> dict:
    return await domains.bd.ingestion.runtime.dispatch.service.wipe_book(book_id)


@router.delete("/{book_id}/extraction", response_model = schemas.ExtractionWipeResponse)
async def wipe_extraction(book_id: str) -> dict:
    """Delete extracted and derived artifacts while preserving the uploaded source book."""
    try:
        return await domains.bd.ingestion.runtime.dispatch.service.wipe_extraction(book_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code = 404, detail = str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code = 409, detail = str(error)) from error


def _pages_where(manifest: dict, keep) -> list[int]:
    return sorted(page["page_number"] for page in manifest.get("pages", []) if keep(page))


async def _agentic_status(manifest: dict) -> dict:
    progress = manifest["progress"]
    selected_page_count = manifest.get("selected_page_count")
    if type(selected_page_count) is not int or selected_page_count < 0:
        selected_page_count = 0
    excluded_page_count = manifest.get("excluded_page_count")
    if type(excluded_page_count) is not int or excluded_page_count < 0:
        excluded_page_count = 0
    selection_revision = manifest.get("content_selection_revision")
    if manifest.get("status") == "done":
        try:
            current = await domains.bd.ingestion.ai.service.read_selection(
                manifest["book_id"], manifest["agentic_run_id"],
            )
            if current is not None:
                selection, _, _ = current
                selected_page_count = sum(page.disposition == "include" for page in selection.pages)
                excluded_page_count = sum(page.disposition == "exclude" for page in selection.pages)
                selection_revision = selection.revision
        except Exception as error:  # noqa: BLE001 — keep the run-status response available during storage outages.
            logger.warning(
                "[bd-ingestion] couldn't load reviewed selection for run %s (%s)",
                manifest.get("agentic_run_id"),
                type(error).__name__,
            )
    return {
        "book_id": manifest["book_id"],
        "extraction_run_id": manifest["extraction_run_id"],
        "agentic_run_id": manifest["agentic_run_id"],
        "status": manifest["status"],
        "expected_page_count": manifest["expected_page_count"],
        "published_page_count": len(progress["processed_pages"]),
        "quality_status": manifest.get("quality_status"),
        "delivered_page_count": manifest.get("delivered_page_count", 0),
        "discarded_blank_page_count": manifest.get("discarded_blank_page_count", 0),
        "selected_page_count": selected_page_count,
        "excluded_page_count": excluded_page_count,
        "content_selection_revision": selection_revision,
        "review_page_count": manifest.get("review_page_count", 0),
        "review_pages": _pages_where(manifest, lambda page: page["status"] == "review_required"),
        "unverified_page_count": manifest.get("unverified_page_count", 0),
        "unverified_pages": manifest.get("unverified_pages", []),
        "discarded_pages": _pages_where(manifest, lambda page: page["delivery_disposition"] == "discarded_blank"),
        "image_only_pages": _pages_where(manifest, lambda page: page["delivery_disposition"] == "image_only"),
        "progress": progress,
        "created_at": manifest["created_at"],
        "updated_at": manifest["updated_at"],
        "finished_at": manifest["finished_at"],
        "error": manifest["error"],
    }


async def _queue(gate: dict, refresh: bool) -> dict:
    """Send the extraction task for a `reserved` gate; any other answer (cached, locked) is returned as it is."""
    if gate["status"] != "reserved":
        return gate
    book_id, run_id = gate["book_id"], gate["run_id"]
    try:
        task = domains.bd.ingestion.task.run_extraction.delay(book_id, run_id, refresh)
        infra.opentelemetry.service.tag_celery_task(task.id)
    except Exception:
        await domains.bd.ingestion.runtime.dispatch.service.release_run(book_id, run_id)
        raise
    return domains.bd.ingestion.runtime.dispatch.domain.queued(book_id, run_id, gate["resumed"])


async def _read_upload(file: UploadFile) -> bytes:
    """The upload's bytes, read a chunk at a time so an oversized file is refused before it is all in memory."""
    limit = domains.bd.ingestion.params.MAX_UPLOAD_BYTES
    chunks: list[bytes] = []
    size = 0
    while chunk := await file.read(params._UPLOAD_CHUNK_BYTES):
        size += len(chunk)
        if size > limit:
            raise HTTPException(status_code = 413, detail = f"a book may be at most {limit // (1024 * 1024)} MB")
        chunks.append(chunk)
    return b"".join(chunks)
