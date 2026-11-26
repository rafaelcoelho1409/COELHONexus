"""Domain orchestration for Agentic RAG indexing tools."""
from __future__ import annotations
import domains
from . import domain


async def read_run(book_id: str, agentic_run_id: str) -> dict:
    manifest = await domains.bd.rag.ai.runs.service.read_index(book_id, agentic_run_id)
    if manifest is None:
        raise FileNotFoundError("Agentic RAG index run not found")
    return domain.index_summary(manifest).model_dump(mode = "json")


async def read_current_index(book_id: str) -> dict:
    current = await domains.bd.rag.ai.runs.service.read_current_index(book_id)
    if current is None:
        raise FileNotFoundError("The book has no published Agentic RAG index")
    return domain.current_index_summary(current).model_dump(mode = "json")


async def start_index(book_id: str, agentic_run_id: str) -> dict:
    result = await domains.bd.rag.ai.runs.service.queue_index(book_id, agentic_run_id)
    return domain.command_result(result).model_dump(mode = "json")


async def resume_index(book_id: str, agentic_run_id: str) -> dict:
    result = await domains.bd.rag.ai.runs.service.resume_index(book_id, agentic_run_id)
    return domain.command_result(result).model_dump(mode = "json")


async def cancel_index(book_id: str, agentic_run_id: str) -> dict:
    result = await domains.bd.rag.ai.runs.service.request_cancel(book_id, agentic_run_id)
    return domain.command_result(result).model_dump(mode = "json")


async def replay_from_node(
    book_id: str,
    agentic_run_id: str,
    node_id: str,
) -> dict:
    result = await domains.bd.rag.ai.replay.service.replay_index_node(
        book_id,
        agentic_run_id,
        node_id,
    )
    return domain.command_result(result).model_dump(mode = "json")
