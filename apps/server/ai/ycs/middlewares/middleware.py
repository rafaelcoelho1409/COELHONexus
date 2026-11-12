"""Role-scoped behavior for YCS DeepAgents."""
from __future__ import annotations

import domains

import asyncio
import logging
import typing

import langchain.agents.middleware


logger = logging.getLogger(__name__)


class CapacityWaitMiddleware(langchain.agents.middleware.AgentMiddleware[langchain.agents.middleware.AgentState, typing.Any, typing.Any]):
    """Wait for the configured Rotator pool's Retry-After when no arm is available."""

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
                if (
                    wait is None
                    or attempt >= chat.params.CAPACITY_WAIT_ATTEMPTS
                    or waited + wait > chat.params.CAPACITY_WAIT_TOTAL_S
                ):
                    raise
                logger.info(
                    f"[ycs-agent] Rotator has no available arm ({type(error).__name__}); "
                    f"waiting {wait:.0f}s before retrying ({attempt + 1}/"
                    f"{chat.params.CAPACITY_WAIT_ATTEMPTS})"
                )
                await asyncio.sleep(wait)
                waited += wait
        raise AssertionError("unreachable capacity-wait state")


class ToolAllowlistMiddleware(langchain.agents.middleware.AgentMiddleware[langchain.agents.middleware.AgentState, typing.Any, typing.Any]):
    """Expose only the role's explicitly allowed tools to the model."""

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
