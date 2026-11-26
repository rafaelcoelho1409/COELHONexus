"""Retrieval-index graph and storage contract versions."""
from __future__ import annotations

# 9.0 adds token-budgeted graph extraction and durable extraction-quality counters.
WORKFLOW_VERSION = "9.0"
SCHEMA_VERSION = "1.3"
NEO4J_REPLAY_COMPATIBLE_WORKFLOW_VERSIONS = frozenset({"6.0"})
