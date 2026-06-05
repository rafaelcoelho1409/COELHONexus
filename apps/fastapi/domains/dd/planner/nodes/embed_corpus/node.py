"""Substep 2 — embed_corpus: LangGraph node shell.

One-shot embedding pass over the entire corpus through the NIM rotator
(`dd-embed` group). Reads page bodies, chunks token-aware, embeds as
`passage`, L2-normalizes, mean-pools per doc, persists a compact .npz
blob keyed by manifest hash.

All orchestration lives in service.embed_corpus_run.

State writes:
  embeddings_ref — MinIO key of the .npz blob (or "" if no input)
  embed_stats    — observability dict (files / dim / cache_hit / wall_ms
                   / store_path / chunked_count / model)
"""
from __future__ import annotations

from ...runtime.observability import traced
from ...state import PlannerState

from .service import embed_corpus_run


@traced("embed_corpus")
async def embed_corpus(state: PlannerState) -> dict:
    return await embed_corpus_run(state)
