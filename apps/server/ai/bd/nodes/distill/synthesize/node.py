"""Thin graph wrappers for chapter and whole-book synthesis."""
from __future__ import annotations
from . import service
from .. import domain
from .... import graphs

import domains
from langgraph.types import Send


def dispatch_chapters(state: graphs.distill.states.DistillRunState) -> list[Send]:
    """Fan out bounded chapter batches and fail when extraction produced no readable material."""
    refs = state.get("chapter_refs", [])
    if not refs:
        raise ValueError("the completed extraction did not contain readable learning material")
    size = domains.bd.distill.params.FANOUT_CHAPTER_BATCH_SIZE
    identity = domain.graph_identity(state)
    return [
        Send("synthesize_chapter", {**identity, "chapter_batch": refs[index:index + size]})
        for index in range(0, len(refs), size)
    ]


async def prepare_chapters(
    dependencies: graphs.distill.entities.DistillDependencies,
    graph_state: graphs.distill.states.DistillRunState,
) -> dict:
    return await graphs.service.run_node(
        dependencies.progress_callback,
        "prepare_chapters",
        lambda: service.prepare_chapters(dependencies, graph_state),
    )


async def synthesize_chapter(
    dependencies: graphs.distill.entities.DistillDependencies,
    graph_state: graphs.distill.states.DistillRunState,
) -> dict:
    return await graphs.service.run_node(
        dependencies.progress_callback,
        "synthesize_chapter",
        lambda: service.synthesize_chapter_batch(dependencies, graph_state),
    )


async def synthesize_book(
    dependencies: graphs.distill.entities.DistillDependencies,
    graph_state: graphs.distill.states.DistillRunState,
) -> dict:
    return await graphs.service.run_node(
        dependencies.progress_callback,
        "synthesize_book",
        lambda: service.synthesize_book(dependencies, graph_state),
    )
