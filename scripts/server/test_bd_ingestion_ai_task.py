"""Offline checks for the agentic Celery task's checkpointer lifecycle.

    cd apps/server && .venv/bin/python ../../scripts/server/test_bd_ingestion_ai_task.py
"""
import asyncio
import importlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("REDIS_HOST", "localhost")
os.environ.setdefault("ENVIRONMENT", "test")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

import domains.bd.ingestion.ai.task as task
import domains.bd.ingestion.ai.service as ai_service
import infra.postgres.service as postgres
import infra.celery.keys as celery_keys

router = importlib.import_module("entrypoints.api.v1.bd.ingestion.router")


checkpointer = object()
calls: list[object] = []


async def init_checkpointer():
    calls.append("init")


def get_checkpointer():
    calls.append("get")
    return checkpointer


async def close_checkpointer():
    calls.append("close")


async def run_ingestion_ai(
    book_id,
    *,
    expected_extraction_id,
    agentic_run_id,
    checkpointer: object,
    resume: bool,
):
    calls.append((book_id, expected_extraction_id, agentic_run_id, checkpointer, resume))
    return {"status": "complete", "agentic_run_id": agentic_run_id}


original_init = postgres.init_checkpointer
original_get = postgres.get_checkpointer
original_close = postgres.close_checkpointer
original_run = ai_service.run_ingestion_ai
postgres.init_checkpointer = init_checkpointer
postgres.get_checkpointer = get_checkpointer
postgres.close_checkpointer = close_checkpointer
ai_service.run_ingestion_ai = run_ingestion_ai
try:
    result = task.run_ingestion_ai.run("book-test", "extract-test", "agentic-test")
finally:
    postgres.init_checkpointer = original_init
    postgres.get_checkpointer = original_get
    postgres.close_checkpointer = original_close
    ai_service.run_ingestion_ai = original_run

assert result == {
    "book_id": "book-test",
    "extraction_run_id": "extract-test",
    "agentic_run_id": "agentic-test",
    "status": "complete",
    "quality_report": None,
}
assert task.run_ingestion_ai.acks_late is True
assert task.run_ingestion_ai.reject_on_worker_lost is True
assert "domains.bd.ingestion.ai.task" in celery_keys.TASK_INCLUDE
assert celery_keys.TASK_ROUTES["domains.bd.ingestion.ai.task.*"]["queue"] == celery_keys.Q_BD
assert calls == [
    "init",
    "get",
    ("book-test", "extract-test", "agentic-test", checkpointer, True),
    "close",
]
print("ok  Celery task initializes, passes, and closes the Postgres checkpointer")


manifest = {
    "book_id": "book-test",
    "agentic_run_id": "agentic-test",
    "extraction_run_id": "extract-test",
    "status": "queued",
    "expected_page_count": 3,
    "pages": [],
    "created_at": 1.0,
    "updated_at": 1.0,
    "finished_at": None,
    "error": None,
}
dispatches: list[tuple] = []
original_create = ai_service.create_ingestion_ai_run
original_read = ai_service.read_ingestion_ai_run
original_resume = ai_service.queue_ingestion_ai_resume
original_apply_async = task.run_ingestion_ai.apply_async
original_tag = router.infra.opentelemetry.service.tag_celery_task


async def create_run(book_id):
    assert book_id == manifest["book_id"]
    return manifest


async def read_run(book_id, agentic_run_id):
    assert (book_id, agentic_run_id) == (manifest["book_id"], manifest["agentic_run_id"])
    return manifest


async def resume_run(book_id, agentic_run_id):
    return await read_run(book_id, agentic_run_id)


def apply_async(*, args):
    dispatches.append(tuple(args))
    return SimpleNamespace(id = "celery-task-test")


ai_service.create_ingestion_ai_run = create_run
ai_service.read_ingestion_ai_run = read_run
ai_service.queue_ingestion_ai_resume = resume_run
task.run_ingestion_ai.apply_async = apply_async
router.infra.opentelemetry.service.tag_celery_task = lambda task_id: None
try:
    start_result = asyncio.run(router.start_agentic_run("book-test"))
    status_result = asyncio.run(router.get_agentic_run("book-test", "agentic-test"))
    resume_result = asyncio.run(router.resume_agentic_run("book-test", "agentic-test"))
finally:
    ai_service.create_ingestion_ai_run = original_create
    ai_service.read_ingestion_ai_run = original_read
    ai_service.queue_ingestion_ai_resume = original_resume
    task.run_ingestion_ai.apply_async = original_apply_async
    router.infra.opentelemetry.service.tag_celery_task = original_tag

assert start_result["status"] == "queued"
assert status_result["status"] == "queued" and status_result["expected_page_count"] == 3
assert resume_result["agentic_run_id"] == "agentic-test"
assert dispatches == [
    ("book-test", "extract-test", "agentic-test"),
    ("book-test", "extract-test", "agentic-test"),
]
print("ok  manual start, durable status, and failed-run resume routes dispatch separately")
