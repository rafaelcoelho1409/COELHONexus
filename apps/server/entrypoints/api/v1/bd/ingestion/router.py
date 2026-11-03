"""Book ingestion over HTTP: upload a PDF or EPUB, follow extraction by reader unit (SSE), and read the extracted content. The extraction itself runs in the
`bd` Celery queue; this router is HTTP only (see `domains.bd.ingestion.runtime.dispatch`)."""
import domains, infra
import domains.bd.ingestion.task
from . import params, schemas

import asyncio
import json
import logging

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile
from starlette.responses import StreamingResponse


logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("", response_model = schemas.StartResponse)
async def upload_book(file: UploadFile = File(...), refresh: bool = Form(False)) -> dict:
    """Status: queued (the task is dispatched; `resumed` when it continues a stopped extraction) / cached (already extracted, no
    `refresh`) / locked (an extraction of this book is in flight). The same file is the same book."""
    data = await _read_upload(file)
    try:
        gate = await domains.bd.ingestion.runtime.dispatch.service.accept_upload(file.filename or "", data, refresh)
    except domains.bd.ingestion.errors.UnreadableBook as e:
        raise HTTPException(status_code = 400, detail = str(e))
    return await _queue(gate, refresh)


@router.post("/{book_id}/run", response_model = schemas.StartResponse)
async def run_book(book_id: str, refresh: bool = False) -> dict:
    """Start (or continue) the extraction of a stored book: the way to resume a cancelled or failed
    book, or with `refresh` to extract it again, without uploading it again. Same answers as the upload."""
    gate = await domains.bd.ingestion.runtime.dispatch.service.accept_restart(book_id, refresh)
    if gate is None:
        raise HTTPException(status_code = 404, detail = f"book {book_id!r} not found")
    return await _queue(gate, refresh)


@router.get("", response_model = list[schemas.BookRow])
async def list_books() -> list[dict]:
    return await domains.bd.ingestion.storage.service.list_library(infra.minio.service.get_storage())


@router.get("/active", response_model = schemas.ActiveRuns)
async def list_active() -> dict:
    return {"active": await domains.bd.ingestion.runtime.dispatch.service.read_active()}


@router.get("/{book_id}", response_model = schemas.Snapshot)
async def get_book(book_id: str) -> dict:
    """The manifest (outline, chapters, the pages extracted so far) and the run's progress."""
    snapshot = await domains.bd.ingestion.runtime.dispatch.service.read_snapshot(book_id)
    if snapshot is None:
        raise HTTPException(status_code = 404, detail = f"book {book_id!r} not found")
    return snapshot


@router.get("/{book_id}/pages/{page_no}", response_model = schemas.PageBody)
async def get_page(book_id: str, page_no: int) -> dict:
    markdown = await domains.bd.ingestion.storage.service.read_page(infra.minio.service.get_storage(), book_id, page_no)
    if markdown is None:
        raise HTTPException(status_code = 404, detail = f"page {page_no} of book {book_id!r} is not extracted")
    return {"book_id": book_id, "page": page_no, "markdown": markdown}


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
