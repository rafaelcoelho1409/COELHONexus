"""chapter_propose graph node: runs the proposal subgraph (section analysts in parallel, then the architect and its score gate), and the routes between its steps."""
from __future__ import annotations
from domains.dd.pipeline.observability.service import traced
from . import domain, params, service
from ..... import graphs

from langgraph.graph import END
from langgraph.runtime import Runtime
from langgraph.types import Send


@traced("chapter_propose")
async def chapter_propose(
    state: graphs.pipeline.planner.states.PlannerState,
    runtime: Runtime[graphs.pipeline.planner.entities.PlannerContext],
) -> dict:
    result = await graphs.pipeline.planner.chapter_propose.graphs.chapter_propose_graph().ainvoke(
        {
            "framework_slug": state.get("framework_slug") or "",
            "thread_id": state.get("thread_id") or "",
            "raw_files": state.get("raw_files") or [],
            "relevant_files": state.get("relevant_files") or [],
            "doc_distill_ref": state.get("doc_distill_ref"),
        },
        {"max_concurrency": params.ANALYST_CONCURRENCY},
        context = runtime.context,
    )
    return {
        "chapter_proposals_ref": result.get("chapter_proposals_ref"),
        "propose_stats": result.get("propose_stats") or {},
    }


async def prepare(
    state: graphs.pipeline.planner.chapter_propose.states.ProposeState,
    runtime: Runtime[graphs.pipeline.planner.entities.PlannerContext],
) -> dict:
    return await service.prepare(state, runtime)


async def analyze_section(
    state: graphs.pipeline.planner.chapter_propose.entities.SectionInput,
    runtime: Runtime[graphs.pipeline.planner.entities.PlannerContext],
) -> dict:
    return await service.analyze_section(state, runtime)


async def architect(
    state: graphs.pipeline.planner.chapter_propose.states.ProposeState,
    runtime: Runtime[graphs.pipeline.planner.entities.PlannerContext],
) -> dict:
    return await service.architect(state, runtime)


async def finalize(
    state: graphs.pipeline.planner.chapter_propose.states.ProposeState,
    runtime: Runtime[graphs.pipeline.planner.entities.PlannerContext],
) -> dict:
    return await service.finalize(state, runtime)


def after_prepare(graph_state: graphs.pipeline.planner.chapter_propose.states.ProposeState):
    """A corpus of enough pages is read by one analyst per section; a small one goes straight to the architect."""
    if graph_state.get("finished"):
        return END
    shards = graph_state.get("shards") or []
    if not shards:
        return "architect"
    return [
        Send(
            "analyze_section",
            {
                "section": shard["label"],
                "keys": shard["keys"],
                "target": service.section_target(len(shard["keys"])),
                "framework_slug": graph_state["framework_slug"],
            },
        )
        for shard in shards
    ]


def after_architect(graph_state: graphs.pipeline.planner.chapter_propose.states.ProposeState):
    """The score gate: an outline below the bar (or none) sends the architect back, with the pages it left uncovered, until
    ARCHITECT_ROUNDS attempts are made; the best outline seen is kept."""
    attempt = graph_state.get("attempt") or 0
    outlines = [o for o in graph_state.get("outlines") or [] if o["attempt"] == attempt]
    if attempt >= params.ARCHITECT_ROUNDS:
        return "finalize"
    if outlines and not domain.is_weak(outlines[-1]["scored"]):
        return "finalize"
    return "architect"
