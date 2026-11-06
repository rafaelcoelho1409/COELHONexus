"""The pipeline's composition root, synth side: binds the chapter graph, object storage and run context to COELHO Cloud (the shared checkpointer, MinIO, the
Settings chat endpoint) and runs a chapter or a study in the worker. What a run does lives in `ai.dd.graphs.pipeline.synth.{service,study}`; `ai/` never imports
`infra`, so everything it needs from COELHO Cloud is handed to it here."""
from __future__ import annotations
import ai, domains, infra

from typing import Any


def synth_graph():
    """The chapter graph over the shared checkpointer. Reading a thread's state needs nothing else: the run's dependencies are the `context` of a run."""
    return ai.dd.graphs.pipeline.synth.graph.build_synth_graph(checkpointer = infra.postgres.service.get_checkpointer())


def node_order() -> tuple[str, ...]:
    """The chapter graph's nodes in the order they run (what `/info` reports and the web's stage board is checked against)."""
    return ai.dd.graphs.pipeline.synth.graph.NODE_ORDER


def build_context() -> Any:
    """A run's context: object storage and its agents (built when a node asks, from the endpoint the Settings page currently holds)."""
    domains.settings.chat.service.refresh_endpoint(force = True)
    return ai.dd.graphs.pipeline.synth.context.SynthContext(blobs = infra.minio.service.get_storage())


def binding() -> Any:
    """What a synth run is handed: the chapter graph, object storage, a context per run and the thread's config."""
    return ai.dd.graphs.pipeline.runner.entities.Binding(
        graph = synth_graph(), blobs = infra.minio.service.get_storage(), context = build_context, config = domains.dd.pipeline.ai.service.run_config,
    )


def release_synth_lock(slug: str, thread_id: str) -> None:
    """Best-effort release of the slug's synth lock from the task's finally block."""
    if slug and thread_id:
        domains.dd.pipeline.ai.service.release_lock(domains.dd.pipeline.runtime.keys.synth_lock_key(slug), thread_id, "synth")


async def run_single_chapter_async(thread_id: str, slug: str, chapter_id: str, mode: str = "quality") -> dict:
    """Fresh per-chapter run in the worker."""
    return await ai.dd.graphs.pipeline.synth.service.run_chapter(binding(), thread_id, slug, chapter_id, mode)


async def resume_synth_async(thread_id: str) -> dict:
    """Resume a chapter from its last checkpoint."""
    return await ai.dd.graphs.pipeline.synth.service.resume_chapter(binding(), thread_id)


async def run_study_async(study_thread_id: str, slug: str, chapter_ids: list[str], mode: str = "quality") -> dict:
    """A whole study in the worker: the chapters in strict order, then the cross-chapter pass."""
    return await ai.dd.graphs.pipeline.synth.study.run(binding(), study_thread_id, slug, chapter_ids, mode)
