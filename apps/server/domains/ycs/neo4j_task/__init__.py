"""ycs/neo4j_task — Celery: full-transcript entity extraction → Neo4j.

Named `neo4j_task/` (not `neo4j/`) to avoid colliding with the `neo4j`
Python package — `from domains.ycs.neo4j import ...` would otherwise
shadow `from neo4j import ...`.

`task.py` is excluded from this eager chain (§8 Exception 1 —
`import infra.celery.service` (use `infra.celery.service.app`) at module level); reach it via a direct
`from domains.ycs.neo4j_task.task import ingest_to_neo4j`."""
from __future__ import annotations
from . import params, service
