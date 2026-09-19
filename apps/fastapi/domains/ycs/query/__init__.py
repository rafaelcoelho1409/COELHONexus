"""ycs/query — cross-store query service powering the YCS Query page.

Two surface layers:
  · Free-text search  — query_es / query_qdrant / query_neo4j
                        (legacy `q` style for the original empty-page UX)
  · Raw DSL workbench — raw_es / raw_qdrant / raw_neo4j
                        (CodeMirror-driven Phase 1 of the SOTA workbench)

The app→backend support matrix lives in `params.APP_BACKENDS` —
single source of truth for both the service guards and the UI's
grey-out behavior."""
from __future__ import annotations

from . import domain, entities, errors, params, patterns, prompts, schemas, service
