"""book_harmonize steps and routes: the nodes of its subgraph. The pass itself, run by the study after its chapters are rendered, is in `service.run_pass` (it is not a node of a chapter's graph)."""
from __future__ import annotations
from . import service
from ..... import graphs

import langgraph.graph
import langgraph.runtime
import langgraph.types


async def prepare(
    state: graphs.pipeline.synth.book_harmonize.states.HarmonizeState,
    runtime: langgraph.runtime.Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.prepare(state, runtime)


async def extract(
    state: dict,
    runtime: langgraph.runtime.Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.extract(state, runtime)


async def canonicalize(
    state: graphs.pipeline.synth.book_harmonize.states.HarmonizeState,
    runtime: langgraph.runtime.Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.canonicalize(state, runtime)


async def detect(
    state: dict,
    runtime: langgraph.runtime.Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.detect(state, runtime)


async def plan_patches(
    state: graphs.pipeline.synth.book_harmonize.states.HarmonizeState,
    runtime: langgraph.runtime.Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.plan_patches(state, runtime)


async def patch(
    state: dict,
    runtime: langgraph.runtime.Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.patch(state, runtime)


async def finalize(
    state: graphs.pipeline.synth.book_harmonize.states.HarmonizeState,
    runtime: langgraph.runtime.Runtime[graphs.pipeline.synth.entities.SynthContext],
) -> dict:
    return await service.finalize(state, runtime)


def after_prepare(graph_state: graphs.pipeline.synth.book_harmonize.states.HarmonizeState):
    if graph_state.get("finished"):
        return langgraph.graph.END
    return [langgraph.types.Send("extract", {"chapter_id": chapter_id}) for chapter_id in graph_state["chapter_ids"]]


def after_canonicalize(graph_state: graphs.pipeline.synth.book_harmonize.states.HarmonizeState):
    if graph_state.get("finished"):
        return langgraph.graph.END
    return [langgraph.types.Send("detect", {"chapter_id": chapter_id}) for chapter_id in graph_state["chapter_ids"]]


def after_plan(graph_state: graphs.pipeline.synth.book_harmonize.states.HarmonizeState):
    todo = graph_state.get("patch_todo") or []
    if not todo:
        return "finalize"
    return [langgraph.types.Send("patch", {"task": task}) for task in todo]
