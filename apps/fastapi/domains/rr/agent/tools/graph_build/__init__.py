"""Graph-build — deterministic Phase-4 orchestrator tool. Reads the
ranked top-N from fs, embeds abstracts, persists to Neo4j + Qdrant via
`domains.rr.service.persist_paper`."""
from __future__ import annotations

from . import domain, service
