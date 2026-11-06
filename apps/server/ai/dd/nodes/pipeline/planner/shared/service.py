"""Helpers every planner node shares: progress events, content digests, tolerant bulk reads, the Rotator settle wait."""
from __future__ import annotations
import domains
from . import params

import asyncio
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.config import get_stream_writer


logger = logging.getLogger(__name__)


def emit(step: str, kind: str, **fields: Any) -> None:
    """Send one progress event to the run's stream, in the shape the web reads (`step`, `kind`, ...fields).

    The run shell consumes the graph's `custom` stream and republishes each event on the run's channel. Outside a streamed run
    (a test that invokes the graph directly) there is no writer and the event is dropped."""
    try:
        writer = get_stream_writer()
    except RuntimeError:
        return
    writer({"step": step, "kind": kind, **fields})


def digest(*parts: str) -> str:
    """A short content digest of the parts, in order: the identity of a per-item result."""
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8", "replace"))
        h.update(b"\x1f")
    return h.hexdigest()[:params.DIGEST_CHARS]


async def read_all(blobs: Any, keys: list[str]) -> list[str | None]:
    """The text of every key, in order; None for a key that could not be read.

    One bulk read first (a shared client, chunked and parallel); when it fails as a whole, key by key so one bad object costs one page."""
    if not keys:
        return []
    try:
        bodies = await blobs.read_many(keys)
        if len(bodies) == len(keys):
            return list(bodies)
    except Exception as error:
        logger.warning(f"[planner] bulk read failed ({type(error).__name__}: {error}); reading key by key")

    async def one(key: str) -> str | None:
        try:
            return await blobs.read_text(key)
        except Exception:
            return None

    return list(await asyncio.gather(*[one(key) for key in keys]))


async def settle(step: str, cap_s: float) -> None:
    """Wait for the Rotator's pool to recover from the previous node's burst: up to `cap_s`, ending as soon as it has."""
    if cap_s <= 0:
        return
    emit(step, "settling", delay_s = cap_s)
    settled = await domains.settings.chat.service.wait_for_rotator_settle(cap_s)
    logger.info(f"[{step}] settle done: {settled}")


def elapsed_ms(since: float) -> int:
    return int((time.monotonic() - since) * 1000)


async def gather_bounded(limit: int, factories: list[Callable[[], Awaitable[Any]]]) -> list[Any]:
    """Run the awaitables the factories build, at most `limit` at once, results in input order."""
    semaphore = asyncio.Semaphore(limit)

    async def run(factory: Callable[[], Awaitable[Any]]) -> Any:
        async with semaphore:
            return await factory()

    return list(await asyncio.gather(*[run(factory) for factory in factories]))
