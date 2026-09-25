"""Resource URI registry — per docs/CODE-CONVENTIONS.md §2.

Routing names consumed outside `service.py` (by MCP clients and the
`register()` boundary in each resource module) live here so the URIs
can't drift between registration and documentation.
"""
from __future__ import annotations


LATEST_DIGEST_URI: str = "radar://latest_digest"
CONCEPT_URI_TEMPLATE: str = "radar://concept/{name}"
