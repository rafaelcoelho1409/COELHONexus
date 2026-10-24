"""Ingestion runtime — cross-cutting infra: cancel, dispatch, progress.

Mirrors `domains.dd.planner.runtime` / `domains.dd.synth.runtime`'s shape.
No `checkpoint/` here — ingestion isn't a resumable LangGraph checkpointed
pipeline, so that concept doesn't apply. `Progress.raise_if_cancelled` reads
the flag through `cancel/`."""
from __future__ import annotations
from . import cancel, dispatch, progress
