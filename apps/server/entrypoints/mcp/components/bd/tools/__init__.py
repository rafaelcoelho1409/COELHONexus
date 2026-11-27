"""FastMCP protocol elements owned by the BD domain."""

from __future__ import annotations

# Ask readiness embeds the typed stage summaries, so load Ingestion schemas first.
from . import ingestion
from . import ask, distill, query, registry

__all__ = ["ask", "distill", "ingestion", "query", "registry"]
