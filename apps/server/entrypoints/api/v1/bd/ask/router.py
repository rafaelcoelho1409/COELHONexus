"""Book-scoped Ask API routes."""
from __future__ import annotations
from . import schemas, service

from typing import Annotated

from fastapi import APIRouter, Path, Query
from fastapi.responses import StreamingResponse


router = APIRouter()
SafeThreadId = Annotated[str, Path(pattern = r"^[A-Za-z0-9_-]{1,128}$")]


@router.get("/threads")
async def get_threads(
    book_id: str = Query(min_length = 1, max_length = 128, pattern = r"^[A-Za-z0-9_-]+$"),
) -> dict:
    return await service.get_threads(book_id)


@router.get("/history/{thread_id}")
async def get_history(thread_id: SafeThreadId) -> dict:
    return await service.get_history(thread_id)


@router.get("/usage/{thread_id}")
async def get_usage(thread_id: SafeThreadId) -> dict:
    return await service.get_usage(thread_id)


@router.post("/threads/{thread_id}/branch")
async def branch_thread(thread_id: SafeThreadId, payload: schemas.BranchRequest) -> dict:
    return await service.branch_thread(thread_id, payload)


@router.delete("/threads/{thread_id}")
async def delete_thread(thread_id: SafeThreadId) -> dict:
    return await service.delete_thread(thread_id)


@router.post("/turns/{turn_id}/cancel")
async def cancel_turn(turn_id: int) -> dict:
    return await service.cancel_turn(turn_id)


@router.post("/search/stream")
async def ask_stream(payload: schemas.AskRequest) -> StreamingResponse:
    return await service.ask_stream(payload)
