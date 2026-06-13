"""Async Neo4j driver + LangChain `Neo4jGraph` factories.

Two factories because they serve different consumers:
  get_driver() → raw `AsyncDriver` for custom Cypher (retriever Wave 3c)
  get_graph()  → LangChain `Neo4jGraph` wrapper (LLMGraphTransformer
                 Wave 3b + entity retrieval Wave 3c)

PER-LOOP CACHING — load-bearing for Celery prefork:

  The Neo4j AsyncDriver caches `asyncio.Lock` / `Future` instances bound
  to the event loop it was instantiated on. Celery's prefork worker
  runs every task via a fresh `asyncio.run(...)` (one loop per task),
  so a process-wide singleton driver from Task-1 carries Locks bound to
  Task-1's now-dead loop; Task-2's first concurrent Cypher write fails
  with `RuntimeError: Task <...> got Future attached to a different loop`
  (`got Future <Future pending> attached to a different loop`).

  We key the cache by the *running loop* via `WeakKeyDictionary` so:
    - FastAPI process: 1 loop for life → 1 driver (same as the old singleton)
    - Celery process : 1 driver per task; entries auto-evict when the
                       task's loop is GC'd (loops support weakref since 3.8).

`refresh_schema=False` on `Neo4jGraph` — deprecated rationale
(`app.py:L161-167`): APOC's `apoc.meta.data()` schema reflection
stalls 25-45s on every instantiation. Schema introspection isn't
needed for ingestion or retrieval queries we ship. `Neo4jGraph` wraps
a SYNC driver, so it does NOT need per-loop caching."""
from __future__ import annotations

import asyncio
import logging
import weakref
from typing import Any, Optional

from langchain_neo4j import Neo4jGraph
from neo4j import AsyncDriver, AsyncGraphDatabase

from .params import (
    CONNECTION_TIMEOUT_S,
    MAX_CONNECTION_LIFETIME_S,
    MAX_CONNECTION_POOL_SIZE,
    NEO4J_DATABASE,
    NEO4J_PASSWORD,
    NEO4J_URI,
    NEO4J_USERNAME,
)


logger = logging.getLogger(__name__)


# Per-event-loop async driver cache. Entries auto-evict when the loop is
# garbage-collected (i.e. after `asyncio.run()` exits in a Celery task).
_drivers: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, AsyncDriver]" = (
    weakref.WeakKeyDictionary()
)
_graph: Optional[Any] = None  # Neo4jGraph wraps the SYNC driver; no loop issue.


def _make_driver() -> AsyncDriver:
    auth = (NEO4J_USERNAME, NEO4J_PASSWORD) if NEO4J_PASSWORD else None
    return AsyncGraphDatabase.driver(
        NEO4J_URI,
        auth                     = auth,
        max_connection_lifetime  = MAX_CONNECTION_LIFETIME_S,
        max_connection_pool_size = MAX_CONNECTION_POOL_SIZE,
        connection_timeout       = CONNECTION_TIMEOUT_S,
    )


def get_driver() -> AsyncDriver:
    """Return the AsyncDriver bound to the currently-running event loop.

    Falls back to a singleton-style entry when called outside any loop
    (rare — only at import time during tests). Must be called from inside
    a coroutine to get the per-loop driver in production.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — caller is sync. We can't safely build a driver
        # without a loop, so raise instead of returning a doomed singleton.
        raise RuntimeError(
            "[neo4j] get_driver() must be called from inside an async context "
            "(no running event loop)"
        )
    driver = _drivers.get(loop)
    if driver is None:
        driver = _make_driver()
        _drivers[loop] = driver
        logger.info(f"[neo4j] async driver init {NEO4J_URI} (loop={id(loop):x})")
    return driver


def get_graph() -> Neo4jGraph:
    """LangChain `Neo4jGraph` wrapper for `LLMGraphTransformer` + the
    deprecated entity retriever. `refresh_schema=False` is mandatory
    (see module docstring). SYNC under the hood — process-wide singleton
    is fine."""
    global _graph
    if _graph is None:
        _graph = Neo4jGraph(
            url            = NEO4J_URI,
            username       = NEO4J_USERNAME or "neo4j",
            password       = NEO4J_PASSWORD or "",
            database       = NEO4J_DATABASE,
            refresh_schema = False,
        )
        logger.info(f"[neo4j] Neo4jGraph init {NEO4J_URI} db={NEO4J_DATABASE}")
    return _graph


async def verify_connectivity() -> None:
    """Pre-flight check — useful in app.py lifespan to fail fast if
    Neo4j is unreachable. Raises whatever the driver raises."""
    driver = get_driver()
    await driver.verify_connectivity()


async def close_neo4j() -> None:
    """Close THIS event loop's async driver + drop the sync graph wrapper.

    Each Celery task is encouraged to call this in its `finally` block to
    release the driver's connection pool deterministically before the loop
    dies (otherwise the WeakKeyDictionary would still drop the entry, but
    the pool would leak its sockets until GC runs the driver's __del__)."""
    global _graph
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        driver = _drivers.pop(loop, None)
        if driver is not None:
            try:
                await driver.close()
            except Exception as e:
                logger.warning(f"[neo4j] driver close failed: {e}")
    # `Neo4jGraph` wraps a sync driver internally; releasing the reference is
    # the cleanup. There's no public close() on it.
    _graph = None
