"""Pure helpers — no I/O. Used by the resume catch-up path to detect
IMPLEMENTED nodes that haven't run for a thread (e.g. a node landed AFTER
the thread already completed → LangGraph would short-circuit `ainvoke(None)`
because the prior checkpoint's END marker is already consumed).
"""
from __future__ import annotations

from ...graph import IMPLEMENTED, NODE_TO_FIELD


def missing_implemented_nodes(state: dict) -> list[str]:
    """IMPLEMENTED node names whose primary output field is missing/empty."""
    missing: list[str] = []
    for name in IMPLEMENTED:
        field = NODE_TO_FIELD.get(name)
        if not field:
            continue
        val = state.get(field)
        if val is None or val == "" or val == []:
            missing.append(name)
    return missing
