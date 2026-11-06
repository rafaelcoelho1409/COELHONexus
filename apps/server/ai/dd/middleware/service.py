"""Behaviour that applies to every model call of a Docs Distiller agent, so it lives next to the agent instead of in each node."""
from __future__ import annotations
import domains

import asyncio
import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState


logger = logging.getLogger(__name__)


class CapacityWaitMiddleware(AgentMiddleware[AgentState, Any, Any]):
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
