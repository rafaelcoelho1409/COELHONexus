"""Start and read source-grounded Learning Guide runs."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Response
from starlette.responses import StreamingResponse

import domains
import domains.bd.distill.params
import domains.bd.distill.task
import infra

from . import schemas

logger = logging.getLogger(__name__)
router = APIRouter()
SafePathSegment = Annotated[str, Path(pattern = r"^[A-Za-z0-9_-]+$")]


@router.get("/capabilities", response_model = schemas.Capabilities)
async def get_capabilities() -> dict[str, bool]:
    return {"execution_enabled": domains.bd.distill.params.DISTILL_EXECUTION_ENABLED}


@router.post("/{book_id}/runs", response_model = schemas.RunStart, status_code = 202)
async def start_run(book_id: SafePathSegment, body: schemas.StartRequest | None = None) -> dict:
    _require_execution_enabled()
    try:
        manifest = await domains.bd.distill.service.create_distill_run(
            book_id,
            expected_ingestion_ai_run_id = body.ingestion_ai_run_id if body is not None else None,
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code = 404, detail = str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code = 409, detail = str(error)) from error
    try:
        task = domains.bd.distill.task.run_distill.apply_async(args = (book_id, manifest["run_id"]))
    except Exception as error:
        await domains.bd.distill.service.mark_failed(book_id, manifest["run_id"], error)
        logger.exception("[bd-distill-api] unable to queue run for %s", book_id)
        raise HTTPException(status_code = 503, detail = "Unable to queue the Distill run") from error
    infra.opentelemetry.service.tag_celery_task(task.id)
    return {
        "status": "queued",
        "book_id": book_id,
        "extraction_run_id": manifest["extraction_run_id"],
        "ingestion_ai_run_id": manifest["ingestion_ai_run_id"],
        "run_id": manifest["run_id"],
    }


@router.get("/{book_id}/runs")
async def list_runs(book_id: SafePathSegment) -> list[dict]:
    return await domains.bd.distill.service.list_runs(book_id)


@router.get("/{book_id}/runs/current")
async def get_current_run(book_id: SafePathSegment) -> dict:
    manifest = await domains.bd.distill.service.read_current_run(book_id)
    if manifest is None:
        raise HTTPException(status_code = 404, detail = f"no Distill run exists for book {book_id!r}")
    return manifest


@router.get("/{book_id}/runs/{run_id}")
async def get_run(book_id: SafePathSegment, run_id: SafePathSegment) -> dict:
    manifest = await domains.bd.distill.service.read_run(book_id, run_id)
    if manifest is None:
        raise HTTPException(status_code = 404, detail = "Distill run not found")
    return manifest


@router.delete("/{book_id}/runs/{run_id}", response_model = schemas.WipeResponse)
async def wipe_run(book_id: SafePathSegment, run_id: SafePathSegment) -> dict:
    """Delete one terminal Distill run and its artifacts while preserving the book and ingestion results."""
    try:
        return await domains.bd.distill.service.wipe_run(book_id, run_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code = 404, detail = str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code = 409, detail = str(error)) from error


@router.post("/{book_id}/runs/{run_id}/resume", response_model = schemas.RunStart, status_code = 202)
async def resume_run(book_id: SafePathSegment, run_id: SafePathSegment) -> dict:
    _require_execution_enabled()
    try:
        manifest = await domains.bd.distill.service.queue_resume(book_id, run_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code = 404, detail = str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code = 409, detail = str(error)) from error
    try:
        task = domains.bd.distill.task.run_distill.apply_async(args = (book_id, run_id))
    except Exception as error:
        await domains.bd.distill.service.mark_failed(book_id, run_id, error)
        logger.exception("[bd-distill-api] unable to resume run %s", run_id)
        raise HTTPException(status_code = 503, detail = "Unable to resume the Distill run") from error
    infra.opentelemetry.service.tag_celery_task(task.id)
    return {
        "status": "queued",
        "book_id": book_id,
        "extraction_run_id": manifest["extraction_run_id"],
        "ingestion_ai_run_id": manifest["ingestion_ai_run_id"],
        "run_id": run_id,
    }


@router.post("/{book_id}/runs/{run_id}/cancel", response_model = schemas.CancelResponse)
async def cancel_run(book_id: SafePathSegment, run_id: SafePathSegment) -> dict:
    try:
        return await domains.bd.distill.service.request_cancel(book_id, run_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code = 404, detail = str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code = 409, detail = str(error)) from error


@router.get("/{book_id}/runs/{run_id}/report", response_model = domains.bd.distill.schemas.StudyGuideDocument)
async def get_report(book_id: SafePathSegment, run_id: SafePathSegment) -> dict:
    try:
        report, _ = await domains.bd.distill.service.read_report(book_id, run_id)
        return report
    except FileNotFoundError as error:
        raise HTTPException(status_code = 404, detail = str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code = 409, detail = str(error)) from error


@router.get("/{book_id}/runs/{run_id}/rag-index", response_model = schemas.RagIndexStatus)
async def get_rag_index_status(book_id: SafePathSegment, run_id: SafePathSegment) -> dict:
    run = await domains.bd.distill.service.read_run(book_id, run_id)
    if run is None:
        raise HTTPException(status_code = 404, detail = "Distill run not found")
    index = await domains.bd.distill.rag.service.read_index(book_id, run_id)
    if index is not None:
        return index
    return {
        "book_id": book_id,
        "distill_run_id": run_id,
        "extraction_run_id": run["extraction_run_id"],
        "status": "not_started",
    }


@router.post(
    "/{book_id}/runs/{run_id}/rag-index",
    response_model = schemas.RagIndexStatus,
    status_code = 202,
)
async def retry_rag_index(book_id: SafePathSegment, run_id: SafePathSegment) -> dict:
    try:
        return await domains.bd.distill.rag.service.queue_index(book_id, run_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code = 404, detail = str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code = 409, detail = str(error)) from error


@router.get("/{book_id}/runs/{run_id}/report.html")
async def get_report_html(book_id: SafePathSegment, run_id: SafePathSegment) -> Response:
    try:
        content = await domains.bd.distill.service.read_html(book_id, run_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code = 404, detail = str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code = 409, detail = str(error)) from error
    return Response(
        content = content,
        media_type = domains.bd.distill.params.HTML_TYPE,
        headers = {
            "Content-Disposition": "inline; filename=learning-guide.html",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/{book_id}/runs/{run_id}/report.pdf")
async def get_report_pdf(book_id: SafePathSegment, run_id: SafePathSegment) -> Response:
    try:
        content = await domains.bd.distill.service.read_pdf(book_id, run_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code = 404, detail = str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code = 409, detail = str(error)) from error
    except RuntimeError as error:
        logger.warning("[bd-distill-api] PDF renderer unavailable for %s/%s: %s", book_id, run_id, error)
        raise HTTPException(status_code = 503, detail = "The PDF renderer is unavailable") from error
    return Response(
        content = content,
        media_type = domains.bd.distill.params.PDF_TYPE,
        headers = {
            "Content-Disposition": f'attachment; filename="learning-guide-{book_id}.pdf"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/{book_id}/runs/{run_id}/events")
async def run_events(book_id: SafePathSegment, run_id: SafePathSegment) -> StreamingResponse:
    manifest = await domains.bd.distill.service.read_run(book_id, run_id)
    if manifest is None:
        raise HTTPException(status_code = 404, detail = "Distill run not found")
    done = object()

    async def _gen():
        yield b": stream open\n\n"
        queue: asyncio.Queue = asyncio.Queue()

        async def _pump() -> None:
            try:
                async for event in domains.bd.distill.progress.service.subscribe(book_id, run_id):
                    await queue.put(event)
                    if event.get("kind") in {"run_done", "run_failed", "run_cancelled"}:
                        break
            except asyncio.CancelledError:
                return
            except Exception as error:  # noqa: BLE001 — a failed progress stream must not affect the Celery run.
                logger.warning("[bd-distill-events] %s/%s: %s", book_id, run_id, type(error).__name__)
            finally:
                await queue.put(done)

        pump_task = asyncio.create_task(_pump())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout = 15.0)
                except TimeoutError:
                    yield b": keepalive\n\n"
                    continue
                if event is done:
                    return
                yield f"data: {json.dumps(event, default = str)}\n\n".encode()
        except asyncio.CancelledError:
            return
        finally:
            pump_task.cancel()
            try:
                await pump_task
            except asyncio.CancelledError:
                pass

    return StreamingResponse(
        _gen(),
        media_type = "text/event-stream",
        headers = {"Cache-Control": "no-cache, no-transform", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


def _require_execution_enabled() -> None:
    if not domains.bd.distill.params.DISTILL_EXECUTION_ENABLED:
        raise HTTPException(status_code = 503, detail = "Distill execution is disabled")
