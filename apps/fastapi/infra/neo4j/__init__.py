"""infra/neo4j — async driver + LangChain `Neo4jGraph` shared singletons.

Two consumer flavors:
  - `get_driver()` for custom Cypher (entity retriever)
  - `get_graph()`  for `LLMGraphTransformer` + LangChain helpers

Mirror of `infra/qdrant/` shape. See `docs/CODE-CONVENTIONS.md` §8 +
`docs/YCS-PORT-PLAN-2026-06-06.md` Wave 2."""
from .params import (
    NEO4J_DATABASE,
    NEO4J_URI,
    NEO4J_USERNAME,
)
from .service import (
    close_neo4j,
    get_driver,
    get_graph,
    verify_connectivity,
)


__all__ = [
    "NEO4J_DATABASE",
    "NEO4J_URI",
    "NEO4J_USERNAME",
    "close_neo4j",
    "get_driver",
    "get_graph",
    "verify_connectivity",
]
