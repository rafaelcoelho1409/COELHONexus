"""Ingestion runtime — cross-cutting infra: dispatch, observability, progress.

Mirrors `domains.dd.planner.runtime` / `domains.dd.synth.runtime`'s shape.
No `cancel/`/`checkpoint/` here — ingestion isn't a resumable LangGraph
checkpointed pipeline, so those two concepts don't apply; cancellation is
folded into `progress/` instead (`raise_if_cancelled`)."""
from __future__ import annotations
from . import dispatch, observability, progress
