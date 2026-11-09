"""Start, monitor, stop, and wipe a book's retrieval materialization run."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, status

import domains

from . import schemas


router = APIRouter()
SafePathSegment = Annotated[str, Path(pattern = r"^[A-Za-z0-9_-]+$")]


@router.get("/capabilities", response_model = schemas.RagCapabilities)
async def get_capabilities() -> dict[str, bool]:
    return {"execution_enabled": domains.bd.rag.params.RAG_EXECUTION_ENABLED}


@router.get(
    "/{book_id}/runs/{run_id}/index",
    response_model = schemas.RagIndexStatus,
)
async def get_index(book_id: SafePathSegment, run_id: SafePathSegment) -> dict:
    run = await domains.bd.distill.service.read_run(book_id, run_id)
    if run is None:
        raise HTTPException(status_code = 404, detail = "Distill run not found")
    index = await domains.bd.rag.service.read_index(book_id, run_id)
    if index is not None:
        return index
    return {
        "book_id": book_id,
        "distill_run_id": run_id,
        "extraction_run_id": run["extraction_run_id"],
        "status": "not_started",
    }


@router.post(
    "/{book_id}/runs/{run_id}/index",
    response_model = schemas.RagIndexStatus,
    status_code = status.HTTP_202_ACCEPTED,
)
async def start_index(book_id: SafePathSegment, run_id: SafePathSegment) -> dict:
    try:
        return await domains.bd.rag.service.queue_index(book_id, run_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code = 404, detail = str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code = 409, detail = str(error)) from error


@router.post(
    "/{book_id}/runs/{run_id}/index/cancel",
    response_model = schemas.RagIndexStatus,
    status_code = status.HTTP_202_ACCEPTED,
)
async def cancel_index(book_id: SafePathSegment, run_id: SafePathSegment) -> dict:
    try:
        return await domains.bd.rag.service.request_cancel(book_id, run_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code = 404, detail = str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code = 409, detail = str(error)) from error


@router.delete(
    "/{book_id}/runs/{run_id}/index",
    response_model = schemas.RagWipeResponse,
)
async def wipe_index(book_id: SafePathSegment, run_id: SafePathSegment) -> dict:
    try:
        return await domains.bd.rag.service.wipe_index(book_id, run_id)
    except FileNotFoundError as error:
        raise HTTPException(status_code = 404, detail = str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code = 409, detail = str(error)) from error
