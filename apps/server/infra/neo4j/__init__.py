"""infra/neo4j — async driver + LangChain `Neo4jGraph` shared singletons.

Two consumer flavors:
  - `get_driver()` for custom Cypher (entity retriever)
  - `get_graph()`  for `LLMGraphTransformer` + LangChain helpers

Mirror of `infra/qdrant/` shape. See `docs/CODE-CONVENTIONS.md` §8 +
`docs/YCS-PORT-PLAN-2026-06-06.md` Wave 2."""
from . import domain, params, service


__all__ = [
    "domain",
    "params",
    "service",
]
