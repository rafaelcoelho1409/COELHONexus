"""embed_corpus node shell — token-aware chunking + NIM passage embed + L2-norm + mean-pool per doc, cached under manifest hash."""
from __future__ import annotations

from ...runtime.observability import traced
from ...state import PlannerState

from .service import embed_corpus_run


@traced("embed_corpus")
async def embed_corpus(state: PlannerState) -> dict:
    return await embed_corpus_run(state)
