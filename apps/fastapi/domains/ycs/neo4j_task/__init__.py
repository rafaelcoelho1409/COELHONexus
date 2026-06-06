"""ycs/neo4j_task — Celery: full-transcript entity extraction → Neo4j.

Named `neo4j_task/` (not `neo4j/`) to avoid colliding with the `neo4j`
Python package — `from domains.ycs.neo4j import ...` would otherwise
shadow `from neo4j import ...`."""
from .task import ingest_to_neo4j


__all__ = ["ingest_to_neo4j"]
