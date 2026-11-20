"""YCS Query workbench routes."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from . import schemas, service

router = APIRouter()


@router.get("/namespaces", response_model = schemas.NamespaceMap)
async def get_namespaces() -> dict:
    return service.get_namespaces()


@router.post("/elasticsearch", response_model = schemas.QueryResponse)
async def run_elasticsearch_query(payload: schemas.QueryRequest, request: Request) -> dict:
    return await service.run_simple_query("elasticsearch", payload, request)


@router.post("/qdrant", response_model = schemas.QueryResponse)
async def run_qdrant_query(payload: schemas.QueryRequest, request: Request) -> dict:
    return await service.run_simple_query("qdrant", payload, request)


@router.post("/neo4j", response_model = schemas.QueryResponse)
async def run_neo4j_query(payload: schemas.QueryRequest, request: Request) -> dict:
    return await service.run_simple_query("neo4j", payload, request)


@router.post("/raw/elasticsearch", response_model = schemas.RawQueryResponse)
async def run_raw_elasticsearch_query(payload: schemas.RawQueryRequest, request: Request) -> dict:
    return await service.run_raw_query("elasticsearch", payload, request)


@router.post("/raw/qdrant", response_model = schemas.RawQueryResponse)
async def run_raw_qdrant_query(payload: schemas.RawQueryRequest, request: Request) -> dict:
    return await service.run_raw_query("qdrant", payload, request)


@router.post("/raw/neo4j", response_model = schemas.RawQueryResponse)
async def run_raw_neo4j_query(payload: schemas.RawQueryRequest, request: Request) -> dict:
    return await service.run_raw_query("neo4j", payload, request)


@router.get("/schema/{backend}", response_model = schemas.QuerySchemaResponse)
async def get_schema(backend: schemas.BackendLiteral, request: Request, refresh: bool = False) -> dict:
    return await service.get_schema(backend, request, refresh = refresh)


@router.post("/ai/{backend}")
async def generate_query(
    backend: schemas.BackendLiteral,
    payload: schemas.AIGenerateRequest,
    request: Request,
) -> StreamingResponse:
    return service.generate_query(backend, payload, request)


@router.get("/history", response_model = schemas.HistoryResponse)
async def list_history(
    request: Request,
    backend: schemas.BackendLiteral | None = None,
    app: schemas.AppLiteral | None = None,
    limit: int = 50,
) -> dict:
    return await service.list_history(request, app = app, backend = backend, limit = limit)


@router.post("/history")
async def save_history(request: Request, payload: schemas.SaveHistoryRequest) -> dict:
    return await service.save_history(request, payload)


@router.delete("/history/{entry_id}")
async def delete_history(entry_id: int, request: Request) -> dict:
    return await service.delete_history(request, entry_id)
