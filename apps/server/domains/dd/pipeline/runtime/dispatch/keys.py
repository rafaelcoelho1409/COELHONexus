"""Planner dispatch keys — the thread_id prefix.

The `docs-distiller/{slug}/{uuid}` format is a cross-boundary contract (the web pre-generates ids so the Cancel button
works from click 1), so the prefix lives here, next to its builder in `domain.make_thread_id`.
"""
from __future__ import annotations


THREAD_PREFIX = "docs-distiller"
