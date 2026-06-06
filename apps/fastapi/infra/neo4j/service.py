"""Async Neo4j driver + LangChain `Neo4jGraph` factories.

Two singletons because they serve different consumers:
  get_driver() → raw `AsyncDriver` for custom Cypher (retriever Wave 3c)
  get_graph()  → LangChain `Neo4jGraph` wrapper (LLMGraphTransformer
                 Wave 3b + entity retrieval Wave 3c)

`refresh_schema=False` on `Neo4jGraph` — deprecated rationale
(`app.py:L161-167`): APOC's `apoc.meta.data()` schema reflection
stalls 25-45s on every instantiation. Schema introspection isn't
needed for ingestion or retrieval queries we ship."""
from __future__ import annotations

import logging
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


_driver: Optional[AsyncDriver] = None
_graph: Optional[Any] = None  # Neo4jGraph instance (lazy import)


def get_driver() -> AsyncDriver:
    """Process-bound singleton async driver. Use for custom Cypher
    queries (retrievers, graph stats)."""
    global _driver
    if _driver is None:
        auth = (NEO4J_USERNAME, NEO4J_PASSWORD) if NEO4J_PASSWORD else None
        _driver = AsyncGraphDatabase.driver(
            NEO4J_URI,
            auth = auth,
            max_connection_lifetime = MAX_CONNECTION_LIFETIME_S,
            max_connection_pool_size = MAX_CONNECTION_POOL_SIZE,
            connection_timeout = CONNECTION_TIMEOUT_S,
        )
        logger.info(f"[neo4j] async driver init {NEO4J_URI}")
    return _driver


def get_graph() -> Neo4jGraph:
    """LangChain `Neo4jGraph` wrapper for `LLMGraphTransformer` + the
    deprecated entity retriever. `refresh_schema=False` is mandatory
    (see module docstring)."""
    global _graph
    if _graph is None:
        _graph = Neo4jGraph(
            url = NEO4J_URI,
            username = NEO4J_USERNAME or "neo4j",
            password = NEO4J_PASSWORD or "",
            database = NEO4J_DATABASE,
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
    """Lifespan shutdown. Idempotent. Closes both the async driver and
    drops the LangChain wrapper reference."""
    global _driver, _graph
    if _driver is not None:
        try:
            await _driver.close()
        except Exception as e:
            logger.warning(f"[neo4j] driver close failed: {e}")
        _driver = None
    # `Neo4jGraph` wraps a sync driver internally; releasing the
    # reference is the cleanup. There's no public close() on it.
    _graph = None
