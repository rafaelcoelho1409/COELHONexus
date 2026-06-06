"""infra/neo4j — env reads + connection tunables.

Env vars come from `commonEnvVars` (`k8s/helm/templates/_helpers.tpl`):
  NEO4J_URI       — bolt URL to the in-cluster Neo4j service
  NEO4J_USERNAME  — loaded from coelhonexus-secret
  NEO4J_PASSWORD  — loaded from coelhonexus-secret"""
from __future__ import annotations

import os


NEO4J_URI = os.environ.get(
    "NEO4J_URI", "bolt://neo4j.neo4j.svc.cluster.local:7687",
)
NEO4J_USERNAME = os.environ.get("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "") or None

# Default database name (Neo4j Community Edition is single-DB; this just
# matches the deprecated implicit default).
NEO4J_DATABASE = os.environ.get("NEO4J_DATABASE", "neo4j")

# Connection pool tunables.
MAX_CONNECTION_LIFETIME_S = 3600
MAX_CONNECTION_POOL_SIZE = 50
CONNECTION_TIMEOUT_S = 30.0
