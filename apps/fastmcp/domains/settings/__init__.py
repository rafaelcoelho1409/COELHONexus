"""Settings bounded context — external endpoints + credential store.

Mirrors `apps/server/domains/settings/`: same functionality, same path.
This peer app carries the read-only subset it needs (credential injection
at boot); chat/embeddings gateways and write paths live server-side in
the server app and are intentionally absent here.

No MCP surface: nothing here registers tools/resources/prompts. It exists
so cross-tree navigation finds credential code in the same place.
"""
from __future__ import annotations
from . import credentials


__all__ = ["credentials"]
