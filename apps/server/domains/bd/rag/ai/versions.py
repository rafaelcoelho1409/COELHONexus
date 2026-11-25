"""Retrieval-index graph and storage contract versions."""
from __future__ import annotations

# 8.0 rebuilds persisted retrieval graphs and checkpoints with Docling Graph.
WORKFLOW_VERSION = "8.0"
SCHEMA_VERSION = "1.2"
NEO4J_REPLAY_COMPATIBLE_WORKFLOW_VERSIONS = frozenset({"6.0"})
