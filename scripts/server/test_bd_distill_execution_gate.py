"""Verify Distill cannot queue or execute while its server flag is disabled.

    cd apps/server && .venv/bin/python ../../scripts/server/test_bd_distill_execution_gate.py
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace

for name, value in {
    "REDIS_HOST": "localhost",
    "REDIS_PORT": "6379",
    "REDIS_PASSWORD": "x",
    "ENVIRONMENT": "local",
}.items():
    os.environ.setdefault(name, value)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

import domains  # noqa: E402
from fastapi import HTTPException  # noqa: E402

distill_router = importlib.import_module("entrypoints.api.v1.bd.distill.router")


def check(label: str, got, want) -> None:
    assert got == want, f"{label}: expected {want!r}, got {got!r}"
    print(f"ok  {label}")


async def verify_gate() -> None:
    params = domains.bd.distill.params
    service = domains.bd.distill.service
    task = domains.bd.distill.task.run_distill
    old_flag = params.DISTILL_EXECUTION_ENABLED
    old_create = service.create_distill_run
    old_resume = service.queue_resume
    old_apply_async = task.apply_async
    calls = {"create": 0, "resume": 0, "queue": 0}

    async def create(*args, **kwargs):
        calls["create"] += 1
        return {"run_id": "run1", "extraction_run_id": "extract1", "ingestion_ai_run_id": "qa1"}

    async def resume(*args, **kwargs):
        calls["resume"] += 1
        return {"run_id": "run1", "extraction_run_id": "extract1", "ingestion_ai_run_id": "qa1"}

    def queue(*args, **kwargs):
        calls["queue"] += 1
        return SimpleNamespace(id = "test-task")

    params.DISTILL_EXECUTION_ENABLED = False
    service.create_distill_run = create
    service.queue_resume = resume
    task.apply_async = queue
    try:
        for label, request in (
            ("start", distill_router.start_run("book1")),
            ("resume", distill_router.resume_run("book1", "run1")),
        ):
            try:
                await request
            except HTTPException as error:
                check(f"{label} endpoint returns service unavailable while disabled", error.status_code, 503)
            else:
                raise AssertionError(f"{label} endpoint unexpectedly accepted execution")

        check(
            "disabled endpoints do not create, resume, or queue runs",
            calls,
            {"create": 0, "resume": 0, "queue": 0},
        )

        try:
            await service.run_distill("book1", "run1", checkpointer = None)
        except RuntimeError as error:
            check("worker refuses execution while disabled", str(error), "Distill execution is disabled")
        else:
            raise AssertionError("worker unexpectedly accepted execution")
    finally:
        params.DISTILL_EXECUTION_ENABLED = old_flag
        service.create_distill_run = old_create
        service.queue_resume = old_resume
        task.apply_async = old_apply_async


asyncio.run(verify_gate())
