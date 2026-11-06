"""What every node of a planner run is handed at run time (LangGraph's `context`): object storage and the run's agents."""
from __future__ import annotations
from .... import agents

from dataclasses import dataclass, field
from typing import Any


class PlannerAgents:
    """The agents of one planner run, each built on first use.

    An agent compiles a graph, and a run uses some roles many times (one call per page), so each is built once. The roles whose
    shape depends on the run (the scorer carries the run's chapters, the architect and the curator carry tools bound to the run's
    data) are built by the node that has that data. `overrides` replaces a role by name (`agents.entities.Role` or any object with
    `.agent.ainvoke` and `.files`): a test passes fakes, a run passes none."""

    def __init__(self, **overrides: Any) -> None:
        self._overrides = overrides
        self._built: dict[str, Any] = {}

    def _once(self, name: str, build) -> Any:
        if name in self._overrides:
            return self._overrides[name]
        if name not in self._built:
            self._built[name] = build()
        return self._built[name]

    def page_judge(self):
        return self._once("page_judge", lambda: agents.pipeline.planner.page_judge.agent.page_judge())

    def page_distiller(self):
        return self._once("page_distiller", lambda: agents.pipeline.planner.page_distiller.agent.page_distiller())

    def section_analyst(self):
        return self._once("section_analyst", lambda: agents.pipeline.planner.section_analyst.agent.section_analyst())

    def chapter_sequencer(self):
        return self._once("chapter_sequencer", lambda: agents.pipeline.planner.chapter_sequencer.agent.chapter_sequencer())

    def page_scorer(self, proposals: list[dict], *, fixed_keys: bool):
        return self._once(
            "page_scorer",
            lambda: agents.pipeline.planner.page_scorer.agent.page_scorer(proposals, fixed_keys = fixed_keys),
        )

    def chapter_architect(self, *, backend: Any, tools: list[Any]):
        if "chapter_architect" in self._overrides:
            return self._overrides["chapter_architect"]
        return agents.pipeline.planner.chapter_architect.agent.chapter_architect(backend = backend, tools = tools)

    def chapter_curator(self, *, tools: list[Any]):
        if "chapter_curator" in self._overrides:
            return self._overrides["chapter_curator"]
        return agents.pipeline.planner.chapter_curator.agent.chapter_curator(tools = tools)


@dataclass
class PlannerContext:
    """`blobs` is the run's object storage (read_text, read_many, write, exists, list); `agents` its agent registry."""

    blobs: Any
    agents: PlannerAgents = field(default_factory = PlannerAgents)
    # Running totals the parallel branches of a fan-out node add to, per node, for its progress events (a branch cannot see its siblings).
    counters: dict[str, dict[str, int]] = field(default_factory = dict)
    # Data a node's `prepare` loads once for its parallel branches (a branch is sent only a key, not 700 copies of the run's chapters).
    # Not checkpointed: a resume re-runs `prepare`, which loads it again.
    scratch: dict[str, Any] = field(default_factory = dict)
