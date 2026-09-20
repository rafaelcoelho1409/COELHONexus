"""domains — business domains (bounded contexts): dd (Docs Distiller), llm (rotator/credentials),
rr (Research Radar), ycs (YouCanSee).

Each domain owns its router, schemas, service/graph code, tasks and exceptions.
Cross-domain references go through the dotted `domains.*` path (see
docs/CODE-CONVENTIONS.md §8) rather than bare `from domains.x.y import z` imports.
`dd`, `rr`, and `ycs` have all completed the §8 rollout; `llm` joins for
`llm.rotator.chain` + `llm.embeddings` specifically (needed by dd/planner's,
rr/agent's, and ycs/rag's node services).
"""
from __future__ import annotations

from . import dd, llm, rr, ycs
