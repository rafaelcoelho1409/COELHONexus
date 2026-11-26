"""Prepare bounded Ask queries for parallel indexed retrieval."""
from __future__ import annotations
from .. import service


def prepare_retrieval(state: dict) -> dict:
    """Normalize planned queries before source fan-out."""
    return {"queries": service.normalize_queries(state.get("queries"))}
