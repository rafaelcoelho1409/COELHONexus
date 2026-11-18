"""HTTP route declarations for Book Distiller ingestion."""
from __future__ import annotations
from . import schemas, service

from typing import Literal

from fastapi import APIRouter, File, Form, Response, UploadFile
from starlette.responses import StreamingResponse


router = APIRouter()


@router.post("", response_model = schemas.StartResponse)
async def upload_book(
    file: UploadFile = File(...),
    refresh: bool = Form(False),
    auto_chain: bool = Form(False),
) -> dict:
    """Status: queued (the task is dispatched; `resumed` when it continues a stopped extraction) / cached (already extracted, no
    `refresh`) / locked (an extraction of this book is in flight). The same file is the same book."""
    return await service.upload_book(file, refresh, auto_chain)


@router.post("/{book_id}/run", response_model = schemas.StartResponse)
async def run_book(book_id: str, refresh: bool = False, auto_chain: bool = False) -> dict:
    """Start (or continue) the extraction of a stored book: the way to resume a cancelled or failed
    book, or with `refresh` to extract it again, without uploading it again. Same answers as the upload."""
    return await service.run_book(book_id, refresh, auto_chain)


@router.get("", response_model = list[schemas.BookRow])
async def list_books() -> list[dict]:
    return await service.list_books()


@router.get("/active", response_model = schemas.ActiveRuns)
async def list_active() -> dict:
    return await service.list_active()


@router.get("/agentic-graph", include_in_schema = False)
async def agentic_graph(response: Response) -> dict:
    """Return the compiled Book Distiller Ingestion graph without running its nodes."""
    return await service.agentic_graph(response)


@router.get("/{book_id}", response_model = schemas.Snapshot)
async def get_book(book_id: str) -> dict:
    """The manifest (outline, chapters, the pages extracted so far) and the run's progress."""
    return await service.get_book(book_id)


@router.post("/{book_id}/agentic/runs", response_model = schemas.AgenticRunStart, status_code = 202)
async def start_agentic_run(book_id: str) -> dict:
    """Queue one manual candidate run against the book's current completed extraction."""
    return await service.start_agentic_run(book_id)


@router.post(
    "/{book_id}/agentic/runs/{agentic_run_id}/resume",
    response_model = schemas.AgenticRunStart,
    status_code = 202,
)
async def resume_agentic_run(book_id: str, agentic_run_id: str) -> dict:
    """Requeue a failed or cancelled candidate, preserving its source and LangGraph thread identity."""
    return await service.resume_agentic_run(book_id, agentic_run_id)


@router.post(
    "/{book_id}/agentic/runs/{agentic_run_id}/replay/{node_id}",
    response_model = schemas.AgenticRunStart,
    status_code = 202,
)
async def replay_agentic_run_from_node(book_id: str, agentic_run_id: str, node_id: str) -> dict:
    """Replay one node and its descendants from the persisted pre-node LangGraph checkpoint."""
    return await service.replay_agentic_run_from_node(book_id, agentic_run_id, node_id)


@router.post(
    "/{book_id}/agentic/runs/{agentic_run_id}/cancel",
    response_model = schemas.AgenticRunCancelResponse,
)
async def cancel_agentic_run(book_id: str, agentic_run_id: str) -> dict:
    """Request a cooperative Agentic AI stop at the next safe work or LLM-call boundary."""
    return await service.cancel_agentic_run(book_id, agentic_run_id)


@router.delete(
    "/{book_id}/agentic/runs/{agentic_run_id}",
    response_model = schemas.AgenticRunWipeResponse,
)
async def wipe_agentic_run(book_id: str, agentic_run_id: str) -> dict:
    """Delete one stopped/finished Agentic candidate without touching Docling extraction."""
    return await service.wipe_agentic_run(book_id, agentic_run_id)


@router.get(
    "/{book_id}/agentic/runs/{agentic_run_id}/llm-counters",
)
async def get_agentic_llm_counters(book_id: str, agentic_run_id: str) -> dict:
    return await service.get_agentic_llm_counters(book_id, agentic_run_id)


@router.get(
    "/{book_id}/agentic/runs/{agentic_run_id}",
    response_model = schemas.AgenticRunStatus,
)
async def get_agentic_run(book_id: str, agentic_run_id: str) -> dict:
    """Read queued/running/completed candidate status from its durable MinIO manifest."""
    return await service.get_agentic_run(book_id, agentic_run_id)


@router.get(
    "/{book_id}/agentic/runs/{agentic_run_id}/retrieval-index",
    response_model = schemas.RetrievalIndexStatus,
)
async def get_retrieval_index(book_id: str, agentic_run_id: str) -> dict:
    return await service.get_retrieval_index(book_id, agentic_run_id)


@router.get(
    "/{book_id}/agentic/runs/{agentic_run_id}/retrieval-index/llm-counters",
)
async def get_retrieval_index_llm_counters(book_id: str, agentic_run_id: str) -> dict:
    return await service.get_retrieval_index_llm_counters(book_id, agentic_run_id)


@router.post(
    "/{book_id}/agentic/runs/{agentic_run_id}/retrieval-index",
    response_model = schemas.RetrievalIndexStatus,
    status_code = 202,
)
async def start_retrieval_index(book_id: str, agentic_run_id: str) -> dict:
    return await service.start_retrieval_index(book_id, agentic_run_id)


@router.post(
    "/{book_id}/agentic/runs/{agentic_run_id}/retrieval-index/resume",
    response_model = schemas.RetrievalIndexStatus,
    status_code = 202,
)
async def resume_retrieval_index(book_id: str, agentic_run_id: str) -> dict:
    return await service.resume_retrieval_index(book_id, agentic_run_id)


@router.post(
    "/{book_id}/agentic/runs/{agentic_run_id}/retrieval-index/replay/{node_id}",
    response_model = schemas.RetrievalIndexStatus,
    status_code = 202,
)
async def replay_retrieval_index_node(book_id: str, agentic_run_id: str, node_id: str) -> dict:
    return await service.replay_retrieval_index_node(book_id, agentic_run_id, node_id)


@router.post(
    "/{book_id}/agentic/runs/{agentic_run_id}/retrieval-index/cancel",
    response_model = schemas.RetrievalIndexStatus,
)
async def cancel_retrieval_index(book_id: str, agentic_run_id: str) -> dict:
    return await service.cancel_retrieval_index(book_id, agentic_run_id)


@router.delete(
    "/{book_id}/agentic/runs/{agentic_run_id}/retrieval-index",
    response_model = schemas.RetrievalIndexWipeResponse,
)
async def wipe_retrieval_index(book_id: str, agentic_run_id: str) -> dict:
    return await service.wipe_retrieval_index(book_id, agentic_run_id)


@router.get(
    "/{book_id}/agentic/runs/{agentic_run_id}/content-selection",
    response_model = schemas.ContentSelectionResponse,
)
async def get_agentic_content_selection(book_id: str, agentic_run_id: str) -> dict:
    return await service.get_agentic_content_selection(book_id, agentic_run_id)


@router.put(
    "/{book_id}/agentic/runs/{agentic_run_id}/content-selection",
    response_model = schemas.ContentSelectionResponse,
)
async def update_agentic_content_selection(
    book_id: str,
    agentic_run_id: str,
    body: schemas.ContentSelectionUpdate,
) -> dict:
    return await service.update_agentic_content_selection(book_id, agentic_run_id, body)


@router.get(
    "/{book_id}/agentic/runs/{agentic_run_id}/processed-book",
    response_model = schemas.ProcessedBookResponse,
)
async def get_processed_book(book_id: str, agentic_run_id: str) -> dict:
    """Read selected pages with compact numbering and the canonical contents tree."""
    return await service.get_processed_book(book_id, agentic_run_id)


@router.get(
    "/{book_id}/agentic/runs/{agentic_run_id}/pages/{page_no}",
    response_model = schemas.PageBody,
)
async def get_agentic_run_page(book_id: str, agentic_run_id: str, page_no: int) -> dict:
    """Read the final processed page from one exact immutable ingestion run."""
    return await service.get_agentic_run_page(book_id, agentic_run_id, page_no)


@router.get("/{book_id}/agentic/runs/{agentic_run_id}/events")
async def agentic_run_events(book_id: str, agentic_run_id: str) -> StreamingResponse:
    """Replay and stream graph-node and per-page progress for one candidate run."""
    return await service.agentic_run_events(book_id, agentic_run_id)


@router.get("/{book_id}/pages/{page_no}", response_model = schemas.PageBody)
async def get_page(
    book_id: str,
    page_no: int,
    source: Literal["docling", "agentic"] | None = None,
) -> dict:
    """The page as the reader shows it: the AI page when the current run has one, else the raw Docling page.
    `source` pins one side, so the reader can compare the raw page with the AI-processed one."""
    return await service.get_page(book_id, page_no, source)


@router.get("/{book_id}/pages/{page_no}/quality", response_model = schemas.PageQuality)
async def get_page_quality(book_id: str, page_no: int) -> dict:
    """What the current Agentic AI run found on this page (issues, fixes applied, what still needs a reviewer)."""
    return await service.get_page_quality(book_id, page_no)


@router.get("/{book_id}/figures/{name}")
async def get_figure(book_id: str, name: str) -> Response:
    return await service.get_figure(book_id, name)


@router.get("/{book_id}/events")
async def book_events(book_id: str) -> StreamingResponse:
    """Server-sent events of the book's extraction: the history first (a late subscriber replays it), then live. Kinds: started,
    page, slice_done, then one of done / failed / cancelled. A 15 s comment keeps the connection alive through the ingress."""
    return await service.book_events(book_id)


@router.post("/{book_id}/cancel", response_model = schemas.CancelResponse)
async def cancel_book(book_id: str) -> dict:
    return await service.cancel_book(book_id)


@router.delete("/{book_id}", response_model = schemas.DeleteResponse)
async def delete_book(book_id: str) -> dict:
    return await service.delete_book(book_id)


@router.delete("/{book_id}/extraction", response_model = schemas.ExtractionWipeResponse)
async def wipe_extraction(book_id: str) -> dict:
    """Delete extracted and derived artifacts while preserving the uploaded source book."""
    return await service.wipe_extraction(book_id)
