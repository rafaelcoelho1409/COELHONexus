"""Behaviour that applies to every model call of a Docs Distiller agent, so it lives next to the agent instead of in each node."""
from __future__ import annotations
import domains

import asyncio
import logging
import typing

import langchain.agents.middleware


logger = logging.getLogger(__name__)


class ToolAllowlistMiddleware(langchain.agents.middleware.AgentMiddleware[langchain.agents.middleware.AgentState, typing.Any, typing.Any]):
    """Shows the model only the tools its role may use, and no others.

    A role's reach is its own: the filter belongs to the agent, not to the process. Deep Agents' harness profile is keyed by the
    model, which is the same Rotator model for every role, and its excluded tools union across registrations, so a tool hidden
    for one role would be hidden from every role built after it."""

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


class CapacityWaitMiddleware(langchain.agents.middleware.AgentMiddleware[langchain.agents.middleware.AgentState, typing.Any, typing.Any]):
    """When the Rotator reports it has no usable arm right now, waits out its Retry-After (bounded) and calls the model again.

    The same rule `domains.settings.chat.service` applies to its own calls; an agent talks to the model directly, so without
    this a pool crunch would fail every in-flight agent at once instead of pausing them. Sits inside the transient-error retry,
    so each retry attempt gets its own bounded waits."""

    def __init__(self) -> None:
        super().__init__()
        self._chat = domains.settings.chat

    async def awrap_model_call(self, request, handler):
        chat = self._chat
        waited = 0.0
        for attempt in range(chat.params.CAPACITY_WAIT_ATTEMPTS + 1):
            try:
                return await handler(request)
            except Exception as error:
                wait = chat.domain.capacity_wait_s(
                    error,
                    default_s = chat.params.CAPACITY_WAIT_DEFAULT_S,
                    min_s = chat.params.CAPACITY_WAIT_MIN_S,
                    cap_s = chat.params.CAPACITY_WAIT_CAP_S,
                )
                if wait is None or attempt >= chat.params.CAPACITY_WAIT_ATTEMPTS or waited + wait > chat.params.CAPACITY_WAIT_TOTAL_S:
                    raise
                logger.info(
                    f"[dd-agent] rotator has no usable arm ({type(error).__name__}); waiting {wait:.0f}s, "
                    f"then calling again ({attempt + 1}/{chat.params.CAPACITY_WAIT_ATTEMPTS})",
                )
                await asyncio.sleep(wait)
                waited += wait
        raise AssertionError("unreachable capacity-wait state")
