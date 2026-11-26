"""Queue Agentic Ingestion lifecycle commands through the existing Celery worker."""
from __future__ import annotations

import hashlib
import logging

import infra

from .. import progress
from .. import service as run_service

logger = logging.getLogger(__name__)


class IngestionDispatchError(RuntimeError):
    """A run was persisted but its Celery task could not be queued."""


def _mcp_run_id(book_id: str, idempotency_key: str) -> str:
    digest = hashlib.sha256(f"{book_id}\0{idempotency_key}".encode()).hexdigest()
    return f"mcp-{digest[:40]}"


async def start_run(
    book_id: str,
    *,
    idempotency_key: str | None = None,
    extraction_run_id: str | None = None,
) -> dict:
    """Create or recover an MCP-idempotent run, then dispatch its Celery task."""
    if idempotency_key is not None and (
        not idempotency_key or len(idempotency_key) > 160
    ):
        raise ValueError("idempotency_key must contain between 1 and 160 characters")
    run_id = _mcp_run_id(book_id, idempotency_key) if idempotency_key else None
    try:
        manifest = await run_service.create_ingestion_ai_run(
            book_id,
            expected_extraction_id = extraction_run_id,
            agentic_run_id = run_id,
        )
    except ValueError:
        if run_id is None:
            raise
        existing = await run_service.read_ingestion_ai_run(book_id, run_id)
        if existing is None or (
            extraction_run_id is not None
            and existing.get("extraction_run_id") != extraction_run_id
        ):
            raise
        # A repeated idempotency key returns the original run without enqueueing a
        # second Celery delivery. This also covers a client retry after a lost reply.
        return existing

    return await _dispatch(manifest, action = "start")


async def resume_run(book_id: str, agentic_run_id: str) -> dict:
    manifest = await run_service.queue_ingestion_ai_resume(book_id, agentic_run_id)
    return await _dispatch(manifest, action = "resume")


async def replay_from_node(book_id: str, agentic_run_id: str, node_id: str) -> dict:
    manifest = await run_service.queue_ingestion_ai_node_replay(
        book_id,
        agentic_run_id,
        node_id,
    )
    return await _dispatch(manifest, action = "replay")


async def _dispatch(manifest: dict, *, action: str) -> dict:
    book_id = manifest["book_id"]
    run_id = manifest["agentic_run_id"]
    status = manifest["status"]
    if status != "queued":
        return manifest

    try:
        await progress.service.emit(
            book_id,
            run_id,
            "run_queued",
            status = status,
            progress = manifest["progress"],
        )
    except Exception:
        # Event delivery is observational; it must not leave a persisted run queued
        # without its worker task when clients retry with the same idempotency key.
        logger.warning(
            "[bd-agentic] unable to emit queued event for book_id=%s run_id=%s",
            book_id,
            run_id,
            exc_info = True,
        )
    try:
        from .. import task

        celery_task = task.run_ingestion_ai.apply_async(args = (
            book_id,
            manifest["extraction_run_id"],
            run_id,
        ))
    except Exception as error:  # Persist failed dispatches for every caller.
        logger.exception(
            "[bd-agentic] unable to queue %s for book_id=%s run_id=%s",
            action,
            book_id,
            run_id,
        )
        await run_service.mark_ingestion_ai_run_failed(book_id, run_id, error)
        failed = await run_service.read_ingestion_ai_run(book_id, run_id)
        if failed is not None:
            await progress.service.emit(
                book_id,
                run_id,
                "run_failed",
                status = failed["status"],
                error = failed["error"],
                progress = failed["progress"],
            )
        raise IngestionDispatchError(
            f"Unable to queue the Agentic Ingestion {action}"
        ) from error
    infra.opentelemetry.service.tag_celery_task(celery_task.id)

    return {
        "status": "queued",
        "book_id": book_id,
        "extraction_run_id": manifest["extraction_run_id"],
        "agentic_run_id": run_id,
        "replay_from_node": manifest.get("replay_from_node"),
    }
