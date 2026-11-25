"""BD Query workbench routes."""
from __future__ import annotations
from . import schemas, service

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse


router = APIRouter()


@router.get("/namespaces", response_model = schemas.NamespaceResponse)
async def get_namespaces() -> dict:
    return service.get_namespaces()


@router.post("/raw/{backend}", response_model = schemas.RawQueryResponse)
async def run_raw_query(
    backend: schemas.QueryBackend,
    payload: schemas.RawQueryRequest,
) -> dict:
    return await service.run_raw_query(backend, payload)


@router.get("/schema/{backend}", response_model = schemas.QuerySchemaResponse)
async def get_schema(
    backend: schemas.QueryBackend,
) -> dict:
    return await service.get_schema(backend)


@router.get("/history", response_model = schemas.QueryHistoryResponse)
async def list_history(
    request: Request,
    backend: schemas.QueryBackend | None = None,
    limit: int = 50,
) -> dict:
    return await service.list_history(request, backend = backend, limit = limit)


@router.post("/history")
async def save_history(request: Request, payload: schemas.SaveQueryHistoryRequest) -> dict:
    return await service.save_history(request, payload)


@router.delete("/history/{entry_id}")
async def delete_history(entry_id: int, request: Request) -> dict:
    return await service.delete_history(request, entry_id)


@router.post("/ai/{backend}")
async def generate_query(
    backend: schemas.QueryBackend,
    payload: schemas.QueryAIRequest,
) -> StreamingResponse:
    return service.generate_query(backend, payload)
