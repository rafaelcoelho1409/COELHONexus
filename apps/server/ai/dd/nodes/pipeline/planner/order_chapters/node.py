"""order_chapters graph node: runs the ordering subgraph (N sequencer samples in parallel, then the Borda aggregate and the pins), and the routes between its steps."""
from __future__ import annotations
import domains.dd.pipeline.observability.service
from . import params, service
from ..... import graphs

from langgraph.graph import END
from langgraph.runtime import Runtime
from langgraph.types import Send


@domains.dd.pipeline.observability.service.traced("order_chapters")
async def order_chapters(
    state: graphs.pipeline.planner.states.PlannerState,
    runtime: Runtime[graphs.pipeline.planner.entities.PlannerContext],
) -> dict:
    result = await graphs.pipeline.planner.order_chapters.graphs.order_chapters_graph().ainvoke(
        {
            "framework_slug": state.get("framework_slug") or "",
            "thread_id": state.get("thread_id") or "",
            "chapter_plan_ref": state.get("chapter_plan_ref") or "",
        },
        {"max_concurrency": params.SAMPLE_CONCURRENCY},
        context = runtime.context,
    )
    return {
        "chapter_order_ref": result.get("chapter_order_ref") or "",
        "order_chapters_stats": result.get("order_chapters_stats") or {},
    }


async def prepare(
    state: graphs.pipeline.planner.order_chapters.states.OrderState,
    runtime: Runtime[graphs.pipeline.planner.entities.PlannerContext],
) -> dict:
    return await service.prepare(state, runtime)


async def sample_order(
    state: graphs.pipeline.planner.order_chapters.entities.SampleInput,
    runtime: Runtime[graphs.pipeline.planner.entities.PlannerContext],
) -> dict:
    return await service.sample_order(state, runtime)


def collect(state: graphs.pipeline.planner.order_chapters.states.OrderState) -> dict:
    return service.collect(state)


async def finalize(
    state: graphs.pipeline.planner.order_chapters.states.OrderState,
    runtime: Runtime[graphs.pipeline.planner.entities.PlannerContext],
) -> dict:
    return await service.finalize(state, runtime)


def sample_input(
    graph_state: graphs.pipeline.planner.order_chapters.states.OrderState,
    sample_idx: int,
) -> graphs.pipeline.planner.order_chapters.entities.SampleInput:
    return {
        "sample_idx": sample_idx,
        "framework_slug": graph_state["framework_slug"],
        "chapters": graph_state["chapters"],
        "composition": graph_state.get("composition"),
    }


def after_prepare(graph_state: graphs.pipeline.planner.order_chapters.states.OrderState):
    """A node answered from its cache or by a rule ends here; otherwise one sequencer sample per Send."""
    if graph_state.get("finished"):
        return END
    return [Send("sample_order", sample_input(graph_state, i)) for i in range(params.N_SAMPLES)]


def after_collect(graph_state: graphs.pipeline.planner.order_chapters.states.OrderState):
    """A first round with fewer valid samples than MIN_VALID_SAMPLES is topped up once, for the missing indexes only."""
    retry = graph_state.get("retry_samples") or []
    if retry:
        return [Send("sample_order", sample_input(graph_state, i)) for i in retry]
    return "finalize"
