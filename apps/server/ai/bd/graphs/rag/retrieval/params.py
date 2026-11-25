"""Rollout and execution limits for agentic source retrieval."""
from __future__ import annotations

import os


SPECIALIST_AGENTS_ENABLED = os.environ.get(
    "BD_RAG_SPECIALIST_AGENTS_ENABLED", "false",
).strip().casefold() in {"1", "true", "yes", "on"}
MAX_CONCURRENT_SOURCES = 4
