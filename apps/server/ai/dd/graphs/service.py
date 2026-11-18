"""Progress reporting shared by Docs Distiller graphs and their nodes: the events a node sends, its wall-clock, and the wait for the Rotator's pool."""
from __future__ import annotations
import domains

import logging
import time
from typing import Any

from langgraph.config import get_stream_writer


logger = logging.getLogger(__name__)


def mermaid_source(compiled_graph: Any) -> str:
    """Render a compiled graph's topology, including nested subgraphs, without invoking its nodes."""
    return compiled_graph.get_graph(xray = True).draw_mermaid()


def emit(step: str, kind: str, **fields: Any) -> None:
    """Send one progress event to the run's stream, in the shape the web reads (`step`, `kind`, ...fields).

    The run shell consumes the graph's `custom` stream and republishes each event on the run's channel. Outside a streamed run
    (a test that invokes the graph directly) there is no writer and the event is dropped."""
    try:
        writer = get_stream_writer()
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
