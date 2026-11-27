"""Dispatch Distill lifecycle commands through the existing Celery worker."""
from __future__ import annotations

import hashlib
import logging

import infra

from .. import service as run_service


logger = logging.getLogger(__name__)


class DistillDispatchError(RuntimeError):
    """A Distill run was persisted but its Celery task could not be queued."""


def _mcp_run_id(book_id: str, idempotency_key: str) -> str:
    digest = hashlib.sha256(f"{book_id}\0{idempotency_key}".encode()).hexdigest()
    return f"mcp-{digest[:40]}"


async def start_run(
    book_id: str,
    *,
    idempotency_key: str | None = None,
    ingestion_ai_run_id: str | None = None,
) -> dict:
    """Create or recover one run, then dispatch its full Distill graph."""
    if idempotency_key is not None and (
        not idempotency_key or len(idempotency_key) > 160
    ):
        raise ValueError("idempotency_key must contain between 1 and 160 characters")
    run_id = _mcp_run_id(book_id, idempotency_key) if idempotency_key else None
    try:
        manifest = await run_service.create_distill_run(
            book_id,
            expected_ingestion_ai_run_id = ingestion_ai_run_id,
            run_id = run_id,
        )
    except ValueError:
        if run_id is None:
            raise
        existing = await run_service.read_run(book_id, run_id)
        if existing is None or (
            ingestion_ai_run_id is not None
            and existing.get("ingestion_ai_run_id") != ingestion_ai_run_id
        ):
            raise
        # A retry after a lost response returns the original run without a second
        # Celery delivery. Failed runs can be resumed through the explicit command.
        return _command_result(existing)

    return await _dispatch(manifest, action = "start")


async def resume_run(book_id: str, run_id: str) -> dict:
    manifest = await run_service.queue_resume(book_id, run_id)
    return await _dispatch(manifest, action = "resume")


async def replay_from_node(book_id: str, run_id: str, node_id: str) -> dict:
    manifest = await run_service.queue_node_replay(book_id, run_id, node_id)
    return await _dispatch(manifest, action = "replay")


async def cancel_run(book_id: str, run_id: str) -> dict:
    await run_service.request_cancel(book_id, run_id)
    manifest = await run_service.read_run(book_id, run_id)
    if manifest is None:
        raise FileNotFoundError(f"Distill run {run_id} was not found")
    return _command_result(manifest)


def _command_result(manifest: dict) -> dict:
    return {
        "status": manifest["status"],
        "book_id": manifest["book_id"],
        "run_id": manifest["run_id"],
        "extraction_run_id": manifest["extraction_run_id"],
        "ingestion_ai_run_id": manifest["ingestion_ai_run_id"],
        "replay_from_node": manifest.get("replay_from_node"),
        "error": manifest.get("error"),
    }


async def _dispatch(manifest: dict, *, action: str) -> dict:
    book_id = manifest["book_id"]
    run_id = manifest["run_id"]
    if manifest["status"] != "queued":
        return _command_result(manifest)

    try:
        from .. import task

        celery_task = task.run_distill.apply_async(args = (book_id, run_id))
    except Exception as error:  # Persist failed dispatches for every caller.
        logger.exception(
            "[bd-distill] unable to queue %s for book_id=%s run_id=%s",
            action,
            book_id,
            run_id,
        )
        await run_service.mark_failed(book_id, run_id, error)
        raise DistillDispatchError(f"Unable to queue the Distill {action}") from error

    infra.opentelemetry.service.tag_celery_task(celery_task.id)
    return _command_result(manifest)
