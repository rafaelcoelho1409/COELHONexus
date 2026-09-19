"""domains — business domains (bounded contexts): dd (Docs Distiller), llm (rotator/credentials),
rr (Research Radar), ycs (YouCanSee).

Each domain owns its router, schemas, service/graph code, tasks and exceptions.
Cross-domain references go through the dotted `domains.*` path (see
docs/CODE-CONVENTIONS.md §8) rather than bare `from domains.x.y import z` imports.
`dd` is re-exported here as the §8 pilot; `llm` joins for `llm.rotator.chain`
specifically (needed by dd/planner's node services); `rr`/`ycs` join once
their own pilot pass lands.
"""
from __future__ import annotations

from . import dd, llm
