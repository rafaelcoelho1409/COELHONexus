"""The pipeline's composition root, planner side: binds the planner graph, object storage and run context to COELHO Cloud (the shared checkpointer, MinIO, the
Settings chat endpoint) and runs the graph in the worker. What a run does lives in `ai.dd.graphs.pipeline.planner.service`; `ai/` never imports `infra`, so
everything it needs from COELHO Cloud is handed to it here."""
from __future__ import annotations
import ai, domains, infra

import logging
from typing import Any


logger = logging.getLogger(__name__)


def planner_graph():
    """The planner graph over the shared checkpointer. Reading a thread's state needs nothing else: the run's dependencies are the `context` of a run."""
    return ai.dd.graphs.pipeline.planner.graph.build_planner_graph(checkpointer = infra.postgres.service.get_checkpointer())


def node_order() -> tuple[str, ...]:
    """The planner's stages in the order they run (what `/info` reports and the web's stage board is checked against)."""
    return ai.dd.graphs.pipeline.planner.graph.NODE_ORDER


def build_context() -> Any:
    """A run's context: object storage and its agents (built on first use, from the endpoint the Settings page currently holds)."""
    domains.settings.chat.service.refresh_endpoint(force = True)
    return ai.dd.graphs.pipeline.planner.context.PlannerContext(blobs = infra.minio.service.get_storage())


def run_config(thread_id: str) -> dict:
    return domains.dd.pipeline.observability.service.run_config(thread_id)


def binding() -> Any:
    """What the planner run is handed: its graph, object storage, a context per run and the thread's config."""
    return ai.dd.graphs.pipeline.runner.entities.Binding(
        graph = planner_graph(), blobs = infra.minio.service.get_storage(), context = build_context, config = run_config,
    )


async def init_and_run(coro):
    """Ensure the AsyncPostgresSaver is open in this worker's interpreter (idempotent module-scope cache), then run `coro`."""
    await infra.postgres.service.init_checkpointer()
    return await coro


def release_lock(lock_key: str, thread_id: str, label: str) -> None:
    """Best-effort sync CAD release of a run's single-flight lock from the task's finally block. Failure → stuck lock until TTL expiry; never raised."""
    try:
        with infra.redis.service.client_sync() as r:
            infra.redis.service.release_if_owner_sync(r, lock_key, thread_id)
    except Exception as error:
        logger.warning(f"[task] {label} lock release failed for {lock_key!r}: {type(error).__name__}: {error}")


def release_planner_lock(slug: str, thread_id: str) -> None:
    if slug and thread_id:
        release_lock(domains.dd.pipeline.runtime.keys.lock_key(slug), thread_id, "planner")


async def run_planner_async(thread_id: str, slug: str, mode: str = "llm") -> dict:
    """Fresh planner kickoff in the worker."""
    return await ai.dd.graphs.pipeline.planner.service.run(binding(), thread_id, slug, mode)


async def resume_planner_async(thread_id: str) -> dict:
    """Resume the planner from its last checkpoint."""
    return await ai.dd.graphs.pipeline.planner.service.resume(binding(), thread_id)
