"""What every node of a synth run is handed at run time (LangGraph's `context`): object storage and the run's agents."""
from __future__ import annotations
from .... import agents

from dataclasses import dataclass, field
from typing import Any


class SynthAgents:
    """The agents of one synth run, each built when a node asks for it.

    A role with no data of its own (the judge, the extenders, the study's editors) is built once. A role whose tools or files are bound
    to the data of one call (the outliner's sources, a section's code bank, a source's hashes) is built for that call: building an agent
    only compiles a small graph. `overrides` replaces a role by name (`agents.entities.Role` or any object with `.agent.ainvoke` and
    `.files`): a test passes fakes, a run passes none."""

    def __init__(self, **overrides: Any) -> None:
        self._overrides = overrides
        self._built: dict[str, Any] = {}

    def _once(self, name: str, build) -> Any:
        if name in self._overrides:
            return self._overrides[name]
        if name not in self._built:
            self._built[name] = build()
        return self._built[name]

    def _per_call(self, name: str, build) -> Any:
        return self._overrides[name] if name in self._overrides else build()

    @property
    def roles(self):
        return agents.pipeline.synth

    def chapter_judge(self):
        return self._once("chapter_judge", lambda: self.roles.chapter_judge.agent.chapter_judge())

    def coverage_extender(self):
        return self._once("coverage_extender", lambda: self.roles.coverage_extender.agent.coverage_extender())

    def source_extender(self):
        return self._once("source_extender", lambda: self.roles.source_extender.agent.source_extender())

    def claim_extractor(self):
        return self._once("claim_extractor", lambda: self.roles.claim_extractor.agent.claim_extractor())

    def term_canonicalizer(self):
        return self._once("term_canonicalizer", lambda: self.roles.term_canonicalizer.agent.term_canonicalizer())

    def paragraph_patcher(self):
        return self._once("paragraph_patcher", lambda: self.roles.paragraph_patcher.agent.paragraph_patcher())

    def chapter_outliner(self, *, backend: Any, tools: list[Any]):
        return self._per_call("chapter_outliner", lambda: self.roles.chapter_outliner.agent.chapter_outliner(backend = backend, tools = tools))

    def source_router(self, *, tools: list[Any]):
        return self._per_call("source_router", lambda: self.roles.source_router.agent.source_router(tools = tools))

    def section_writer(self, *, backend: Any, tools: list[Any]):
        return self._per_call("section_writer", lambda: self.roles.section_writer.agent.section_writer(backend = backend, tools = tools))

    def code_deriver(self, *, tools: list[Any]):
        return self._per_call("code_deriver", lambda: self.roles.code_deriver.agent.code_deriver(tools = tools))

    def code_normalizer(self, *, tools: list[Any]):
        return self._per_call("code_normalizer", lambda: self.roles.code_normalizer.agent.code_normalizer(tools = tools))

    def chapter_reviewer(self, *, tools: list[Any]):
        return self._per_call("chapter_reviewer", lambda: self.roles.chapter_reviewer.agent.chapter_reviewer(tools = tools))

    def drift_detector(self, *, backend: Any):
        return self._per_call("drift_detector", lambda: self.roles.drift_detector.agent.drift_detector(backend = backend))


@dataclass
class SynthContext:
    """`blobs` is the run's object storage (read_text, read_many, write, exists, list); `agents` its agent registry."""

    blobs: Any
    agents: SynthAgents = field(default_factory = SynthAgents)
    # Running totals the parallel branches of a fan-out node add to, per node, for its progress events (a branch cannot see its siblings).
    counters: dict[str, dict[str, int]] = field(default_factory = dict)
    # Data a node's `prepare` loads once for its parallel branches (a branch is sent only a key, not the chapter's sources). Not
    # checkpointed: a resume re-runs `prepare`, which loads it again.
    scratch: dict[str, Any] = field(default_factory = dict)
