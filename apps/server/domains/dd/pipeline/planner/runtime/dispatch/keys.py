"""Planner dispatch keys — thread_id key shape.

The `docs-distiller/{slug}/{uuid}` format is a cross-boundary contract
(JS pre-generates ids so the Cancel button works from click 1), so the
prefix and its builder live together here.
"""
from __future__ import annotations

import uuid


THREAD_PREFIX = "docs-distiller"


def make_thread_id(slug: str) -> str:
    """Per-planner-run thread_id. Format: `docs-distiller/{slug}/{uuid}`.
    Matches the JS-pre-generated id format so the Cancel button has a real
    thread_id from click 1 (no 'pending' dead-zone)."""
    return f"{THREAD_PREFIX}/{slug}/{uuid.uuid4()}"
