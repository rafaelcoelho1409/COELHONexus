"""Authorization and domain orchestration for BD Agentic Ingestion MCP tools."""
from __future__ import annotations

import domains

from ... import access
from . import domain, params


async def read_run(book_id: str, agentic_run_id: str, trusted_in_process: bool) -> dict:
    await access.service.authorize_book(
        book_id,
        trusted_in_process,
        required_scope = params.READ_SCOPE,
    )
    manifest = await domains.bd.ingestion.ai.service.read_ingestion_ai_run(book_id, agentic_run_id)
    if manifest is None:
        raise FileNotFoundError("Agentic Ingestion run not found")
    return domain.run_summary(manifest).model_dump(mode = "json")


async def read_quality(book_id: str, agentic_run_id: str, trusted_in_process: bool) -> dict:
    await access.service.authorize_book(
        book_id,
        trusted_in_process,
        required_scope = params.READ_SCOPE,
    )
    manifest = await domains.bd.ingestion.ai.service.read_ingestion_ai_run(book_id, agentic_run_id)
    if manifest is None:
        raise FileNotFoundError("Agentic Ingestion run not found")
    return domain.quality_summary(manifest).model_dump(mode = "json")


async def start_run(
    book_id: str,
    extraction_run_id: str | None,
    idempotency_key: str,
    trusted_in_process: bool,
) -> dict:
    await access.service.authorize_book(
        book_id,
        trusted_in_process,
        required_scope = params.WRITE_SCOPE,
        mutation = True,
    )
    result = await domains.bd.ingestion.ai.dispatch.service.start_run(
        book_id,
        idempotency_key = idempotency_key,
        extraction_run_id = extraction_run_id,
    )
    return domain.command_result(result).model_dump(mode = "json")


async def resume_run(book_id: str, agentic_run_id: str, trusted_in_process: bool) -> dict:
    await access.service.authorize_book(
        book_id,
        trusted_in_process,
        required_scope = params.WRITE_SCOPE,
        mutation = True,
    )
    result = await domains.bd.ingestion.ai.dispatch.service.resume_run(book_id, agentic_run_id)
    return domain.command_result(result).model_dump(mode = "json")


async def cancel_run(book_id: str, agentic_run_id: str, trusted_in_process: bool) -> dict:
    await access.service.authorize_book(
        book_id,
        trusted_in_process,
        required_scope = params.WRITE_SCOPE,
        mutation = True,
    )
    manifest = await domains.bd.ingestion.ai.service.read_ingestion_ai_run(book_id, agentic_run_id)
    if manifest is None:
        raise FileNotFoundError("Agentic Ingestion run not found")
    result = await domains.bd.ingestion.ai.service.cancel_ingestion_ai_run(book_id, agentic_run_id)
    return domain.command_result({
        **result,
        "extraction_run_id": manifest["extraction_run_id"],
    }).model_dump(mode = "json")


async def replay_from_node(
    book_id: str,
    agentic_run_id: str,
    node_id: str,
    trusted_in_process: bool,
) -> dict:
    await access.service.authorize_book(
        book_id,
        trusted_in_process,
        required_scope = params.REPLAY_SCOPE,
        mutation = True,
    )
    result = await domains.bd.ingestion.ai.dispatch.service.replay_from_node(
        book_id,
        agentic_run_id,
        node_id,
    )
    return domain.command_result(result).model_dump(mode = "json")
