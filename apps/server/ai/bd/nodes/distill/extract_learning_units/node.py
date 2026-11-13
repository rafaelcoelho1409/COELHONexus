"""Thin graph wrappers for source preparation and learning extraction."""
from __future__ import annotations
import domains
from . import service
from .. import domain
from .... import graphs

from langgraph.types import Send


def after_start(state: graphs.distill.states.DistillRunState) -> str:
    """Resume chapter-stage failures from their persisted unit inputs when possible."""
    if state.get("resume_from_node") in {
        "prepare_chapters",
        "synthesize_chapter",
        "synthesize_book",
        "publish_report",
    }:
        return "prepare_chapters"
    return "prepare_units"


def dispatch_units(state: graphs.distill.states.DistillRunState) -> list[Send] | str:
    """Fan out bounded unit batches or continue directly when there are no units."""
    refs = state.get("unit_refs", [])
    if not refs:
        return "prepare_chapters"
    size = domains.bd.distill.params.FANOUT_UNIT_BATCH_SIZE
    identity = domain.graph_identity(state)
    return [
        Send("extract_unit", {**identity, "unit_batch": refs[index:index + size]})
        for index in range(0, len(refs), size)
    ]


async def prepare_units(
    dependencies: graphs.distill.entities.DistillDependencies,
    graph_state: graphs.distill.states.DistillRunState,
) -> dict:
    return await graphs.service.run_node(
        dependencies.progress_callback,
        "prepare_units",
        lambda: service.prepare_units(dependencies, graph_state),
    )


async def extract_unit(
    dependencies: graphs.distill.entities.DistillDependencies,
    graph_state: graphs.distill.states.DistillRunState,
) -> dict:
    return await graphs.service.run_node(
        dependencies.progress_callback,
        "extract_unit",
        lambda: service.extract_unit_batch(dependencies, graph_state),
    )
