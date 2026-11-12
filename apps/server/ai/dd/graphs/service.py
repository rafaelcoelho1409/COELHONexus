"""Progress reporting shared by Docs Distiller graphs and their nodes: the events a node sends, its wall-clock, and the wait for the Rotator's pool."""
from __future__ import annotations
import domains

import logging
import time
import collections.abc
import typing

import langgraph.config


logger = logging.getLogger(__name__)


class ProgressReporter(typing.Protocol):
    async def raise_if_cancelled(self) -> None: ...

    async def update(self, *, current: int) -> None: ...

    async def update_agentic_node(
        self,
        node: str,
        *,
        status: str | None = None,
        current: int | None = None,
        total: int | None = None,
    ) -> None: ...


async def run_node(
    progress: ProgressReporter | None,
    node: str,
    operation: collections.abc.Callable[[], collections.abc.Awaitable[dict[str, typing.Any]]],
) -> dict[str, typing.Any]:
    """Run one graph node's work and report it as running, then done, or failed if it raises."""
    if progress is not None:
        await progress.update_agentic_node(node, status = "running")
    try:
        result = await operation()
    except BaseException:
        if progress is not None:
            await progress.update_agentic_node(node, status = "failed")
        raise
    if progress is not None:
        await progress.update_agentic_node(node, status = "done")
    return result


def emit(step: str, kind: str, **fields: typing.Any) -> None:
    """Send one progress event to the run's stream, in the shape the web reads (`step`, `kind`, ...fields).

    The run shell consumes the graph's `custom` stream and republishes each event on the run's channel. Outside a streamed run
    (a test that invokes the graph directly) there is no writer and the event is dropped."""
    try:
        writer = langgraph.config.get_stream_writer()
    except RuntimeError:
        return
    writer({"step": step, "kind": kind, **fields})


def elapsed_ms(since: float) -> int:
    return int((time.monotonic() - since) * 1000)


async def settle(step: str, cap_s: float) -> None:
    """Wait for the Rotator's pool to recover from the previous node's burst: up to `cap_s`, ending as soon as it has."""
    if cap_s <= 0:
        return
    emit(step, "settling", delay_s = cap_s)
    settled = await domains.settings.chat.service.wait_for_rotator_settle(cap_s)
    logger.info(f"[{step}] settle done: {settled}")
