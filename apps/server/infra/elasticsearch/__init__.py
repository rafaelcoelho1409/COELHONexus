"""infra/elasticsearch — shared async ES client + application index bootstrap.

YCS and BD define their index contracts here; domains share the client.

Mirror of `infra/qdrant/` shape. See `docs/CODE-CONVENTIONS.md` §8 +
`docs/YCS-PORT-PLAN-2026-06-06.md` Wave 2."""
from . import domain, keys, params, schemas, service


__all__ = [
    "domain",
    "keys",
    "params",
    "schemas",
    "service",
]
