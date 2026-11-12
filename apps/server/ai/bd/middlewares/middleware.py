"""Behaviour that applies to every model call of a Book Distiller agent, so it lives next to the agent instead of in each node."""
from __future__ import annotations

import typing

import langchain.agents.middleware


class ToolAllowlistMiddleware(langchain.agents.middleware.AgentMiddleware[langchain.agents.middleware.AgentState, typing.Any, typing.Any]):
    """Shows the model only the tools its role may use, and no others.

    A role's reach is its own: the filter belongs to the agent, not to the process. Deep Agents' harness profile is keyed by the
    model, which is the same Rotator model for every role in the worker, and its excluded tools union across registrations, so a
    tool hidden for one role (here or in another feature) would be hidden from every role built after it."""

    def __init__(self, allowed: frozenset[str]) -> None:
        super().__init__()
        self._allowed = frozenset(allowed)

    @staticmethod
    def _name(tool: typing.Any) -> str | None:
        name = tool.get("name") if isinstance(tool, dict) else getattr(tool, "name", None)
        return name if isinstance(name, str) else None

    def _restricted(self, request):
        return request.override(tools = [tool for tool in request.tools if self._name(tool) in self._allowed])

    def wrap_model_call(self, request, handler):
        return handler(self._restricted(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._restricted(request))
